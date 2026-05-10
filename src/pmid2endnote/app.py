"""Application service used by both the CLI and GUI."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import os
from pathlib import Path

from pmid2endnote.enw import validate_enw_file, write_enw
from pmid2endnote.errors import InputDocumentError, PMID2EndNoteError, PubMedFetchError, PubMedParseError
from pmid2endnote.models import IdentifierKind, IdentifierResolution, ReferenceRecord
from pmid2endnote.nbib import pmids_in_nbib_text, validate_nbib_file
from pmid2endnote.pubmed import PubMedClient
from pmid2endnote.references import ReferenceResolver
from pmid2endnote.report import create_report, write_report
from pmid2endnote.settings import resolve_email, save_email
from pmid2endnote.word import (
    ReplacementOptions,
    ScanResult,
    default_enw_path,
    default_nbib_path,
    default_output_path,
    default_report_path,
    replace_pmids_in_docx,
    scan_docx,
)


ENDNOTE_INSTRUCTIONS = (
    "Open EndNote and import {enw_file} into the target EndNote library using the "
    "EndNote Import option. In EndNote Temporary Citation preferences, set Use field "
    "instead of Record Number to Accession Number. Then open {output_docx} in Word "
    "and run EndNote > Update Citations and Bibliography."
)


StatusCallback = Callable[[str], None]
IdentifierKey = tuple[IdentifierKind, str]


@dataclass(frozen=True)
class ProcessingOptions:
    """User-selected options for one PMID2EndNote processing run."""

    input_docx: Path
    email: str | None = None
    output_docx: Path | None = None
    nbib_file: Path | None = None
    enw_file: Path | None = None
    report_file: Path | None = None
    api_key: str | None = None
    include_tables: bool = True
    include_comments: bool = True
    include_headers: bool = False
    include_footers: bool = False
    include_footnotes: bool = False
    dry_run: bool = False
    keep_pmid_text: bool = False
    mark_unresolved: bool = False
    scan_parenthetical_pmids: bool = False
    scan_dois: bool = True
    scan_bare_dois: bool = False
    doi_source: str = "auto"
    import_format: str = "enw"
    save_email: bool = True


@dataclass(frozen=True)
class ProcessingResult:
    """Result of one processing run."""

    exit_code: int
    report: dict
    output_docx: Path
    nbib_file: Path
    enw_file: Path
    report_file: Path
    messages: tuple[str, ...]


def process_document(
    options: ProcessingOptions,
    *,
    status_callback: StatusCallback | None = None,
) -> ProcessingResult:
    """Run the full scan, fetch, replace, and report workflow."""

    status = status_callback or (lambda _message: None)
    input_docx = options.input_docx.resolve()
    output_docx = (options.output_docx or default_output_path(input_docx)).resolve()
    nbib_file = (options.nbib_file or default_nbib_path(input_docx)).resolve()
    enw_file = (options.enw_file or default_enw_path(input_docx)).resolve()
    report_file = (options.report_file or default_report_path(input_docx)).resolve()
    api_key = options.api_key or os.environ.get("NCBI_API_KEY")
    messages: list[str] = []

    report = create_report(
        input_docx=input_docx,
        output_docx=output_docx,
        nbib_file=nbib_file,
        enw_file=enw_file,
    )
    report["scan_parenthetical_pmids"] = options.scan_parenthetical_pmids
    report["scan_dois"] = options.scan_dois
    report["scan_bare_dois"] = options.scan_bare_dois
    report["doi_source"] = options.doi_source
    report["import_format"] = options.import_format
    report["include_comments"] = options.include_comments

    if options.import_format != "enw":
        message = "Only EndNote Tagged Import format is supported: --import-format enw"
        report["errors"].append(message)
        write_report(report, report_file)
        return ProcessingResult(2, report, output_docx, nbib_file, enw_file, report_file, (message,))

    replacement_options = ReplacementOptions(
        include_tables=options.include_tables,
        include_comments=options.include_comments,
        include_headers=options.include_headers,
        include_footers=options.include_footers,
        include_footnotes=options.include_footnotes,
        dry_run=options.dry_run,
        keep_pmid_text=options.keep_pmid_text,
        mark_unresolved=options.mark_unresolved,
        scan_parenthetical_pmids=options.scan_parenthetical_pmids,
        scan_dois=options.scan_dois,
        scan_bare_dois=options.scan_bare_dois,
    )

    try:
        status("Scanning Word document for PMID/DOI blocks...")
        scan_result = scan_docx(input_docx, replacement_options)
    except InputDocumentError as exc:
        report["errors"].append(str(exc))
        write_report(report, report_file)
        return ProcessingResult(2, report, output_docx, nbib_file, enw_file, report_file, (str(exc),))

    report["warnings"].extend(scan_result.warnings)
    report["unique_pmids"] = scan_result.unique_pmids
    report["unique_identifiers"] = [
        {"kind": kind, "normalized": value} for kind, value in scan_result.unique_identifiers
    ]
    report["total_pmid_occurrences"] = sum(
        len(occurrence.block.pmids) for occurrence in scan_result.occurrences
    )
    report["total_identifier_occurrences"] = sum(
        len(occurrence.block.identifiers) for occurrence in scan_result.occurrences
    )
    source_map = _source_map(scan_result)

    if not scan_result.unique_identifiers:
        write_report(report, report_file)
        messages.extend(
            [
                "No matching PMID or DOI blocks were found. No Word or EndNote files were written.",
                f"Wrote report: {report_file}",
            ]
        )
        return ProcessingResult(0, report, output_docx, nbib_file, enw_file, report_file, tuple(messages))

    email = resolve_email(options.email)
    if not email:
        message = (
            "A PubMed/NCBI email address is required. Enter it in the macOS launcher, "
            "pass --email, or set PMID2ENDNOTE_EMAIL."
        )
        report["errors"].append(message)
        write_report(report, report_file)
        return ProcessingResult(2, report, output_docx, nbib_file, enw_file, report_file, (message,))

    if options.save_email:
        try:
            save_email(email)
        except OSError as exc:
            report["warnings"].append(f"Could not save PubMed email setting: {exc}")

    try:
        status(f"Resolving {len(scan_result.unique_identifiers)} unique identifier(s)...")
        client = PubMedClient(email=email, api_key=api_key)
        resolver = ReferenceResolver(pubmed_client=client, doi_source=options.doi_source)
        resolutions = resolver.resolve(scan_result.unique_identifiers)
        records = list(resolver.references_by_key.values())
        records_by_identifier = _records_by_identifier(resolutions)

        resolved_pmids = _resolved_pmids(records)
        unresolved_pmids = [
            value
            for kind, value in scan_result.unique_identifiers
            if kind == "pmid" and resolutions.get((kind, value), _empty_resolution(kind, value)).reference is None
        ]
        report["resolved_pmids"] = resolved_pmids
        report["unresolved_pmids"] = unresolved_pmids
        included_keys: set[str] = set()
        included_pmids_in_nbib: set[str] = set()

        unresolved_identifiers = [
            f"{kind.upper()}:{value}"
            for kind, value in scan_result.unique_identifiers
            if resolutions.get((kind, value), _empty_resolution(kind, value)).reference is None
        ]
        if unresolved_identifiers:
            report["warnings"].append(
                "Unresolved identifiers were left unchanged unless marked by --mark-unresolved: "
                + ", ".join(unresolved_identifiers)
            )

        if not records:
            report["errors"].append("No identifiers could be resolved into EndNote import records.")
            report["identifier_statuses"] = build_identifier_statuses(
                scan_result=scan_result,
                resolutions=resolutions,
                included_keys=set(),
                replacements=[],
            )
            report["pmid_statuses"] = build_pmid_statuses(
                scan_result=scan_result,
                resolved_pmids=set(),
                included_pmids=set(),
                replacements=[],
                sources_by_pmid=source_map,
                warnings_by_pmid=_warnings_by_pmid(scan_result.unique_pmids, report["warnings"]),
            )
            write_report(report, report_file)
            return ProcessingResult(
                2,
                report,
                output_docx,
                nbib_file,
                enw_file,
                report_file,
                ("Error: no identifiers could be resolved.", f"Wrote report: {report_file}"),
            )

        if not options.dry_run:
            status("Writing EndNote Tagged Import file...")
            enw_file.parent.mkdir(parents=True, exist_ok=True)
            enw_file.write_text(write_enw(records), encoding="utf-8")
            included_keys, enw_errors = validate_enw_file(enw_file, records)
            if enw_errors:
                report["errors"].extend(enw_errors)
                report["identifier_statuses"] = build_identifier_statuses(
                    scan_result=scan_result,
                    resolutions=resolutions,
                    included_keys=included_keys,
                    replacements=[],
                )
                report["pmid_statuses"] = build_pmid_statuses(
                    scan_result=scan_result,
                    resolved_pmids=set(resolved_pmids),
                    included_pmids=set(),
                    replacements=[],
                    sources_by_pmid=source_map,
                    warnings_by_pmid=_warnings_by_pmid(scan_result.unique_pmids, enw_errors),
                )
                write_report(report, report_file)
                messages.extend(
                    [
                        "Error: .endnote-import.enw validation failed.",
                        "The Word document was not modified.",
                        f"Wrote report: {report_file}",
                    ]
                )
                return ProcessingResult(
                    2, report, output_docx, nbib_file, enw_file, report_file, tuple(messages)
                )

            included_pmids_in_nbib = _write_auxiliary_nbib(
                client=client,
                records=records,
                nbib_file=nbib_file,
                warnings=report["warnings"],
            )
        else:
            included_keys = {record.citation_key for record in records}
            included_pmids_in_nbib = _dry_run_auxiliary_nbib_pmids(client, records, report["warnings"])

        status("Writing modified Word document...")
        replacement_result = replace_pmids_in_docx(
            input_docx=input_docx,
            output_docx=output_docx,
            records_by_identifier=records_by_identifier,
            options=replacement_options,
        )
        report["replacements"] = replacement_result.replacements
        report["warnings"].extend(replacement_result.warnings)
        if replacement_result.backup_path is not None:
            report["warnings"].append(f"Created backup copy: {replacement_result.backup_path}")
        if not options.dry_run and not output_docx.exists():
            report["errors"].append(f"Modified Word document was not written: {output_docx}")
            write_report(report, report_file)
            return ProcessingResult(
                2,
                report,
                output_docx,
                nbib_file,
                enw_file,
                report_file,
                ("Error: modified Word document was not written.", f"Wrote report: {report_file}"),
            )

        report["identifier_statuses"] = build_identifier_statuses(
            scan_result=scan_result,
            resolutions=resolutions,
            included_keys=included_keys,
            replacements=replacement_result.replacements,
        )
        report["pmid_statuses"] = build_pmid_statuses(
            scan_result=scan_result,
            resolved_pmids=set(resolved_pmids),
            included_pmids=included_pmids_in_nbib,
            replacements=replacement_result.replacements,
            sources_by_pmid=source_map,
            warnings_by_pmid=_warnings_by_pmid(scan_result.unique_pmids, report["warnings"]),
        )

    except (PubMedFetchError, PubMedParseError) as exc:
        report["errors"].append(str(exc))
        report["identifier_statuses"] = build_identifier_statuses(
            scan_result=scan_result,
            resolutions={},
            included_keys=set(),
            replacements=[],
            extra_warnings=[str(exc)],
        )
        report["pmid_statuses"] = build_pmid_statuses(
            scan_result=scan_result,
            resolved_pmids=set(),
            included_pmids=set(),
            replacements=[],
            sources_by_pmid=source_map,
            warnings_by_pmid={pmid: [str(exc)] for pmid in scan_result.unique_pmids},
        )
        write_report(report, report_file)
        messages.extend(
            [
                f"Error: {exc}",
                "The Word document was not modified.",
                f"Wrote report: {report_file}",
            ]
        )
        return ProcessingResult(2, report, output_docx, nbib_file, enw_file, report_file, tuple(messages))
    except PMID2EndNoteError as exc:
        report["errors"].append(str(exc))
        report["identifier_statuses"] = build_identifier_statuses(
            scan_result=scan_result,
            resolutions={},
            included_keys=set(),
            replacements=report["replacements"],
            extra_warnings=[str(exc)],
        )
        report["pmid_statuses"] = build_pmid_statuses(
            scan_result=scan_result,
            resolved_pmids=set(report["resolved_pmids"]),
            included_pmids=set(),
            replacements=report["replacements"],
            sources_by_pmid=source_map,
            warnings_by_pmid={pmid: [str(exc)] for pmid in scan_result.unique_pmids},
        )
        write_report(report, report_file)
        messages.extend([f"Error: {exc}", f"Wrote report: {report_file}"])
        return ProcessingResult(2, report, output_docx, nbib_file, enw_file, report_file, tuple(messages))

    write_report(report, report_file)

    if options.dry_run:
        messages.extend(
            [
                "Dry run complete. No .docx, .enw, or .nbib files were written.",
                f"Wrote report: {report_file}",
            ]
        )
    else:
        messages.extend(
            [
                f"Wrote modified Word document: {output_docx}",
                f"Wrote EndNote Tagged Import file: {enw_file}",
                f"Wrote auxiliary PubMed/NLM file: {nbib_file}"
                if nbib_file.exists()
                else "No auxiliary .references.nbib file was written because no PubMed records were resolved.",
                f"Wrote report: {report_file}",
                ENDNOTE_INSTRUCTIONS.format(enw_file=enw_file, output_docx=output_docx),
            ]
        )

    return ProcessingResult(0, report, output_docx, nbib_file, enw_file, report_file, tuple(messages))


def build_identifier_statuses(
    *,
    scan_result: ScanResult,
    resolutions: dict[IdentifierKey, IdentifierResolution],
    included_keys: set[str],
    replacements: list[dict],
    extra_warnings: list[str] | None = None,
) -> list[dict]:
    """Build per-identifier-occurrence report statuses."""

    replacement_counts = _replacement_counts(replacements)
    statuses: list[dict] = []
    for occurrence in scan_result.occurrences:
        block = occurrence.block
        for identifier in block.identifiers:
            key = (block.kind, identifier)
            resolution = resolutions.get(key, _empty_resolution(block.kind, identifier))
            reference = resolution.reference
            warnings = list(resolution.warnings)
            warnings.extend(reference.warnings if reference else ())
            if extra_warnings:
                warnings.extend(extra_warnings)
            if reference is None:
                warnings.append(f"Unresolved {block.kind.upper()}: {identifier}")
            elif reference.citation_key not in included_keys:
                warnings.append("Resolved reference was not validated in the EndNote import file.")
            count = replacement_counts.get(key, 0)
            if count == 0:
                warnings.append("Identifier was not replaced in the Word document.")
            statuses.append(
                {
                    "input": block.original_text,
                    "kind": block.kind,
                    "normalized": identifier,
                    "resolved": reference is not None,
                    "metadata_source": resolution.metadata_source,
                    "pmid": reference.pmid if reference else None,
                    "doi": reference.doi if reference else (identifier if block.kind == "doi" else None),
                    "citation_key": reference.citation_key if reference else None,
                    "included_in_import": reference.citation_key in included_keys if reference else False,
                    "replacement_count": count,
                    "source": block.source,
                    "location": occurrence.location.as_report_dict(),
                    "warnings": _dedupe(warnings),
                }
            )
    return statuses


def build_pmid_statuses(
    *,
    scan_result: ScanResult,
    resolved_pmids: set[str],
    included_pmids: set[str],
    replacements: list[dict],
    sources_by_pmid: dict[str, set[str]],
    warnings_by_pmid: dict[str, list[str]] | None = None,
) -> list[dict]:
    """Build per-PMID compatibility report statuses."""

    unique_pmids = scan_result.unique_pmids
    replacement_counts = {pmid: 0 for pmid in unique_pmids}
    for replacement in replacements:
        for pmid in replacement.get("pmids", []):
            if pmid in replacement_counts:
                replacement_counts[pmid] += 1

    warnings_by_pmid = warnings_by_pmid or {}
    statuses = []
    for pmid in unique_pmids:
        warnings = list(warnings_by_pmid.get(pmid, []))
        if pmid not in resolved_pmids and not any("unresolved" in warning.lower() for warning in warnings):
            warnings.append("PMID was unresolved by PubMed.")
        if pmid in resolved_pmids and pmid not in included_pmids:
            warnings.append("PMID was resolved but not validated in the auxiliary .nbib file.")
        if replacement_counts.get(pmid, 0) == 0:
            warnings.append("PMID was not replaced in the Word document.")

        statuses.append(
            {
                "pmid": pmid,
                "resolved": pmid in resolved_pmids,
                "included_in_nbib": pmid in included_pmids,
                "replacement_count": replacement_counts.get(pmid, 0),
                "sources": sorted(sources_by_pmid.get(pmid, set())),
                "warnings": _dedupe(warnings),
            }
        )
    return statuses


def _records_by_identifier(
    resolutions: dict[IdentifierKey, IdentifierResolution],
) -> dict[IdentifierKey, ReferenceRecord]:
    return {
        key: resolution.reference
        for key, resolution in resolutions.items()
        if resolution.reference is not None
    }


def _resolved_pmids(records: Iterable[ReferenceRecord]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for record in records:
        if record.pmid and record.pmid not in seen:
            seen.add(record.pmid)
            ordered.append(record.pmid)
    return ordered


def _write_auxiliary_nbib(
    *,
    client: PubMedClient,
    records: list[ReferenceRecord],
    nbib_file: Path,
    warnings: list[str],
) -> set[str]:
    pmids = _resolved_pmids(records)
    if not pmids:
        return set()
    try:
        nbib_file.parent.mkdir(parents=True, exist_ok=True)
        nbib_file.write_text(client.fetch_nbib(pmids), encoding="utf-8")
        included_pmids, nbib_errors = validate_nbib_file(nbib_file, pmids)
        warnings.extend(nbib_errors)
        return included_pmids
    except PubMedFetchError as exc:
        warnings.append(f"Auxiliary .nbib file was not written: {exc}")
        return set()


def _dry_run_auxiliary_nbib_pmids(
    client: PubMedClient,
    records: list[ReferenceRecord],
    warnings: list[str],
) -> set[str]:
    pmids = _resolved_pmids(records)
    if not pmids:
        return set()
    try:
        return pmids_in_nbib_text(client.fetch_nbib(pmids))
    except PubMedFetchError as exc:
        warnings.append(f"Auxiliary .nbib dry-run validation failed: {exc}")
        return set()


def _replacement_counts(replacements: list[dict]) -> Counter[IdentifierKey]:
    counts: Counter[IdentifierKey] = Counter()
    for replacement in replacements:
        identifiers = replacement.get("identifiers")
        if identifiers:
            for item in identifiers:
                counts[(item["kind"], item["normalized"])] += 1
            continue
        for pmid in replacement.get("pmids", []):
            counts[("pmid", pmid)] += 1
        for doi in replacement.get("dois", []):
            counts[("doi", doi)] += 1
    return counts


def _source_map(scan_result: ScanResult) -> dict[str, set[str]]:
    sources: dict[str, set[str]] = {}
    for occurrence in scan_result.occurrences:
        for pmid in occurrence.block.pmids:
            sources.setdefault(pmid, set()).add(occurrence.block.source)
    return sources


def _warnings_by_pmid(pmids: list[str], warnings: list[str]) -> dict[str, list[str]]:
    mapped: dict[str, list[str]] = {pmid: [] for pmid in pmids}
    for warning in warnings:
        for pmid in pmids:
            if pmid in warning:
                mapped[pmid].append(warning)
    return {pmid: values for pmid, values in mapped.items() if values}


def _empty_resolution(kind: IdentifierKind, value: str) -> IdentifierResolution:
    return IdentifierResolution(kind=kind, normalized=value, reference=None)


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))

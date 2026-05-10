"""Word document scanning and identifier block replacement."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from pmid2endnote.endnote import make_temporary_citation
from pmid2endnote.errors import InputDocumentError, WordProcessingError
from pmid2endnote.models import IdentifierKind, ReferenceRecord
from pmid2endnote.pubmed import PubMedRecord
from pmid2endnote.references import reference_from_pubmed
from pmid2endnote.scanner import (
    IdentifierBlock,
    extract_unique_identifiers,
    extract_unique_pmids,
    scan_text,
)


IdentifierKey = tuple[IdentifierKind, str]


@dataclass(frozen=True)
class TextLocation:
    """Location of a paragraph-like text container in a Word document."""

    part: str
    paragraph_index: int
    comment_id: int | None = None

    def as_report_dict(self) -> dict[str, Any]:
        location = {
            "part": self.part,
            "paragraph_index": self.paragraph_index,
        }
        if self.comment_id is not None:
            location["comment_id"] = self.comment_id
        return location


@dataclass(frozen=True)
class DocumentPmidOccurrence:
    """An identifier block found in a Word paragraph-like location."""

    block: IdentifierBlock
    location: TextLocation


@dataclass(frozen=True)
class ReplacementOptions:
    """Options that control Word replacement behavior."""

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


@dataclass(frozen=True)
class ScanResult:
    """PMID scanning result for a Word document."""

    occurrences: list[DocumentPmidOccurrence]
    unique_pmids: list[str]
    unique_identifiers: list[IdentifierKey]
    warnings: list[str]


@dataclass(frozen=True)
class ReplacementResult:
    """Replacement result for a Word document."""

    replacements: list[dict[str, Any]]
    warnings: list[str]
    backup_path: Path | None = None


def default_output_path(input_docx: Path) -> Path:
    return input_docx.with_name(f"{input_docx.stem}.endnote.docx")


def default_nbib_path(input_docx: Path) -> Path:
    return input_docx.with_name(f"{input_docx.stem}.references.nbib")


def default_enw_path(input_docx: Path) -> Path:
    return input_docx.with_name(f"{input_docx.stem}.endnote-import.enw")


def default_report_path(input_docx: Path) -> Path:
    return input_docx.with_name(f"{input_docx.stem}.pmid2endnote.report.json")


def validate_input_docx(path: Path) -> None:
    if not path.exists():
        raise InputDocumentError(f"Input .docx does not exist: {path}")
    if not path.is_file():
        raise InputDocumentError(f"Input path is not a file: {path}")
    if path.suffix.lower() != ".docx":
        raise InputDocumentError(f"Input file must be a .docx document: {path}")


def scan_docx(path: Path, options: ReplacementOptions | None = None) -> ScanResult:
    """Scan a .docx document for PMID blocks."""

    options = options or ReplacementOptions()
    validate_input_docx(path)
    document = _open_document(path)
    warnings: list[str] = []
    occurrences: list[DocumentPmidOccurrence] = []

    for paragraph, location in _iter_scannable_paragraphs(document, options):
        blocks = scan_text(
            paragraph.text,
            scan_parenthetical_pmids=options.scan_parenthetical_pmids,
            scan_dois=options.scan_dois,
            scan_bare_dois=options.scan_bare_dois,
        )
        if not blocks:
            continue
        if _paragraph_has_unsafe_fields(paragraph):
            warnings.append(
                "Skipped PMID block in paragraph with field or hidden text content at "
                f"{location.part} paragraph {location.paragraph_index}."
            )
            continue
        occurrences.extend(DocumentPmidOccurrence(block=block, location=location) for block in blocks)

    if options.include_footnotes:
        warnings.append(
            "Footnotes were requested, but python-docx does not expose footnotes for safe "
            "replacement in this implementation."
        )

    return ScanResult(
        occurrences=occurrences,
        unique_pmids=extract_unique_pmids(occurrence.block for occurrence in occurrences),
        unique_identifiers=extract_unique_identifiers(
            occurrence.block for occurrence in occurrences
        ),
        warnings=warnings,
    )


def replace_pmids_in_docx(
    *,
    input_docx: Path,
    output_docx: Path,
    records_by_identifier: dict[IdentifierKey, ReferenceRecord] | None = None,
    records_by_pmid: dict[str, PubMedRecord] | None = None,
    options: ReplacementOptions | None = None,
) -> ReplacementResult:
    """Replace identifier blocks with EndNote temporary citations."""

    options = options or ReplacementOptions()
    resolved_records = _coerce_records_by_identifier(
        records_by_identifier=records_by_identifier,
        records_by_pmid=records_by_pmid,
    )
    validate_input_docx(input_docx)
    document = _open_document(input_docx)
    warnings: list[str] = []
    replacements: list[dict[str, Any]] = []

    for paragraph, location in _iter_scannable_paragraphs(document, options, include_comments=False):
        paragraph_replacements, paragraph_warnings = _replace_paragraph_blocks(
            paragraph=paragraph,
            location=location,
            records_by_identifier=resolved_records,
            keep_pmid_text=options.keep_pmid_text,
            mark_unresolved=options.mark_unresolved,
            dry_run=options.dry_run,
            scan_parenthetical_pmids=options.scan_parenthetical_pmids,
            scan_dois=options.scan_dois,
            scan_bare_dois=options.scan_bare_dois,
        )
        replacements.extend(paragraph_replacements)
        warnings.extend(paragraph_warnings)

    if options.include_comments:
        comment_replacements, comment_warnings = _insert_comment_pmids_at_anchors(
            document=document,
            options=options,
            records_by_identifier=resolved_records,
        )
        replacements.extend(comment_replacements)
        warnings.extend(comment_warnings)

    if options.include_footnotes:
        warnings.append(
            "Footnotes were requested, but python-docx does not expose footnotes for safe "
            "replacement in this implementation."
        )

    backup_path: Path | None = None
    if not options.dry_run:
        output_docx.parent.mkdir(parents=True, exist_ok=True)
        backup_path = _create_backup(input_docx)
        document.save(output_docx)

    return ReplacementResult(
        replacements=replacements,
        warnings=warnings,
        backup_path=backup_path,
    )


def _open_document(path: Path) -> DocxDocument:
    try:
        return Document(path)
    except Exception as exc:  # python-docx raises a mix of package errors here.
        raise InputDocumentError(f"Could not open .docx document: {path}") from exc


def _coerce_records_by_identifier(
    *,
    records_by_identifier: dict[IdentifierKey, ReferenceRecord] | None,
    records_by_pmid: dict[str, PubMedRecord] | None,
) -> dict[IdentifierKey, ReferenceRecord]:
    if records_by_identifier is not None:
        return records_by_identifier
    if records_by_pmid is None:
        return {}
    return {
        ("pmid", pmid): reference_from_pubmed(record)
        for pmid, record in records_by_pmid.items()
    }


def _iter_scannable_paragraphs(
    document: DocxDocument,
    options: ReplacementOptions,
    *,
    include_comments: bool = True,
) -> Iterator[tuple[Paragraph, TextLocation]]:
    body_index = 0
    for paragraph in document.paragraphs:
        yield paragraph, TextLocation(part="body", paragraph_index=body_index)
        body_index += 1

    if options.include_tables:
        table_index = 0
        for table in document.tables:
            for paragraph in _iter_table_paragraphs(table):
                yield paragraph, TextLocation(part="table", paragraph_index=table_index)
                table_index += 1

    if include_comments:
        yield from _iter_comment_paragraphs(document, options)

    if options.include_headers:
        header_index = 0
        for section in document.sections:
            for paragraph in section.header.paragraphs:
                yield paragraph, TextLocation(part="header", paragraph_index=header_index)
                header_index += 1

    if options.include_footers:
        footer_index = 0
        for section in document.sections:
            for paragraph in section.footer.paragraphs:
                yield paragraph, TextLocation(part="footer", paragraph_index=footer_index)
                footer_index += 1


def _iter_table_paragraphs(table: Table) -> Iterator[Paragraph]:
    for row in table.rows:
        for cell in row.cells:
            yield from cell.paragraphs
            for nested_table in cell.tables:
                yield from _iter_table_paragraphs(nested_table)


def _iter_comment_paragraphs(
    document: DocxDocument,
    options: ReplacementOptions,
) -> Iterator[tuple[Paragraph, TextLocation]]:
    if not options.include_comments:
        return
    comment_index = 0
    for comment in document.comments:
        comment_id = int(comment.comment_id)
        for paragraph in comment.paragraphs:
            yield paragraph, TextLocation(
                part="comment",
                paragraph_index=comment_index,
                comment_id=comment_id,
            )
            comment_index += 1
        if options.include_tables:
            for table in comment.tables:
                for paragraph in _iter_table_paragraphs(table):
                    yield paragraph, TextLocation(
                        part="comment",
                        paragraph_index=comment_index,
                        comment_id=comment_id,
                    )
                    comment_index += 1


def _paragraph_has_unsafe_fields(paragraph: Paragraph) -> bool:
    xml = paragraph._p.xml
    return any(marker in xml for marker in ("w:fldChar", "w:instrText", "w:vanish"))


def _replace_paragraph_blocks(
    *,
    paragraph: Paragraph,
    location: TextLocation,
    records_by_identifier: dict[IdentifierKey, ReferenceRecord],
    keep_pmid_text: bool,
    mark_unresolved: bool,
    dry_run: bool,
    scan_parenthetical_pmids: bool,
    scan_dois: bool,
    scan_bare_dois: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    text = paragraph.text
    blocks = scan_text(
        text,
        scan_parenthetical_pmids=scan_parenthetical_pmids,
        scan_dois=scan_dois,
        scan_bare_dois=scan_bare_dois,
    )
    if not blocks:
        return [], []

    if _paragraph_has_unsafe_fields(paragraph):
        return [], [
            "Skipped PMID block in paragraph with field or hidden text content at "
            f"{location.part} paragraph {location.paragraph_index}."
        ]

    planned: list[tuple[IdentifierBlock, str]] = []
    report_replacements: list[dict[str, Any]] = []
    warnings: list[str] = []

    for block in blocks:
        replacement_text = _replacement_for_block(
            block=block,
            records_by_identifier=records_by_identifier,
            keep_pmid_text=keep_pmid_text,
            mark_unresolved=mark_unresolved,
        )
        if replacement_text is None:
            identifier_label = _identifier_label(block.kind)
            warnings.append(
                f"Left unresolved {identifier_label} block unchanged at {location.part} paragraph "
                f"{location.paragraph_index}: {block.original_text}"
            )
            continue
        planned.append((block, replacement_text))
        report_replacements.append(
            {
                "original_text": block.original_text,
                "replacement_text": replacement_text,
                "pmids": list(block.pmids),
                "dois": list(block.dois),
                "identifiers": _block_identifier_report(block),
                "kind": block.kind,
                "source": block.source,
                "location": location.as_report_dict(),
            }
        )

    if not dry_run:
        for block, replacement_text in sorted(planned, key=lambda item: item[0].start, reverse=True):
            _replace_paragraph_range(paragraph, block.start, block.end, replacement_text)

    return report_replacements, warnings


def _insert_comment_pmids_at_anchors(
    *,
    document: DocxDocument,
    options: ReplacementOptions,
    records_by_identifier: dict[IdentifierKey, ReferenceRecord],
) -> tuple[list[dict[str, Any]], list[str]]:
    anchor_locations = _comment_anchor_locations(document, options)
    planned_by_comment: dict[int, list[tuple[IdentifierBlock, str, TextLocation]]] = {}
    warnings: list[str] = []

    for paragraph, comment_location in _iter_comment_paragraphs(document, options):
        blocks = scan_text(
            paragraph.text,
            scan_parenthetical_pmids=options.scan_parenthetical_pmids,
            scan_dois=options.scan_dois,
            scan_bare_dois=options.scan_bare_dois,
        )
        if not blocks:
            continue
        if _paragraph_has_unsafe_fields(paragraph):
            warnings.append(
                "Skipped PMID block in comment with field or hidden text content at "
                f"comment {comment_location.comment_id} paragraph {comment_location.paragraph_index}."
            )
            continue
        if comment_location.comment_id is None:
            continue
        for block in blocks:
            replacement_text = _replacement_for_block(
                block=block,
                records_by_identifier=records_by_identifier,
                keep_pmid_text=options.keep_pmid_text,
                mark_unresolved=options.mark_unresolved,
            )
            if replacement_text is None:
                identifier_label = _identifier_label(block.kind)
                warnings.append(
                    f"Left unresolved {identifier_label} block unchanged in comment {comment_location.comment_id}: "
                    f"{block.original_text}"
                )
                continue
            planned_by_comment.setdefault(comment_location.comment_id, []).append(
                (block, replacement_text, comment_location)
            )

    report_replacements: list[dict[str, Any]] = []
    for comment_id, planned in planned_by_comment.items():
        anchor = anchor_locations.get(comment_id)
        if anchor is None:
            identifiers = sorted(
                {identifier for block, _, _ in planned for identifier in block.identifiers}
            )
            warnings.append(
                f"Could not find document anchor for comment {comment_id}; identifiers not inserted: "
                + ", ".join(identifiers)
            )
            continue

        anchor_paragraph, anchor_location = anchor
        insertion_text = " " + " ".join(replacement_text for _, replacement_text, _ in planned)
        if not options.dry_run:
            _insert_text_after_comment_reference(anchor_paragraph, comment_id, insertion_text)

        for block, replacement_text, comment_location in planned:
            location = anchor_location.as_report_dict()
            location["comment_id"] = comment_id
            location["source_part"] = "comment"
            location["source_paragraph_index"] = comment_location.paragraph_index
            report_replacements.append(
                {
                    "original_text": block.original_text,
                    "replacement_text": replacement_text,
                    "pmids": list(block.pmids),
                    "dois": list(block.dois),
                    "identifiers": _block_identifier_report(block),
                    "kind": block.kind,
                    "source": block.source,
                    "location": location,
                }
            )

    return report_replacements, warnings


def _comment_anchor_locations(
    document: DocxDocument,
    options: ReplacementOptions,
) -> dict[int, tuple[Paragraph, TextLocation]]:
    anchors: dict[int, tuple[Paragraph, TextLocation]] = {}
    for paragraph, location in _iter_scannable_paragraphs(document, options, include_comments=False):
        for comment_id in _comment_ids_in_paragraph(paragraph):
            anchors.setdefault(comment_id, (paragraph, location))
    return anchors


def _comment_ids_in_paragraph(paragraph: Paragraph) -> Iterator[int]:
    for ref in paragraph._p.iter(qn("w:commentReference")):
        value = ref.get(qn("w:id"))
        if value is not None and value.isdigit():
            yield int(value)
    for ref in paragraph._p.iter(qn("w:commentRangeEnd")):
        value = ref.get(qn("w:id"))
        if value is not None and value.isdigit():
            yield int(value)


def _insert_text_after_comment_reference(
    paragraph: Paragraph,
    comment_id: int,
    text: str,
) -> None:
    anchor_element = _find_comment_anchor_element(paragraph, comment_id)
    if anchor_element is None:
        raise WordProcessingError(f"Could not find comment anchor for comment {comment_id}.")

    run = OxmlElement("w:r")
    text_element = OxmlElement("w:t")
    text_element.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text_element.text = text
    run.append(text_element)
    anchor_element.addnext(run)


def _find_comment_anchor_element(paragraph: Paragraph, comment_id: int):
    id_text = str(comment_id)
    for ref in paragraph._p.iter(qn("w:commentReference")):
        if ref.get(qn("w:id")) == id_text:
            return ref.getparent()
    for ref in paragraph._p.iter(qn("w:commentRangeEnd")):
        if ref.get(qn("w:id")) == id_text:
            return ref
    return None


def _replacement_for_block(
    *,
    block: IdentifierBlock,
    records_by_identifier: dict[IdentifierKey, ReferenceRecord],
    keep_pmid_text: bool,
    mark_unresolved: bool,
) -> str | None:
    resolved_records = [
        records_by_identifier[(block.kind, identifier)]
        for identifier in block.identifiers
        if (block.kind, identifier) in records_by_identifier
    ]
    unresolved_identifiers = [
        identifier
        for identifier in block.identifiers
        if (block.kind, identifier) not in records_by_identifier
    ]

    replacement_parts: list[str] = []
    if resolved_records:
        replacement_parts.append(make_temporary_citation(resolved_records))

    if unresolved_identifiers:
        if mark_unresolved or resolved_records:
            label = _identifier_label(block.kind, plural=len(unresolved_identifiers) > 1)
            replacement_parts.append(
                f"[unresolved {label}: {', '.join(unresolved_identifiers)}]"
            )
        else:
            return None

    replacement_text = " ".join(replacement_parts)
    if keep_pmid_text:
        replacement_text = f"{block.original_text} {replacement_text}"
    return replacement_text


def _identifier_label(kind: IdentifierKind, *, plural: bool = False) -> str:
    label = kind.upper()
    if plural:
        return label + "s"
    return label


def _block_identifier_report(block: IdentifierBlock) -> list[dict[str, str]]:
    return [
        {"kind": block.kind, "normalized": identifier, "source": block.source}
        for identifier in block.identifiers
    ]


def _replace_paragraph_range(paragraph: Paragraph, start: int, end: int, replacement: str) -> None:
    runs = list(paragraph.runs)
    if not runs:
        paragraph.text = paragraph.text[:start] + replacement + paragraph.text[end:]
        return

    spans: list[tuple[int, int, Any]] = []
    cursor = 0
    for run in runs:
        run_text = run.text
        run_start = cursor
        run_end = cursor + len(run_text)
        spans.append((run_start, run_end, run))
        cursor = run_end

    overlapping = [
        (run_start, run_end, run)
        for run_start, run_end, run in spans
        if run_start < end and run_end > start
    ]
    if not overlapping:
        raise WordProcessingError(
            f"Could not map paragraph replacement range {start}:{end} to Word runs."
        )

    first_start, first_end, first_run = overlapping[0]
    last_start, last_end, last_run = overlapping[-1]
    before = first_run.text[: max(0, start - first_start)]
    after = last_run.text[max(0, end - last_start) :]

    if first_run is last_run:
        first_run.text = before + replacement + after
        return

    first_run.text = before + replacement
    for _, _, run in overlapping[1:-1]:
        run.text = ""
    last_run.text = after


def _create_backup(input_docx: Path) -> Path:
    backup_path = input_docx.with_name(f"{input_docx.stem}.pmid2endnote.backup{input_docx.suffix}")
    if backup_path.exists():
        counter = 1
        while True:
            candidate = input_docx.with_name(
                f"{input_docx.stem}.pmid2endnote.backup.{counter}{input_docx.suffix}"
            )
            if not candidate.exists():
                backup_path = candidate
                break
            counter += 1
    shutil.copy2(input_docx, backup_path)
    return backup_path

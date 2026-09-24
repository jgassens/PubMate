"""Word document scanning and identifier block replacement."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
from typing import Any, Protocol, TypeVar

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run

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


class TextRange(Protocol):
    """A detected text block with offsets into a Word paragraph."""

    start: int
    end: int


TextRangeT = TypeVar("TextRangeT", bound=TextRange)
FieldDepthCache = dict[Any, dict[Any, int]]

REFERENCE_SECTION_HEADINGS = {
    "references",
    "bibliography",
    "works cited",
    "literature cited",
    "references cited",
}

REFERENCE_HEADING_PREFIX_RE = re.compile(
    r"""
    ^
    (?:
        \d+
        |
        [ivxlcdm]+
    )
    [.)]?
    \s+
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class ReplacementBlock:
    """One Word-text range to replace, potentially containing mixed identifiers."""

    original_text: str
    start: int
    end: int
    blocks: tuple[IdentifierBlock, ...]
    source: str

    @property
    def identifier_keys(self) -> tuple[IdentifierKey, ...]:
        keys: list[IdentifierKey] = []
        for block in self.blocks:
            keys.extend((block.kind, identifier) for identifier in block.identifiers)
        return tuple(keys)

    @property
    def pmids(self) -> tuple[str, ...]:
        return tuple(value for kind, value in self.identifier_keys if kind == "pmid")

    @property
    def dois(self) -> tuple[str, ...]:
        return tuple(value for kind, value in self.identifier_keys if kind == "doi")

    @property
    def kind(self) -> str:
        kinds = {kind for kind, _ in self.identifier_keys}
        return next(iter(kinds)) if len(kinds) == 1 else "mixed"


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
class ScannableParagraph:
    """A paragraph-like Word location with reference-section skip state."""

    paragraph: Paragraph
    location: TextLocation
    skipped_by_reference_section: bool = False


@dataclass(frozen=True)
class ReferenceSectionContext:
    """Reference-section metadata used for body/table/comment filtering."""

    paragraphs: list[ScannableParagraph]
    reference_section_start: dict[str, Any] | None
    anchor_locations: dict[int, tuple[Paragraph, TextLocation]]
    skipped_comment_ids: set[int]


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
    skip_reference_section: bool = True
    create_backup: bool = False


@dataclass(frozen=True)
class ScanResult:
    """PMID scanning result for a Word document."""

    occurrences: list[DocumentPmidOccurrence]
    unique_pmids: list[str]
    unique_identifiers: list[IdentifierKey]
    warnings: list[str]
    skipped_identifiers: list[dict[str, Any]]
    reference_section_start: dict[str, Any] | None


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


def is_reference_section_heading(text: str) -> bool:
    """Return True for conservative standalone bibliography headings."""

    normalized = " ".join(text.strip().split())
    normalized = normalized.strip(" \t\r\n:;.").lower()
    normalized = REFERENCE_HEADING_PREFIX_RE.sub("", normalized).strip()
    return normalized in REFERENCE_SECTION_HEADINGS


def scan_docx(path: Path, options: ReplacementOptions | None = None) -> ScanResult:
    """Scan a .docx document for PMID blocks."""

    options = options or ReplacementOptions()
    validate_input_docx(path)
    document = _open_document(path)
    warnings: list[str] = []
    occurrences: list[DocumentPmidOccurrence] = []
    skipped_identifiers: list[dict[str, Any]] = []
    context = _reference_section_context(document, options)
    field_depth_cache: FieldDepthCache = {}

    for item in context.paragraphs:
        paragraph = item.paragraph
        location = item.location
        blocks = scan_text(
            paragraph.text,
            scan_parenthetical_pmids=options.scan_parenthetical_pmids,
            scan_dois=options.scan_dois,
            scan_bare_dois=options.scan_bare_dois,
        )
        if not blocks:
            continue
        if item.skipped_by_reference_section:
            skipped_identifiers.extend(
                _skipped_identifier_reports(blocks, location, reason="reference_section")
            )
            continue
        replacement_blocks = _replacement_blocks_for_text(
            paragraph.text,
            scan_parenthetical_pmids=options.scan_parenthetical_pmids,
            scan_dois=options.scan_dois,
            scan_bare_dois=options.scan_bare_dois,
        )
        (
            replacement_blocks,
            field_or_hidden_blocks,
            tracked_change_blocks,
            non_text_blocks,
        ) = _partition_blocks_around_unsafe_content(
            paragraph,
            replacement_blocks,
            field_depth_cache,
        )
        if field_or_hidden_blocks:
            warnings.append(
                _unsafe_content_warning(location, len(field_or_hidden_blocks))
            )
        if non_text_blocks:
            warnings.append(
                _non_text_content_warning(location, len(non_text_blocks))
            )
        if tracked_change_blocks:
            warnings.append(
                _tracked_change_warning(location, len(tracked_change_blocks))
            )
        occurrences.extend(
            DocumentPmidOccurrence(block=block, location=location)
            for replacement_block in replacement_blocks
            for block in replacement_block.blocks
        )

    if options.include_comments:
        for paragraph, location in _iter_comment_paragraphs(document, options):
            blocks = scan_text(
                paragraph.text,
                scan_parenthetical_pmids=options.scan_parenthetical_pmids,
                scan_dois=options.scan_dois,
                scan_bare_dois=options.scan_bare_dois,
            )
            if not blocks:
                continue
            if location.comment_id in context.skipped_comment_ids:
                skipped_identifiers.extend(
                    _skipped_identifier_reports(blocks, location, reason="reference_section")
                )
                continue
            replacement_blocks = _replacement_blocks_for_text(
                paragraph.text,
                scan_parenthetical_pmids=options.scan_parenthetical_pmids,
                scan_dois=options.scan_dois,
                scan_bare_dois=options.scan_bare_dois,
            )
            (
                replacement_blocks,
                field_or_hidden_blocks,
                tracked_change_blocks,
                _,
            ) = _partition_blocks_around_unsafe_content(
                paragraph,
                replacement_blocks,
                field_depth_cache,
                check_run_editability=False,
            )
            if field_or_hidden_blocks:
                warnings.append(
                    _unsafe_content_warning(location, len(field_or_hidden_blocks))
                )
            if tracked_change_blocks:
                warnings.append(
                    _tracked_change_warning(location, len(tracked_change_blocks))
                )
            occurrences.extend(
                DocumentPmidOccurrence(block=block, location=location)
                for replacement_block in replacement_blocks
                for block in replacement_block.blocks
            )

    for paragraph, location in _iter_header_footer_paragraphs(document, options):
        blocks = scan_text(
            paragraph.text,
            scan_parenthetical_pmids=options.scan_parenthetical_pmids,
            scan_dois=options.scan_dois,
            scan_bare_dois=options.scan_bare_dois,
        )
        if not blocks:
            continue
        replacement_blocks = _replacement_blocks_for_text(
            paragraph.text,
            scan_parenthetical_pmids=options.scan_parenthetical_pmids,
            scan_dois=options.scan_dois,
            scan_bare_dois=options.scan_bare_dois,
        )
        (
            replacement_blocks,
            field_or_hidden_blocks,
            tracked_change_blocks,
            non_text_blocks,
        ) = _partition_blocks_around_unsafe_content(
            paragraph,
            replacement_blocks,
            field_depth_cache,
        )
        if field_or_hidden_blocks:
            warnings.append(
                _unsafe_content_warning(location, len(field_or_hidden_blocks))
            )
        if non_text_blocks:
            warnings.append(
                _non_text_content_warning(location, len(non_text_blocks))
            )
        if tracked_change_blocks:
            warnings.append(
                _tracked_change_warning(location, len(tracked_change_blocks))
            )
        occurrences.extend(
            DocumentPmidOccurrence(block=block, location=location)
            for replacement_block in replacement_blocks
            for block in replacement_block.blocks
        )

    if skipped_identifiers:
        warnings.append(
            f"Skipped {len(skipped_identifiers)} identifier block"
            f"{'' if len(skipped_identifiers) == 1 else 's'} in the detected reference section."
        )

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
        skipped_identifiers=skipped_identifiers,
        reference_section_start=context.reference_section_start,
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
    context = _reference_section_context(document, options)
    field_depth_cache: FieldDepthCache = {}

    for item in context.paragraphs:
        if item.skipped_by_reference_section:
            continue
        paragraph_replacements, paragraph_warnings = _replace_paragraph_blocks(
            paragraph=item.paragraph,
            location=item.location,
            records_by_identifier=resolved_records,
            keep_pmid_text=options.keep_pmid_text,
            mark_unresolved=options.mark_unresolved,
            dry_run=options.dry_run,
            scan_parenthetical_pmids=options.scan_parenthetical_pmids,
            scan_dois=options.scan_dois,
            scan_bare_dois=options.scan_bare_dois,
            field_depth_cache=field_depth_cache,
        )
        replacements.extend(paragraph_replacements)
        warnings.extend(paragraph_warnings)

    if options.include_comments:
        comment_replacements, comment_warnings = _insert_comment_pmids_at_anchors(
            document=document,
            options=options,
            records_by_identifier=resolved_records,
            context=context,
            field_depth_cache=field_depth_cache,
        )
        replacements.extend(comment_replacements)
        warnings.extend(comment_warnings)

    for paragraph, location in _iter_header_footer_paragraphs(document, options):
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
            field_depth_cache=field_depth_cache,
        )
        replacements.extend(paragraph_replacements)
        warnings.extend(paragraph_warnings)

    if options.include_footnotes:
        warnings.append(
            "Footnotes were requested, but python-docx does not expose footnotes for safe "
            "replacement in this implementation."
        )

    _validate_replacement_citations(replacements)

    backup_path: Path | None = None
    if not options.dry_run:
        output_docx.parent.mkdir(parents=True, exist_ok=True)
        if options.create_backup:
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


def _reference_section_context(
    document: DocxDocument,
    options: ReplacementOptions,
) -> ReferenceSectionContext:
    paragraphs: list[ScannableParagraph] = []
    reference_section_start: dict[str, Any] | None = None
    in_reference_section = False
    anchor_locations: dict[int, tuple[Paragraph, TextLocation]] = {}
    skipped_comment_ids: set[int] = set()

    for paragraph, location in _iter_body_table_paragraphs_in_order(document, options):
        if (
            options.skip_reference_section
            and not in_reference_section
            and is_reference_section_heading(paragraph.text)
        ):
            in_reference_section = True
            reference_section_start = location.as_report_dict()
        paragraphs.append(
            ScannableParagraph(
                paragraph=paragraph,
                location=location,
                skipped_by_reference_section=in_reference_section,
            )
        )
        for comment_id in _comment_ids_in_paragraph(paragraph):
            if in_reference_section and options.skip_reference_section:
                skipped_comment_ids.add(comment_id)
            else:
                anchor_locations.setdefault(comment_id, (paragraph, location))

    return ReferenceSectionContext(
        paragraphs=paragraphs,
        reference_section_start=reference_section_start,
        anchor_locations=anchor_locations,
        skipped_comment_ids=skipped_comment_ids,
    )


def _iter_body_table_paragraphs_in_order(
    document: DocxDocument,
    options: ReplacementOptions,
) -> Iterator[tuple[Paragraph, TextLocation]]:
    body_index = 0
    table_index = 0
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document), TextLocation(part="body", paragraph_index=body_index)
            body_index += 1
        elif child.tag == qn("w:tbl") and options.include_tables:
            table = Table(child, document)
            for paragraph in _iter_table_paragraphs(table):
                yield paragraph, TextLocation(part="table", paragraph_index=table_index)
                table_index += 1

def _iter_header_footer_paragraphs(
    document: DocxDocument,
    options: ReplacementOptions,
) -> Iterator[tuple[Paragraph, TextLocation]]:
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


def _iter_scannable_paragraphs(
    document: DocxDocument,
    options: ReplacementOptions,
    *,
    include_comments: bool = True,
) -> Iterator[tuple[Paragraph, TextLocation]]:
    context = _reference_section_context(document, options)
    for item in context.paragraphs:
        if not item.skipped_by_reference_section:
            yield item.paragraph, item.location
    if include_comments:
        for paragraph, location in _iter_comment_paragraphs(document, options):
            if location.comment_id not in context.skipped_comment_ids:
                yield paragraph, location
    yield from _iter_header_footer_paragraphs(document, options)


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


def _partition_blocks_around_unsafe_content(
    paragraph: Paragraph,
    blocks: list[TextRangeT],
    field_depth_cache: FieldDepthCache,
    *,
    check_run_editability: bool = True,
) -> tuple[list[TextRangeT], list[TextRangeT], list[TextRangeT], list[TextRangeT]]:
    """Keep blocks outside Word fields/hidden runs and reject overlapping blocks.

    Word and EndNote fields may safely coexist elsewhere in a paragraph. The
    previous paragraph-wide check discarded ordinary PMID text beside an
    existing citation field. If run offsets cannot be mapped exactly back to
    ``paragraph.text``, this helper deliberately retains the old fail-closed
    behavior.
    """

    mapped_runs = _paragraph_text_runs(paragraph)
    if mapped_runs is None:
        return [], blocks, [], []

    unsafe_ranges = _unsafe_text_ranges(paragraph, field_depth_cache, mapped_runs)
    if unsafe_ranges is None:
        return [], blocks, [], []

    safe: list[TextRangeT] = []
    field_or_hidden: list[TextRangeT] = []
    tracked_changes: list[TextRangeT] = []
    non_text: list[TextRangeT] = []
    for block in blocks:
        if _range_overlaps_any(block.start, block.end, unsafe_ranges):
            field_or_hidden.append(block)
        elif _identifier_touches_omitted_content(
            paragraph,
            mapped_runs,
            block.start,
            block.end,
        ):
            tracked_changes.append(block)
        elif not check_run_editability:
            safe.append(block)
        elif _mapped_runs_interrupted(mapped_runs, block.start, block.end):
            tracked_changes.append(block)
        elif _mapped_runs_can_replace_range(mapped_runs, block.start, block.end):
            safe.append(block)
        else:
            non_text.append(block)
    return safe, field_or_hidden, tracked_changes, non_text


def _unsafe_text_ranges(
    paragraph: Paragraph,
    field_depth_cache: FieldDepthCache,
    mapped_runs: list[tuple[Run, bool]],
) -> list[tuple[int, int]] | None:
    """Return visible spans controlled by fields, hyperlinks, or hidden formatting."""

    mapped_field_markers = {
        marker
        for run, _ in mapped_runs
        for marker in _iter_story_elements(run._r)
        if marker.tag == qn("w:fldChar")
    }
    paragraph_field_markers = [
        element
        for element in _iter_story_elements(paragraph._p)
        if element.tag == qn("w:fldChar")
    ]
    if any(marker not in mapped_field_markers for marker in paragraph_field_markers):
        # A marker inside tracked changes, structured content, or another
        # wrapper omitted from paragraph.text has no reliable text offset.
        return None

    ranges: list[tuple[int, int]] = []
    cursor = 0
    field_depth = _field_depth_at_paragraph_start(paragraph, field_depth_cache)
    for run, is_hyperlink_run in mapped_runs:
        field_types = [
            marker.get(qn("w:fldCharType"))
            for marker in _iter_story_elements(run._r)
            if marker.tag == qn("w:fldChar")
        ]
        begin_count = field_types.count("begin")
        end_count = field_types.count("end")
        field_depth += begin_count

        start = cursor
        cursor += len(run.text)
        is_hidden = bool(run._r.xpath(".//w:vanish"))
        if cursor > start and (field_depth > 0 or is_hidden or is_hyperlink_run):
            ranges.append((start, cursor))

        field_depth = max(0, field_depth - end_count)

    return ranges


def _field_depth_at_paragraph_start(
    paragraph: Paragraph,
    field_depth_cache: FieldDepthCache,
) -> int:
    """Return complex-field depth where *paragraph* starts in its Word story."""

    story_root = _paragraph_story_root(paragraph)
    depths = field_depth_cache.get(story_root)
    if depths is None:
        depths = _story_field_depths(story_root)
        field_depth_cache[story_root] = depths
    return depths.get(paragraph._p, 0)


def _paragraph_story_root(paragraph: Paragraph) -> Any:
    """Return the root of the Word story containing *paragraph*."""

    comment_tag = qn("w:comment")
    for ancestor in paragraph._p.iterancestors():
        if ancestor.tag == comment_tag:
            return ancestor
    return paragraph._p.getroottree().getroot()


def _story_field_depths(story_root: Any) -> dict[Any, int]:
    """Map each paragraph element in a story to its starting field depth."""

    paragraph_tag = qn("w:p")
    field_char_tag = qn("w:fldChar")
    field_char_type_attr = qn("w:fldCharType")
    field_depth = 0
    depths: dict[Any, int] = {}
    for element in _iter_story_elements(story_root):
        if element.tag == paragraph_tag:
            depths[element] = field_depth
        elif element.tag == field_char_tag:
            field_type = element.get(field_char_type_attr)
            if field_type == "begin":
                field_depth += 1
            elif field_type == "end":
                field_depth = max(0, field_depth - 1)
    return depths


def _iter_story_elements(story_root: Any) -> Iterator[Any]:
    """Yield story XML in order without entering nested or fallback stories."""

    nested_story_tag = qn("w:txbxContent")
    fallback_tag = (
        "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
    )
    elements = [story_root]
    while elements:
        element = elements.pop()
        yield element
        children = [
            child
            for child in element.iterchildren()
            if child.tag not in {nested_story_tag, fallback_tag}
        ]
        elements.extend(reversed(children))


def _paragraph_text_runs(paragraph: Paragraph) -> list[tuple[Run, bool]] | None:
    """Map ``paragraph.text`` to contributing runs in exact document order.

    python-docx 1.2.0 defines paragraph text as its direct runs and the runs in
    direct hyperlinks. ``iter_inner_content()`` is the public traversal that
    exposes those same elements in order. The boolean records hyperlink
    membership so callers never edit linked content.
    """

    mapped_runs: list[tuple[Run, bool]] = []
    for content in paragraph.iter_inner_content():
        if isinstance(content, Run):
            mapped_runs.append((content, False))
        else:
            mapped_runs.extend((run, True) for run in content.runs)

    if "".join(run.text for run, _ in mapped_runs) != paragraph.text:
        return None
    return mapped_runs


def _mapped_runs_can_replace_range(
    mapped_runs: list[tuple[Run, bool]],
    start: int,
    end: int,
) -> bool:
    """Return whether a range maps only to safely editable Word runs."""

    if start >= end:
        return False

    cursor = 0
    found_overlap = False
    for run, is_hyperlink_run in mapped_runs:
        run_start = cursor
        run_end = cursor + len(run.text)
        cursor = run_end
        if run_start == run_end or run_start >= end or run_end <= start:
            continue
        if is_hyperlink_run or not _run_has_only_safe_children(run):
            return False
        found_overlap = True
    return found_overlap


def _mapped_runs_interrupted(
    mapped_runs: list[tuple[Run, bool]],
    start: int,
    end: int,
) -> bool:
    """Return whether editing a text range would cross unsafe Word XML."""

    overlapping: list[Run] = []
    cursor = 0
    for run, _ in mapped_runs:
        run_start = cursor
        run_end = cursor + len(run.text)
        cursor = run_end
        if run_start < run_end and run_start < end and run_end > start:
            overlapping.append(run)

    if len(overlapping) < 2:
        return False

    parent = overlapping[0]._r.getparent()
    if parent is None or any(run._r.getparent() is not parent for run in overlapping[1:]):
        return True

    indexes = [parent.index(run._r) for run in overlapping]
    for left, right in zip(indexes, indexes[1:]):
        for element in parent[left + 1 : right]:
            if not _element_is_harmless_between_runs(element):
                return True
    return False


_HARMLESS_PARAGRAPH_MARKERS = frozenset(
    qn(f"w:{local_name}")
    for local_name in (
        "proofErr",
        "bookmarkStart",
        "bookmarkEnd",
        "commentRangeStart",
        "commentRangeEnd",
        "permStart",
        "permEnd",
    )
)

_OMITTED_CONTENT_CONTAINERS = frozenset(
    qn(f"w:{local_name}")
    for local_name in (
        "ins",
        "del",
        "moveFrom",
        "moveTo",
        "sdt",
        "smartTag",
        "customXml",
        "fldSimple",
    )
)

_UNSAFE_RANGE_MARKERS = frozenset(
    qn(f"w:{local_name}")
    for local_name in (
        "moveFromRangeStart",
        "moveFromRangeEnd",
        "moveToRangeStart",
        "moveToRangeEnd",
    )
)


def _element_is_harmless_between_runs(element: Any) -> bool:
    """Return whether an omitted sibling can remain between edited runs."""

    if element.tag in _HARMLESS_PARAGRAPH_MARKERS:
        return True
    if element.tag in _OMITTED_CONTENT_CONTAINERS | _UNSAFE_RANGE_MARKERS:
        return False
    if element.tag == qn("w:r"):
        return not _run_element_text(element) and _run_element_has_only_safe_children(
            element
        )
    return not _element_has_content(element)


def _identifier_touches_omitted_content(
    paragraph: Paragraph,
    mapped_runs: list[tuple[Run, bool]],
    start: int,
    end: int,
) -> bool:
    """Return whether omitted content is within or directly beside an identifier.

    ``paragraph.text`` excludes tracked insertions/deletions and content-control
    wrappers. Their boundary offset is still knowable from the surrounding
    mapped runs, so content at offsets from ``start`` through ``end`` may be
    part of the identifier that the visible text appears to contain.
    """

    children = list(paragraph._p)
    child_indexes = {child: index for index, child in enumerate(children)}
    visible_lengths = [0] * len(children)
    for run, _ in mapped_runs:
        top_level = _top_level_paragraph_child(paragraph, run._r)
        if top_level is None:
            return True
        visible_lengths[child_indexes[top_level]] += len(run.text)

    boundary = 0
    for child, visible_length in zip(children, visible_lengths):
        if (
            child.tag in _OMITTED_CONTENT_CONTAINERS
            and _element_has_content(child)
            and start <= boundary <= end
        ):
            return True
        boundary += visible_length
    return False


def _top_level_paragraph_child(paragraph: Paragraph, element: Any) -> Any | None:
    child = element
    while child.getparent() is not paragraph._p:
        child = child.getparent()
        if child is None:
            return None
    return child


def _element_has_content(element: Any) -> bool:
    """Return whether an omitted element contains text or non-text run content."""

    return any(
        _run_element_text(run)
        or any(
            child.tag not in {qn("w:rPr"), qn("w:t"), qn("w:lastRenderedPageBreak")}
            for child in run
        )
        for run in element.iter(qn("w:r"))
    )


def _run_element_text(run_element: Any) -> str:
    return "".join(
        text.text or ""
        for text in run_element.iter()
        if text.tag in {qn("w:t"), qn("w:delText"), qn("w:instrText")}
    )


_SAFE_EDITABLE_RUN_CHILDREN = frozenset(
    qn(f"w:{local_name}")
    for local_name in (
        "rPr",
        "t",
        "tab",
        "lastRenderedPageBreak",
    )
)


def _run_has_only_safe_children(run: Run) -> bool:
    """Return whether assigning ``run.text`` cannot remove protected content."""

    return _run_element_has_only_safe_children(run._r)


def _run_element_has_only_safe_children(run_element: Any) -> bool:
    """Return whether a run element contains only children preserved as text."""

    break_tag = qn("w:br")
    break_type_attr = qn("w:type")
    break_clear_attr = qn("w:clear")
    return all(
        child.tag in _SAFE_EDITABLE_RUN_CHILDREN
        or (
            child.tag == break_tag
            and child.get(break_type_attr) in (None, "textWrapping")
            and child.get(break_clear_attr) is None
        )
        for child in run_element
    )


def _range_overlaps_any(
    start: int,
    end: int,
    ranges: list[tuple[int, int]],
) -> bool:
    return any(start < range_end and end > range_start for range_start, range_end in ranges)


def _unsafe_content_warning(location: TextLocation, count: int) -> str:
    label = "identifier block" if count == 1 else "identifier blocks"
    return (
        f"Skipped {count} {label} overlapping field or hidden text content at "
        f"{location.part} paragraph {location.paragraph_index}."
    )


def _non_text_content_warning(location: TextLocation, count: int) -> str:
    label = "identifier block" if count == 1 else "identifier blocks"
    return (
        f"Skipped {count} {label} sharing a Word run with non-text content "
        f"(image, text box, page break, etc.) at {location.part} paragraph "
        f"{location.paragraph_index}."
    )


def _tracked_change_warning(location: TextLocation, count: int) -> str:
    label = "identifier block" if count == 1 else "identifier blocks"
    return (
        f"Skipped {count} {label} next to tracked changes or content controls "
        f"that may alter the identifier at "
        f"{location.part} paragraph {location.paragraph_index}."
    )


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
    field_depth_cache: FieldDepthCache,
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

    replacement_blocks = _replacement_blocks_for_text(
        text,
        scan_parenthetical_pmids=scan_parenthetical_pmids,
        scan_dois=scan_dois,
        scan_bare_dois=scan_bare_dois,
    )
    (
        replacement_blocks,
        field_or_hidden_blocks,
        tracked_change_blocks,
        non_text_blocks,
    ) = _partition_blocks_around_unsafe_content(
        paragraph,
        replacement_blocks,
        field_depth_cache,
    )
    planned: list[tuple[ReplacementBlock, str, dict[str, Any]]] = []
    report_replacements: list[dict[str, Any]] = []
    warnings = (
        [_unsafe_content_warning(location, len(field_or_hidden_blocks))]
        if field_or_hidden_blocks
        else []
    )
    if non_text_blocks:
        warnings.append(_non_text_content_warning(location, len(non_text_blocks)))
    if tracked_change_blocks:
        warnings.append(_tracked_change_warning(location, len(tracked_change_blocks)))

    for block in replacement_blocks:
        replacement_text = _replacement_for_block(
            block=block,
            records_by_identifier=records_by_identifier,
            keep_pmid_text=keep_pmid_text,
            mark_unresolved=mark_unresolved,
        )
        if replacement_text is None:
            identifier_label = _replacement_block_label(block)
            warnings.append(
                f"Left unresolved {identifier_label} block unchanged at {location.part} paragraph "
                f"{location.paragraph_index}: {block.original_text}"
            )
            continue
        report = {
            "original_text": block.original_text,
            "replacement_text": replacement_text,
            "pmids": list(block.pmids),
            "dois": list(block.dois),
            "identifiers": _block_identifier_report(block),
            "kind": block.kind,
            "source": block.source,
            "location": location.as_report_dict(),
        }
        planned.append((block, replacement_text, report))
        if dry_run:
            report_replacements.append(report)

    if not dry_run:
        applied_reports: list[dict[str, Any]] = []
        application_warnings: list[str] = []
        for block, replacement_text, report in sorted(
            planned,
            key=lambda item: item[0].start,
            reverse=True,
        ):
            if _replace_paragraph_range(
                paragraph,
                block.start,
                block.end,
                replacement_text,
            ):
                applied_reports.append(report)
            else:
                application_warnings.append(
                    f"Could not apply replacement at {location.part} paragraph "
                    f"{location.paragraph_index}; left original text unchanged: "
                    f"{block.original_text}"
                )
        report_replacements.extend(reversed(applied_reports))
        warnings.extend(reversed(application_warnings))

    return report_replacements, warnings


def _insert_comment_pmids_at_anchors(
    *,
    document: DocxDocument,
    options: ReplacementOptions,
    records_by_identifier: dict[IdentifierKey, ReferenceRecord],
    context: ReferenceSectionContext,
    field_depth_cache: FieldDepthCache,
) -> tuple[list[dict[str, Any]], list[str]]:
    anchor_locations = context.anchor_locations
    planned_by_comment: dict[int, list[tuple[ReplacementBlock, str, TextLocation]]] = {}
    warnings: list[str] = []

    for paragraph, comment_location in _iter_comment_paragraphs(document, options):
        if comment_location.comment_id in context.skipped_comment_ids:
            continue
        blocks = _replacement_blocks_for_text(
            paragraph.text,
            scan_parenthetical_pmids=options.scan_parenthetical_pmids,
            scan_dois=options.scan_dois,
            scan_bare_dois=options.scan_bare_dois,
        )
        if not blocks:
            continue
        (
            blocks,
            field_or_hidden_blocks,
            tracked_change_blocks,
            _,
        ) = _partition_blocks_around_unsafe_content(
            paragraph,
            blocks,
            field_depth_cache,
            check_run_editability=False,
        )
        if field_or_hidden_blocks:
            warnings.append(
                _unsafe_content_warning(comment_location, len(field_or_hidden_blocks))
            )
        if tracked_change_blocks:
            warnings.append(
                _tracked_change_warning(comment_location, len(tracked_change_blocks))
            )
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
                identifier_label = _replacement_block_label(block)
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
                {identifier for block, _, _ in planned for _, identifier in block.identifier_keys}
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
    block: ReplacementBlock,
    records_by_identifier: dict[IdentifierKey, ReferenceRecord],
    keep_pmid_text: bool,
    mark_unresolved: bool,
) -> str | None:
    resolved_records: list[ReferenceRecord] = []
    seen_record_keys: set[str] = set()
    unresolved_by_kind: dict[IdentifierKind, list[str]] = {"pmid": [], "doi": []}

    for identifier_key in block.identifier_keys:
        record = records_by_identifier.get(identifier_key)
        if record is None:
            unresolved_by_kind[identifier_key[0]].append(identifier_key[1])
            continue
        if record.citation_key in seen_record_keys:
            continue
        seen_record_keys.add(record.citation_key)
        resolved_records.append(record)

    replacement_parts: list[str] = []
    if resolved_records:
        replacement_parts.append(make_temporary_citation(resolved_records))

    unresolved_total = sum(len(values) for values in unresolved_by_kind.values())
    if unresolved_total:
        if mark_unresolved or resolved_records:
            for kind, values in unresolved_by_kind.items():
                if not values:
                    continue
                label = _identifier_label(kind, plural=len(values) > 1)
                replacement_parts.append(f"[unresolved {label}: {', '.join(values)}]")
        else:
            return None

    replacement_text = " ".join(replacement_parts)
    if keep_pmid_text:
        replacement_text = f"{block.original_text} {replacement_text}"
    return replacement_text


def _replacement_blocks_for_text(
    text: str,
    *,
    scan_parenthetical_pmids: bool,
    scan_dois: bool,
    scan_bare_dois: bool,
) -> list[ReplacementBlock]:
    identifier_blocks = scan_text(
        text,
        scan_parenthetical_pmids=scan_parenthetical_pmids,
        scan_dois=scan_dois,
        scan_bare_dois=scan_bare_dois,
    )
    if not identifier_blocks:
        return []

    grouped: list[ReplacementBlock] = []
    used_block_indexes: set[int] = set()
    for start, end in _identifier_only_wrappers(text):
        inside_indexes = [
            index
            for index, block in enumerate(identifier_blocks)
            if _block_inside_wrapper(block, start, end)
        ]
        if not inside_indexes or any(index in used_block_indexes for index in inside_indexes):
            continue
        inside_blocks = tuple(identifier_blocks[index] for index in inside_indexes)
        if not _wrapper_contains_only_identifiers(text, start, end, inside_blocks):
            continue
        grouped.append(
            ReplacementBlock(
                original_text=text[start:end],
                start=start,
                end=end,
                blocks=inside_blocks,
                source="wrapper",
            )
        )
        used_block_indexes.update(inside_indexes)

    for run in _adjacent_identifier_runs(text, identifier_blocks, used_block_indexes):
        if len(run) > 1:
            grouped.append(
                ReplacementBlock(
                    original_text=text[run[0].start : run[-1].end],
                    start=run[0].start,
                    end=run[-1].end,
                    blocks=tuple(run),
                    source="identifier_run",
                )
            )
            continue
        block = run[0]
        grouped.append(
            ReplacementBlock(
                original_text=block.original_text,
                start=block.start,
                end=block.end,
                blocks=(block,),
                source=block.source,
            )
        )

    return sorted(grouped, key=lambda block: block.start)


def _identifier_only_wrappers(text: str) -> Iterator[tuple[int, int]]:
    for match in re.finditer(r"\[[^\[\]]+\]", text):
        yield match.start(), match.end()
    for match in re.finditer(r"\([^()]+\)", text):
        yield match.start(), match.end()


def _adjacent_identifier_runs(
    text: str,
    blocks: list[IdentifierBlock],
    used_block_indexes: set[int],
) -> Iterator[list[IdentifierBlock]]:
    available = [
        block for index, block in enumerate(blocks)
        if index not in used_block_indexes
    ]
    if not available:
        return

    run = [available[0]]
    for block in available[1:]:
        separator = text[run[-1].end : block.start]
        if _should_group_adjacent(run[-1], block) and re.fullmatch(r"[\s,;]+", separator or ""):
            run.append(block)
            continue
        yield run
        run = [block]
    yield run


def _should_group_adjacent(left: IdentifierBlock, right: IdentifierBlock) -> bool:
    return left.source != "parenthetical" and right.source != "parenthetical"


def _block_inside_wrapper(block: IdentifierBlock, start: int, end: int) -> bool:
    return block.start >= start and block.end <= end


def _wrapper_contains_only_identifiers(
    text: str,
    start: int,
    end: int,
    blocks: tuple[IdentifierBlock, ...],
) -> bool:
    inner_start = start + 1
    inner_end = end - 1
    cursor = inner_start
    remaining: list[str] = []
    for block in sorted(blocks, key=lambda value: value.start):
        block_start = max(block.start, inner_start)
        block_end = min(block.end, inner_end)
        if block_start > cursor:
            remaining.append(text[cursor:block_start])
        cursor = max(cursor, block_end)
    if cursor < inner_end:
        remaining.append(text[cursor:inner_end])
    return re.fullmatch(r"[\s,;]*", "".join(remaining)) is not None


def _validate_replacement_citations(replacements: list[dict[str, Any]]) -> None:
    bad_replacements = [
        replacement["replacement_text"]
        for replacement in replacements
        if "#PMID-" in replacement.get("replacement_text", "")
        or "#DOI-" in replacement.get("replacement_text", "")
    ]
    if bad_replacements:
        raise WordProcessingError(
            "Generated temporary citation used the EndNote record-number marker "
            "with an app-controlled PMID/DOI key."
        )


def _replacement_block_label(block: ReplacementBlock) -> str:
    kinds = {kind for kind, _ in block.identifier_keys}
    if kinds == {"pmid"}:
        return "PMID"
    if kinds == {"doi"}:
        return "DOI"
    return "identifier"


def _identifier_label(kind: IdentifierKind, *, plural: bool = False) -> str:
    label = kind.upper()
    if plural:
        return label + "s"
    return label


def _block_identifier_report(block: ReplacementBlock) -> list[dict[str, str]]:
    report: list[dict[str, str]] = []
    for identifier_block in block.blocks:
        report.extend(
            {
                "kind": identifier_block.kind,
                "normalized": identifier,
                "source": identifier_block.source,
            }
            for identifier in identifier_block.identifiers
        )
    return report


def _skipped_identifier_reports(
    blocks: list[IdentifierBlock],
    location: TextLocation,
    *,
    reason: str,
) -> list[dict[str, Any]]:
    skipped: list[dict[str, Any]] = []
    for block in blocks:
        for identifier in block.identifiers:
            skipped.append(
                {
                    "original_text": block.original_text,
                    "kind": block.kind,
                    "normalized": identifier,
                    "source": block.source,
                    "location": location.as_report_dict(),
                    "reason": reason,
                }
            )
    return skipped


def _replace_paragraph_range(
    paragraph: Paragraph,
    start: int,
    end: int,
    replacement: str,
) -> bool:
    mapped_runs = _paragraph_text_runs(paragraph)
    if (
        mapped_runs is None
        or _identifier_touches_omitted_content(paragraph, mapped_runs, start, end)
        or _mapped_runs_interrupted(mapped_runs, start, end)
        or not _mapped_runs_can_replace_range(mapped_runs, start, end)
    ):
        return False

    spans: list[tuple[int, int, Run, bool]] = []
    cursor = 0
    for run, is_hyperlink_run in mapped_runs:
        run_text = run.text
        run_start = cursor
        run_end = cursor + len(run_text)
        spans.append((run_start, run_end, run, is_hyperlink_run))
        cursor = run_end

    overlapping = [
        (run_start, run_end, run, is_hyperlink_run)
        for run_start, run_end, run, is_hyperlink_run in spans
        if run_start < run_end and run_start < end and run_end > start
    ]
    if not overlapping:
        return False
    if any(is_hyperlink_run for _, _, _, is_hyperlink_run in overlapping):
        return False

    first_start, first_end, first_run, _ = overlapping[0]
    last_start, last_end, last_run, _ = overlapping[-1]
    before = first_run.text[: max(0, start - first_start)]
    after = last_run.text[max(0, end - last_start) :]

    if first_run is last_run:
        first_run.text = before + replacement + after
        return True

    first_run.text = before + replacement
    for _, _, run, _ in overlapping[1:-1]:
        run.text = ""
    last_run.text = after
    return True


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

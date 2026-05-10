"""EndNote Tagged Import writer and validator."""

from __future__ import annotations

from pathlib import Path
import re

from pmid2endnote.models import ReferenceRecord


CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def write_enw(records: list[ReferenceRecord]) -> str:
    """Return line-oriented EndNote Tagged Import text."""

    blocks = [_record_to_enw(record) for record in records]
    return "\n\n".join(block for block in blocks if block).rstrip() + ("\n" if blocks else "")


def _record_to_enw(record: ReferenceRecord) -> str:
    lines: list[str] = ["%0 Journal Article"]
    for author in record.authors or ((record.first_author,) if record.first_author else ()):
        if author:
            lines.append(f"%A {_sanitize_field(author)}")
    _append(lines, "%D", record.year)
    _append(lines, "%T", record.title)
    _append(lines, "%J", record.journal)
    _append(lines, "%V", record.volume)
    _append(lines, "%N", record.issue)
    _append(lines, "%P", record.pages)
    _append(lines, "%R", record.doi)
    _append(lines, "%U", record.url)
    lines.append(f"%M {_sanitize_field(record.citation_key)}")
    lines.append(f"%F {_sanitize_field(record.citation_key)}")
    source_note = _source_note(record)
    if source_note:
        lines.append(f"%Z {_sanitize_field(source_note)}")
    return "\n".join(lines)


def _append(lines: list[str], tag: str, value: str | None) -> None:
    if value:
        lines.append(f"{tag} {_sanitize_field(value)}")


def _sanitize_field(value: str) -> str:
    cleaned = CONTROL_CHAR_RE.sub(" ", value)
    cleaned = cleaned.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    cleaned = _remove_unmatched_braces(cleaned)
    return re.sub(r" +", " ", cleaned).strip()


def _remove_unmatched_braces(value: str) -> str:
    if value.count("{") != value.count("}"):
        return value.replace("{", "").replace("}", "")
    return value


def _source_note(record: ReferenceRecord) -> str:
    parts = list(record.source_identifiers)
    if record.pmid and f"PMID:{record.pmid}" not in parts:
        parts.append(f"PMID:{record.pmid}")
    if record.doi and f"DOI:{record.doi}" not in parts:
        parts.append(f"DOI:{record.doi}")
    parts.append(f"metadata_source:{record.metadata_source}")
    return "; ".join(parts)


def validate_enw_file(path: Path, records: list[ReferenceRecord]) -> tuple[set[str], list[str]]:
    """Validate that an ENW file contains required fields for every record."""

    if not path.exists():
        return set(), [f"EndNote Tagged Import file was not written: {path}"]
    if not path.is_file():
        return set(), [f"EndNote Tagged Import path is not a file: {path}"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return set(), [f"EndNote Tagged Import file could not be read: {path}: {exc}"]
    if not text.strip():
        return set(), [f"EndNote Tagged Import file is empty: {path}"]

    included = set(re.findall(r"(?m)^%M\s+(.+?)\s*$", text))
    errors: list[str] = []
    for record in records:
        key = re.escape(record.citation_key)
        if not re.search(rf"(?m)^%M\s+{key}\s*$", text):
            errors.append(f"ENW file missing %M citation key: {record.citation_key}")
        if not re.search(rf"(?m)^%F\s+{key}\s*$", text):
            errors.append(f"ENW file missing %F citation key: {record.citation_key}")
        if record.doi and not re.search(rf"(?m)^%R\s+{re.escape(record.doi)}\s*$", text):
            errors.append(f"ENW file missing %R DOI for {record.citation_key}: {record.doi}")
        if not _has_source_note(text, record):
            errors.append(f"ENW file missing %Z source note for {record.citation_key}")
    return included, errors


def _has_source_note(text: str, record: ReferenceRecord) -> bool:
    notes = re.findall(r"(?m)^%Z\s+(.+?)\s*$", text)
    for note in notes:
        if record.pmid and f"PMID:{record.pmid}" not in note:
            continue
        if record.doi and f"DOI:{record.doi}" not in note:
            continue
        if f"metadata_source:{record.metadata_source}" not in note:
            continue
        return True
    return False

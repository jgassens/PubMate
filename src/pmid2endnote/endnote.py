"""EndNote temporary citation formatting."""

from __future__ import annotations

import re

from pmid2endnote.models import ReferenceRecord


CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_temporary_citation_author(author: str | None) -> str:
    """Remove characters that would break EndNote temporary citation syntax."""

    if not author:
        return "Unknown"
    cleaned = CONTROL_CHAR_RE.sub(" ", author)
    cleaned = cleaned.replace("{", "").replace("}", "").replace(";", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "Unknown"


def make_temporary_citation(records: list[ReferenceRecord]) -> str:
    """Build an EndNote temporary citation using the Any Text search form."""

    parts = []
    for record in records:
        author = sanitize_temporary_citation_author(record.first_author)
        year = record.year or "0000"
        citation_key = getattr(record, "citation_key", None) or getattr(record, "pmid")
        parts.append(f"{author}, {year}, {citation_key}")
    return "{" + ";".join(parts) + "}"

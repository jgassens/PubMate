"""MEDLINE/.nbib validation helpers."""

from __future__ import annotations

from pathlib import Path
import re


def pmids_in_nbib_text(text: str) -> set[str]:
    """Return PMIDs present in MEDLINE PMID lines."""

    return set(re.findall(r"(?m)^PMID\s*-\s*(\d+)\s*$", text))


def validate_nbib_file(path: Path, resolved_pmids: list[str]) -> tuple[set[str], list[str]]:
    """Validate that an .nbib file exists and contains every resolved PMID."""

    errors: list[str] = []
    if not path.exists():
        return set(), [f"EndNote import file was not written: {path}"]
    if not path.is_file():
        return set(), [f"EndNote import path is not a file: {path}"]

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return set(), [f"EndNote import file could not be read: {path}: {exc}"]
    if not text.strip():
        return set(), [f"EndNote import file is empty: {path}"]

    included_pmids = pmids_in_nbib_text(text)
    missing_pmids = [pmid for pmid in resolved_pmids if pmid not in included_pmids]
    if missing_pmids:
        errors.append(
            "EndNote import file is missing MEDLINE PMID lines for resolved PMID(s): "
            + ", ".join(missing_pmids)
        )

    return included_pmids, errors

"""Processing report helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def create_report(
    *,
    input_docx: Path,
    output_docx: Path,
    nbib_file: Path,
    enw_file: Path | None = None,
    unique_pmids: list[str] | None = None,
) -> dict[str, Any]:
    """Create a report object with the required top-level fields."""

    return {
        "input_docx": str(input_docx),
        "output_docx": str(output_docx),
        "nbib_file": str(nbib_file),
        "enw_file": str(enw_file) if enw_file is not None else None,
        "scan_parenthetical_pmids": False,
        "scan_dois": True,
        "scan_bare_dois": False,
        "doi_source": "auto",
        "import_format": "enw",
        "include_comments": True,
        "skip_reference_section": True,
        "create_backup": False,
        "reference_section_start": None,
        "skipped_identifiers": [],
        "total_pmid_occurrences": 0,
        "total_identifier_occurrences": 0,
        "unique_pmids": unique_pmids or [],
        "unique_identifiers": [],
        "resolved_pmids": [],
        "unresolved_pmids": [],
        "pmid_statuses": [],
        "identifier_statuses": [],
        "replacements": [],
        "warnings": [],
        "errors": [],
    }


def write_report(report: dict[str, Any], path: Path) -> None:
    """Write a pretty JSON report to disk."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")

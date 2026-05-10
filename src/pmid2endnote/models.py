"""Shared identifier and reference models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


IdentifierKind = Literal["pmid", "doi"]


@dataclass(frozen=True)
class ReferenceRecord:
    """Normalized reference metadata used for temporary citations and ENW import."""

    citation_key: str
    first_author: str | None
    year: str
    authors: tuple[str, ...] = ()
    title: str | None = None
    journal: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    doi: str | None = None
    url: str | None = None
    pmid: str | None = None
    metadata_source: str = "none"
    source_identifiers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdentifierResolution:
    """Resolution result for one normalized identifier."""

    kind: IdentifierKind
    normalized: str
    reference: ReferenceRecord | None
    metadata_source: str = "none"
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdentifierStatus:
    """Report-ready status for one identifier."""

    input: str
    kind: IdentifierKind
    normalized: str
    resolved: bool
    metadata_source: str
    pmid: str | None
    doi: str | None
    citation_key: str | None
    included_in_import: bool
    replacement_count: int
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "input": self.input,
            "kind": self.kind,
            "normalized": self.normalized,
            "resolved": self.resolved,
            "metadata_source": self.metadata_source,
            "pmid": self.pmid,
            "doi": self.doi,
            "citation_key": self.citation_key,
            "included_in_import": self.included_in_import,
            "replacement_count": self.replacement_count,
            "warnings": self.warnings,
        }

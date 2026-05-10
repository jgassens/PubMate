"""Identifier block detection and de-duplication."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Literal
from urllib.parse import unquote

from pmid2endnote.models import IdentifierKind


PMID_BLOCK_RE = re.compile(
    r"""
    (?<![A-Za-z0-9])
    (?P<label>PMIDs?)
    \s*
    (?:
        [:#]\s*
        |
        \s+
    )
    (?P<ids>
        \d{1,12}
        (?:
            \s*[,;]\s*
            \d{1,12}
        )*
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

PARENTHETICAL_PMID_BLOCK_RE = re.compile(
    r"""
    \(
    (?P<ids>
        \s*\d{5,12}\s*
        (?:
            [,;]\s*
            \d{5,12}\s*
        )*
    )
    \)
    """,
    re.VERBOSE,
)

DOI_CORE_RE = r"10\.\d{4,9}/[^\s<>{}\"']+"

DOI_LABELED_RE = re.compile(
    rf"""
    (?<![A-Za-z0-9])
    DOI
    \s*:\s*
    (?P<doi>
        (?:
            https?://
            (?:
                dx\.
            )?
            doi\.org/
        )?
        {DOI_CORE_RE}
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

DOI_URL_RE = re.compile(
    rf"""
    https?://
    (?:
        dx\.
    )?
    doi\.org/
    (?P<doi>{DOI_CORE_RE})
    """,
    re.IGNORECASE | re.VERBOSE,
)

BARE_DOI_RE = re.compile(rf"(?<![A-Za-z0-9/])(?P<doi>{DOI_CORE_RE})", re.IGNORECASE)

TRAILING_EXTERNAL_PUNCTUATION = ".,"


@dataclass(frozen=True)
class IdentifierBlock:
    """An identifier occurrence found in a text node."""

    original_text: str
    identifiers: tuple[str, ...]
    start: int
    end: int
    kind: IdentifierKind
    source: Literal["labeled", "parenthetical", "doi_label", "doi_url", "bare_doi"] = "labeled"

    @property
    def pmids(self) -> tuple[str, ...]:
        return self.identifiers if self.kind == "pmid" else ()

    @property
    def dois(self) -> tuple[str, ...]:
        return self.identifiers if self.kind == "doi" else ()


PmidBlock = IdentifierBlock


def normalize_pmid(value: str) -> str:
    """Return the digit-only PMID string."""

    return value.strip()


def normalize_doi(value: str) -> str:
    """Normalize DOI labels, resolver URLs, and external punctuation."""

    doi = unquote(value.strip())
    doi = re.sub(r"(?i)^doi\s*:\s*", "", doi).strip()
    doi = re.sub(r"(?i)^https?://(?:dx\.)?doi\.org/", "", doi).strip()
    doi = doi.strip()
    doi = _strip_external_doi_punctuation(doi)
    return doi.lower()


def _strip_external_doi_punctuation(doi: str) -> str:
    while doi and doi[-1] in TRAILING_EXTERNAL_PUNCTUATION:
        doi = doi[:-1]
    while doi and doi[-1] == "]" and doi.count("[") < doi.count("]"):
        doi = doi[:-1]
    while doi and doi[-1] == ")" and doi.count("(") < doi.count(")"):
        doi = doi[:-1]
    return doi


def _external_doi_suffix_length(raw_doi: str) -> int:
    stripped = _strip_external_doi_punctuation(raw_doi)
    return len(raw_doi) - len(stripped)


def scan_text(
    text: str,
    *,
    scan_parenthetical_pmids: bool = False,
    scan_dois: bool = True,
    scan_bare_dois: bool = False,
) -> list[IdentifierBlock]:
    """Find PMID and DOI identifier blocks in text.

    Only text that starts with a clear PMID/PMIDs label is returned. Unlabeled
    numeric strings are ignored by default, even if they look like PubMed
    identifiers. Parenthetical digit-only lists can be enabled explicitly.
    """

    candidates: list[IdentifierBlock] = []
    for match in PMID_BLOCK_RE.finditer(text):
        ids_text = match.group("ids")
        pmids = tuple(normalize_pmid(part) for part in re.split(r"\s*[,;]\s*", ids_text))
        candidates.append(
            IdentifierBlock(
                original_text=match.group(0),
                identifiers=pmids,
                start=match.start(),
                end=match.end(),
                kind="pmid",
                source="labeled",
            )
        )

    if scan_parenthetical_pmids:
        for match in PARENTHETICAL_PMID_BLOCK_RE.finditer(text):
            ids_text = match.group("ids")
            pmids = tuple(normalize_pmid(part) for part in re.split(r"\s*[,;]\s*", ids_text.strip()))
            candidates.append(
                IdentifierBlock(
                    original_text=match.group(0),
                    identifiers=pmids,
                    start=match.start(),
                    end=match.end(),
                    kind="pmid",
                    source="parenthetical",
                )
            )

    if scan_dois:
        candidates.extend(_doi_blocks(text, scan_bare_dois=scan_bare_dois))

    return _select_longest_non_overlapping(candidates)


def _doi_blocks(text: str, *, scan_bare_dois: bool) -> list[IdentifierBlock]:
    blocks: list[IdentifierBlock] = []
    for regex, source in ((DOI_LABELED_RE, "doi_label"), (DOI_URL_RE, "doi_url")):
        for match in regex.finditer(text):
            raw_doi = match.group("doi")
            normalized = normalize_doi(raw_doi)
            suffix_len = _external_doi_suffix_length(raw_doi)
            end = match.end() - suffix_len
            blocks.append(
                IdentifierBlock(
                    original_text=text[match.start() : end],
                    identifiers=(normalized,),
                    start=match.start(),
                    end=end,
                    kind="doi",
                    source=source,  # type: ignore[arg-type]
                )
            )

    if scan_bare_dois:
        for match in BARE_DOI_RE.finditer(text):
            raw_doi = match.group("doi")
            normalized = normalize_doi(raw_doi)
            suffix_len = _external_doi_suffix_length(raw_doi)
            end = match.end() - suffix_len
            blocks.append(
                IdentifierBlock(
                    original_text=text[match.start() : end],
                    identifiers=(normalized,),
                    start=match.start(),
                    end=end,
                    kind="doi",
                    source="bare_doi",
                )
            )

    return blocks


def _select_longest_non_overlapping(candidates: list[IdentifierBlock]) -> list[IdentifierBlock]:
    ordered = sorted(candidates, key=lambda block: (-(block.end - block.start), block.start))
    selected: list[IdentifierBlock] = []
    for candidate in ordered:
        if not _overlaps_existing_block(candidate.start, candidate.end, selected):
            selected.append(candidate)
    return sorted(selected, key=lambda block: block.start)


def _overlaps_existing_block(start: int, end: int, blocks: Iterable[IdentifierBlock]) -> bool:
    return any(start < block.end and end > block.start for block in blocks)


def extract_unique_pmids(blocks: Iterable[IdentifierBlock]) -> list[str]:
    """Return first-seen PMIDs while preserving document order."""

    seen: set[str] = set()
    ordered: list[str] = []
    for block in blocks:
        for pmid in block.pmids:
            if pmid not in seen:
                seen.add(pmid)
                ordered.append(pmid)
    return ordered


def extract_unique_identifiers(blocks: Iterable[IdentifierBlock]) -> list[tuple[IdentifierKind, str]]:
    """Return first-seen identifiers while preserving document order."""

    seen: set[tuple[IdentifierKind, str]] = set()
    ordered: list[tuple[IdentifierKind, str]] = []
    for block in blocks:
        for identifier in block.identifiers:
            key = (block.kind, identifier)
            if key not in seen:
                seen.add(key)
                ordered.append(key)
    return ordered


def find_pmids_in_texts(
    texts: Iterable[str],
    *,
    scan_parenthetical_pmids: bool = False,
) -> list[IdentifierBlock]:
    """Scan multiple text chunks and return their blocks in chunk order."""

    found: list[IdentifierBlock] = []
    for text in texts:
        found.extend(scan_text(text, scan_parenthetical_pmids=scan_parenthetical_pmids))
    return found

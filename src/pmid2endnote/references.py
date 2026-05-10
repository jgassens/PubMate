"""Reference-record construction and identifier resolution."""

from __future__ import annotations

import hashlib
from typing import Iterable

import requests

from pmid2endnote.models import IdentifierKind, IdentifierResolution, ReferenceRecord
from pmid2endnote.pubmed import PubMedClient, PubMedRecord
from pmid2endnote.scanner import normalize_doi


def pmid_citation_key(pmid: str) -> str:
    return f"PMID-{pmid}"


def doi_citation_key(normalized_doi: str) -> str:
    digest = hashlib.sha1(normalized_doi.encode("utf-8")).hexdigest().upper()
    return f"DOI-{digest[:12]}"


def reference_from_pubmed(record: PubMedRecord, *, source_identifiers: Iterable[str] = ()) -> ReferenceRecord:
    sources = tuple(dict.fromkeys((f"PMID:{record.pmid}", *source_identifiers)))
    doi = normalize_doi(record.doi) if record.doi else None
    url = f"https://doi.org/{doi}" if doi else None
    return ReferenceRecord(
        citation_key=pmid_citation_key(record.pmid),
        first_author=record.first_author,
        year=record.year,
        authors=record.authors or ((record.first_author,) if record.first_author else ()),
        title=record.title,
        journal=record.journal,
        volume=record.volume,
        issue=record.issue,
        pages=record.pages,
        doi=doi,
        url=url,
        pmid=record.pmid,
        metadata_source="pubmed",
        source_identifiers=sources,
    )


class ReferenceResolver:
    """Resolve PMID and DOI identifiers into canonical ReferenceRecord objects."""

    def __init__(
        self,
        *,
        pubmed_client: PubMedClient,
        doi_source: str = "auto",
        session: requests.Session | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.pubmed_client = pubmed_client
        self.doi_source = doi_source
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.references_by_key: dict[str, ReferenceRecord] = {}
        self.resolutions: dict[tuple[IdentifierKind, str], IdentifierResolution] = {}

    def resolve(self, identifiers: list[tuple[IdentifierKind, str]]) -> dict[tuple[IdentifierKind, str], IdentifierResolution]:
        pmids = [value for kind, value in identifiers if kind == "pmid"]
        pubmed_records = self.pubmed_client.fetch_records(pmids) if pmids else {}
        for pmid in pmids:
            record = pubmed_records.get(pmid)
            if record is None:
                self.resolutions[("pmid", pmid)] = IdentifierResolution("pmid", pmid, None)
                continue
            reference = self._store_reference(reference_from_pubmed(record))
            self.resolutions[("pmid", pmid)] = IdentifierResolution("pmid", pmid, reference, "pubmed")

        for kind, value in identifiers:
            if kind == "doi":
                self.resolutions[(kind, value)] = self._resolve_doi(value)

        return self.resolutions

    def _resolve_doi(self, doi: str) -> IdentifierResolution:
        warnings: list[str] = []
        if self.doi_source in {"auto", "pubmed-first"}:
            candidate_pmids = self.pubmed_client.search_pmids_by_doi(doi)
            if candidate_pmids:
                records = self.pubmed_client.fetch_records(candidate_pmids)
                for pmid in candidate_pmids:
                    record = records.get(pmid)
                    if record and _pubmed_record_has_doi(record, doi):
                        reference = self._store_reference(
                            reference_from_pubmed(record, source_identifiers=(f"DOI:{doi}",))
                        )
                        return IdentifierResolution("doi", doi, reference, "pubmed", tuple(warnings))
                warnings.append(
                    "PubMed DOI search returned candidates, but none had an exact normalized DOI match."
                )

        reference, metadata_warnings = self._resolve_doi_metadata(doi)
        warnings.extend(metadata_warnings)
        if reference is None:
            return IdentifierResolution("doi", doi, None, "none", tuple(warnings))
        reference = self._store_reference(reference)
        return IdentifierResolution("doi", doi, reference, reference.metadata_source, tuple(warnings))

    def _resolve_doi_metadata(self, doi: str) -> tuple[ReferenceRecord | None, list[str]]:
        warnings: list[str] = []
        order = self._metadata_order()
        for source in order:
            try:
                payload = self._fetch_doi_payload(doi, source)
            except requests.RequestException as exc:
                warnings.append(f"{source} DOI lookup failed: {exc}")
                continue
            if not payload:
                continue
            reference = _reference_from_doi_payload(doi, payload, source)
            if reference is not None:
                return reference, warnings
        warnings.append(f"DOI could not be resolved: {doi}")
        return None, warnings

    def _metadata_order(self) -> list[str]:
        if self.doi_source == "crossref":
            return ["crossref"]
        if self.doi_source == "datacite":
            return ["datacite"]
        if self.doi_source == "content-negotiation":
            return ["content-negotiation"]
        return ["content-negotiation", "crossref", "datacite"]

    def _fetch_doi_payload(self, doi: str, source: str) -> dict | None:
        if source == "content-negotiation":
            response = self.session.get(
                f"https://doi.org/{doi}",
                headers={"Accept": "application/vnd.citationstyles.csl+json"},
                timeout=self.timeout_seconds,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        if source == "crossref":
            response = self.session.get(
                f"https://api.crossref.org/works/{doi}",
                timeout=self.timeout_seconds,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json().get("message", {})
        if source == "datacite":
            response = self.session.get(
                f"https://api.datacite.org/dois/{doi}",
                timeout=self.timeout_seconds,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json().get("data", {}).get("attributes", {})
        return None

    def _store_reference(self, reference: ReferenceRecord) -> ReferenceRecord:
        existing = self.references_by_key.get(reference.citation_key)
        if existing is not None:
            return existing
        self.references_by_key[reference.citation_key] = reference
        return reference


def _pubmed_record_has_doi(record: PubMedRecord, doi: str) -> bool:
    return any(normalize_doi(value) == doi for value in record.doi_values)


def _reference_from_doi_payload(doi: str, payload: dict, source: str) -> ReferenceRecord | None:
    title = _first_text(payload.get("title"))
    authors = _authors_from_payload(payload)
    year = _year_from_payload(payload)
    journal = _first_text(payload.get("container-title")) or payload.get("publisher")
    volume = payload.get("volume")
    issue = payload.get("issue")
    pages = payload.get("page")
    url = payload.get("URL") or payload.get("url") or f"https://doi.org/{doi}"
    payload_doi = normalize_doi(str(payload.get("DOI") or payload.get("doi") or doi))
    return ReferenceRecord(
        citation_key=doi_citation_key(doi),
        first_author=_first_author_from_authors(authors),
        year=year,
        authors=tuple(authors),
        title=title,
        journal=journal,
        volume=str(volume) if volume else None,
        issue=str(issue) if issue else None,
        pages=str(pages) if pages else None,
        doi=payload_doi,
        url=url,
        metadata_source=source,
        source_identifiers=(f"DOI:{doi}",),
        warnings=() if title else ("DOI metadata did not include a title.",),
    )


def _first_text(value) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return None


def _authors_from_payload(payload: dict) -> list[str]:
    people = payload.get("author") or payload.get("creators") or []
    authors: list[str] = []
    if not isinstance(people, list):
        return authors
    for person in people:
        if not isinstance(person, dict):
            continue
        if "name" in person:
            authors.append(str(person["name"]))
            continue
        family = person.get("family") or person.get("familyName")
        given = person.get("given") or person.get("givenName")
        if family and given:
            authors.append(f"{family}, {given}")
        elif family:
            authors.append(str(family))
    return authors


def _first_author_from_authors(authors: list[str]) -> str | None:
    if not authors:
        return None
    first = authors[0]
    return first.split(",", 1)[0].strip() or first


def _year_from_payload(payload: dict) -> str:
    for key in ("issued", "published-print", "published-online"):
        value = payload.get(key)
        year = _year_from_date_parts(value)
        if year:
            return year
    for key in ("published", "publicationYear"):
        value = payload.get(key)
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
            return value[:4]
        year = _year_from_date_parts(value)
        if year:
            return year
    return "0000"


def _year_from_date_parts(value) -> str | None:
    if isinstance(value, dict):
        date_parts = value.get("date-parts")
        if isinstance(date_parts, list) and date_parts and date_parts[0]:
            return str(date_parts[0][0])
    return None

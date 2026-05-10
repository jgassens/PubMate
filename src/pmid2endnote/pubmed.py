"""PubMed E-utilities client and XML parsing."""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Callable, Iterable
from xml.etree import ElementTree

import requests

from pmid2endnote.errors import PubMedFetchError, PubMedParseError


EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class PubMedRecord:
    """Minimal metadata needed for EndNote temporary citations and reports."""

    pmid: str
    first_author: str | None
    year: str
    authors: tuple[str, ...] = ()
    title: str | None = None
    journal: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    doi: str | None = None
    doi_values: tuple[str, ...] = ()
    retraction_update_flags: tuple[str, ...] = ()


def batch_pmids(pmids: Iterable[str], batch_size: int = 200) -> list[list[str]]:
    """Split PMIDs into EFetch-sized batches."""

    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    batch: list[str] = []
    batches: list[list[str]] = []
    for pmid in pmids:
        batch.append(pmid)
        if len(batch) == batch_size:
            batches.append(batch)
            batch = []
    if batch:
        batches.append(batch)
    return batches


class PubMedClient:
    """Small PubMed EFetch client using POST requests."""

    def __init__(
        self,
        *,
        email: str,
        api_key: str | None = None,
        tool: str = "pmid2endnote",
        session: requests.Session | None = None,
        timeout_seconds: float = 30.0,
        batch_size: int = 200,
        max_retries: int = 3,
        min_delay_seconds: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not email:
            raise ValueError("email is required for NCBI E-utilities requests")

        self.email = email
        self.api_key = api_key
        self.tool = tool
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.min_delay_seconds = (
            min_delay_seconds if min_delay_seconds is not None else (0.12 if api_key else 0.34)
        )
        self._sleep = sleep
        self._last_request_at = 0.0

    def fetch_records(self, pmids: Iterable[str]) -> dict[str, PubMedRecord]:
        """Fetch and parse PubMed XML records keyed by PMID."""

        records: dict[str, PubMedRecord] = {}
        for batch in batch_pmids(pmids, self.batch_size):
            xml_text = self._post_efetch(
                batch,
                extra_params={
                    "retmode": "xml",
                },
            )
            for record in parse_pubmed_xml(xml_text):
                records[record.pmid] = record
        return records

    def search_pmids_by_doi(self, doi: str) -> list[str]:
        """Search PubMed Article Identifier field for a DOI."""

        data = {
            "db": "pubmed",
            "term": f'"{doi}"[aid]',
            "retmode": "json",
            "tool": self.tool,
            "email": self.email,
        }
        if self.api_key:
            data["api_key"] = self.api_key
        text = self._post_eutils(ESEARCH_URL, data)
        try:
            payload = requests.models.complexjson.loads(text)
        except ValueError as exc:
            raise PubMedParseError(f"Malformed PubMed ESearch JSON: {exc}") from exc
        ids = payload.get("esearchresult", {}).get("idlist", [])
        return [str(value) for value in ids]

    def fetch_nbib(self, pmids: Iterable[str]) -> str:
        """Fetch PubMed/NLM MEDLINE text suitable for EndNote import."""

        texts: list[str] = []
        for batch in batch_pmids(pmids, self.batch_size):
            text = self._post_efetch(
                batch,
                extra_params={
                    "rettype": "medline",
                    "retmode": "text",
                },
            )
            if text.strip():
                texts.append(text.rstrip() + "\n")
        return "\n".join(texts)

    def _post_efetch(self, pmids: list[str], *, extra_params: dict[str, str]) -> str:
        data = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "tool": self.tool,
            "email": self.email,
            **extra_params,
        }
        if self.api_key:
            data["api_key"] = self.api_key
        return self._post_eutils(EFETCH_URL, data)

    def _post_eutils(self, url: str, data: dict[str, str]) -> str:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._respect_rate_limit()
            try:
                response = self.session.post(
                    url,
                    data=data,
                    timeout=self.timeout_seconds,
                )
                self._last_request_at = time.monotonic()
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self._sleep(2**attempt)
                continue

            if response.status_code in TRANSIENT_HTTP_STATUSES and attempt < self.max_retries:
                last_error = PubMedFetchError(
                    f"PubMed transient HTTP {response.status_code} for {url}"
                )
                self._sleep(2**attempt)
                continue

            if not response.ok:
                raise PubMedFetchError(
                    f"PubMed request failed with HTTP {response.status_code}: {response.text[:500]}"
                )

            return response.text

        raise PubMedFetchError("PubMed request failed after retries") from last_error

    def _respect_rate_limit(self) -> None:
        if self._last_request_at <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.min_delay_seconds - elapsed
        if remaining > 0:
            self._sleep(remaining)


def parse_pubmed_xml(xml_text: str) -> list[PubMedRecord]:
    """Parse PubMed EFetch XML into records."""

    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise PubMedParseError(f"Malformed PubMed XML: {exc}") from exc

    records: list[PubMedRecord] = []
    for article in root.findall(".//PubmedArticle"):
        medline = article.find("MedlineCitation")
        if medline is None:
            continue
        pmid = _element_text(medline.find("PMID"))
        if not pmid:
            continue
        article_node = medline.find("Article")
        if article_node is None:
            article_node = ElementTree.Element("Article")

        records.append(
            PubMedRecord(
                pmid=pmid,
                first_author=_first_author(article_node),
                year=_publication_year(article_node),
                authors=_authors(article_node),
                title=_article_title(article_node),
                journal=_journal_title(article_node),
                volume=_element_text(article_node.find("./Journal/JournalIssue/Volume")),
                issue=_element_text(article_node.find("./Journal/JournalIssue/Issue")),
                pages=_element_text(article_node.find("./Pagination/MedlinePgn")),
                doi=_doi(article),
                doi_values=_doi_values(article),
                retraction_update_flags=_retraction_update_flags(medline),
            )
        )
    return records


def _element_text(element: ElementTree.Element | None) -> str | None:
    if element is None:
        return None
    text = "".join(element.itertext()).strip()
    return _collapse_whitespace(text) if text else None


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _first_author(article_node: ElementTree.Element) -> str | None:
    authors = article_node.findall("./AuthorList/Author")
    for author in authors:
        last_name = _element_text(author.find("LastName"))
        if last_name:
            return last_name
    for author in authors:
        collective_name = _element_text(author.find("CollectiveName"))
        if collective_name:
            return collective_name
    return None


def _authors(article_node: ElementTree.Element) -> tuple[str, ...]:
    names: list[str] = []
    for author in article_node.findall("./AuthorList/Author"):
        collective_name = _element_text(author.find("CollectiveName"))
        if collective_name:
            names.append(collective_name)
            continue
        last_name = _element_text(author.find("LastName"))
        fore_name = _element_text(author.find("ForeName")) or _element_text(author.find("Initials"))
        if last_name and fore_name:
            names.append(f"{last_name}, {fore_name}")
        elif last_name:
            names.append(last_name)
    return tuple(names)


def _publication_year(article_node: ElementTree.Element) -> str:
    article_date_year = _element_text(article_node.find("./ArticleDate/Year"))
    if article_date_year:
        return article_date_year

    journal_year = _element_text(article_node.find("./Journal/JournalIssue/PubDate/Year"))
    if journal_year:
        return journal_year

    medline_date = _element_text(article_node.find("./Journal/JournalIssue/PubDate/MedlineDate"))
    if medline_date:
        match = re.search(r"\b(1[89]\d{2}|20\d{2}|21\d{2})\b", medline_date)
        if match:
            return match.group(1)

    return "0000"


def _article_title(article_node: ElementTree.Element) -> str | None:
    return _element_text(article_node.find("ArticleTitle"))


def _journal_title(article_node: ElementTree.Element) -> str | None:
    return _element_text(article_node.find("./Journal/Title")) or _element_text(
        article_node.find("./Journal/ISOAbbreviation")
    )


def _doi(article: ElementTree.Element) -> str | None:
    values = _doi_values(article)
    return values[0] if values else None


def _doi_values(article: ElementTree.Element) -> tuple[str, ...]:
    values: list[str] = []
    for article_id in article.findall(".//ArticleIdList/ArticleId"):
        id_type = (article_id.attrib.get("IdType") or "").lower()
        if id_type == "doi":
            value = _element_text(article_id)
            if value:
                values.append(value)
    for elocation_id in article.findall(".//ELocationID"):
        id_type = (elocation_id.attrib.get("EIdType") or "").lower()
        if id_type == "doi":
            value = _element_text(elocation_id)
            if value:
                values.append(value)
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)


def _retraction_update_flags(medline: ElementTree.Element) -> tuple[str, ...]:
    flags: list[str] = []
    for comments_corrections in medline.findall("./CommentsCorrectionsList/CommentsCorrections"):
        ref_type = comments_corrections.attrib.get("RefType")
        if ref_type:
            flags.append(ref_type)

    for publication_type in medline.findall("./Article/PublicationTypeList/PublicationType"):
        value = _element_text(publication_type)
        if value and any(term in value.lower() for term in ("retract", "update", "corrected")):
            flags.append(value)

    seen: set[str] = set()
    ordered: list[str] = []
    for flag in flags:
        if flag not in seen:
            seen.add(flag)
            ordered.append(flag)
    return tuple(ordered)

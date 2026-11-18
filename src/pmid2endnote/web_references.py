"""Metadata for supported citation URLs without a DOI or PMID in the link."""

from __future__ import annotations

import hashlib
import re
from xml.etree import ElementTree

from lxml import html

from pmid2endnote.models import ReferenceRecord


BOOK_URL_RE = re.compile(r"https://www\.ncbi\.nlm\.nih\.gov/books/(NBK\d+)/")
PMC_URL_RE = re.compile(r"https://pmc\.ncbi\.nlm\.nih\.gov/articles/(PMC\d+)/")
EAU_URL_RE = re.compile(r"https://uroweb\.org/guidelines/[a-z0-9-]+")


def _url_citation_key(url: str) -> str:
    return "URL-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12].upper()


def books_reference(url: str, summary: dict) -> ReferenceRecord | None:
    """Use the matching Bookshelf record and its NCBI-supplied citation."""

    accession = url.rstrip("/").rsplit("/", 1)[-1]
    if summary.get("rid") != accession or not summary.get("title"):
        return None
    book_info = ElementTree.fromstring(summary.get("bookinfo") or "<Info/>")
    contributors = book_info.findtext("./Citation/MainRecord/Contributors", "")
    authors = []
    for name in contributors.rstrip(".").split(", "):
        if name and name != "et al":
            authors.append(re.sub(r"^(.+) ([A-Z]{1,5})$", r"\1, \2", name))
    year = re.match(r"\d{4}", summary.get("pubdate") or "")
    publication = book_info.findtext("./Citation/ParentRecord/PublicationStatement", "")
    publisher = re.search(r":\s*([^;]+);", publication)
    return ReferenceRecord(
        citation_key=_url_citation_key(url),
        first_author=authors[0].split(",", 1)[0] if authors else None,
        authors=tuple(authors),
        year=year[0] if year else "",
        title=summary["title"],
        book_title=book_info.findtext("./Citation/ParentRecord/Title"),
        publisher=publisher[1] if publisher else None,
        url=url,
        metadata_source="ncbi-bookshelf",
        source_identifiers=(f"URL:{url}",),
        reference_type="Book Section" if summary.get("rtype") == "chapter" else "Book",
    )


def eau_reference(url: str, page: bytes) -> ReferenceRecord | None:
    """Use structured metadata from the European Association of Urology site."""

    document = html.fromstring(page)
    titles = document.xpath("//head/meta[@property='og:title']/@content")
    if not titles or not titles[0].strip():
        return None
    organization = "European Association of Urology"
    dates = document.xpath("//head/meta[@property='article:published_time']/@content")
    year = re.match(r"\d{4}", dates[0]) if dates else None
    return ReferenceRecord(
        citation_key=_url_citation_key(url),
        first_author=organization,
        authors=(f"{organization},",),
        year=year[0] if year else "",
        title=titles[0].removesuffix(" - Uroweb").strip(),
        publisher=organization,
        url=url,
        metadata_source="uroweb",
        source_identifiers=(f"URL:{url}",),
        warnings=() if year else ("The guideline page did not specify a publication year.",),
        reference_type="Web Page",
    )

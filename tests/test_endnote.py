from pmid2endnote.endnote import make_temporary_citation
from pmid2endnote.models import ReferenceRecord


def test_temporary_citation_generation_single_and_multiple() -> None:
    records = [
        ReferenceRecord(citation_key="PMID-12345678", pmid="12345678", first_author="Smith", year="2024"),
        ReferenceRecord(citation_key="PMID-23456789", pmid="23456789", first_author="Jones", year="2021"),
    ]

    assert make_temporary_citation(records) == "{Smith, 2024, PMID-12345678;Jones, 2021, PMID-23456789}"


def test_temporary_citation_sanitizes_author_and_falls_back() -> None:
    records = [
        ReferenceRecord(citation_key="PMID-123", pmid="123", first_author="Smith; {Lab}\n", year=""),
        ReferenceRecord(citation_key="PMID-456", pmid="456", first_author=None, year="2020"),
    ]

    assert make_temporary_citation(records) == "{Smith Lab, 0000, PMID-123;Unknown, 2020, PMID-456}"


def test_doi_temporary_citation_uses_any_text_key() -> None:
    records = [
        ReferenceRecord(
            citation_key="DOI-31F31430F853",
            doi="10.1021/example",
            first_author="Zhang",
            year="2016",
        )
    ]

    citation = make_temporary_citation(records)

    assert citation == "{Zhang, 2016, DOI-31F31430F853}"
    assert "#DOI-" not in citation
    assert "#PMID-" not in citation

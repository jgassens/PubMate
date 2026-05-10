from pmid2endnote.endnote import make_temporary_citation
from pmid2endnote.pubmed import PubMedRecord


def test_temporary_citation_generation_single_and_multiple() -> None:
    records = [
        PubMedRecord(pmid="12345678", first_author="Smith", year="2024"),
        PubMedRecord(pmid="23456789", first_author="Jones", year="2021"),
    ]

    assert make_temporary_citation(records) == "{Smith, 2024 #12345678;Jones, 2021 #23456789}"


def test_temporary_citation_sanitizes_author_and_falls_back() -> None:
    records = [
        PubMedRecord(pmid="123", first_author="Smith; {Lab}\n", year=""),
        PubMedRecord(pmid="456", first_author=None, year="2020"),
    ]

    assert make_temporary_citation(records) == "{Smith Lab, 0000 #123;Unknown, 2020 #456}"

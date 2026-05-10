from pathlib import Path

from pmid2endnote.enw import validate_enw_file, write_enw
from pmid2endnote.models import ReferenceRecord


def test_enw_record_includes_keys_doi_and_source_note(tmp_path: Path) -> None:
    record = ReferenceRecord(
        citation_key="DOI-31F31430F853",
        first_author="Zhang",
        year="2016",
        authors=("Zhang, Jane",),
        title="Example",
        journal="Journal",
        doi="10.1021/example",
        url="https://doi.org/10.1021/example",
        metadata_source="crossref",
        source_identifiers=("DOI:10.1021/example",),
    )

    text = write_enw([record])
    path = tmp_path / "refs.enw"
    path.write_text(text, encoding="utf-8")
    included, errors = validate_enw_file(path, [record])

    assert "%M DOI-31F31430F853" in text
    assert "%F DOI-31F31430F853" in text
    assert "%R 10.1021/example" in text
    assert "%Z citation_key:DOI-31F31430F853" in text
    assert included == {"DOI-31F31430F853"}
    assert errors == []

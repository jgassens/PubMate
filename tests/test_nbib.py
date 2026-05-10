from pathlib import Path

from pmid2endnote.nbib import pmids_in_nbib_text, validate_nbib_file


def test_pmids_in_nbib_text_allows_medline_spacing() -> None:
    text = "PMID- 6426050\nPMID  - 104929\n"

    assert pmids_in_nbib_text(text) == {"6426050", "104929"}


def test_validate_nbib_file_success(tmp_path: Path) -> None:
    nbib = tmp_path / "refs.nbib"
    nbib.write_text("PMID- 6426050\nTI  - Example\n\nPMID- 104929\n", encoding="utf-8")

    included, errors = validate_nbib_file(nbib, ["6426050", "104929"])

    assert included == {"6426050", "104929"}
    assert errors == []


def test_validate_nbib_file_missing_empty_and_incomplete(tmp_path: Path) -> None:
    missing = tmp_path / "missing.nbib"
    assert validate_nbib_file(missing, ["6426050"])[1]

    empty = tmp_path / "empty.nbib"
    empty.write_text("", encoding="utf-8")
    assert "empty" in validate_nbib_file(empty, ["6426050"])[1][0]

    incomplete = tmp_path / "incomplete.nbib"
    incomplete.write_text("PMID- 6426050\n", encoding="utf-8")
    assert "104929" in validate_nbib_file(incomplete, ["6426050", "104929"])[1][0]

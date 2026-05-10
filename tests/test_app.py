from pathlib import Path

from docx import Document

from pmid2endnote import app
from pmid2endnote.app import ProcessingOptions, process_document
from pmid2endnote.pubmed import PubMedRecord


def _save_docx(path: Path, text: str) -> None:
    document = Document()
    document.add_paragraph(text)
    document.save(path)


class FakePubMedClient:
    records = {"6426050": PubMedRecord("6426050", "Petersen", "1983")}
    nbib_text = "PMID- 6426050\nTI  - Example\n"

    def __init__(self, *, email: str, api_key: str | None = None) -> None:
        self.email = email
        self.api_key = api_key

    def fetch_records(self, pmids):
        return {pmid: self.records[pmid] for pmid in pmids if pmid in self.records}

    def search_pmids_by_doi(self, doi):
        return []

    def fetch_nbib(self, pmids):
        return self.nbib_text


def test_non_dry_run_writes_valid_enw_before_docx_and_reports_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(app, "PubMedClient", FakePubMedClient)
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "input.endnote.docx"
    nbib_file = tmp_path / "input.references.nbib"
    enw_file = tmp_path / "input.endnote-import.enw"
    report_file = tmp_path / "input.report.json"
    _save_docx(input_docx, "See (6426050).")

    result = process_document(
        ProcessingOptions(
            input_docx=input_docx,
            email="test@example.edu",
            output_docx=output_docx,
            nbib_file=nbib_file,
            enw_file=enw_file,
            report_file=report_file,
            scan_parenthetical_pmids=True,
            save_email=False,
        )
    )

    assert result.exit_code == 0
    assert enw_file.exists()
    assert nbib_file.exists()
    assert output_docx.exists()
    assert "%M PMID-6426050" in enw_file.read_text(encoding="utf-8")
    assert result.report["pmid_statuses"] == [
        {
            "pmid": "6426050",
            "resolved": True,
            "included_in_nbib": True,
            "replacement_count": 1,
            "sources": ["parenthetical"],
            "warnings": [],
        }
    ]
    assert result.report["identifier_statuses"][0]["citation_key"] == "PMID-6426050"
    assert str(enw_file) in result.messages[-1]
    assert "EndNote Import option" in result.messages[-1]
    assert "Accession Number" not in result.messages[-1]


def test_empty_auxiliary_nbib_does_not_block_docx_when_enw_is_valid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app, "PubMedClient", FakePubMedClient)
    monkeypatch.setattr(FakePubMedClient, "nbib_text", "")
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "input.endnote.docx"
    nbib_file = tmp_path / "input.references.nbib"
    report_file = tmp_path / "input.report.json"
    _save_docx(input_docx, "PMID: 6426050")

    result = process_document(
        ProcessingOptions(
            input_docx=input_docx,
            email="test@example.edu",
            output_docx=output_docx,
            nbib_file=nbib_file,
            report_file=report_file,
            save_email=False,
        )
    )

    assert result.exit_code == 0
    assert nbib_file.exists()
    assert output_docx.exists()
    assert any("empty" in warning for warning in result.report["warnings"])


def test_enw_validation_failure_blocks_docx(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app, "PubMedClient", FakePubMedClient)
    monkeypatch.setattr(
        app,
        "validate_enw_file",
        lambda _path, _records: (set(), ["EndNote Tagged Import file was not written: missing"]),
    )
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "input.endnote.docx"
    _save_docx(input_docx, "PMID: 6426050")

    result = process_document(
        ProcessingOptions(
            input_docx=input_docx,
            email="test@example.edu",
            output_docx=output_docx,
            nbib_file=tmp_path / "input.references.nbib",
            report_file=tmp_path / "input.report.json",
            save_email=False,
        )
    )

    assert result.exit_code == 2
    assert not output_docx.exists()
    assert "not written" in result.report["errors"][0]


def test_dry_run_does_not_require_written_nbib_or_docx(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app, "PubMedClient", FakePubMedClient)
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "input.endnote.docx"
    nbib_file = tmp_path / "input.references.nbib"
    _save_docx(input_docx, "PMID: 6426050")

    result = process_document(
        ProcessingOptions(
            input_docx=input_docx,
            email="test@example.edu",
            output_docx=output_docx,
            nbib_file=nbib_file,
            report_file=tmp_path / "input.report.json",
            dry_run=True,
            save_email=False,
        )
    )

    assert result.exit_code == 0
    assert not nbib_file.exists()
    assert not output_docx.exists()
    assert not result.enw_file.exists()
    assert result.report["pmid_statuses"][0]["replacement_count"] == 1

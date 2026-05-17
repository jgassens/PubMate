import json
from pathlib import Path

from pmid2endnote.app import build_pmid_statuses
from pmid2endnote.report import create_report, write_report
from pmid2endnote.scanner import scan_text
from pmid2endnote.word import DocumentPmidOccurrence, ScanResult, TextLocation


def test_report_json_creation(tmp_path: Path) -> None:
    report = create_report(
        input_docx=tmp_path / "input.docx",
        output_docx=tmp_path / "input.endnote.docx",
        nbib_file=tmp_path / "input.references.nbib",
        enw_file=tmp_path / "input.endnote-import.enw",
        unique_pmids=["12345678"],
    )
    report["total_pmid_occurrences"] = 1
    report_path = tmp_path / "input.pmid2endnote.report.json"

    write_report(report, report_path)

    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert loaded["unique_pmids"] == ["12345678"]
    assert loaded["pmid_statuses"] == []
    assert loaded["replacements"] == []
    assert loaded["errors"] == []
    assert loaded["create_backup"] is False


def test_pmid_statuses_include_sources_resolution_inclusion_and_replacement_count() -> None:
    blocks = scan_text(
        "PMID: 6426050 (104929) (999999999)",
        scan_parenthetical_pmids=True,
    )
    scan_result = ScanResult(
        occurrences=[
            DocumentPmidOccurrence(block=block, location=TextLocation("body", index))
            for index, block in enumerate(blocks)
        ],
        unique_pmids=["6426050", "104929", "999999999"],
        unique_identifiers=[
            ("pmid", "6426050"),
            ("pmid", "104929"),
            ("pmid", "999999999"),
        ],
        warnings=[],
        skipped_identifiers=[],
        reference_section_start=None,
    )
    statuses = build_pmid_statuses(
        scan_result=scan_result,
        resolved_pmids={"6426050", "104929"},
        included_pmids={"6426050"},
        replacements=[
            {"pmids": ["6426050", "104929"]},
            {"pmids": ["6426050"]},
        ],
        sources_by_pmid={
            "6426050": {"labeled", "parenthetical"},
            "104929": {"parenthetical"},
            "999999999": {"parenthetical"},
        },
    )

    assert statuses[0]["pmid"] == "6426050"
    assert statuses[0]["resolved"] is True
    assert statuses[0]["included_in_nbib"] is True
    assert statuses[0]["replacement_count"] == 2
    assert statuses[0]["sources"] == ["labeled", "parenthetical"]
    assert statuses[1]["included_in_nbib"] is False
    assert "not validated in the auxiliary .nbib" in statuses[1]["warnings"][0]
    assert statuses[2]["resolved"] is False
    assert "unresolved" in statuses[2]["warnings"][0].lower()

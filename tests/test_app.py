from pathlib import Path

import pytest
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from pmid2endnote import app
from pmid2endnote.app import ProcessingOptions, process_document
from pmid2endnote.pubmed import PubMedRecord


def _save_docx(path: Path, text: str) -> None:
    document = Document()
    document.add_paragraph(text)
    document.save(path)


class FakePubMedClient:
    tool = "pmid2endnote"
    records = {
        "6426050": PubMedRecord("6426050", "Petersen", "1983"),
        "38408451": PubMedRecord(
            "38408451",
            "Wijesundara",
            "2024",
            doi="10.1021/acs.chemrev.3c00409",
            doi_values=("10.1021/acs.chemrev.3c00409",),
        ),
        "36737783": PubMedRecord(
            "36737783",
            "Stillman",
            "2023",
            doi="10.1186/s12951-023-01782-w",
            doi_values=("10.1186/s12951-023-01782-w",),
        ),
        "30826373": PubMedRecord("30826373", "Zhong", "2019"),
        "34866536": PubMedRecord("34866536", "Li", "2021"),
    }
    doi_to_pmids = {
        "10.1021/acs.chemrev.3c00409": ["38408451"],
        "10.1186/s12951-023-01782-w": ["36737783"],
    }
    nbib_text = None

    def __init__(self, *, email: str, api_key: str | None = None) -> None:
        self.email = email
        self.api_key = api_key

    def fetch_records(self, pmids):
            return {pmid: self.records[pmid] for pmid in pmids if pmid in self.records}

    def search_pmids_by_doi(self, doi):
        return self.doi_to_pmids.get(doi, [])

    def fetch_nbib(self, pmids):
        if self.nbib_text is not None:
            return self.nbib_text
        return "\n\n".join(f"PMID- {pmid}\nTI  - Example" for pmid in pmids)


def test_url_only_references_import_with_types_and_report_unsupported_links(tmp_path: Path, monkeypatch) -> None:
    class Client(FakePubMedClient):
        def fetch_book_summary(self, accession):
            assert accession == "NBK123"
            return {
                "rid": accession,
                "rtype": "chapter",
                "title": "Example chapter",
                "pubdate": "2025/01/01",
                "bookinfo": (
                    "<Info><Citation><MainRecord><Contributors>Chen J, Park L</Contributors>"
                    "</MainRecord><ParentRecord><Title>Example book</Title></ParentRecord></Citation></Info>"
                ),
            }

    class Response:
        status_code = 200
        content = b'<meta property="og:title" content="Example Guidelines - Uroweb">'

    def fetch_page(session, url, **kwargs):
        assert url == "https://uroweb.org/guidelines/example"
        assert kwargs["allow_redirects"] is False
        return Response()

    monkeypatch.setattr(app, "PubMedClient", Client)
    monkeypatch.setattr("pmid2endnote.references.requests.Session.get", fetch_page)
    docx = tmp_path / "input.docx"
    _save_docx(docx, "See (https://www.ncbi.nlm.nih.gov/books/NBK123/), (https://uroweb.org/guidelines/example), "
                     "and (https://example.com/unresolved).")
    result = process_document(ProcessingOptions(input_docx=docx, email="test@example.edu", save_email=False))
    assert result.exit_code == 0
    assert len(result.report["replacements"]) == 2
    enw = result.enw_file.read_text()
    assert "%0 Book Section\n%A Chen, J\n%A Park, L\n%D 2025\n%T Example chapter\n%B Example book" in enw
    assert "%0 Web Page\n%A European Association of Urology,\n%T Example Guidelines" in enw
    output_text = Document(result.output_docx).paragraphs[0].text
    assert output_text.startswith("See,{Chen, 2025, URL-")
    assert ";European Association of Urology, , URL-" in output_text
    assert "https://example.com/unresolved" in output_text
    statuses = result.report["identifier_statuses"]
    assert [entry["resolved"] for entry in statuses] == [True, True, False]
    assert any("Could not resolve 1 reference" in message for message in result.messages)


def test_unsupported_urls_only_write_a_report_without_requiring_email(tmp_path: Path, monkeypatch) -> None:
    def unexpected_email_check(*args):
        pytest.fail("There is no supported reference to look up")

    monkeypatch.setattr(app, "resolve_email", unexpected_email_check)
    docx = tmp_path / "input.docx"
    url = "https://example.org/article?id=10.1234/other"
    document = Document()
    document.add_paragraph(f"See ({url}).")
    paragraph = document.add_paragraph()
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), paragraph.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True))
    hyperlink.append(paragraph.add_run(url)._r)
    paragraph._p.append(hyperlink)
    document.save(docx)

    result = process_document(ProcessingOptions(input_docx=docx, scan_bare_dois=True))

    assert result.exit_code == 0
    assert result.report["errors"] == []
    assert result.report_file.exists()
    assert not result.output_docx.exists() and not result.enw_file.exists()
    statuses = result.report["identifier_statuses"]
    assert [status["source"] for status in statuses] == ["citation_url", "citation_link"]
    assert all(status["normalized"] == url and not status["resolved"] for status in statuses)
    assert all("No metadata provider for this URL." in status["warnings"] for status in statuses)
    assert any("only unsupported citation URLs" in message for message in result.messages)


@pytest.mark.parametrize("scan_dois", [False, True])
@pytest.mark.parametrize("query_value", ["10.1186/s12951-023-01782-w", "https://doi.org/10.1186/s12951-023-01782-w"])
def test_pubmed_url_query_cannot_select_a_different_record(tmp_path: Path, monkeypatch, scan_dois, query_value) -> None:
    monkeypatch.setattr(app, "PubMedClient", FakePubMedClient)
    docx = tmp_path / "input.docx"
    _save_docx(docx, f"See (https://pubmed.ncbi.nlm.nih.gov/6426050/?id={query_value}).")

    result = process_document(ProcessingOptions(
        input_docx=docx, email="test@example.edu", save_email=False,
        scan_dois=scan_dois, scan_bare_dois=True,
    ))

    assert result.exit_code == 0
    status, = result.report["identifier_statuses"]
    assert status["citation_key"] == "PMID-6426050"
    assert Document(result.output_docx).paragraphs[0].text == "See.{Petersen, 1983, PMID-6426050}"


def test_doi_lookup_preserves_literal_percent_encoding_in_record_identity(tmp_path: Path, monkeypatch) -> None:
    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"DOI": "10.1234/example%2Fpart", "title": ["Paper with encoded DOI"]}}

    def fetch_doi(session, url, **kwargs):
        assert url == "https://api.crossref.org/works/10.1234/example%252fpart"
        return Response()

    monkeypatch.setattr(app, "PubMedClient", FakePubMedClient)
    monkeypatch.setattr("pmid2endnote.references.requests.Session.get", fetch_doi)
    docx = tmp_path / "input.docx"
    _save_docx(docx, "See (https://doi.org/10.1234/example%252Fpart).")

    result = process_document(ProcessingOptions(
        input_docx=docx, email="test@example.edu", save_email=False, doi_source="crossref"
    ))

    assert result.exit_code == 0
    assert "%R 10.1234/example%2fpart\n" in result.enw_file.read_text()
    assert "%U https://doi.org/10.1234/example%252fpart\n" in result.enw_file.read_text()


@pytest.mark.parametrize("payload", [[], {"records": None}, {"records": [None]}, {"records": [{"pmcid": "PMC123", "doi": []}]}])
def test_malformed_pmc_payload_preserves_url_and_other_references(tmp_path: Path, monkeypatch, payload) -> None:
    class Response:
        status_code = 200

        def json(self):
            return payload

    def fetch_pmc(session, url, **kwargs):
        assert kwargs["params"]["ids"] == "PMC123"
        return Response()

    monkeypatch.setattr(app, "PubMedClient", FakePubMedClient)
    monkeypatch.setattr("pmid2endnote.references.requests.Session.get", fetch_pmc)
    docx = tmp_path / "input.docx"
    url = "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/"
    _save_docx(docx, f"See ({url}). Also see PMID: 6426050.")

    result = process_document(ProcessingOptions(input_docx=docx, email="test@example.edu", save_email=False))

    assert result.exit_code == 0
    url_status, pmid_status = result.report["identifier_statuses"]
    assert not url_status["resolved"] and "URL metadata lookup failed." in url_status["warnings"]
    assert pmid_status["resolved"] and pmid_status["replacement_count"] == 1
    assert Document(result.output_docx).paragraphs[0].text == f"See ({url}). Also see.{{Petersen, 1983, PMID-6426050}}"


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
    assert result.report["create_backup"] is False
    assert not any("Created backup copy" in warning for warning in result.report["warnings"])
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


def test_optional_nbib_and_report_can_be_disabled(tmp_path: Path, monkeypatch) -> None:
    class NoNbibClient(FakePubMedClient):
        def fetch_nbib(self, pmids):
            raise AssertionError("NBIB fetch must be skipped")

    monkeypatch.setattr(app, "PubMedClient", NoNbibClient)
    monkeypatch.setattr(
        app,
        "write_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("report writer must be skipped")
        ),
    )
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
            write_nbib=False,
            write_report=False,
            save_email=False,
        )
    )

    assert result.exit_code == 0
    assert output_docx.exists()
    assert result.enw_file.exists()
    assert not nbib_file.exists()
    assert not report_file.exists()
    assert not any(".nbib" in message for message in result.messages)
    assert not any("Wrote report:" in message for message in result.messages)
    assert result.report["resolved_pmids"] == ["6426050"]
    assert not any("nbib" in warning.lower() for warning in result.report["warnings"])
    assert not any("nbib" in str(status).lower() for status in result.report["pmid_statuses"])
    assert result.report["pmid_statuses"][0]["included_in_enw"] is True


def test_reference_section_skipped_identifiers_do_not_enter_import_or_statuses(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(app, "PubMedClient", FakePubMedClient)
    input_docx = tmp_path / "stress.docx"
    output_docx = tmp_path / "stress.endnote.docx"
    nbib_file = tmp_path / "stress.references.nbib"
    enw_file = tmp_path / "stress.endnote-import.enw"
    report_file = tmp_path / "stress.report.json"
    document = Document()
    document.add_paragraph("Body PMID: 38408451 and DOI: 10.1186/s12951-023-01782-w.")
    document.add_paragraph("Parenthetical stress (30826373; 34866536).")
    document.add_paragraph("References")
    document.add_paragraph("1. Skipped repeat. PMID: 38408451. DOI: 10.1021/acs.chemrev.3c00409.")
    document.add_paragraph("2. Skipped DOI-only-looking item. DOI: 10.1002/adfm.201600650.")
    document.save(input_docx)

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
    assert {item["normalized"] for item in result.report["skipped_identifiers"]} == {
        "38408451",
        "10.1021/acs.chemrev.3c00409",
        "10.1002/adfm.201600650",
    }
    assert result.report["reference_section_start"] == {"part": "body", "paragraph_index": 2}
    assert {item["normalized"] for item in result.report["unique_identifiers"]} == {
        "38408451",
        "10.1186/s12951-023-01782-w",
        "30826373",
        "34866536",
    }
    assert {item["normalized"] for item in result.report["identifier_statuses"]} == {
        "38408451",
        "10.1186/s12951-023-01782-w",
        "30826373",
        "34866536",
    }
    enw_text = enw_file.read_text(encoding="utf-8")
    assert "PMID-38408451" in enw_text
    assert "PMID-36737783" in enw_text
    assert "PMID-30826373" in enw_text
    assert "PMID-34866536" in enw_text
    assert "10.1002/adfm.201600650" not in enw_text
    output_document = Document(output_docx)
    assert output_document.paragraphs[3].text == (
        "1. Skipped repeat. PMID: 38408451. DOI: 10.1021/acs.chemrev.3c00409."
    )


def _codeless_field_docx(path: Path, visible_text: str) -> None:
    """Write a .docx whose identifier sits in a leftover EndNote wrapper."""

    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    document = Document()
    paragraph = document.add_paragraph("Lead text. ")
    for _ in range(2):
        begin = OxmlElement("w:fldChar")
        begin.set(qn("w:fldCharType"), "begin")
        paragraph.add_run()._r.append(begin)
    nested_end = OxmlElement("w:fldChar")
    nested_end.set(qn("w:fldCharType"), "end")
    paragraph.add_run()._r.append(nested_end)
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    paragraph.add_run()._r.append(separate)
    paragraph.add_run(visible_text)
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    paragraph.add_run()._r.append(end)
    document.save(path)


def test_locked_citations_are_converted_without_opting_in(tmp_path: Path, monkeypatch) -> None:
    """A manuscript already formatted by EndNote must work out of the box."""

    input_docx = tmp_path / "input.docx"
    _codeless_field_docx(input_docx, "(PMID: 12345678)")

    captured: dict[str, object] = {}

    class _Client:
        def __init__(self, **kwargs) -> None:
            pass

        def fetch_records(self, pmids):
            captured["pmids"] = list(pmids)
            return {pmid: PubMedRecord(pmid, "Smith", "2024") for pmid in pmids}

    monkeypatch.setattr(app, "PubMedClient", _Client)

    result = process_document(
        ProcessingOptions(
            input_docx=input_docx,
            email="someone@example.edu",
            save_email=False,
            write_report=False,
            write_nbib=False,
        )
    )

    assert result.report["unique_pmids"] == ["12345678"]
    assert not any("converted none" in message for message in result.messages)


def test_nothing_converted_message_reports_what_was_found(tmp_path: Path) -> None:
    """Saying nothing was found, when 1 was found and rejected, sends the
    user hunting for a scanning bug that does not exist."""

    input_docx = tmp_path / "input.docx"
    _codeless_field_docx(input_docx, "(PMID: 12345678)")

    result = process_document(
        ProcessingOptions(
            input_docx=input_docx,
            email="someone@example.edu",
            save_email=False,
            write_report=False,
            write_nbib=False,
            convert_locked_citations=False,
        )
    )

    message = " ".join(result.messages)
    assert "Found 1 PMID/DOI identifier but converted none" in message
    assert "inside a Word field, hyperlink, or hidden text" in message
    assert "--no-convert-locked-citations" in message
    assert "No matching PMID, DOI, or citation link was found" not in message


def test_document_with_no_identifiers_still_says_none_were_found(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    _save_docx(input_docx, "This manuscript cites nothing at all.")

    result = process_document(
        ProcessingOptions(
            input_docx=input_docx,
            email="someone@example.edu",
            save_email=False,
            write_report=False,
            write_nbib=False,
        )
    )

    assert any(
        "No matching PMID, DOI, or citation link was found" in message
        for message in result.messages
    )


def test_real_endnote_citation_skip_does_not_blame_a_setting(tmp_path: Path) -> None:
    """With conversion already on, no setting would convert these.

    Naming one sends the user to a checkbox that is already ticked, which is
    the same wrong-turn the old "nothing was found" message caused.
    """

    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    input_docx = tmp_path / "input.docx"
    document = Document()
    paragraph = document.add_paragraph("Lead text. ")
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    paragraph.add_run()._r.append(begin)
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " ADDIN EN.CITE real-citation "
    paragraph.add_run()._r.append(instruction)
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    paragraph.add_run()._r.append(separate)
    paragraph.add_run("(PMID: 12345678)")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    paragraph.add_run()._r.append(end)
    document.save(input_docx)

    result = process_document(
        ProcessingOptions(
            input_docx=input_docx,
            email="someone@example.edu",
            save_email=False,
            write_report=False,
            write_nbib=False,
        )
    )

    message = " ".join(result.messages)
    assert "Found 1 PMID/DOI identifier but converted none" in message
    assert "could not safely separate" in message
    assert "currently turned off" not in message
    assert "--no-convert-locked-citations" not in message


@pytest.mark.parametrize(
    "story, reason",
    [(story, reason) for story in ("body", "header", "footer", "comment")
     for reason in ("locked_citation", "tracked_changes", "non_text_content")
     if (story, reason) != ("comment", "non_text_content")],
)
def test_rejected_identifiers_are_reported_in_every_scanned_story(
    tmp_path: Path, story: str, reason: str,
) -> None:
    input_docx = tmp_path / "input.docx"
    document = Document()
    anchor = document.add_paragraph().add_run("Anchor text")
    if story == "body":
        paragraph = document.add_paragraph()
    elif story == "comment":
        paragraph = document.add_comment(anchor, text="", author="Reviewer").paragraphs[0]
    else:
        paragraph = getattr(document.sections[0], story).paragraphs[0]
    if reason == "locked_citation":
        paragraph.add_run("PMIDs: 12345678, 23456789").font.hidden = True
        count = 2
    elif reason == "tracked_changes":
        paragraph.add_run("PMID: 123")
        change = OxmlElement("w:ins")
        change.set(qn("w:id"), "1")
        run = OxmlElement("w:r")
        text = OxmlElement("w:t")
        text.text = "4"
        run.append(text)
        change.append(run)
        paragraph._p.append(change)
        paragraph.add_run("5678")
        count = 1
    else:
        paragraph.add_run("PMID: 12345678")._r.append(OxmlElement("w:drawing"))
        count = 1
    document.save(input_docx)

    result = process_document(ProcessingOptions(
        input_docx=input_docx,
        include_headers=True,
        include_footers=True,
        include_comments=True,
        save_email=False,
        write_report=False,
        write_nbib=False,
    ))

    skipped = result.report["skipped_identifiers"]
    assert len(skipped) == count
    assert {entry["reason"] for entry in skipped} == {reason}
    assert {entry["location"]["part"] for entry in skipped} == {story}
    message = " ".join(result.messages)
    assert f"Found {count} PMID/DOI identifier{'s' if count > 1 else ''} but converted none" in message
    assert "No matching PMID, DOI, or citation link was found" not in message
    assert not result.output_docx.exists()
    assert not result.enw_file.exists()


@pytest.mark.parametrize("story", ["header", "footer"])
def test_linked_header_or_footer_is_counted_once(tmp_path: Path, story: str) -> None:
    input_docx = tmp_path / "input.docx"
    document = Document()
    document.add_paragraph("Body text")
    paragraph = getattr(document.sections[0], story).paragraphs[0]
    paragraph.add_run("PMID: 12345678").font.hidden = True
    document.add_section()  # This section reuses the same header/footer XML.
    document.save(input_docx)

    result = process_document(ProcessingOptions(
        input_docx=input_docx,
        include_headers=True,
        include_footers=True,
        save_email=False,
        write_report=False,
        write_nbib=False,
    ))

    assert len(result.report["skipped_identifiers"]) == 1
    assert "Found 1 PMID/DOI identifier but converted none" in " ".join(result.messages)


@pytest.mark.parametrize("enabled", [False, True])
def test_locked_skip_message_does_not_assume_endnote_ownership(
    tmp_path: Path, enabled: bool,
) -> None:
    input_docx = tmp_path / "input.docx"
    _codeless_field_docx(input_docx, "See (PMID: 12345678) for details")
    result = process_document(ProcessingOptions(
        input_docx=input_docx,
        convert_locked_citations=enabled,
        save_email=False,
        write_report=False,
        write_nbib=False,
    ))

    message = " ".join(result.messages)
    assert "inside a Word field, hyperlink, or hidden text" in message
    assert "citations EndNote owns" not in message
    assert "cannot be converted without damaging" not in message
    if enabled:
        assert "could not safely separate" in message
        assert "turned off" not in message
    else:
        assert "leftover citation wrappers is currently turned off" in message
        assert "Real EndNote fields and other unsafe content remain protected" in message


def test_nothing_converted_counts_mixed_reasons_and_pluralizes() -> None:
    message = app._nothing_converted_message(
        [{"reason": reason} for reason in (
            "reference_section", "reference_section", "locked_citation",
            "tracked_changes", "non_text_content", "unsafe_comment_anchor",
        )],
        convert_locked_citations=True,
    )
    assert "Found 6 PMID/DOI identifiers but converted none" in message
    assert "2 in the document's reference section" in message
    assert "1 inside a Word field, hyperlink, or hidden text" in message
    assert "1 next to tracked changes or content controls" in message
    assert "1 sharing a run with an image or page break" in message
    assert "1 in comments without a safe document anchor" in message

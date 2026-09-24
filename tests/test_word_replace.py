import base64
from pathlib import Path

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

from pmid2endnote.models import ReferenceRecord
from pmid2endnote.pubmed import PubMedRecord
from pmid2endnote import word as word_module
from pmid2endnote.word import (
    ReplacementOptions,
    is_reference_section_heading,
    replace_pmids_in_docx,
    scan_docx,
)


def _save_docx(path: Path, paragraphs: list[str]) -> None:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    document.save(path)


def _read_paragraph_text(path: Path, index: int = 0) -> str:
    return Document(path).paragraphs[index].text


def _add_word_field(paragraph, instruction: str, visible_text: str) -> None:
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    paragraph.add_run()._r.append(begin)

    instruction_text = OxmlElement("w:instrText")
    instruction_text.set(qn("xml:space"), "preserve")
    instruction_text.text = instruction
    paragraph.add_run()._r.append(instruction_text)

    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    paragraph.add_run()._r.append(separate)
    paragraph.add_run(visible_text)

    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    paragraph.add_run()._r.append(end)


def _add_endnote_hyperlink_field(paragraph, visible_text: str) -> None:
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    paragraph.add_run()._r.append(begin)

    instruction_text = OxmlElement("w:instrText")
    instruction_text.set(qn("xml:space"), "preserve")
    instruction_text.text = " ADDIN EN.CITE existing-citation "
    paragraph.add_run()._r.append(instruction_text)

    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    paragraph.add_run()._r.append(separate)

    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("w:anchor"), "_ENREF_1")
    hyperlink_run = OxmlElement("w:r")
    hyperlink_text = OxmlElement("w:t")
    hyperlink_text.text = visible_text
    hyperlink_run.append(hyperlink_text)
    hyperlink.append(hyperlink_run)
    paragraph._p.append(hyperlink)

    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    paragraph.add_run()._r.append(end)


def _endnote_field_xml(paragraph) -> tuple[bytes, ...]:
    elements = paragraph._p.xpath(
        "./w:r[w:fldChar or w:instrText] | ./w:hyperlink"
    )
    return tuple(element.xml.encode("utf-8") for element in elements)


def _add_hyperlink(paragraph, text: str | None, anchor: str = "x"):
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("w:anchor"), anchor)
    hyperlink_run = OxmlElement("w:r")
    if text is not None:
        hyperlink_text = OxmlElement("w:t")
        hyperlink_text.text = text
        hyperlink_run.append(hyperlink_text)
    hyperlink.append(hyperlink_run)
    paragraph._p.append(hyperlink)
    return hyperlink


def _protected_xml(paragraph) -> tuple[bytes, ...]:
    elements = paragraph._p.xpath(
        ".//w:fldChar | .//w:instrText | .//w:hyperlink | .//w:ins | .//w:fldSimple"
    )
    return tuple(etree.tostring(element) for element in elements)


def _append_tracked_change(paragraph, tag: str, text: str) -> None:
    change = OxmlElement(tag)
    change.set(qn("w:id"), "1")
    change_run = OxmlElement("w:r")
    change_text = OxmlElement("w:delText" if tag == "w:del" else "w:t")
    change_text.text = text
    change_run.append(change_text)
    change.append(change_run)
    paragraph._p.append(change)


def _omitted_content(tag: str, text: str) -> etree._Element:
    container = etree.Element(qn(tag))
    if tag in {"w:ins", "w:del", "w:moveFrom", "w:moveTo"}:
        container.set(qn("w:id"), "1")
    content_parent = container
    if tag == "w:sdt":
        content_parent = etree.SubElement(container, qn("w:sdtContent"))
    run = etree.SubElement(content_parent, qn("w:r"))
    text_element = etree.SubElement(
        run,
        qn("w:delText" if tag in {"w:del", "w:moveFrom"} else "w:t"),
    )
    text_element.text = text
    return container


def _append_text_box_alternate_content(
    run,
    *,
    field_branches: tuple[str, ...] = (),
    fallback_direct_field: bool = False,
) -> None:
    mc_namespace = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    wp_namespace = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    a_namespace = "http://schemas.openxmlformats.org/drawingml/2006/main"
    wps_namespace = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
    v_namespace = "urn:schemas-microsoft-com:vml"
    alternate_content = etree.Element(
        f"{{{mc_namespace}}}AlternateContent",
        nsmap={"mc": mc_namespace, "wps": wps_namespace, "v": v_namespace},
    )
    for branch_name in ("Choice", "Fallback"):
        branch = etree.SubElement(
            alternate_content,
            f"{{{mc_namespace}}}{branch_name}",
        )
        if branch_name == "Fallback" and fallback_direct_field:
            field_run = OxmlElement("w:r")
            begin = OxmlElement("w:fldChar")
            begin.set(qn("w:fldCharType"), "begin")
            field_run.append(begin)
            branch.append(field_run)
        if branch_name == "Choice":
            branch.set("Requires", "wps")
            drawing = etree.SubElement(branch, qn("w:drawing"))
            inline = etree.SubElement(drawing, f"{{{wp_namespace}}}inline")
            graphic = etree.SubElement(inline, f"{{{a_namespace}}}graphic")
            graphic_data = etree.SubElement(graphic, f"{{{a_namespace}}}graphicData")
            shape = etree.SubElement(graphic_data, f"{{{wps_namespace}}}wsp")
            text_box = etree.SubElement(shape, f"{{{wps_namespace}}}txbx")
        else:
            pict = etree.SubElement(branch, qn("w:pict"))
            shape = etree.SubElement(pict, f"{{{v_namespace}}}shape")
            text_box = etree.SubElement(shape, f"{{{v_namespace}}}textbox")
        text_box_content = OxmlElement("w:txbxContent")
        text_box_paragraph = OxmlElement("w:p")
        if branch_name in field_branches:
            field_run = OxmlElement("w:r")
            begin = OxmlElement("w:fldChar")
            begin.set(qn("w:fldCharType"), "begin")
            field_run.append(begin)
            text_box_paragraph.append(field_run)
        text_box_content.append(text_box_paragraph)
        text_box.append(text_box_content)
    run._r.append(alternate_content)


def _add_unclosed_text_box_field(
    paragraph,
    *,
    field_branches: tuple[str, ...] = ("Choice", "Fallback"),
    fallback_direct_field: bool = False,
) -> None:
    _append_text_box_alternate_content(
        paragraph.add_run(),
        field_branches=field_branches,
        fallback_direct_field=fallback_direct_field,
    )


def test_reference_section_heading_detection() -> None:
    triggers = [
        "References",
        "Bibliography",
        "Works Cited",
        "Literature Cited",
        "References Cited",
        "1. References",
        "VII. References",
    ]

    for text in triggers:
        assert is_reference_section_heading(text)

    assert not is_reference_section_heading("References to previous work are discussed here")


def test_word_replacement_preserves_non_pmid_text(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    _save_docx(input_docx, ["Before PMID: 12345678 after."])

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={"12345678": PubMedRecord("12345678", "Smith", "2024")},
    )

    assert _read_paragraph_text(output_docx) == "Before {Smith, 2024, PMID-12345678} after."
    assert result.replacements[0]["original_text"] == "PMID: 12345678"
    assert result.backup_path is None
    assert not list(tmp_path.glob("*.backup*.docx"))


def test_word_replacement_can_create_backup_when_requested(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    _save_docx(input_docx, ["Before PMID: 12345678 after."])

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={"12345678": PubMedRecord("12345678", "Smith", "2024")},
        options=ReplacementOptions(create_backup=True),
    )

    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert _read_paragraph_text(result.backup_path) == "Before PMID: 12345678 after."


def test_word_replacement_preserves_first_replaced_run_formatting(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("See ")
    pmid_run = paragraph.add_run("PMID: 12345678")
    pmid_run.bold = True
    paragraph.add_run(" now.")
    document.save(input_docx)

    replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={"12345678": PubMedRecord("12345678", "Smith", "2024")},
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    assert output_paragraph.text == "See {Smith, 2024, PMID-12345678} now."
    assert output_paragraph.runs[1].bold is True


def test_replaces_pmids_beside_existing_endnote_field(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph(
        "preparation, illustrating the broader challenge of preserving antigen structure "
        "during bacterial inactivation."
    )
    _add_word_field(paragraph, " ADDIN EN.CITE existing-citation ", "11-13")
    paragraph.add_run(
        " \u00a0(PMID 38319200; PMID 38644097; PMID 27966556).   &#x20;"
    )
    document.save(input_docx)

    scan = scan_docx(input_docx)

    assert scan.unique_pmids == ["38319200", "38644097", "27966556"]
    assert scan.warnings == []

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "38319200": PubMedRecord("38319200", "A", "2024"),
            "38644097": PubMedRecord("38644097", "B", "2024"),
            "27966556": PubMedRecord("27966556", "C", "2017"),
        },
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    assert output_paragraph.text == (
        "preparation, illustrating the broader challenge of preserving antigen structure "
        "during bacterial inactivation.11-13 \u00a0"
        "{A, 2024, PMID-38319200;B, 2024, PMID-38644097;C, 2017, PMID-27966556}.   "
        "&#x20;"
    )
    assert len(output_paragraph._p.xpath(".//w:fldChar")) == 3
    assert len(output_paragraph._p.xpath(".//w:instrText")) == 1
    assert result.warnings == []


def test_replaces_pmids_before_and_after_endnote_hyperlink_field(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph("Before (PMID 12345678) ")
    _add_endnote_hyperlink_field(paragraph, "1")
    paragraph.add_run(" after (PMID 23456789).")
    document.save(input_docx)

    input_paragraph = Document(input_docx).paragraphs[0]
    original_field_xml = _endnote_field_xml(input_paragraph)
    assert input_paragraph.text == (
        "Before (PMID 12345678) 1 after (PMID 23456789)."
    )

    scan = scan_docx(input_docx)

    assert scan.unique_pmids == ["12345678", "23456789"]
    assert scan.warnings == []

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "First", "2024"),
            "23456789": PubMedRecord("23456789", "Second", "2025"),
        },
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    assert output_paragraph.text == (
        "Before {First, 2024, PMID-12345678} 1 after "
        "{Second, 2025, PMID-23456789}."
    )
    assert _endnote_field_xml(output_paragraph) == original_field_xml
    assert result.warnings == []


def test_pmid_inside_endnote_hyperlink_field_is_rejected(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph("Before ")
    _add_endnote_hyperlink_field(paragraph, "PMID 12345678")
    paragraph.add_run(" after.")
    document.save(input_docx)

    original_field_xml = _endnote_field_xml(Document(input_docx).paragraphs[0])
    scan = scan_docx(input_docx)

    assert scan.unique_pmids == []
    assert scan.warnings == [
        "Skipped 1 identifier block overlapping field or hidden text content at "
        "body paragraph 0."
    ]

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Unsafe", "2024"),
        },
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    assert output_paragraph.text == "Before PMID 12345678 after."
    assert _endnote_field_xml(output_paragraph) == original_field_xml
    assert result.replacements == []
    assert result.warnings == scan.warnings


def test_pmid_inside_standalone_hyperlink_is_rejected(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph("Before ")
    _add_hyperlink(paragraph, "PMID 12345678")
    paragraph.add_run(" after.")
    document.save(input_docx)

    input_paragraph = Document(input_docx).paragraphs[0]
    original_hyperlink_xml = _protected_xml(input_paragraph)
    scan = scan_docx(input_docx)

    assert scan.unique_pmids == []
    assert scan.warnings == [
        "Skipped 1 identifier block overlapping field or hidden text content at "
        "body paragraph 0."
    ]

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Unsafe", "2024"),
        },
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    assert output_paragraph.text == "Before PMID 12345678 after."
    assert _protected_xml(output_paragraph) == original_hyperlink_xml
    assert result.replacements == []
    assert result.warnings == scan.warnings


def test_field_spanning_paragraphs_protects_second_paragraph_pmid(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    field_start = document.add_paragraph()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    field_start.add_run()._r.append(begin)
    instruction = OxmlElement("w:instrText")
    instruction.text = " ADDIN EN.CITE existing-citation "
    field_start.add_run()._r.append(instruction)
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    field_start.add_run()._r.append(separate)

    field_result = document.add_paragraph()
    _add_hyperlink(field_result, "1")
    field_result.add_run(" PMID 12345678")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    field_result.add_run()._r.append(end)
    document.save(input_docx)

    input_document = Document(input_docx)
    original_start_xml = _protected_xml(input_document.paragraphs[0])
    original_result_xml = _protected_xml(input_document.paragraphs[1])
    scan = scan_docx(input_docx)

    assert scan.unique_pmids == []
    assert scan.warnings == [
        "Skipped 1 identifier block overlapping field or hidden text content at "
        "body paragraph 1."
    ]

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Unsafe", "2024"),
        },
    )

    output_document = Document(output_docx)
    assert output_document.paragraphs[0].text == ""
    assert output_document.paragraphs[1].text == "1 PMID 12345678"
    assert _protected_xml(output_document.paragraphs[0]) == original_start_xml
    assert _protected_xml(output_document.paragraphs[1]) == original_result_xml
    assert result.replacements == []
    assert result.warnings == scan.warnings


def test_field_markers_inside_tracked_insertion_protect_pmid(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph("Before ")
    insertion = OxmlElement("w:ins")
    insertion.set(qn("w:id"), "1")
    for field_type in ("begin", "separate"):
        run = OxmlElement("w:r")
        if field_type == "separate":
            instruction_run = OxmlElement("w:r")
            instruction = OxmlElement("w:instrText")
            instruction.text = " ADDIN EN.CITE existing-citation "
            instruction_run.append(instruction)
            insertion.append(instruction_run)
        marker = OxmlElement("w:fldChar")
        marker.set(qn("w:fldCharType"), field_type)
        run.append(marker)
        insertion.append(run)
    paragraph._p.append(insertion)
    paragraph.add_run("PMID 12345678")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    paragraph.add_run()._r.append(end)
    paragraph.add_run(" after.")
    document.save(input_docx)

    input_paragraph = Document(input_docx).paragraphs[0]
    original_field_xml = _protected_xml(input_paragraph)
    scan = scan_docx(input_docx)

    assert scan.unique_pmids == []
    assert scan.warnings == [
        "Skipped 1 identifier block overlapping field or hidden text content at "
        "body paragraph 0."
    ]

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Unsafe", "2024"),
        },
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    assert output_paragraph.text == "Before PMID 12345678 after."
    assert _protected_xml(output_paragraph) == original_field_xml
    assert result.replacements == []
    assert result.warnings == scan.warnings


def test_empty_hyperlink_run_beside_pmid_does_not_block_replacement(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph("PMID ")
    _add_hyperlink(paragraph, None)
    paragraph.add_run("12345678")
    document.save(input_docx)

    input_paragraph = Document(input_docx).paragraphs[0]
    original_hyperlink_xml = _protected_xml(input_paragraph)
    scan = scan_docx(input_docx)
    assert scan.unique_pmids == ["12345678"]
    assert scan.warnings == []

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    assert output_paragraph.text == "{Safe, 2024, PMID-12345678}"
    assert _protected_xml(output_paragraph) == original_hyperlink_xml
    assert result.warnings == []


def test_plain_paragraph_after_closed_multi_paragraph_field_is_replaced(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    field_start = document.add_paragraph()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    field_start.add_run()._r.append(begin)
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    field_start.add_run()._r.append(separate)
    field_end = document.add_paragraph("field result")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    field_end.add_run()._r.append(end)
    document.add_paragraph("After PMID 12345678.")
    document.save(input_docx)

    scan = scan_docx(input_docx)
    assert scan.unique_pmids == ["12345678"]
    assert scan.warnings == []

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    output_document = Document(output_docx)
    assert output_document.paragraphs[0].text == ""
    assert output_document.paragraphs[1].text == "field result"
    assert output_document.paragraphs[2].text == (
        "After {Safe, 2024, PMID-12345678}."
    )
    assert result.warnings == []


def test_unclosed_text_box_fields_do_not_affect_later_body_paragraph(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    _add_unclosed_text_box_field(document.add_paragraph())
    document.add_paragraph("(PMID 12345678)")
    document.save(input_docx)

    scan = scan_docx(input_docx)

    assert scan.unique_pmids == ["12345678"]
    assert scan.warnings == []

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    assert Document(output_docx).paragraphs[1].text == (
        "{Safe, 2024, PMID-12345678}"
    )
    assert len(result.replacements) == 1
    assert result.warnings == []


def test_unclosed_field_only_in_text_box_fallback_does_not_affect_body(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    _add_unclosed_text_box_field(
        document.add_paragraph(),
        fallback_direct_field=True,
    )
    document.add_paragraph("(PMID 12345678)")
    document.save(input_docx)

    scan = scan_docx(input_docx)

    assert scan.unique_pmids == ["12345678"]
    assert scan.warnings == []

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    assert Document(output_docx).paragraphs[1].text == (
        "{Safe, 2024, PMID-12345678}"
    )
    assert len(result.replacements) == 1
    assert result.warnings == []


def test_text_box_fields_do_not_affect_text_in_the_host_paragraph(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph("PMID 12345678")
    _add_unclosed_text_box_field(paragraph)
    document.save(input_docx)

    scan = scan_docx(input_docx)

    assert scan.unique_pmids == ["12345678"]
    assert scan.warnings == []

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    assert Document(output_docx).paragraphs[0].text == (
        "{Safe, 2024, PMID-12345678}"
    )
    assert len(result.replacements) == 1
    assert result.warnings == []


def test_story_field_depths_are_computed_once_per_scan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_docx = tmp_path / "input.docx"
    _save_docx(
        input_docx,
        ["PMID 12345678", "PMID 23456789", "PMID 34567890"],
    )
    calls = 0
    original = word_module._story_field_depths

    def counting_story_field_depths(story_root):
        nonlocal calls
        calls += 1
        return original(story_root)

    monkeypatch.setattr(
        word_module,
        "_story_field_depths",
        counting_story_field_depths,
    )

    scan = scan_docx(input_docx)

    assert scan.unique_pmids == ["12345678", "23456789", "34567890"]
    assert calls == 1


def test_story_field_depths_are_computed_once_per_replacement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    _save_docx(
        input_docx,
        ["PMID 12345678", "PMID 23456789", "PMID 34567890"],
    )
    calls = 0
    original = word_module._story_field_depths

    def counting_story_field_depths(story_root):
        nonlocal calls
        calls += 1
        return original(story_root)

    monkeypatch.setattr(
        word_module,
        "_story_field_depths",
        counting_story_field_depths,
    )

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            pmid: PubMedRecord(pmid, "Safe", "2024")
            for pmid in ("12345678", "23456789", "34567890")
        },
    )

    assert len(result.replacements) == 3
    assert result.warnings == []
    assert calls == 1


def test_replacement_rejects_pmid_run_containing_text_box(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph()
    pmid_run = paragraph.add_run("PMID 12345678")
    _append_text_box_alternate_content(pmid_run)
    document.save(input_docx)

    input_paragraph = Document(input_docx).paragraphs[0]
    original_run_xml = etree.tostring(input_paragraph.runs[0]._r)

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Unsafe", "2024"),
        },
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    output_run = output_paragraph.runs[0]
    assert etree.tostring(output_run._r) == original_run_xml
    assert sum(1 for _ in output_run._r.iter(qn("w:txbxContent"))) == 2
    assert output_paragraph.text == "PMID 12345678"
    assert result.replacements == []
    assert len(result.warnings) == 1
    assert "sharing a Word run with non-text content" in result.warnings[0]


def test_replacement_edits_pmid_run_beside_text_box_run(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph("Before ")
    paragraph.add_run("PMID 12345678")
    _append_text_box_alternate_content(paragraph.add_run())
    paragraph.add_run(" after.")
    document.save(input_docx)

    input_paragraph = Document(input_docx).paragraphs[0]
    original_text_box_xml = etree.tostring(input_paragraph.runs[2]._r)

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    assert output_paragraph.text == "Before {Safe, 2024, PMID-12345678} after."
    assert etree.tostring(output_paragraph.runs[2]._r) == original_text_box_xml
    assert sum(
        1 for _ in output_paragraph.runs[2]._r.iter(qn("w:txbxContent"))
    ) == 2
    assert len(result.replacements) == 1
    assert result.warnings == []


@pytest.mark.parametrize("change_tag", ["w:del", "w:ins"])
def test_replacement_rejects_pmid_split_by_tracked_change(
    tmp_path: Path,
    change_tag: str,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("PMID 12")
    _append_tracked_change(paragraph, change_tag, "tracked")
    paragraph.add_run("345678")
    document.save(input_docx)

    original_paragraph_xml = etree.tostring(Document(input_docx).paragraphs[0]._p)
    scan = scan_docx(input_docx)

    assert scan.unique_pmids == []
    assert scan.warnings == [
        "Skipped 1 identifier block next to tracked changes or content controls "
        "that may alter the identifier at body paragraph 0."
    ]

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Unsafe", "2024"),
        },
    )

    assert etree.tostring(Document(output_docx).paragraphs[0]._p) == original_paragraph_xml
    assert result.replacements == []
    assert result.warnings == scan.warnings


def test_replacement_allows_pmid_split_across_adjacent_plain_runs(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("Before PMID 12")
    paragraph.add_run("345678 after")
    document.save(input_docx)

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    assert Document(output_docx).paragraphs[0].text == (
        "Before {Safe, 2024, PMID-12345678} after"
    )
    assert len(result.replacements) == 1
    assert result.warnings == []


def test_replacement_preserves_proof_error_inside_pmid_span(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("PMID 12")
    paragraph.add_run("345678")
    document.save(input_docx)

    injected = Document(input_docx)
    injected_paragraph = injected.paragraphs[0]
    proof_error = etree.Element(qn("w:proofErr"))
    proof_error.set(qn("w:type"), "spellStart")
    injected_paragraph.runs[1]._r.addprevious(proof_error)
    injected.save(input_docx)

    reloaded = Document(input_docx).paragraphs[0]
    before_tags = [child.tag for child in reloaded._p]
    before_marker_xml = etree.tostring(reloaded._p[1])
    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    assert output_paragraph.text == "{Safe, 2024, PMID-12345678}"
    assert [child.tag for child in output_paragraph._p] == before_tags
    assert etree.tostring(output_paragraph._p[1]) == before_marker_xml
    assert len(result.replacements) == 1
    assert result.warnings == []


def test_replacement_preserves_go_back_bookmark_inside_pmid_span(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("PMID 12")
    paragraph.add_run("34")
    paragraph.add_run("5678")
    document.save(input_docx)

    injected = Document(input_docx)
    injected_paragraph = injected.paragraphs[0]
    bookmark_start = etree.Element(qn("w:bookmarkStart"))
    bookmark_start.set(qn("w:id"), "0")
    bookmark_start.set(qn("w:name"), "_GoBack")
    bookmark_end = etree.Element(qn("w:bookmarkEnd"))
    bookmark_end.set(qn("w:id"), "0")
    injected_paragraph.runs[1]._r.addprevious(bookmark_start)
    injected_paragraph.runs[1]._r.addnext(bookmark_end)
    injected.save(input_docx)

    reloaded = Document(input_docx).paragraphs[0]
    before_tags = [child.tag for child in reloaded._p]
    before_markers = (
        etree.tostring(reloaded._p[1]),
        etree.tostring(reloaded._p[3]),
    )
    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    assert output_paragraph.text == "{Safe, 2024, PMID-12345678}"
    assert [child.tag for child in output_paragraph._p] == before_tags
    assert (
        etree.tostring(output_paragraph._p[1]),
        etree.tostring(output_paragraph._p[3]),
    ) == before_markers
    assert len(result.replacements) == 1
    assert result.warnings == []


def test_replacement_preserves_comment_range_inside_pmid_span(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("PMID 12")
    paragraph.add_run("34")
    paragraph.add_run("5678")
    comment_range_start = OxmlElement("w:commentRangeStart")
    comment_range_start.set(qn("w:id"), "7")
    comment_range_end = OxmlElement("w:commentRangeEnd")
    comment_range_end.set(qn("w:id"), "7")
    paragraph.runs[1]._r.addprevious(comment_range_start)
    paragraph.runs[1]._r.addnext(comment_range_end)
    document.save(input_docx)

    reloaded = Document(input_docx).paragraphs[0]
    before_tags = [child.tag for child in reloaded._p]
    before_markers = (
        etree.tostring(reloaded._p[1]),
        etree.tostring(reloaded._p[3]),
    )
    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    assert output_paragraph.text == "{Safe, 2024, PMID-12345678}"
    assert [child.tag for child in output_paragraph._p] == before_tags
    assert (
        etree.tostring(output_paragraph._p[1]),
        etree.tostring(output_paragraph._p[3]),
    ) == before_markers
    assert len(result.replacements) == 1
    assert result.warnings == []


def test_replacement_preserves_empty_run_inside_pmid_span(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("PMID 12")
    paragraph.add_run()
    paragraph.add_run("345678")
    document.save(input_docx)

    reloaded = Document(input_docx).paragraphs[0]
    empty_run_xml = etree.tostring(reloaded._p[1])
    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    assert output_paragraph.text == "{Safe, 2024, PMID-12345678}"
    assert etree.tostring(output_paragraph._p[1]) == empty_run_xml
    assert len(result.replacements) == 1
    assert result.warnings == []


def test_replacement_rejects_tracked_deletion_immediately_before_pmid(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph()
    _append_tracked_change(paragraph, "w:del", "old")
    paragraph.add_run("PMID 12345678")
    document.save(input_docx)

    original_paragraph_xml = etree.tostring(Document(input_docx).paragraphs[0]._p)
    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    assert etree.tostring(output_paragraph._p) == original_paragraph_xml
    assert output_paragraph.text == "PMID 12345678"
    assert result.replacements == []
    assert result.warnings == [
        "Skipped 1 identifier block next to tracked changes or content controls "
        "that may alter the identifier at body paragraph 0."
    ]


def test_replacement_rejects_inserted_tail_digits_after_pmid(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph("PMID 1234")
    paragraph._p.append(_omitted_content("w:ins", "5678"))
    document.save(input_docx)

    original_paragraph_xml = etree.tostring(Document(input_docx).paragraphs[0]._p)
    scan = scan_docx(input_docx)
    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={"1234": PubMedRecord("1234", "Wrong", "2024")},
    )

    warning = (
        "Skipped 1 identifier block next to tracked changes or content controls "
        "that may alter the identifier at body paragraph 0."
    )
    assert scan.unique_pmids == []
    assert scan.warnings == [warning]
    assert etree.tostring(Document(output_docx).paragraphs[0]._p) == original_paragraph_xml
    assert result.replacements == []
    assert result.warnings == [warning]


def test_replacement_rejects_content_control_after_pmid(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph("PMID 12345678")
    paragraph._p.append(_omitted_content("w:sdt", "9"))
    document.save(input_docx)

    original_paragraph_xml = etree.tostring(Document(input_docx).paragraphs[0]._p)
    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Wrong", "2024"),
        },
    )

    assert etree.tostring(Document(output_docx).paragraphs[0]._p) == original_paragraph_xml
    assert result.replacements == []
    assert result.warnings == [
        "Skipped 1 identifier block next to tracked changes or content controls "
        "that may alter the identifier at body paragraph 0."
    ]


def test_replacement_allows_distant_inserted_content(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph("PMID 12345678 followed by ordinary text")
    paragraph._p.append(_omitted_content("w:ins", "tracked"))
    document.save(input_docx)

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    assert output_paragraph.text == (
        "{Safe, 2024, PMID-12345678} followed by ordinary text"
    )
    assert output_paragraph._p[-1].tag == qn("w:ins")
    assert len(result.replacements) == 1
    assert result.warnings == []


@pytest.mark.parametrize("story", ["header", "footer"])
def test_replacement_rejects_inserted_tail_digits_in_header_or_footer(
    tmp_path: Path,
    story: str,
) -> None:
    input_docx = tmp_path / f"{story}.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = getattr(document.sections[0], story).paragraphs[0]
    paragraph.add_run("PMID 1234")
    paragraph._p.append(_omitted_content("w:ins", "5678"))
    document.save(input_docx)
    options = ReplacementOptions(
        include_headers=story == "header",
        include_footers=story == "footer",
    )

    scan = scan_docx(input_docx, options)
    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={"1234": PubMedRecord("1234", "Wrong", "2024")},
        options=options,
    )

    warning = (
        "Skipped 1 identifier block next to tracked changes or content controls "
        f"that may alter the identifier at {story} paragraph 0."
    )
    assert scan.unique_pmids == []
    assert scan.warnings == [warning]
    assert result.replacements == []
    assert result.warnings == [warning]


def test_replacement_rejects_pmid_run_containing_inline_image(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    image_path = tmp_path / "pixel.png"
    image_path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
    )
    document = Document()
    paragraph = document.add_paragraph()
    pmid_run = paragraph.add_run("PMID 12345678")
    pmid_run.add_picture(str(image_path))
    document.save(input_docx)

    input_paragraph = Document(input_docx).paragraphs[0]
    original_drawing_xml = etree.tostring(
        next(input_paragraph.runs[0]._r.iter(qn("w:drawing")))
    )

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Unsafe", "2024"),
        },
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    output_drawing = next(output_paragraph.runs[0]._r.iter(qn("w:drawing")))
    assert etree.tostring(output_drawing) == original_drawing_xml
    assert output_paragraph.text == "PMID 12345678"
    assert result.replacements == []
    assert len(result.warnings) == 1


def test_replacement_allows_run_with_tabs_and_breaks(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    run = document.add_paragraph().add_run("PMID 12345678")
    run.add_tab()
    run.add_break()
    run.add_text("tail")
    document.save(input_docx)

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    assert Document(output_docx).paragraphs[0].text == (
        "{Safe, 2024, PMID-12345678}\t\ntail"
    )
    assert len(result.replacements) == 1
    assert result.warnings == []


@pytest.mark.parametrize(
    ("content_name", "child_tag", "attributes"),
    [
        ("page break", "w:br", {qn("w:type"): "page"}),
        ("column break", "w:br", {qn("w:type"): "column"}),
        ("cleared break", "w:br", {qn("w:clear"): "all"}),
        ("carriage return", "w:cr", {}),
        ("soft hyphen", "w:softHyphen", {}),
        ("non-breaking hyphen", "w:noBreakHyphen", {}),
        ("positioned tab", "w:ptab", {}),
    ],
)
def test_replacement_rejects_pmid_run_with_non_text_content(
    tmp_path: Path,
    content_name: str,
    child_tag: str,
    attributes: dict[str, str],
) -> None:
    input_docx = tmp_path / f"{content_name}.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    run = document.add_paragraph().add_run("PMID 12345678")
    child = OxmlElement(child_tag)
    for name, value in attributes.items():
        child.set(name, value)
    run._r.append(child)
    document.save(input_docx)

    original_run_xml = etree.tostring(Document(input_docx).paragraphs[0].runs[0]._r)
    scan = scan_docx(input_docx)

    assert scan.unique_pmids == []
    assert scan.warnings == [
        "Skipped 1 identifier block sharing a Word run with non-text content "
        "(image, text box, page break, etc.) at body paragraph 0."
    ]

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Unsafe", "2024"),
        },
    )

    output_run = Document(output_docx).paragraphs[0].runs[0]
    assert etree.tostring(output_run._r) == original_run_xml
    assert result.replacements == []
    assert result.warnings == scan.warnings


def test_scan_and_replace_partition_parenthetical_block_with_page_break_identically(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph()
    opening_run = paragraph.add_run("(")
    page_break = OxmlElement("w:br")
    page_break.set(qn("w:type"), "page")
    opening_run._r.append(page_break)
    paragraph.add_run("PMID 12345678)")
    document.save(input_docx)

    scan = scan_docx(input_docx)
    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Unsafe", "2024"),
        },
    )

    expected_warning = (
        "Skipped 1 identifier block sharing a Word run with non-text content "
        "(image, text box, page break, etc.) at body paragraph 0."
    )
    assert scan.unique_pmids == []
    assert scan.warnings == [expected_warning]
    assert result.replacements == []
    assert result.warnings == scan.warnings


def test_replacement_allows_last_rendered_page_break_in_pmid_run(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    run = document.add_paragraph().add_run("PMID 12345678 tail")
    run.bold = True
    run._r.insert(1, OxmlElement("w:lastRenderedPageBreak"))
    document.save(input_docx)

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    output_paragraph = Document(output_docx).paragraphs[0]
    assert output_paragraph.text == "{Safe, 2024, PMID-12345678} tail"
    assert output_paragraph.runs[0].bold is True
    assert len(result.replacements) == 1
    assert result.warnings == []


def test_unapplied_replacement_is_warned_and_not_reported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    _save_docx(input_docx, ["Before PMID 12345678 after."])
    monkeypatch.setattr(
        word_module,
        "_replace_paragraph_range",
        lambda paragraph, start, end, replacement: False,
    )

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    assert Document(output_docx).paragraphs[0].text == "Before PMID 12345678 after."
    assert result.replacements == []
    assert result.warnings == [
        "Could not apply replacement at body paragraph 0; left original text "
        "unchanged: PMID 12345678"
    ]


def test_identifier_inside_existing_word_field_remains_untouched(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph("Before ")
    _add_word_field(paragraph, " ADDIN EN.CITE existing-citation ", "PMID 38319200")
    paragraph.add_run(" after PMID 38644097.")
    document.save(input_docx)

    scan = scan_docx(input_docx)

    assert scan.unique_pmids == ["38644097"]
    assert "overlapping field" in scan.warnings[0]

    replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "38319200": PubMedRecord("38319200", "Unsafe", "2024"),
            "38644097": PubMedRecord("38644097", "Safe", "2024"),
        },
    )

    assert Document(output_docx).paragraphs[0].text == (
        "Before PMID 38319200 after {Safe, 2024, PMID-38644097}."
    )


def test_unresolved_pmid_handling_leaves_unresolved_text(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    _save_docx(input_docx, ["PMIDs: 12345678, 99999999"])

    replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={"12345678": PubMedRecord("12345678", "Smith", "2024")},
    )

    assert _read_paragraph_text(output_docx) == "{Smith, 2024, PMID-12345678} [unresolved PMID: 99999999]"


def test_dry_run_reports_without_writing_output(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    _save_docx(input_docx, ["PMID: 12345678"])

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={"12345678": PubMedRecord("12345678", "Smith", "2024")},
        options=ReplacementOptions(dry_run=True),
    )

    assert result.replacements[0]["replacement_text"] == "{Smith, 2024, PMID-12345678}"
    assert not output_docx.exists()
    assert _read_paragraph_text(input_docx) == "PMID: 12345678"


def test_scan_docx_finds_tables_by_default(tmp_path: Path) -> None:
    input_docx = tmp_path / "table.docx"
    document = Document()
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "PMID: 12345678"
    document.save(input_docx)

    scan = scan_docx(input_docx)

    assert scan.unique_pmids == ["12345678"]
    assert scan.occurrences[0].location.part == "table"


def test_scan_docx_finds_pmids_in_comments_by_default(tmp_path: Path) -> None:
    input_docx = tmp_path / "comment.docx"
    document = Document()
    paragraph = document.add_paragraph("Anchor text")
    document.add_comment(paragraph.runs[0], text="Please cite PMID: 12345678", author="Reviewer")
    document.save(input_docx)

    scan = scan_docx(input_docx)

    assert scan.unique_pmids == ["12345678"]
    assert scan.occurrences[0].location.part == "comment"
    assert scan.occurrences[0].location.comment_id == 0


def test_scan_docx_can_skip_comments(tmp_path: Path) -> None:
    input_docx = tmp_path / "comment.docx"
    document = Document()
    paragraph = document.add_paragraph("Anchor text")
    document.add_comment(paragraph.runs[0], text="Please cite PMID: 12345678", author="Reviewer")
    document.save(input_docx)

    scan = scan_docx(input_docx, ReplacementOptions(include_comments=False))

    assert scan.unique_pmids == []


def test_parenthetical_single_pmid_replacement(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    _save_docx(input_docx, ["Before (6426050) after."])

    replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={"6426050": PubMedRecord("6426050", "Petersen", "1983")},
        options=ReplacementOptions(scan_parenthetical_pmids=True),
    )

    assert _read_paragraph_text(output_docx) == "Before {Petersen, 1983, PMID-6426050} after."


def test_word_replacement_inserts_comment_pmids_at_anchor(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("before ")
    anchor = paragraph.add_run("stimulation")
    paragraph.add_run(" after")
    document.add_comment(anchor, text="Please cite PMID: 12345678", author="Reviewer")
    document.save(input_docx)

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={"12345678": PubMedRecord("12345678", "Smith", "2024")},
    )

    output_document = Document(output_docx)
    assert output_document.paragraphs[0].text == "before stimulation {Smith, 2024, PMID-12345678} after"
    assert output_document.comments.get(0).text == "Please cite PMID: 12345678"
    assert result.replacements[0]["location"]["part"] == "body"
    assert result.replacements[0]["location"]["comment_id"] == 0
    assert result.replacements[0]["location"]["source_part"] == "comment"


def test_comment_pmid_with_soft_hyphen_is_inserted_at_anchor(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    anchor = document.add_paragraph().add_run("Anchor text")
    comment = document.add_comment(
        anchor,
        text="Please cite PMID 12345678",
        author="Reviewer",
    )
    pmid_run = next(run for run in comment.paragraphs[0].runs if "PMID" in run.text)
    pmid_run._r.append(OxmlElement("w:softHyphen"))
    document.save(input_docx)

    scan = scan_docx(input_docx)
    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    assert scan.unique_pmids == ["12345678"]
    assert scan.warnings == []
    assert Document(output_docx).paragraphs[0].text == (
        "Anchor text {Safe, 2024, PMID-12345678}"
    )
    assert len(result.replacements) == 1
    assert result.warnings == []


def test_comment_pmid_split_by_insertion_is_not_inserted(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    anchor = document.add_paragraph().add_run("Anchor text")
    comment = document.add_comment(anchor, text="", author="Reviewer")
    comment_paragraph = comment.paragraphs[0]
    comment_paragraph.add_run("PMID 12")
    comment_paragraph._p.append(_omitted_content("w:ins", "34"))
    comment_paragraph.add_run("5678")
    document.save(input_docx)

    original_comment_xml = etree.tostring(
        Document(input_docx).comments.get(0).paragraphs[0]._p
    )
    scan = scan_docx(input_docx)
    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={"125678": PubMedRecord("125678", "Wrong", "2024")},
    )

    warning = (
        "Skipped 1 identifier block next to tracked changes or content controls "
        "that may alter the identifier at comment paragraph 0."
    )
    output_document = Document(output_docx)
    assert scan.unique_pmids == []
    assert scan.warnings == [warning]
    assert output_document.paragraphs[0].text == "Anchor text"
    assert (
        etree.tostring(output_document.comments.get(0).paragraphs[0]._p)
        == original_comment_xml
    )
    assert result.replacements == []
    assert result.warnings == [warning]


def test_unclosed_field_in_one_comment_does_not_protect_later_comment(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "comment.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    first_anchor = document.add_paragraph().add_run("First anchor")
    second_anchor = document.add_paragraph().add_run("Second anchor")
    first_comment = document.add_comment(first_anchor, text="", author="Reviewer")
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    first_comment.paragraphs[0].add_run()._r.append(begin)
    document.add_comment(
        second_anchor,
        text="Please cite PMID 12345678",
        author="Reviewer",
    )
    document.save(input_docx)

    scan = scan_docx(input_docx)

    assert scan.unique_pmids == ["12345678"]
    assert scan.warnings == []

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Safe", "2024"),
        },
    )

    assert Document(output_docx).paragraphs[1].text == (
        "Second anchor {Safe, 2024, PMID-12345678}"
    )
    assert len(result.replacements) == 1
    assert result.warnings == []


def test_comment_pmid_beside_unrelated_hyperlink_is_inserted_at_anchor(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    anchor_paragraph = document.add_paragraph()
    anchor = anchor_paragraph.add_run("Anchor text")
    comment = document.add_comment(
        anchor,
        text="Please cite PMID 12345678. See ",
        author="Reviewer",
    )
    _add_hyperlink(comment.paragraphs[0], "source")
    document.save(input_docx)

    input_document = Document(input_docx)
    input_comment_paragraph = input_document.comments.get(0).paragraphs[0]
    original_hyperlink_xml = _protected_xml(input_comment_paragraph)
    scan = scan_docx(input_docx)

    assert scan.unique_pmids == ["12345678"]
    assert scan.warnings == []

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Smith", "2024"),
        },
    )

    output_document = Document(output_docx)
    output_comment_paragraph = output_document.comments.get(0).paragraphs[0]
    assert output_document.paragraphs[0].text == (
        "Anchor text {Smith, 2024, PMID-12345678}"
    )
    assert output_comment_paragraph.text == "Please cite PMID 12345678. See source"
    assert _protected_xml(output_comment_paragraph) == original_hyperlink_xml
    assert result.warnings == []


def test_reference_section_identifiers_are_skipped_by_default(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    _save_docx(
        input_docx,
        [
            "Before PMID: 12345678.",
            "References",
            "1. Example article. PMID: 99999999. DOI: 10.1021/acs.chemrev.3c00409.",
        ],
    )

    scan = scan_docx(input_docx)
    assert scan.unique_pmids == ["12345678"]
    assert scan.reference_section_start == {"part": "body", "paragraph_index": 1}
    assert {item["normalized"] for item in scan.skipped_identifiers} == {
        "99999999",
        "10.1021/acs.chemrev.3c00409",
    }

    replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Smith", "2024"),
            "99999999": PubMedRecord("99999999", "Wrong", "2020"),
        },
    )

    output_document = Document(output_docx)
    assert output_document.paragraphs[0].text == "Before {Smith, 2024, PMID-12345678}."
    assert output_document.paragraphs[2].text == (
        "1. Example article. PMID: 99999999. DOI: 10.1021/acs.chemrev.3c00409."
    )


def test_reference_section_skip_can_be_disabled(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    _save_docx(input_docx, ["References", "PMID: 99999999"])

    replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={"99999999": PubMedRecord("99999999", "Smith", "2020")},
        options=ReplacementOptions(skip_reference_section=False),
    )

    assert _read_paragraph_text(output_docx, 1) == "{Smith, 2020, PMID-99999999}"


def test_tables_after_reference_section_are_skipped(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    before_table = document.add_table(rows=1, cols=1)
    before_table.cell(0, 0).text = "PMID: 12345678"
    document.add_paragraph("References")
    after_table = document.add_table(rows=1, cols=1)
    after_table.cell(0, 0).text = "PMID: 99999999"
    document.save(input_docx)

    scan = scan_docx(input_docx)
    assert scan.unique_pmids == ["12345678"]
    assert [item["normalized"] for item in scan.skipped_identifiers] == ["99999999"]

    replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Before", "2024"),
            "99999999": PubMedRecord("99999999", "After", "2020"),
        },
    )

    output_document = Document(output_docx)
    assert output_document.tables[0].cell(0, 0).text == "{Before, 2024, PMID-12345678}"
    assert output_document.tables[1].cell(0, 0).text == "PMID: 99999999"


def test_comments_anchored_after_reference_section_are_skipped(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    before = document.add_paragraph()
    before_anchor = before.add_run("before")
    document.add_comment(before_anchor, text="Please cite PMID: 12345678", author="Reviewer")
    document.add_paragraph("References")
    after = document.add_paragraph()
    after_anchor = after.add_run("after")
    document.add_comment(after_anchor, text="Please cite PMID: 99999999", author="Reviewer")
    document.save(input_docx)

    scan = scan_docx(input_docx)
    assert scan.unique_pmids == ["12345678"]
    assert [item["normalized"] for item in scan.skipped_identifiers] == ["99999999"]

    replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "12345678": PubMedRecord("12345678", "Before", "2024"),
            "99999999": PubMedRecord("99999999", "After", "2020"),
        },
    )

    output_document = Document(output_docx)
    assert output_document.paragraphs[0].text == "before {Before, 2024, PMID-12345678}"
    assert output_document.paragraphs[2].text == "after"


def test_word_replacement_can_skip_comment_anchor_insertions(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    document = Document()
    paragraph = document.add_paragraph("Anchor text")
    document.add_comment(paragraph.runs[0], text="Please cite PMID: 12345678", author="Reviewer")
    document.save(input_docx)

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={"12345678": PubMedRecord("12345678", "Smith", "2024")},
        options=ReplacementOptions(include_comments=False),
    )

    assert Document(output_docx).paragraphs[0].text == "Anchor text"
    assert result.replacements == []


def test_parenthetical_multi_pmid_replacement(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    _save_docx(input_docx, ["Before (6426050, 104929) after."])

    replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "6426050": PubMedRecord("6426050", "Petersen", "1983"),
            "104929": PubMedRecord("104929", "Petersen", "1978"),
        },
        options=ReplacementOptions(scan_parenthetical_pmids=True),
    )

    assert (
        _read_paragraph_text(output_docx)
        == "Before {Petersen, 1983, PMID-6426050;Petersen, 1978, PMID-104929} after."
    )


def test_parenthetical_unresolved_pmid_stays_outside_braces(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    _save_docx(input_docx, ["Before (6426050, 999999999) after."])

    replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={"6426050": PubMedRecord("6426050", "Petersen", "1983")},
        options=ReplacementOptions(scan_parenthetical_pmids=True),
    )

    assert (
        _read_paragraph_text(output_docx)
        == "Before {Petersen, 1983, PMID-6426050} [unresolved PMID: 999999999] after."
    )


def test_parenthetical_mode_preserves_surrounding_punctuation(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    _save_docx(input_docx, ["activity. (6426050), (104929), (9036716)."])

    replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "6426050": PubMedRecord("6426050", "A", "1983"),
            "104929": PubMedRecord("104929", "B", "1978"),
            "9036716": PubMedRecord("9036716", "C", "1997"),
        },
        options=ReplacementOptions(scan_parenthetical_pmids=True),
    )

    assert (
        _read_paragraph_text(output_docx)
        == "activity. {A, 1983, PMID-6426050}, {B, 1978, PMID-104929}, {C, 1997, PMID-9036716}."
    )


def test_square_bracketed_pmid_doi_same_article_deduplicates_and_consumes_wrapper(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    doi = "10.1021/acs.chemrev.3c00409"
    _save_docx(input_docx, [f"Before [PMID: 38408451; DOI: {doi}] after."])
    record = ReferenceRecord(
        citation_key="PMID-38408451",
        first_author="Wijesundara",
        year="2024",
        pmid="38408451",
        doi=doi,
    )

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_identifier={
            ("pmid", "38408451"): record,
            ("doi", doi): record,
        },
    )

    assert _read_paragraph_text(output_docx) == "Before {Wijesundara, 2024, PMID-38408451} after."
    assert result.replacements[0]["original_text"] == f"[PMID: 38408451; DOI: {doi}]"
    assert result.replacements[0]["replacement_text"].count("PMID-38408451") == 1
    assert "#PMID-" not in result.replacements[0]["replacement_text"]


def test_unwrapped_adjacent_pmid_doi_same_article_deduplicates(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    doi = "10.1186/s12951-023-01782-w"
    _save_docx(input_docx, [f"Before PMID: 36737783; DOI: {doi} after."])
    record = ReferenceRecord(
        citation_key="PMID-36737783",
        first_author="Stillman",
        year="2023",
        pmid="36737783",
        doi=doi,
    )

    result = replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_identifier={
            ("pmid", "36737783"): record,
            ("doi", doi): record,
        },
    )

    assert _read_paragraph_text(output_docx) == "Before {Stillman, 2023, PMID-36737783} after."
    assert result.replacements[0]["replacement_text"].count("PMID-36737783") == 1


def test_parenthetical_labeled_identifier_only_wrapper_is_consumed(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    _save_docx(input_docx, ["Before (PMID: 30826373; PMID: 34866536) after."])

    replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={
            "30826373": PubMedRecord("30826373", "Zhong", "2019"),
            "34866536": PubMedRecord("34866536", "Li", "2021"),
        },
    )

    assert (
        _read_paragraph_text(output_docx)
        == "Before {Zhong, 2019, PMID-30826373;Li, 2021, PMID-34866536} after."
    )


def test_wrapper_with_prose_is_not_consumed(tmp_path: Path) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    _save_docx(input_docx, ["Before [see PMID: 38408451] after."])

    replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={"38408451": PubMedRecord("38408451", "Wijesundara", "2024")},
    )

    assert _read_paragraph_text(output_docx) == "Before [see {Wijesundara, 2024, PMID-38408451}] after."


def test_mixed_resolved_unresolved_wrapper_keeps_unresolved_outside_braces(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "input.docx"
    output_docx = tmp_path / "output.docx"
    _save_docx(input_docx, ["Before [PMID: 30826373; DOI: 10.9999/unresolved] after."])

    replace_pmids_in_docx(
        input_docx=input_docx,
        output_docx=output_docx,
        records_by_pmid={"30826373": PubMedRecord("30826373", "Zhong", "2019")},
    )

    assert (
        _read_paragraph_text(output_docx)
        == "Before {Zhong, 2019, PMID-30826373} [unresolved DOI: 10.9999/unresolved] after."
    )

from pmid2endnote.scanner import extract_unique_pmids, normalize_doi, scan_text


def test_pmid_regex_extraction_examples() -> None:
    text = (
        "PMID: 12345678; PMID 23456789; PMID#34567890; "
        "pmid:45678901; PMIDs: 56789012, 67890123."
    )

    blocks = scan_text(text)

    assert [block.pmids for block in blocks] == [
        ("12345678",),
        ("23456789",),
        ("34567890",),
        ("45678901",),
        ("56789012", "67890123"),
    ]


def test_multiple_pmids_in_one_block_with_semicolon() -> None:
    blocks = scan_text("Supported by PMIDs 12345678; 23456789 and more text.")

    assert len(blocks) == 1
    assert blocks[0].original_text == "PMIDs 12345678; 23456789"
    assert blocks[0].pmids == ("12345678", "23456789")


def test_deduplication_preserves_first_seen_order() -> None:
    blocks = scan_text("PMID: 222 PMID: 111 PMIDs: 222, 333")

    assert extract_unique_pmids(blocks) == ["222", "111", "333"]


def test_unlabeled_numbers_are_ignored() -> None:
    blocks = scan_text("The trial number 12345678 is not labeled, but PMID: 87654321 is.")

    assert [block.pmids for block in blocks] == [("87654321",)]


def test_default_mode_ignores_parenthetical_pmids() -> None:
    assert scan_text("activity. (6426050)") == []


def test_parenthetical_mode_detects_single_and_lists() -> None:
    blocks = scan_text(
        "activity. (6426050), (104929; 9036716), and (6426050,104929;9036716).",
        scan_parenthetical_pmids=True,
    )

    assert [block.original_text for block in blocks] == [
        "(6426050)",
        "(104929; 9036716)",
        "(6426050,104929;9036716)",
    ]
    assert [block.pmids for block in blocks] == [
        ("6426050",),
        ("104929", "9036716"),
        ("6426050", "104929", "9036716"),
    ]
    assert {block.kind for block in blocks} == {"pmid"}
    assert {block.source for block in blocks} == {"parenthetical"}


def test_parenthetical_mode_rejects_ambiguous_values() -> None:
    text = "(1983) (12-15) (Fig. 2) (6426050a) (6426050 and 104929) (6426050.104929) 6426050"

    assert scan_text(text, scan_parenthetical_pmids=True) == []


def test_labeled_behavior_is_unchanged_with_parenthetical_mode() -> None:
    blocks = scan_text("PMIDs: 12345678, 23456789", scan_parenthetical_pmids=True)

    assert len(blocks) == 1
    assert blocks[0].kind == "pmid"
    assert blocks[0].source == "labeled"
    assert blocks[0].pmids == ("12345678", "23456789")


def test_doi_label_and_url_detection_without_double_match() -> None:
    blocks = scan_text("See DOI: https://doi.org/10.1021/ACS.JOC.0C00770.")

    assert len(blocks) == 1
    assert blocks[0].kind == "doi"
    assert blocks[0].source == "doi_label"
    assert blocks[0].dois == ("10.1021/acs.joc.0c00770",)


def test_bare_doi_requires_opt_in() -> None:
    text = "Bare DOI 10.1038/s41586-020-2649-2 should be opt-in."

    assert scan_text(text) == []
    blocks = scan_text(text, scan_bare_dois=True)
    assert [block.dois for block in blocks] == [("10.1038/s41586-020-2649-2",)]


def test_doi_normalization_preserves_internal_punctuation() -> None:
    assert normalize_doi("DOI:10.1002/(SICI)1097-4571(199609)47:9;2-0") == (
        "10.1002/(sici)1097-4571(199609)47:9;2-0"
    )
    assert normalize_doi("https://doi.org/10.5555/foo(bar);baz).") == "10.5555/foo(bar);baz"
    assert normalize_doi("DOI: 10.1021/acs.chemrev.3c00409]") == "10.1021/acs.chemrev.3c00409"

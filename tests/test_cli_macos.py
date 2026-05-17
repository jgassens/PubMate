from pathlib import Path

from pmid2endnote import cli
from pmid2endnote.app import ProcessingResult


def test_cli_help_includes_parenthetical_flag() -> None:
    help_text = cli.build_parser().format_help()
    assert "--scan-parenthetical-pmids" in help_text
    assert "--scan-dois" in help_text
    assert "--scan-bare-dois" in help_text
    assert "--enw" in help_text
    assert "--no-include-comments" in help_text
    assert "--no-skip-reference-section" in help_text
    assert "--backup" in help_text


def test_cli_passes_parenthetical_flag_to_processing(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_process(options):
        captured["scan_parenthetical_pmids"] = options.scan_parenthetical_pmids
        return ProcessingResult(
            exit_code=0,
            report={},
            output_docx=tmp_path / "out.docx",
            nbib_file=tmp_path / "out.nbib",
            enw_file=tmp_path / "out.enw",
            report_file=tmp_path / "out.json",
            messages=(),
        )

    monkeypatch.setattr(cli, "process_document", fake_process)

    exit_code = cli.main(
        [
            str(tmp_path / "input.docx"),
            "--email",
            "test@example.edu",
            "--scan-parenthetical-pmids",
        ]
    )

    assert exit_code == 0
    assert captured["scan_parenthetical_pmids"] is True


def test_cli_passes_include_comments_default(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_process(options):
        captured["include_comments"] = options.include_comments
        return ProcessingResult(
            exit_code=0,
            report={},
            output_docx=tmp_path / "out.docx",
            nbib_file=tmp_path / "out.nbib",
            enw_file=tmp_path / "out.enw",
            report_file=tmp_path / "out.json",
            messages=(),
        )

    monkeypatch.setattr(cli, "process_document", fake_process)

    cli.main([str(tmp_path / "input.docx"), "--email", "test@example.edu"])
    assert captured["include_comments"] is True

    cli.main([str(tmp_path / "input.docx"), "--email", "test@example.edu", "--no-include-comments"])
    assert captured["include_comments"] is False


def test_cli_passes_skip_reference_section_default_and_override(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_process(options):
        captured["skip_reference_section"] = options.skip_reference_section
        return ProcessingResult(
            exit_code=0,
            report={},
            output_docx=tmp_path / "out.docx",
            nbib_file=tmp_path / "out.nbib",
            enw_file=tmp_path / "out.enw",
            report_file=tmp_path / "out.json",
            messages=(),
        )

    monkeypatch.setattr(cli, "process_document", fake_process)

    cli.main([str(tmp_path / "input.docx"), "--email", "test@example.edu"])
    assert captured["skip_reference_section"] is True

    cli.main(
        [
            str(tmp_path / "input.docx"),
            "--email",
            "test@example.edu",
            "--no-skip-reference-section",
        ]
    )
    assert captured["skip_reference_section"] is False


def test_cli_passes_backup_default_and_flag(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_process(options):
        captured["create_backup"] = options.create_backup
        return ProcessingResult(
            exit_code=0,
            report={},
            output_docx=tmp_path / "out.docx",
            nbib_file=tmp_path / "out.nbib",
            enw_file=tmp_path / "out.enw",
            report_file=tmp_path / "out.json",
            messages=(),
        )

    monkeypatch.setattr(cli, "process_document", fake_process)

    cli.main([str(tmp_path / "input.docx"), "--email", "test@example.edu"])
    assert captured["create_backup"] is False

    cli.main([str(tmp_path / "input.docx"), "--email", "test@example.edu", "--backup"])
    assert captured["create_backup"] is True


def test_macos_launcher_prompts_and_passes_parenthetical_flag() -> None:
    launcher = Path("macos/PMID2EndNote.command").read_text(encoding="utf-8")

    assert "Scan raw parenthetical PMID citations like (6426050) or (6426050, 104929)?" in launcher
    assert 'ARGS+=("--scan-parenthetical-pmids")' in launcher
    assert 'default button "No"' in launcher
    assert "Ignore PMIDs/DOIs after a References/Bibliography heading?" in launcher
    assert 'ARGS+=("--no-skip-reference-section")' in launcher
    assert 'default button "Yes"' in launcher
    assert ".endnote-import.enw" in launcher

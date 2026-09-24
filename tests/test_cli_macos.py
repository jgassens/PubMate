from pathlib import Path

from pmid2endnote import cli
from pmid2endnote import macos_launcher
from pmid2endnote.app import ProcessingResult


def test_frozen_macos_launcher_replaces_non_tty_stdin_for_tk(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(macos_launcher.sys, "platform", "darwin")
    monkeypatch.setattr(macos_launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr(macos_launcher.os, "isatty", lambda fd: False)
    monkeypatch.setattr(macos_launcher.os, "pipe", lambda: (11, 12))
    monkeypatch.setattr(
        macos_launcher.os, "dup2", lambda source, target: calls.append(("dup2", source, target))
    )
    monkeypatch.setattr(
        macos_launcher.os, "close", lambda fd: calls.append(("close", fd))
    )
    monkeypatch.setenv("TK_CONSOLE", "1")

    assert macos_launcher._disable_tk_console_for_frozen_macos() is True
    assert calls == [("dup2", 11, 0), ("close", 11), ("close", 12)]
    assert "TK_CONSOLE" not in macos_launcher.os.environ


def test_frozen_macos_launcher_ignores_pipe_failure(monkeypatch) -> None:
    monkeypatch.setattr(macos_launcher.sys, "platform", "darwin")
    monkeypatch.setattr(macos_launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr(macos_launcher.os, "isatty", lambda _fd: False)
    monkeypatch.setattr(
        macos_launcher.os,
        "pipe",
        lambda: (_ for _ in ()).throw(OSError("pipe unavailable")),
    )

    assert macos_launcher._disable_tk_console_for_frozen_macos() is False


def test_tk_stdin_guard_does_not_change_cli_process(monkeypatch) -> None:
    monkeypatch.setattr(macos_launcher.sys, "platform", "darwin")
    monkeypatch.delattr(macos_launcher.sys, "frozen", raising=False)
    monkeypatch.setattr(
        macos_launcher.os,
        "isatty",
        lambda _fd: (_ for _ in ()).throw(AssertionError("must not inspect CLI stdin")),
    )

    assert macos_launcher._disable_tk_console_for_frozen_macos() is False


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


def test_cli_keeps_auxiliary_output_defaults_enabled(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_process(options):
        captured["write_nbib"] = options.write_nbib
        captured["write_report"] = options.write_report
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

    assert captured == {"write_nbib": True, "write_report": True}

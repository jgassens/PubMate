import builtins
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest

import pmid2endnote
from pmid2endnote import macos_launcher


def test_macos_distribution_scripts_are_present_and_parse() -> None:
    assert Path("macos/build_distribution.sh").exists()
    assert Path("macos/notarize_distribution.sh").exists()
    assert Path("macos/prepare_sparkle_appcast.sh").exists()
    assert Path("macos/pubmate_launcher_entry.py").exists()
    assert Path("macos/make_icon.py").exists()
    assert Path("macos/PubMateSparkleBridge.m").exists()
    assert Path("macos/PubMateSparkleState.c").exists()
    assert Path("macos/PubMateSparkleState.h").exists()
    assert Path("macos/SparkleSupport/Package.swift").exists()
    assert Path("docs/appcast.xml").exists()
    assert Path("docs/macos-distribution.md").exists()

    zsh = shutil.which("zsh")
    if zsh is not None:
        subprocess.run(
            [
                zsh,
                "-n",
                "macos/PMID2EndNote.command",
                "macos/build_distribution.sh",
                "macos/notarize_distribution.sh",
                "macos/prepare_sparkle_appcast.sh",
            ],
            check=True,
        )

    py_compile.compile("macos/pubmate_launcher_entry.py", doraise=True)
    py_compile.compile("macos/make_icon.py", doraise=True)
    py_compile.compile("src/pmid2endnote/macos_launcher.py", doraise=True)


def test_pyproject_has_macos_packaging_extra() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "macos = [" in text
    assert "pyinstaller" in text
    assert "pyobjc-framework-Cocoa" not in text


def test_package_version_matches_pyproject() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert pmid2endnote.__version__ == project["project"]["version"]


def test_notarization_help_exits_successfully(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["TMPPREFIX"] = str(tmp_path / "zsh")
    completed = subprocess.run(
        ["macos/notarize_distribution.sh", "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0
    assert "Submit a PubMate DMG" in completed.stdout


def test_notarization_helper_can_fall_back_to_full_xcode() -> None:
    text = Path("macos/notarize_distribution.sh").read_text(encoding="utf-8")
    assert "xcrun --find notarytool" in text
    assert "/Applications/Xcode.app/Contents/Developer" in text


def test_build_script_checks_developer_id_identity() -> None:
    text = Path("macos/build_distribution.sh").read_text(encoding="utf-8")
    assert "security find-identity -p codesigning -v" in text
    assert "Developer ID signing identity is not installed" in text


def test_build_script_guards_debug_feed_compiler_flag() -> None:
    lines = Path("macos/build_distribution.sh").read_text(encoding="utf-8").splitlines()
    define = "-DPUBMATE_DEBUG_FEED=1"
    define_lines = [index for index, line in enumerate(lines) if define in line]

    assert len(define_lines) == 1

    guard_start = next(
        index
        for index, line in enumerate(lines)
        if line.strip() == 'if [[ "${PUBMATE_DEBUG_FEED:-0}" == "1" ]]; then'
    )
    guard_end = next(
        index
        for index in range(guard_start + 1, len(lines))
        if lines[index].strip() == "fi"
    )
    assert guard_start < define_lines[0] < guard_end


def test_build_script_embeds_sparkle_defaults() -> None:
    text = Path("macos/build_distribution.sh").read_text(encoding="utf-8")
    assert "SUFeedURL" in text
    assert "SUPublicEDKey" in text
    assert 'plist_set_bool "SUEnableAutomaticChecks" "true"' in text
    assert 'plist_set_integer "SUScheduledCheckInterval" "86400"' in text
    assert 'plist_set_bool "SUAllowsAutomaticUpdates" "true"' in text
    assert 'plist_set_bool "SUAutomaticallyUpdate" "true"' in text
    assert 'plist_set_bool "SUPromptUserOnFirstLaunch" "false"' in text


def test_sparkle_appcast_rejects_directory_tool_override(tmp_path: Path) -> None:
    zsh = shutil.which("zsh")
    if zsh is None:
        pytest.skip("zsh is unavailable")
    dmg = tmp_path / "PubMate.dmg"
    dmg.touch()
    directory = tmp_path / "generate_appcast"
    directory.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHON": sys.executable,
            "DMG_PATH": str(dmg),
            "SPARKLE_GENERATE_APPCAST": str(directory),
        }
    )

    completed = subprocess.run(
        [zsh, "macos/prepare_sparkle_appcast.sh"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode != 0
    assert "not an executable file" in completed.stderr


def test_sparkle_appcast_accepts_executable_tool_override(tmp_path: Path) -> None:
    zsh = shutil.which("zsh")
    if zsh is None:
        pytest.skip("zsh is unavailable")
    dmg = tmp_path / "PubMate.dmg"
    dmg.touch()
    tool = tmp_path / "generate_appcast"
    tool.write_text(
        "#!/bin/sh\n"
        "for argument do updates_dir=$argument; done\n"
        'touch "$updates_dir/appcast.xml"\n',
        encoding="utf-8",
    )
    tool.chmod(0o755)
    appcast = tmp_path / "published" / "appcast.xml"
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHON": sys.executable,
            "DMG_PATH": str(dmg),
            "UPDATES_DIR": str(tmp_path / "updates"),
            "APPCAST_OUTPUT": str(appcast),
            "SPARKLE_GENERATE_APPCAST": str(tool),
        }
    )

    completed = subprocess.run(
        [zsh, "macos/prepare_sparkle_appcast.sh"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert appcast.is_file()


def test_macos_launcher_self_test_does_not_open_dialogs(monkeypatch, capsys) -> None:
    monkeypatch.setattr(macos_launcher.shutil, "which", lambda name: "/usr/bin/osascript")

    assert macos_launcher.main(["--self-test"]) == 0
    assert macos_launcher.SELF_TEST_MESSAGE in capsys.readouterr().out


def _patch_lightweight_processing(monkeypatch) -> None:
    monkeypatch.setattr(
        macos_launcher,
        "_processing_options",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        macos_launcher,
        "_endnote_instructions",
        lambda: "Import {enw_file} into {output_docx}; PubMed data: {nbib_file}.",
    )


def _force_tkinter_fallback(monkeypatch) -> None:
    original_import = builtins.__import__

    def import_without_tkinter(name, *args, **kwargs):
        if name == "tkinter":
            raise ModuleNotFoundError("tkinter unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_tkinter)


def test_macos_launcher_uses_dropped_docx_path(monkeypatch, tmp_path: Path) -> None:
    from pmid2endnote import gui

    input_docx = tmp_path / "dropped.docx"
    input_docx.write_bytes(b"fake docx bytes")
    captured = {}

    def fake_gui_run(initial_docx=None):
        captured["path"] = initial_docx
        return 0

    monkeypatch.setattr(gui, "run", fake_gui_run)

    assert macos_launcher.main([str(input_docx)]) == 0
    assert captured["path"] == input_docx


def test_macos_launcher_reprompts_after_blank_email(monkeypatch, tmp_path: Path) -> None:
    input_docx = tmp_path / "dropped.docx"
    input_docx.write_bytes(b"fake docx bytes")
    captured = {}
    prompts = iter(["", "retry@example.edu", ""])
    retry_prompts = []
    yes_no_answers = iter([False, True])

    monkeypatch.setattr(macos_launcher.shutil, "which", lambda name: "/usr/bin/osascript")
    _force_tkinter_fallback(monkeypatch)
    _patch_lightweight_processing(monkeypatch)
    monkeypatch.setattr(macos_launcher, "get_saved_email", lambda: None)
    monkeypatch.setattr(macos_launcher, "_prompt_text", lambda prompt, optional=False: next(prompts))
    monkeypatch.setattr(
        macos_launcher,
        "_prompt_missing_email_retry",
        lambda: retry_prompts.append(True) or True,
    )
    monkeypatch.setattr(
        macos_launcher,
        "_prompt_yes_no",
        lambda prompt, default_yes: next(yes_no_answers),
    )
    monkeypatch.setattr(macos_launcher, "_display_alert", lambda title, message=None: None)

    def fake_process_document(options, *, status_callback=None):
        captured["options"] = options
        return SimpleNamespace(
            exit_code=0,
            report={},
            output_docx=tmp_path / "output.docx",
            nbib_file=tmp_path / "output.nbib",
            enw_file=tmp_path / "output.enw",
            report_file=tmp_path / "report.json",
            messages=("done",),
        )

    monkeypatch.setattr(macos_launcher, "process_document", fake_process_document)

    assert macos_launcher.main([str(input_docx)]) == 0
    assert captured["options"].email == "retry@example.edu"
    assert retry_prompts == [True]


def test_macos_launcher_closes_from_missing_email_prompt(monkeypatch, tmp_path: Path) -> None:
    input_docx = tmp_path / "dropped.docx"
    input_docx.write_bytes(b"fake docx bytes")

    monkeypatch.setattr(macos_launcher.shutil, "which", lambda name: "/usr/bin/osascript")
    _force_tkinter_fallback(monkeypatch)
    monkeypatch.setattr(macos_launcher, "get_saved_email", lambda: None)
    monkeypatch.setattr(macos_launcher, "_prompt_text", lambda prompt, optional=False: "")
    monkeypatch.setattr(macos_launcher, "_prompt_missing_email_retry", lambda: False)
    monkeypatch.setattr(
        macos_launcher,
        "process_document",
        lambda options, *, status_callback=None: (_ for _ in ()).throw(
            AssertionError("processing should not start without email")
        ),
    )

    assert macos_launcher.main([str(input_docx)]) == 0


def test_macos_launcher_rejects_non_docx_launch_arg(monkeypatch, tmp_path: Path) -> None:
    bad_input = tmp_path / "notes.txt"
    bad_input.write_text("not a Word document", encoding="utf-8")
    alerts = []

    monkeypatch.setattr(macos_launcher.shutil, "which", lambda name: "/usr/bin/osascript")
    monkeypatch.setattr(
        macos_launcher,
        "_choose_docx",
        lambda: (_ for _ in ()).throw(AssertionError("file picker should not open")),
    )
    monkeypatch.setattr(
        macos_launcher,
        "_display_alert",
        lambda title, message=None: alerts.append((title, message)),
    )

    assert macos_launcher.main([str(bad_input)]) == 1
    assert alerts == [
        (
            "PubMate could not open that file.",
            f"PubMate only accepts Word .docx files:\n{bad_input}",
        )
    ]


def test_macos_launcher_reports_unexpected_processing_error(monkeypatch, tmp_path: Path) -> None:
    input_docx = tmp_path / "dropped.docx"
    input_docx.write_bytes(b"fake docx bytes")
    alerts = []
    yes_no_answers = iter([False, True])

    monkeypatch.setattr(macos_launcher.shutil, "which", lambda name: "/usr/bin/osascript")
    _force_tkinter_fallback(monkeypatch)
    _patch_lightweight_processing(monkeypatch)
    monkeypatch.setattr(macos_launcher, "get_saved_email", lambda: "test@example.edu")
    monkeypatch.setattr(macos_launcher, "_prompt_text", lambda prompt, optional=False: "")
    monkeypatch.setattr(
        macos_launcher,
        "_prompt_yes_no",
        lambda prompt, default_yes: next(yes_no_answers),
    )
    monkeypatch.setattr(
        macos_launcher,
        "_display_alert",
        lambda title, message=None: alerts.append((title, message)),
    )
    monkeypatch.setattr(
        macos_launcher,
        "process_document",
        lambda options, *, status_callback=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert macos_launcher.main([str(input_docx)]) == 2
    assert alerts == [("PubMate crashed.", "RuntimeError: boom")]

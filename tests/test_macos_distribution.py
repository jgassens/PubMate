from pathlib import Path
import py_compile
import shutil
import subprocess
import tomllib
from types import SimpleNamespace

import pmid2endnote
from pmid2endnote import macos_launcher


def test_macos_distribution_scripts_are_present_and_parse() -> None:
    assert Path("macos/build_distribution.sh").exists()
    assert Path("macos/notarize_distribution.sh").exists()
    assert Path("macos/prepare_sparkle_appcast.sh").exists()
    assert Path("macos/pubmate_launcher_entry.py").exists()
    assert Path("macos/make_icon.py").exists()
    assert Path("macos/PubMateUpdater.m").exists()
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


def test_notarization_help_exits_successfully() -> None:
    completed = subprocess.run(
        ["macos/notarize_distribution.sh", "--help"],
        check=False,
        capture_output=True,
        text=True,
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


def test_build_script_embeds_sparkle_metadata() -> None:
    text = Path("macos/build_distribution.sh").read_text(encoding="utf-8")
    helper_text = Path("macos/PubMateUpdater.m").read_text(encoding="utf-8")
    assert "Sparkle.framework" in text
    assert "--argv-emulation" in text
    assert "--collect-data docx" in text
    assert "Contents/Frameworks/docx/templates" in text
    assert "Contents/Frameworks/docx/parts" in text
    assert "CFBundleDocumentTypes" in text
    assert "org.openxmlformats.wordprocessingml.document" in text
    assert "SUFeedURL" in text
    assert "SUPublicEDKey" in text
    assert "SUEnableAutomaticChecks" in text
    assert 'plist_set_bool "SUAllowsAutomaticUpdates" "true"' in text
    assert 'plist_set_bool "SUAutomaticallyUpdate" "true"' in text
    assert 'plist_set_bool "SUPromptUserOnFirstLaunch" "false"' in text
    assert "--sparkle-self-test" in text
    assert "macos/PubMateUpdater.m" in text
    assert "Contents/MacOS/PubMateUpdater" in text
    assert "xcrun clang" in text
    assert "lipo -create" in text
    assert "PyObjC" not in text
    assert 'TARGET_ARCH="${MACOS_TARGET_ARCH:-universal2}"' in text
    assert "--target-arch" in text
    assert "Universal2 builds require a universal Python runtime" in text
    assert "require_macho_archs" in text
    assert 'lipo -archs "$binary"' in text
    assert "Every bundled Mach-O file must include" in text
    assert "Use a universal Python runtime and universal native dependencies" in text
    assert "updater.automaticallyChecksForUpdates = YES" in helper_text
    assert "updater.automaticallyDownloadsUpdates = YES" in helper_text
    assert "updater.allowsAutomaticUpdates" in helper_text
    assert "[updater checkForUpdatesInBackground]" in helper_text
    assert '@"SUSkippedVersion"' in helper_text
    assert '@"SUSkippedMajorVersion"' in helper_text
    assert '@"SUSkippedMajorSubreleaseVersion"' in helper_text
    assert "[NSUserDefaults standardUserDefaults]" in helper_text
    assert "NSApplicationActivationPolicyProhibited" in helper_text
    assert text.index('require_macho_archs "$APP_STAGE_PATH"') < text.index(
        '"$APP_STAGE_PATH/Contents/MacOS/$APP_NAME" --self-test'
    )
    assert text.index("--sparkle-self-test") < text.index("codesign --verify --deep --strict")


def test_sparkle_appcast_helper_targets_github_releases() -> None:
    text = Path("macos/prepare_sparkle_appcast.sh").read_text(encoding="utf-8")
    assert "jgassens/PubMate" in text
    assert "releases/download" in text
    assert 'DOWNLOAD_URL_PREFIX="$DOWNLOAD_URL_PREFIX/"' in text
    assert 'TARGET_ARCH="${MACOS_TARGET_ARCH:-universal2}"' in text
    assert ".venv-universal/bin/python" in text
    assert 'rm -f "$UPDATES_DIR"/*.dmg(N) "$UPDATES_DIR"/*.delta(N)' in text
    assert "generate_appcast" in text
    assert "docs/appcast.xml" in text


def test_macos_launcher_self_test_does_not_open_dialogs(monkeypatch, capsys) -> None:
    monkeypatch.setattr(macos_launcher.shutil, "which", lambda name: "/usr/bin/osascript")

    assert macos_launcher.main(["--self-test"]) == 0
    assert macos_launcher.SELF_TEST_MESSAGE in capsys.readouterr().out


def test_macos_launcher_sparkle_self_test_does_not_open_dialogs(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        macos_launcher.sparkle,
        "validate_sparkle_runtime",
        lambda: "Sparkle runtime self-test OK",
    )

    assert macos_launcher.main(["--sparkle-self-test"]) == 0
    assert "Sparkle runtime self-test OK" in capsys.readouterr().out


def test_sparkle_launches_detached_native_helper(monkeypatch, tmp_path: Path) -> None:
    helper = tmp_path / "PubMateUpdater"
    helper.touch()
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(pid=123)

    monkeypatch.delenv("PUBMATE_DISABLE_SPARKLE", raising=False)
    monkeypatch.setattr(macos_launcher.sparkle, "_find_updater_helper", lambda: helper)
    monkeypatch.setattr(macos_launcher.sparkle.subprocess, "Popen", fake_popen)

    assert macos_launcher.sparkle.initialize_sparkle_updater() is None
    assert captured["args"] == [str(helper)]
    assert captured["kwargs"]["start_new_session"] is True
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
    assert captured["kwargs"]["stdout"] is subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] is subprocess.DEVNULL


def test_sparkle_self_test_uses_native_helper_without_network(monkeypatch, tmp_path: Path) -> None:
    helper = tmp_path / "PubMateUpdater"
    helper.touch()
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout="PubMate native Sparkle forced-update self-test OK\n",
            stderr="",
        )

    monkeypatch.delenv("PUBMATE_DISABLE_SPARKLE", raising=False)
    monkeypatch.setattr(macos_launcher.sparkle, "_find_updater_helper", lambda: helper)
    monkeypatch.setattr(macos_launcher.sparkle.subprocess, "run", fake_run)

    result = macos_launcher.sparkle.validate_sparkle_runtime()

    assert result == "PubMate native Sparkle forced-update self-test OK"
    assert captured["args"] == [str(helper), "--self-test"]
    assert captured["kwargs"]["timeout"] == 20


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


def test_macos_launcher_uses_dropped_docx_path(monkeypatch, tmp_path: Path) -> None:
    input_docx = tmp_path / "dropped.docx"
    input_docx.write_bytes(b"fake docx bytes")
    captured = {}
    alerts = []
    yes_no_answers = iter([False, True])

    monkeypatch.setattr(macos_launcher.shutil, "which", lambda name: "/usr/bin/osascript")
    monkeypatch.setattr(macos_launcher.sparkle, "initialize_sparkle_updater", lambda: None)
    _patch_lightweight_processing(monkeypatch)
    monkeypatch.setattr(
        macos_launcher,
        "_choose_docx",
        lambda: (_ for _ in ()).throw(AssertionError("file picker should not open")),
    )
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

    def fake_process_document(options, *, status_callback=None):
        captured["options"] = options
        if status_callback:
            status_callback("Working")
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
    assert captured["options"].input_docx == input_docx
    assert captured["options"].email == "test@example.edu"
    assert captured["options"].scan_parenthetical_pmids is False
    assert captured["options"].skip_reference_section is True
    assert alerts[0][0] == "PubMate finished."


def test_macos_launcher_reprompts_after_blank_email(monkeypatch, tmp_path: Path) -> None:
    input_docx = tmp_path / "dropped.docx"
    input_docx.write_bytes(b"fake docx bytes")
    captured = {}
    prompts = iter(["", "retry@example.edu", ""])
    retry_prompts = []
    yes_no_answers = iter([False, True])

    monkeypatch.setattr(macos_launcher.shutil, "which", lambda name: "/usr/bin/osascript")
    monkeypatch.setattr(macos_launcher.sparkle, "initialize_sparkle_updater", lambda: None)
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
    monkeypatch.setattr(macos_launcher.sparkle, "initialize_sparkle_updater", lambda: None)
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
    monkeypatch.setattr(macos_launcher.sparkle, "initialize_sparkle_updater", lambda: None)
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
    monkeypatch.setattr(macos_launcher.sparkle, "initialize_sparkle_updater", lambda: None)
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

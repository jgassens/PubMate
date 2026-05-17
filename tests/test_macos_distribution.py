from pathlib import Path
import py_compile
import shutil
import subprocess

from pmid2endnote import macos_launcher


def test_macos_distribution_scripts_are_present_and_parse() -> None:
    assert Path("macos/build_distribution.sh").exists()
    assert Path("macos/notarize_distribution.sh").exists()
    assert Path("macos/prepare_sparkle_appcast.sh").exists()
    assert Path("macos/pubmate_launcher_entry.py").exists()
    assert Path("macos/make_icon.py").exists()
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
    assert "pyobjc-framework-Cocoa" in text


def test_notarization_help_exits_successfully() -> None:
    completed = subprocess.run(
        ["macos/notarize_distribution.sh", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "Submit a PubMate DMG" in completed.stdout


def test_build_script_checks_developer_id_identity() -> None:
    text = Path("macos/build_distribution.sh").read_text(encoding="utf-8")
    assert "security find-identity -p codesigning -v" in text
    assert "Developer ID signing identity is not installed" in text


def test_build_script_embeds_sparkle_metadata() -> None:
    text = Path("macos/build_distribution.sh").read_text(encoding="utf-8")
    assert "Sparkle.framework" in text
    assert "SUFeedURL" in text
    assert "SUPublicEDKey" in text
    assert "SUEnableAutomaticChecks" in text
    assert "--sparkle-self-test" in text


def test_sparkle_appcast_helper_targets_github_releases() -> None:
    text = Path("macos/prepare_sparkle_appcast.sh").read_text(encoding="utf-8")
    assert "jgassens/PubMate" in text
    assert "releases/download" in text
    assert 'DOWNLOAD_URL_PREFIX="$DOWNLOAD_URL_PREFIX/"' in text
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

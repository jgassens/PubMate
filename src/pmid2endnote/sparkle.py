"""Launch PubMate's native Sparkle updater helper from the macOS app."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys


DEFAULT_FEED_URL = "https://jgassens.github.io/PubMate/appcast.xml"
DEFAULT_PUBLIC_ED_KEY = "HK2FMFt1/JlsEm52nLZ7X4cXo1nmLLJpAoRzB3y7tYQ="
UPDATER_HELPER_NAME = "PubMateUpdater"

_updater_process: subprocess.Popen[bytes] | None = None


def initialize_sparkle_updater() -> str | None:
    """Start the detached native updater, returning a warning if unavailable."""

    if os.environ.get("PUBMATE_DISABLE_SPARKLE") == "1":
        return None

    helper = _find_updater_helper()
    if helper is None:
        return "Sparkle auto-update is unavailable: PubMateUpdater was not found."

    try:
        process = subprocess.Popen(
            [str(helper)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        global _updater_process
        _updater_process = process
    except OSError as exc:  # pragma: no cover - depends on packaged macOS app
        return f"Sparkle auto-update is unavailable: {exc}"

    return None


def validate_sparkle_runtime() -> str:
    """Validate the packaged native updater without starting a network check."""

    if os.environ.get("PUBMATE_DISABLE_SPARKLE") == "1":
        return "Sparkle runtime self-test skipped because PUBMATE_DISABLE_SPARKLE=1."

    helper = _find_updater_helper()
    if helper is None:
        raise RuntimeError("PubMateUpdater was not found in the app bundle")

    completed = subprocess.run(
        [str(helper), "--self-test"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    output = completed.stdout.strip()
    if completed.returncode != 0:
        detail = completed.stderr.strip() or output or f"exit code {completed.returncode}"
        raise RuntimeError(f"PubMateUpdater self-test failed: {detail}")
    return output or "PubMate native Sparkle forced-update self-test OK"


def _find_updater_helper() -> Path | None:
    override = os.environ.get("PUBMATE_UPDATER_HELPER")
    if override:
        path = Path(override)
        return path if path.exists() else None

    candidates: list[Path] = []
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        candidates.append(executable.parent / UPDATER_HELPER_NAME)

    module_path = Path(__file__).resolve()
    candidates.extend(
        [
            module_path.parents[2]
            / "dist"
            / "PubMate.app"
            / "Contents"
            / "MacOS"
            / UPDATER_HELPER_NAME,
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None

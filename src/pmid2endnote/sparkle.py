"""Launch PubMate's native Sparkle updater helper from the macOS app."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
import threading


DEFAULT_FEED_URL = "https://jgassens.github.io/PubMate/appcast.xml"
DEFAULT_PUBLIC_ED_KEY = "HK2FMFt1/JlsEm52nLZ7X4cXo1nmLLJpAoRzB3y7tYQ="
UPDATER_HELPER_NAME = "PubMateUpdater"

_updater_processes: list[subprocess.Popen[bytes]] = []
_updater_processes_lock = threading.Lock()


def updater_process_is_running() -> bool:
    """Return whether a native updater launched by this process is still alive."""

    with _updater_processes_lock:
        processes = list(_updater_processes)

    finished = [process for process in processes if process.poll() is not None]
    if finished:
        with _updater_processes_lock:
            for process in finished:
                if process in _updater_processes:
                    _updater_processes.remove(process)

    with _updater_processes_lock:
        return bool(_updater_processes)


def _wait_for_updater_process(process: subprocess.Popen[bytes]) -> None:
    """Wait for and promptly remove one helper process from live tracking."""

    wait = getattr(process, "wait", None)
    if wait is None:  # Accommodate small Popen test doubles.
        return
    try:
        wait()
    except OSError:
        # The process may already have been reaped through another poll/wait.
        pass
    finally:
        with _updater_processes_lock:
            if process in _updater_processes:
                _updater_processes.remove(process)


def _remember_updater_process(process: subprocess.Popen[bytes]) -> None:
    with _updater_processes_lock:
        _updater_processes.append(process)
    threading.Thread(
        target=_wait_for_updater_process,
        args=(process,),
        daemon=True,
        name="PubMateUpdaterReaper",
    ).start()


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
        _remember_updater_process(process)
    except OSError as exc:  # pragma: no cover - depends on packaged macOS app
        return f"Sparkle auto-update is unavailable: {exc}"

    return None


def check_for_updates_now() -> str | None:
    """Start a detached user-initiated Sparkle check."""

    if os.environ.get("PUBMATE_DISABLE_SPARKLE") == "1":
        return "Update checks are disabled because PUBMATE_DISABLE_SPARKLE=1."

    helper = _find_updater_helper()
    if helper is None:
        return (
            "Check for Updates is available only in the packaged PubMate macOS app; "
            "PubMateUpdater was not found."
        )

    try:
        process = subprocess.Popen(
            [str(helper), "--check-now"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        _remember_updater_process(process)
    except OSError as exc:  # pragma: no cover - platform-dependent details
        return f"PubMate could not start Check for Updates: {exc}"

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

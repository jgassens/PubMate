from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from pmid2endnote import sparkle


@pytest.fixture(autouse=True)
def clear_tracked_updaters() -> None:
    sparkle._updater_processes.clear()
    yield
    sparkle._updater_processes.clear()


def test_check_for_updates_now_reports_missing_helper(monkeypatch) -> None:
    monkeypatch.delenv("PUBMATE_DISABLE_SPARKLE", raising=False)
    monkeypatch.setattr(sparkle, "_find_updater_helper", lambda: None)

    error = sparkle.check_for_updates_now()

    assert error is not None
    assert "packaged PubMate macOS app" in error
    assert "PubMateUpdater was not found" in error


def test_check_for_updates_now_respects_disabled_environment(monkeypatch) -> None:
    monkeypatch.setenv("PUBMATE_DISABLE_SPARKLE", "1")

    error = sparkle.check_for_updates_now()

    assert error is not None
    assert "disabled" in error
    assert "PUBMATE_DISABLE_SPARKLE=1" in error


def test_check_for_updates_now_reports_popen_error(monkeypatch, tmp_path: Path) -> None:
    helper = tmp_path / "PubMateUpdater"
    helper.touch()
    monkeypatch.delenv("PUBMATE_DISABLE_SPARKLE", raising=False)
    monkeypatch.setattr(sparkle, "_find_updater_helper", lambda: helper)
    monkeypatch.setattr(
        sparkle.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("cannot execute")),
    )

    assert sparkle.check_for_updates_now() == (
        "PubMate could not start Check for Updates: cannot execute"
    )


def test_check_for_updates_now_launches_detached_helper(
    monkeypatch, tmp_path: Path
) -> None:
    helper = tmp_path / "PubMateUpdater"
    helper.touch()
    captured: dict[str, object] = {}
    process = SimpleNamespace(poll=lambda: None)

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return process

    monkeypatch.delenv("PUBMATE_DISABLE_SPARKLE", raising=False)
    monkeypatch.setattr(sparkle, "_find_updater_helper", lambda: helper)
    monkeypatch.setattr(sparkle.subprocess, "Popen", fake_popen)

    assert sparkle.check_for_updates_now() is None
    assert captured["args"] == [str(helper), "--check-now"]
    assert captured["kwargs"] == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "start_new_session": True,
        "close_fds": True,
    }
    assert sparkle.updater_process_is_running() is True


def test_finished_helper_is_waited_for_and_removed_promptly(monkeypatch) -> None:
    waited: list[bool] = []

    class Process:
        def wait(self) -> int:
            waited.append(True)
            return 0

        def poll(self) -> int | None:
            return 0 if waited else None

    class ImmediateThread:
        def __init__(self, *, target, args, **_kwargs) -> None:
            self.target = target
            self.args = args

        def start(self) -> None:
            self.target(*self.args)

    process = Process()
    monkeypatch.setattr(
        sparkle, "threading", SimpleNamespace(Thread=ImmediateThread)
    )

    sparkle._remember_updater_process(process)

    assert waited == [True]
    assert sparkle.updater_process_is_running() is False


def test_sparkle_tracks_helpers_without_dead_single_process_global() -> None:
    assert not hasattr(sparkle, "_updater_process")

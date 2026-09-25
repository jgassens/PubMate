import ctypes
import logging
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from pmid2endnote import sparkle


class FakeFunction:
    def __init__(self, result=None) -> None:
        self.result = result
        self.calls: list[tuple[object, ...]] = []
        self.argtypes = None
        self.restype = object()

    def __call__(self, *args: object):
        self.calls.append(args)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeLibrary:
    def __init__(self, *, start_result: int = 0, can_check: int = 1) -> None:
        self.pm_sparkle_start = FakeFunction(start_result)
        self.pm_sparkle_check_for_updates = FakeFunction()
        self.pm_sparkle_can_check = FakeFunction(can_check)
        self.pm_sparkle_set_busy = FakeFunction()


@pytest.fixture(autouse=True)
def reset_sparkle_wrapper(monkeypatch) -> None:
    monkeypatch.setattr(sparkle, "_library", None)
    monkeypatch.setattr(sparkle, "_load_attempted", False)
    monkeypatch.setattr(sparkle, "_start_attempted", False)
    monkeypatch.setattr(sparkle, "_started", False)


def make_bridge_available(monkeypatch, tmp_path: Path, library: FakeLibrary) -> Path:
    bridge = tmp_path / "libPubMateSparkle.dylib"
    bridge.touch()
    monkeypatch.setattr(sparkle.sys, "platform", "darwin")
    monkeypatch.setattr(sparkle.sys, "frozen", True, raising=False)
    monkeypatch.setattr(sparkle, "_bridge_path", lambda: bridge)
    monkeypatch.setattr(sparkle, "_library_loader", lambda _path: library)
    return bridge


@pytest.mark.parametrize(
    ("platform", "frozen"),
    [("linux", True), ("darwin", False)],
)
def test_available_is_false_outside_frozen_macos(
    monkeypatch, platform: str, frozen: bool
) -> None:
    monkeypatch.setattr(sparkle.sys, "platform", platform)
    monkeypatch.setattr(sparkle.sys, "frozen", frozen, raising=False)

    assert sparkle.available() is False


def test_available_is_false_when_bridge_is_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sparkle.sys, "platform", "darwin")
    monkeypatch.setattr(sparkle.sys, "frozen", True, raising=False)
    monkeypatch.setattr(sparkle, "_bridge_path", lambda: tmp_path / "missing.dylib")

    assert sparkle.available() is False


def test_bridge_path_is_relative_to_frozen_executable(monkeypatch) -> None:
    executable = Path("/Applications/PubMate.app/Contents/MacOS/PubMate")
    monkeypatch.setattr(sparkle.sys, "executable", str(executable))

    assert sparkle._bridge_path() == (
        executable.parent.parent / "Frameworks" / "libPubMateSparkle.dylib"
    )


def test_start_loads_and_starts_bridge_once(monkeypatch, tmp_path: Path) -> None:
    library = FakeLibrary()
    bridge = make_bridge_available(monkeypatch, tmp_path, library)
    loaded_paths: list[str] = []
    monkeypatch.setattr(
        sparkle,
        "_library_loader",
        lambda path: loaded_paths.append(path) or library,
    )

    assert sparkle.start() is True
    assert sparkle.start() is True

    assert loaded_paths == [str(bridge)]
    assert library.pm_sparkle_start.calls == [()]
    assert library.pm_sparkle_start.argtypes == []
    assert library.pm_sparkle_start.restype is ctypes.c_int
    assert library.pm_sparkle_set_busy.argtypes == [ctypes.c_int]


def test_check_now_and_busy_state_call_native_bridge(monkeypatch, tmp_path: Path) -> None:
    library = FakeLibrary()
    make_bridge_available(monkeypatch, tmp_path, library)

    assert sparkle.start() is True
    assert sparkle.check_now() is True
    assert sparkle.set_busy(True) is True
    assert sparkle.set_busy(False) is True

    assert library.pm_sparkle_can_check.calls == [()]
    assert library.pm_sparkle_check_for_updates.calls == [()]
    assert library.pm_sparkle_set_busy.calls == [(1,), (0,)]


def test_check_now_does_not_check_while_updater_cannot_check(
    monkeypatch, tmp_path: Path
) -> None:
    library = FakeLibrary(can_check=0)
    make_bridge_available(monkeypatch, tmp_path, library)

    assert sparkle.start() is True
    assert sparkle.check_now() is False
    assert library.pm_sparkle_check_for_updates.calls == []


def test_load_failure_is_logged_and_all_operations_are_noops(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    library = FakeLibrary()
    make_bridge_available(monkeypatch, tmp_path, library)
    monkeypatch.setattr(
        sparkle,
        "_library_loader",
        lambda _path: (_ for _ in ()).throw(OSError("bad image")),
    )

    with caplog.at_level(logging.WARNING):
        assert sparkle.start() is False
        assert sparkle.check_now() is False
        assert sparkle.set_busy(True) is False

    assert "bad image" in caplog.text


@pytest.mark.skipif(sys.platform != "darwin", reason="requires the macOS clang runtime")
def test_busy_state_machine_compiles_and_runs_without_sparkle(
    tmp_path: Path,
) -> None:
    clang = shutil.which("clang")
    if clang is None:
        pytest.skip("clang is unavailable")

    harness = tmp_path / "busy_state_test.c"
    executable = tmp_path / "busy_state_test"
    harness.write_text(
        r'''
#include <assert.h>
#include "PubMateSparkleState.h"

static int invoked = 0;
static int disposed = 0;
static int logged = 0;

static void invoke(void *context) {
    invoked += *(int *)context;
}

static void dispose(void *context) {
    disposed += *(int *)context;
}

static void log_message(const char *message) {
    assert(message != 0);
    logged += 1;
}

int main(void) {
    PMSparkleBusyState state;
    int first = 1;
    int second = 2;
    int third = 3;
    pm_sparkle_busy_state_init(&state);
    pm_sparkle_busy_state_set_log(&state, log_message);
    pm_sparkle_busy_state_set(&state, 1);
    assert(pm_sparkle_busy_state_postpone(&state, &first, invoke, dispose) == 1);
    assert(pm_sparkle_busy_state_postpone(&state, &second, invoke, dispose) == 1);
    assert(disposed == 1);
    assert(logged == 1);
    pm_sparkle_busy_state_set(&state, 1);
    assert(invoked == 0);
    pm_sparkle_busy_state_set(&state, 0);
    assert(invoked == 2);
    pm_sparkle_busy_state_set(&state, 0);
    assert(invoked == 2);
    assert(pm_sparkle_busy_state_postpone(&state, &third, invoke, dispose) == 0);
    assert(disposed == 4);
    return 0;
}
''',
        encoding="utf-8",
    )
    subprocess.run(
        [
            clang,
            "-std=c11",
            "-I",
            "macos",
            str(harness),
            "macos/PubMateSparkleState.c",
            "-o",
            str(executable),
        ],
        check=True,
    )
    subprocess.run([str(executable)], check=True)


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS frameworks")
def test_full_bridge_release_and_debug_builds(tmp_path: Path) -> None:
    clang = shutil.which("clang")
    framework = (
        Path(__file__).resolve().parents[1]
        / "macos/SparkleSupport/.build/artifacts/sparkle/Sparkle/"
        "Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework"
    )
    if not framework.is_dir():
        framework = Path(
            "/Users/jeremiahgassensmith/programming/PMIDScan/pmid2endnote/"
            "macos/SparkleSupport/.build/artifacts/sparkle/Sparkle/"
            "Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework"
        )
    otool = shutil.which("otool")
    strings = shutil.which("strings")
    if clang is None or otool is None or strings is None or not framework.is_dir():
        pytest.skip("clang, otool, strings, or Sparkle.framework is unavailable")

    state_object = tmp_path / "state.o"
    subprocess.run(
        [
            clang,
            "-c",
            "-std=c11",
            "-mmacosx-version-min=13.0",
            "-I",
            "macos",
            "macos/PubMateSparkleState.c",
            "-o",
            str(state_object),
        ],
        check=True,
    )

    binaries: dict[str, Path] = {}
    for name, debug_flags in (
        ("release", []),
        ("debug", ["-DPUBMATE_DEBUG_FEED=1"]),
    ):
        bridge_object = tmp_path / f"bridge-{name}.o"
        binary = tmp_path / f"libPubMateSparkle-{name}.dylib"
        subprocess.run(
            [
                clang,
                "-c",
                "-fobjc-arc",
                "-fblocks",
                "-mmacosx-version-min=13.0",
                *debug_flags,
                "-F",
                str(framework.parent),
                "-I",
                "macos",
                "macos/PubMateSparkleBridge.m",
                "-o",
                str(bridge_object),
            ],
            check=True,
        )
        subprocess.run(
            [
                clang,
                "-dynamiclib",
                "-mmacosx-version-min=13.0",
                "-F",
                str(framework.parent),
                "-framework",
                "AppKit",
                "-framework",
                "Sparkle",
                "-Wl,-rpath,@loader_path",
                str(bridge_object),
                str(state_object),
                "-o",
                str(binary),
            ],
            check=True,
        )
        binaries[name] = binary

    release_strings = subprocess.run(
        [strings, str(binaries["release"])],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    debug_strings = subprocess.run(
        [strings, str(binaries["debug"])],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "PUBMATE_SPARKLE_FEED_URL" not in release_strings
    assert "PUBMATE_SPARKLE_FEED_URL" in debug_strings

    for binary in binaries.values():
        load_commands = subprocess.run(
            [otool, "-l", str(binary)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "path @loader_path " in load_commands

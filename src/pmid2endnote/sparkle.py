"""Thin ctypes wrapper around PubMate's in-process Sparkle bridge."""

from __future__ import annotations

import ctypes
import logging
from pathlib import Path
import sys
from typing import Any


_LOGGER = logging.getLogger(__name__)
_BRIDGE_NAME = "libPubMateSparkle.dylib"

_library: Any | None = None
_load_attempted = False
_start_attempted = False
_started = False


def _bridge_path() -> Path:
    """Return the bridge path inside the frozen app bundle."""

    executable = Path(sys.executable)
    return executable.parent.parent / "Frameworks" / _BRIDGE_NAME


def _library_loader(path: str) -> Any:
    return ctypes.CDLL(path)


def available() -> bool:
    """Return whether this process can use the bundled Sparkle bridge."""

    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return False
    try:
        return _bridge_path().is_file()
    except (OSError, AttributeError) as exc:
        _LOGGER.warning("Could not inspect the PubMate Sparkle bridge: %s", exc)
        return False


def _load_library() -> Any | None:
    global _library, _load_attempted

    if _load_attempted:
        return _library
    _load_attempted = True

    if not available():
        return None

    try:
        library = _library_loader(str(_bridge_path()))
        library.pm_sparkle_start.argtypes = []
        library.pm_sparkle_start.restype = ctypes.c_int
        library.pm_sparkle_check_for_updates.argtypes = []
        library.pm_sparkle_check_for_updates.restype = None
        library.pm_sparkle_can_check.argtypes = []
        library.pm_sparkle_can_check.restype = ctypes.c_int
        library.pm_sparkle_set_busy.argtypes = [ctypes.c_int]
        library.pm_sparkle_set_busy.restype = None
    except (OSError, AttributeError) as exc:
        _LOGGER.warning("Could not load the PubMate Sparkle bridge: %s", exc)
        return None

    _library = library
    return library


def start() -> bool:
    """Start the process-lifetime Sparkle updater once."""

    global _start_attempted, _started

    if _start_attempted:
        return _started
    _start_attempted = True

    library = _load_library()
    if library is None:
        return False
    try:
        _started = library.pm_sparkle_start() == 0
    except (OSError, AttributeError) as exc:
        _LOGGER.warning("Could not start the PubMate Sparkle updater: %s", exc)
        _started = False
    return _started


def check_now() -> bool:
    """Ask the running updater to perform a user-initiated update check."""

    library = _load_library()
    if library is None:
        return False
    try:
        if not library.pm_sparkle_can_check():
            return False
        library.pm_sparkle_check_for_updates()
    except (OSError, AttributeError) as exc:
        _LOGGER.warning("Could not check for PubMate updates: %s", exc)
        return False
    return True


def set_busy(busy: bool) -> bool:
    """Tell Sparkle whether a document conversion is in progress."""

    library = _load_library()
    if library is None:
        return False
    try:
        library.pm_sparkle_set_busy(1 if busy else 0)
    except (OSError, AttributeError) as exc:
        _LOGGER.warning("Could not update PubMate's Sparkle busy state: %s", exc)
        return False
    return True

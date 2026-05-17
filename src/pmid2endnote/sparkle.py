"""Sparkle auto-update bridge for the packaged macOS app.

The normal CLI stays independent of Sparkle. This module is imported by the
macOS launcher only, and every dependency is loaded lazily so source checkouts
and tests can run without PyObjC or Sparkle.framework installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import os
import sys


DEFAULT_FEED_URL = "https://jgassens.github.io/PubMate/appcast.xml"
DEFAULT_PUBLIC_ED_KEY = "HK2FMFt1/JlsEm52nLZ7X4cXo1nmLLJpAoRzB3y7tYQ="

_framework_loaded = False
_updater_controller: Any | None = None


def initialize_sparkle_updater() -> str | None:
    """Start Sparkle's standard updater, returning a warning if unavailable."""

    if os.environ.get("PUBMATE_DISABLE_SPARKLE") == "1":
        return None

    try:
        _load_sparkle_framework()
        import objc  # type: ignore[import-not-found]

        controller_class = objc.lookUpClass("SPUStandardUpdaterController")
        controller = (
            controller_class.alloc()
            .initWithStartingUpdater_updaterDelegate_userDriverDelegate_(
                True,
                None,
                None,
            )
        )

        global _updater_controller
        _updater_controller = controller
    except Exception as exc:  # pragma: no cover - depends on packaged macOS app
        return f"Sparkle auto-update is unavailable: {exc}"

    return None


def validate_sparkle_runtime() -> str:
    """Validate that the packaged app can load Sparkle.framework."""

    _load_sparkle_framework()

    import objc  # type: ignore[import-not-found]

    controller_class = objc.lookUpClass("SPUStandardUpdaterController")
    controller = (
        controller_class.alloc()
        .initWithStartingUpdater_updaterDelegate_userDriverDelegate_(
            False,
            None,
            None,
        )
    )
    if controller is None:
        raise RuntimeError("SPUStandardUpdaterController could not be created")
    return "Sparkle runtime self-test OK"


def _load_sparkle_framework() -> None:
    global _framework_loaded

    if _framework_loaded:
        return

    framework_path = _find_sparkle_framework()
    if framework_path is None:
        raise RuntimeError("Sparkle.framework was not found in the app bundle")

    from Foundation import NSBundle  # type: ignore[import-not-found]

    bundle = NSBundle.bundleWithPath_(str(framework_path))
    if bundle is None:
        raise RuntimeError(f"Could not create bundle for {framework_path}")
    if not bundle.load():
        raise RuntimeError(f"Could not load {framework_path}")

    _framework_loaded = True


def _find_sparkle_framework() -> Path | None:
    override = os.environ.get("PUBMATE_SPARKLE_FRAMEWORK")
    if override:
        path = Path(override)
        return path if path.exists() else None

    candidates: list[Path] = []
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        candidates.append(executable.parent.parent / "Frameworks" / "Sparkle.framework")

    module_path = Path(__file__).resolve()
    candidates.extend(
        [
            module_path.parents[2] / "dist" / "PubMate.app" / "Contents" / "Frameworks" / "Sparkle.framework",
            module_path.parents[2] / "macos" / "vendor" / "Sparkle.framework",
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None

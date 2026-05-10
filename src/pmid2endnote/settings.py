"""Persistent user settings for PMID2EndNote."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any


APP_NAME = "PMID2EndNote"
EMAIL_ENV_VAR = "PMID2ENDNOTE_EMAIL"


def settings_path() -> Path:
    """Return the platform-appropriate settings path."""

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME / "settings.json"
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / APP_NAME / "settings.json"

    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config) if xdg_config else Path.home() / ".config"
    return base / "pmid2endnote" / "settings.json"


def load_settings(path: Path | None = None) -> dict[str, Any]:
    """Load settings. Invalid settings files are treated as empty."""

    path = path or settings_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_settings(settings: dict[str, Any], path: Path | None = None) -> None:
    """Write settings as JSON."""

    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def get_saved_email(path: Path | None = None) -> str | None:
    """Return the saved PubMed email address, if one exists."""

    value = load_settings(path).get("email")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def save_email(email: str, path: Path | None = None) -> None:
    """Persist the PubMed email address."""

    email = email.strip()
    if not email:
        return
    settings = load_settings(path)
    settings["email"] = email
    write_settings(settings, path)


def resolve_email(provided_email: str | None, path: Path | None = None) -> str | None:
    """Resolve PubMed email from explicit input, env var, then saved settings."""

    if provided_email and provided_email.strip():
        return provided_email.strip()

    env_email = os.environ.get(EMAIL_ENV_VAR)
    if env_email and env_email.strip():
        return env_email.strip()

    return get_saved_email(path)

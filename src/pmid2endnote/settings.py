"""Persistent user settings for PMID2EndNote."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

from pmid2endnote.conversions import DEFAULT_RETENTION_DAYS, RETENTION_CHOICES


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


def get_retention_days(path: Path | None = None) -> int:
    """Return the selected conversion retention period."""

    value = load_settings(path).get("retention_days", DEFAULT_RETENTION_DAYS)
    return _coerce_retention_days(value)


def save_retention_days(days: object, path: Path | None = None) -> None:
    """Persist a supported conversion retention period."""

    _save_setting("retention_days", _coerce_retention_days(days), path)


def get_write_nbib(path: Path | None = None) -> bool:
    """Return whether the GUI should write the auxiliary NBIB file."""

    return _get_bool("write_nbib", False, path)


def save_write_nbib(enabled: bool, path: Path | None = None) -> None:
    """Persist the auxiliary NBIB preference."""

    _save_setting("write_nbib", bool(enabled), path)


def get_write_report(path: Path | None = None) -> bool:
    """Return whether the GUI should write the JSON report."""

    return _get_bool("write_report", False, path)


def save_write_report(enabled: bool, path: Path | None = None) -> None:
    """Persist the JSON report preference."""

    _save_setting("write_report", bool(enabled), path)


def get_api_key(path: Path | None = None) -> str | None:
    """Return the saved NCBI API key, if one exists."""

    value = load_settings(path).get("api_key")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def save_api_key(api_key: str | None, path: Path | None = None) -> None:
    """Persist an NCBI API key; a blank value clears it."""

    settings = load_settings(path)
    value = api_key.strip() if isinstance(api_key, str) else ""
    if value:
        settings["api_key"] = value
    else:
        settings.pop("api_key", None)
    write_settings(settings, path)


def get_scan_parenthetical_pmids(path: Path | None = None) -> bool:
    """Return whether the GUI scans raw parenthetical PMIDs."""

    return _get_bool("scan_parenthetical_pmids", False, path)


def save_scan_parenthetical_pmids(enabled: bool, path: Path | None = None) -> None:
    """Persist the parenthetical PMID scanning preference."""

    _save_setting("scan_parenthetical_pmids", bool(enabled), path)


def get_skip_reference_section(path: Path | None = None) -> bool:
    """Return whether identifiers after a References heading are skipped."""

    return _get_bool("skip_reference_section", True, path)


def save_skip_reference_section(enabled: bool, path: Path | None = None) -> None:
    """Persist the reference-section skip preference."""

    _save_setting("skip_reference_section", bool(enabled), path)


def save_gui_settings(values: dict[str, Any], path: Path | None = None) -> None:
    """Persist all GUI preferences with their normal types in one write."""

    settings = load_settings(path)
    if "email" in values:
        email = str(values["email"]).strip()
        if email:
            settings["email"] = email
    if "api_key" in values:
        api_key = str(values["api_key"] or "").strip()
        if api_key:
            settings["api_key"] = api_key
        else:
            settings.pop("api_key", None)
    if "retention_days" in values:
        settings["retention_days"] = _coerce_retention_days(values["retention_days"])
    for key in (
        "write_nbib",
        "write_report",
        "scan_parenthetical_pmids",
        "skip_reference_section",
    ):
        if key in values:
            settings[key] = bool(values[key])
    write_settings(settings, path)


def resolve_email(provided_email: str | None, path: Path | None = None) -> str | None:
    """Resolve PubMed email from explicit input, env var, then saved settings."""

    if provided_email and provided_email.strip():
        return provided_email.strip()

    env_email = os.environ.get(EMAIL_ENV_VAR)
    if env_email and env_email.strip():
        return env_email.strip()

    return get_saved_email(path)


def _coerce_retention_days(value: object) -> int:
    try:
        days = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_RETENTION_DAYS
    return days if days in RETENTION_CHOICES else DEFAULT_RETENTION_DAYS


def _get_bool(key: str, default: bool, path: Path | None) -> bool:
    value = load_settings(path).get(key, default)
    return value if isinstance(value, bool) else default


def _save_setting(key: str, value: Any, path: Path | None) -> None:
    settings = load_settings(path)
    settings[key] = value
    write_settings(settings, path)

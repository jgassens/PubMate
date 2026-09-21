"""Storage and retention helpers for desktop conversion runs."""

from __future__ import annotations

from datetime import datetime, timedelta
import os
from pathlib import Path
import shutil
import sys

from pmid2endnote.word import (
    default_enw_path,
    default_nbib_path,
    default_output_path,
    default_report_path,
)


RETENTION_CHOICES = (7, 14, 30, 90)
DEFAULT_RETENTION_DAYS = 30
CREATED_MARKER = ".pubmate-created"


def conversions_root() -> Path:
    """Return the platform-appropriate folder for PubMate conversions."""

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PubMate" / "Conversions"
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "PubMate" / "Conversions"

    xdg_data = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
    return base / "pubmate" / "conversions"


def create_conversion_folder(
    input_docx: Path,
    root: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Create a uniquely named folder for one conversion run."""

    root = root or conversions_root()
    timestamp = now or datetime.now().astimezone()
    root.mkdir(parents=True, exist_ok=True)
    base_name = f"{timestamp:%Y-%m-%d %H.%M.%S} {input_docx.stem}"
    candidate = root / base_name
    suffix = 2
    while True:
        try:
            candidate.mkdir()
            break
        except FileExistsError:
            candidate = root / f"{base_name} ({suffix})"
            suffix += 1

    (candidate / CREATED_MARKER).write_text(timestamp.isoformat() + "\n", encoding="utf-8")
    return candidate


def conversion_output_paths(input_docx: Path, folder: Path) -> dict[str, Path]:
    """Return the standard output filenames located inside ``folder``."""

    return {
        "output_docx": folder / default_output_path(input_docx).name,
        "enw_file": folder / default_enw_path(input_docx).name,
        "nbib_file": folder / default_nbib_path(input_docx).name,
        "report_file": folder / default_report_path(input_docx).name,
    }


def clean_old_conversions(
    root: Path | None = None,
    max_age_days: int = DEFAULT_RETENTION_DAYS,
    now: datetime | None = None,
) -> list[Path]:
    """Delete old immediate child folders and return the paths removed."""

    root = root or conversions_root()
    reference_time = now or datetime.now().astimezone()
    cutoff = reference_time.timestamp() - timedelta(days=max_age_days).total_seconds()
    deleted: list[Path] = []

    try:
        if root.is_symlink():
            return deleted
        children = list(root.iterdir())
    except (FileNotFoundError, NotADirectoryError, OSError):
        return deleted

    for child in children:
        try:
            if child.is_symlink() or not child.is_dir():
                continue
            created_at = _folder_created_timestamp(child)
            if created_at >= cutoff:
                continue
            shutil.rmtree(child)
            deleted.append(child)
        except OSError:
            continue

    return deleted


def _folder_created_timestamp(folder: Path) -> float:
    marker = folder / CREATED_MARKER
    try:
        marker_value = marker.read_text(encoding="utf-8").strip()
        return datetime.fromisoformat(marker_value).timestamp()
    except (OSError, ValueError):
        return folder.stat().st_mtime

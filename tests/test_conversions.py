from datetime import datetime, timedelta
import os
from pathlib import Path

from pmid2endnote.conversions import (
    CREATED_MARKER,
    clean_old_conversions,
    conversion_output_paths,
    create_conversion_folder,
)


def test_conversion_folder_naming_uniqueness_marker_and_output_paths(tmp_path: Path) -> None:
    now = datetime(2026, 9, 21, 11, 22, 33)
    input_docx = tmp_path / "My manuscript.docx"

    first = create_conversion_folder(input_docx, root=tmp_path / "runs", now=now)
    second = create_conversion_folder(input_docx, root=tmp_path / "runs", now=now)

    assert first.name == "2026-09-21 11.22.33 My manuscript"
    assert second.name == "2026-09-21 11.22.33 My manuscript (2)"
    assert (first / CREATED_MARKER).read_text(encoding="utf-8").strip() == now.isoformat()
    assert conversion_output_paths(input_docx, first) == {
        "output_docx": first / "My manuscript.endnote.docx",
        "enw_file": first / "My manuscript.endnote-import.enw",
        "nbib_file": first / "My manuscript.references.nbib",
        "report_file": first / "My manuscript.pmid2endnote.report.json",
    }


def test_cleanup_respects_markers_mtime_symlinks_and_root_files(tmp_path: Path) -> None:
    root = tmp_path / "conversions"
    now = datetime(2026, 9, 21, 12, 0, 0)
    old = create_conversion_folder(
        Path("old.docx"), root=root, now=now - timedelta(days=31)
    )
    recent = create_conversion_folder(
        Path("recent.docx"), root=root, now=now - timedelta(days=29)
    )
    markerless = root / "markerless"
    markerless.mkdir()
    old_mtime = (now - timedelta(days=31)).timestamp()
    os.utime(markerless, (old_mtime, old_mtime))
    root_file = root / "keep.txt"
    root_file.write_text("keep", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "linked-folder"
    link.symlink_to(outside, target_is_directory=True)

    deleted = clean_old_conversions(root=root, max_age_days=30, now=now)

    assert set(deleted) == {old, markerless}
    assert not old.exists()
    assert not markerless.exists()
    assert recent.exists()
    assert link.is_symlink()
    assert outside.exists()
    assert root_file.read_text(encoding="utf-8") == "keep"


def test_cleanup_missing_root_is_a_noop(tmp_path: Path) -> None:
    assert clean_old_conversions(root=tmp_path / "missing") == []

from pathlib import Path

from pmid2endnote.settings import get_saved_email, load_settings, resolve_email, save_email


def test_save_and_load_email(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"

    save_email(" name@university.edu ", settings_path)

    assert get_saved_email(settings_path) == "name@university.edu"
    assert load_settings(settings_path) == {"email": "name@university.edu"}


def test_resolve_email_prefers_explicit_then_env_then_saved(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings_path = tmp_path / "settings.json"
    save_email("saved@example.edu", settings_path)
    monkeypatch.setenv("PMID2ENDNOTE_EMAIL", "env@example.edu")

    assert resolve_email("explicit@example.edu", settings_path) == "explicit@example.edu"
    assert resolve_email(None, settings_path) == "env@example.edu"

    monkeypatch.delenv("PMID2ENDNOTE_EMAIL")
    assert resolve_email(None, settings_path) == "saved@example.edu"


def test_invalid_settings_file_is_treated_as_empty(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{not json", encoding="utf-8")

    assert load_settings(settings_path) == {}
    assert get_saved_email(settings_path) is None

from pathlib import Path

from pmid2endnote.settings import (
    get_api_key,
    get_retention_days,
    get_saved_email,
    get_scan_parenthetical_pmids,
    get_skip_reference_section,
    get_write_nbib,
    get_write_report,
    load_settings,
    resolve_email,
    save_api_key,
    save_email,
    save_retention_days,
    save_scan_parenthetical_pmids,
    save_skip_reference_section,
    save_write_nbib,
    save_write_report,
    write_settings,
)


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


def test_gui_setting_defaults_and_individual_savers(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"

    assert get_retention_days(settings_path) == 30
    assert get_write_nbib(settings_path) is False
    assert get_write_report(settings_path) is False
    assert get_api_key(settings_path) is None
    assert get_scan_parenthetical_pmids(settings_path) is False
    assert get_skip_reference_section(settings_path) is True

    save_retention_days(90, settings_path)
    save_write_nbib(True, settings_path)
    save_write_report(True, settings_path)
    save_api_key(" key-123 ", settings_path)
    save_scan_parenthetical_pmids(True, settings_path)
    save_skip_reference_section(False, settings_path)

    assert get_retention_days(settings_path) == 90
    assert get_write_nbib(settings_path) is True
    assert get_write_report(settings_path) is True
    assert get_api_key(settings_path) == "key-123"
    assert get_scan_parenthetical_pmids(settings_path) is True
    assert get_skip_reference_section(settings_path) is False

    save_api_key("", settings_path)
    assert get_api_key(settings_path) is None
    assert "api_key" not in load_settings(settings_path)


def test_retention_days_coerce_invalid_values_to_default(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"

    save_retention_days("14", settings_path)
    assert get_retention_days(settings_path) == 14

    save_retention_days(31, settings_path)
    assert get_retention_days(settings_path) == 30

    write_settings({"retention_days": "bad"}, settings_path)
    assert get_retention_days(settings_path) == 30

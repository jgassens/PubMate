from pathlib import Path
import queue
import tkinter
from types import SimpleNamespace

import pmid2endnote.gui as gui
from pmid2endnote.gui import (
    PubMateGUI,
    build_processing_options,
    create_root,
    email_validation_error,
    parse_dropped_paths,
    reveal_command,
)


def test_parse_dropped_paths_handles_braces_and_spaces_without_tk_window() -> None:
    splitlist = tkinter.Tcl().splitlist

    assert parse_dropped_paths("{/a b/c.docx} /d.docx", splitlist) == [
        Path("/a b/c.docx"),
        Path("/d.docx"),
    ]


def test_build_processing_options_uses_conversion_folder_and_gui_settings(
    tmp_path: Path,
) -> None:
    input_docx = tmp_path / "draft.docx"
    folder = tmp_path / "conversion"

    options = build_processing_options(
        input_docx,
        folder,
        {
            "email": "person@example.edu",
            "api_key": "secret",
            "write_nbib": True,
            "write_report": False,
            "scan_parenthetical_pmids": True,
            "skip_reference_section": False,
        },
    )

    assert options.input_docx == input_docx
    assert options.output_docx == folder / "draft.endnote.docx"
    assert options.enw_file == folder / "draft.endnote-import.enw"
    assert options.nbib_file == folder / "draft.references.nbib"
    assert options.report_file == folder / "draft.pmid2endnote.report.json"
    assert options.email == "person@example.edu"
    assert options.api_key == "secret"
    assert options.write_nbib is True
    assert options.write_report is False
    assert options.scan_parenthetical_pmids is True
    assert options.skip_reference_section is False
    assert options.create_backup is False


def test_reveal_command_for_each_platform(tmp_path: Path) -> None:
    assert reveal_command(tmp_path, "darwin") == ["open", str(tmp_path)]
    assert reveal_command(tmp_path, "win32") is None
    assert reveal_command(tmp_path, "linux") == ["xdg-open", str(tmp_path)]


def test_email_validation_requires_nonempty_address_with_at_sign() -> None:
    assert email_validation_error("")
    assert email_validation_error("not-an-email")
    assert email_validation_error("person@example.edu") is None


def test_create_root_falls_back_when_tkdnd_cannot_load(monkeypatch) -> None:
    class FakeTclError(Exception):
        pass

    fallback_root = object()

    class FakeTkinter:
        TclError = FakeTclError

        @staticmethod
        def Tk() -> object:
            return fallback_root

    class BrokenTkinterDnD:
        @staticmethod
        def Tk() -> object:
            raise FakeTclError("wrong architecture")

    monkeypatch.setattr(gui, "tk", FakeTkinter)
    monkeypatch.setattr(gui, "TkinterDnD", BrokenTkinterDnD)

    root, dnd_available = create_root()

    assert root is fallback_root
    assert dnd_available is False


def test_poll_queue_rearms_after_handler_error() -> None:
    after_calls: list[tuple[int, object]] = []
    logs: list[str] = []
    running_states: list[bool] = []
    application = object.__new__(PubMateGUI)
    application.root = SimpleNamespace(
        after=lambda delay, callback: after_calls.append((delay, callback))
    )
    application._queue = queue.Queue()
    application._queue.put(("result", (object(), Path("conversion"))))
    application._handle_result = lambda _result, _folder: (_ for _ in ()).throw(
        RuntimeError("handler failed")
    )
    application._append_log = logs.append
    application._set_running = running_states.append

    application._poll_queue()

    assert logs == ["Unexpected error handling worker update: handler failed\n"]
    assert running_states == [False]
    assert after_calls == [(100, application._poll_queue)]


def test_open_settings_does_not_stack_dialogs(monkeypatch) -> None:
    application = object.__new__(PubMateGUI)
    application.root = object()
    application._running = False
    application._settings_open = False
    nested_results: list[bool] = []

    class FakeSettingsDialog:
        def __init__(self, _parent: object) -> None:
            nested_results.append(application.open_settings())
            self.saved = True

    monkeypatch.setattr(gui, "SettingsDialog", FakeSettingsDialog)

    assert application.open_settings() is True
    assert nested_results == [False]
    assert application._settings_open is False

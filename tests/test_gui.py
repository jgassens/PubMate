from pathlib import Path
import queue
import tkinter
from types import SimpleNamespace

import pytest

import pmid2endnote.gui as gui
from pmid2endnote.gui import (
    ClickGate,
    PubMateGUI,
    SettingsDialog,
    build_processing_options,
    create_root,
    email_validation_error,
    parse_dropped_paths,
    reveal_command,
)


def test_settings_check_for_updates_uses_in_process_bridge(monkeypatch) -> None:
    checks: list[bool] = []
    window = SimpleNamespace(destroy=lambda: pytest.fail("dialog must stay open"))
    dialog = object.__new__(SettingsDialog)
    dialog.window = window
    monkeypatch.setattr(gui.sparkle, "start", lambda: True)
    monkeypatch.setattr(gui.sparkle, "check_now", lambda: checks.append(True) or True)

    dialog._check_for_updates()

    assert checks == [True]


def test_update_check_reports_bridge_start_failure(monkeypatch) -> None:
    alerts: list[tuple[str, str, object]] = []
    parent = object()
    monkeypatch.setattr(gui.sparkle, "start", lambda: False)
    monkeypatch.setattr(
        gui.sparkle,
        "check_now",
        lambda: pytest.fail("must not check after startup failure"),
    )
    monkeypatch.setattr(
        gui.messagebox,
        "showerror",
        lambda title, message, parent: alerts.append((title, message, parent)),
    )

    gui._check_for_updates(parent)

    assert alerts[0][0] == "Check for Updates"
    assert "The updater could not be loaded" in alerts[0][1]
    assert "https://github.com/jgassens/PubMate/releases" in alerts[0][1]
    assert alerts[0][2] is parent


def test_update_check_does_not_alert_when_sparkle_cannot_check(monkeypatch) -> None:
    monkeypatch.setattr(gui.sparkle, "start", lambda: True)
    monkeypatch.setattr(gui.sparkle, "check_now", lambda: False)
    monkeypatch.setattr(
        gui.messagebox,
        "showerror",
        lambda *_args, **_kwargs: pytest.fail("Sparkle already owns the UI"),
    )

    gui._check_for_updates(object())


def test_settings_update_button_is_hidden_when_sparkle_is_unavailable(
    monkeypatch,
) -> None:
    buttons: list[dict[str, object]] = []

    class FakeWidget:
        def __init__(self, _parent: object = None, **kwargs: object) -> None:
            self.kwargs = kwargs

        def grid(self, **_kwargs: object) -> None:
            pass

        def columnconfigure(self, *_args: object, **_kwargs: object) -> None:
            pass

    class FakeButton(FakeWidget):
        def __init__(self, parent: object, **kwargs: object) -> None:
            super().__init__(parent, **kwargs)
            buttons.append(kwargs)

    dialog = object.__new__(SettingsDialog)
    dialog._check_for_updates = lambda: None
    monkeypatch.setattr(gui.sparkle, "available", lambda: False)
    monkeypatch.setattr(gui.ttk, "Frame", FakeWidget)
    monkeypatch.setattr(gui.ttk, "Label", FakeWidget)
    monkeypatch.setattr(gui.ttk, "Button", FakeButton)

    dialog._build_update_controls(object())

    assert buttons == []


def test_settings_update_button_is_shown_when_sparkle_is_available(
    monkeypatch,
) -> None:
    buttons: list[dict[str, object]] = []

    class FakeWidget:
        def __init__(self, _parent: object = None, **kwargs: object) -> None:
            self.kwargs = kwargs

        def grid(self, **_kwargs: object) -> None:
            pass

        def columnconfigure(self, *_args: object, **_kwargs: object) -> None:
            pass

    class FakeButton(FakeWidget):
        def __init__(self, parent: object, **kwargs: object) -> None:
            super().__init__(parent, **kwargs)
            buttons.append(kwargs)

    dialog = object.__new__(SettingsDialog)
    dialog._check_for_updates = lambda: None
    monkeypatch.setattr(gui.sparkle, "available", lambda: True)
    monkeypatch.setattr(gui.ttk, "Frame", FakeWidget)
    monkeypatch.setattr(gui.ttk, "Label", FakeWidget)
    monkeypatch.setattr(gui.ttk, "Button", FakeButton)

    dialog._build_update_controls(object())

    assert [button["text"] for button in buttons] == ["Check for Updates…"]


def test_settings_return_invokes_focused_button() -> None:
    invoked: list[bool] = []
    focused_button = SimpleNamespace(
        winfo_class=lambda: "TButton",
        invoke=lambda: invoked.append(True),
    )
    dialog = object.__new__(SettingsDialog)
    dialog.window = SimpleNamespace(focus_get=lambda: focused_button)
    dialog._save = lambda: pytest.fail("focused button should handle Return")

    assert dialog._handle_return(None) == "break"
    assert invoked == [True]


def test_settings_return_saves_when_focus_is_not_a_button() -> None:
    saves: list[bool] = []
    focused_entry = SimpleNamespace(winfo_class=lambda: "TEntry")
    dialog = object.__new__(SettingsDialog)
    dialog.window = SimpleNamespace(focus_get=lambda: focused_entry)
    dialog._save = lambda: saves.append(True)

    assert dialog._handle_return(None) == "break"
    assert saves == [True]


def test_settings_return_saves_when_nothing_has_focus() -> None:
    saves: list[bool] = []
    dialog = object.__new__(SettingsDialog)
    dialog.window = SimpleNamespace(focus_get=lambda: None)
    dialog._save = lambda: saves.append(True)

    assert dialog._handle_return(None) == "break"
    assert saves == [True]


def test_click_gate_requires_matching_press_and_inside_release() -> None:
    gate = ClickGate()

    assert gate.release(inside=True) is False
    gate.press()
    assert gate.release(inside=False) is False
    assert gate.release(inside=True) is False
    gate.press()
    assert gate.release(inside=True) is True


def test_click_gate_clears_press_and_suppresses_chooser_after_settings() -> None:
    now = [10.0]
    gate = ClickGate(clock=lambda: now[0])

    assert gate.should_open() is True
    gate.press()
    gate.settings_opened()
    assert gate.release(inside=True) is False
    gate.settings_closed()
    assert gate.should_open() is False
    now[0] += 0.399
    assert gate.should_open() is False
    now[0] += 0.001
    assert gate.should_open() is True


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


def test_open_event_defers_native_file_chooser(monkeypatch) -> None:
    idle_callbacks: list[object] = []
    chooser_calls: list[dict[str, object]] = []
    application = object.__new__(PubMateGUI)
    application.root = SimpleNamespace(
        after_idle=lambda callback: idle_callbacks.append(callback)
    )
    application._running = False
    application._settings_open = False
    application._chooser_pending = False
    application._click_gate = ClickGate()
    monkeypatch.setattr(
        gui.filedialog,
        "askopenfilename",
        lambda **kwargs: chooser_calls.append(kwargs) or "",
    )

    assert application._open_event(object()) == "break"
    assert chooser_calls == []
    assert idle_callbacks == [application._show_file_chooser]

    idle_callbacks[0]()
    assert len(chooser_calls) == 1


def test_drop_callback_defers_all_drop_handling_until_idle() -> None:
    idle_callbacks: list[object] = []
    conversions: list[Path] = []
    application = object.__new__(PubMateGUI)
    application.root = SimpleNamespace(
        after_idle=lambda callback: idle_callbacks.append(callback),
        tk=SimpleNamespace(splitlist=lambda value: (value,)),
    )
    application._running = False
    application.start_conversion = conversions.append

    assert application._on_drop(SimpleNamespace(data="/tmp/draft.docx")) == "break"
    assert conversions == []

    idle_callbacks[0]()
    assert conversions == [Path("/tmp/draft.docx")]


def test_chooser_pending_resets_after_idle_scheduling_or_callback_failure(monkeypatch) -> None:
    idle_callbacks: list[object] = []
    attempts = 0

    def after_idle(callback: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("cannot schedule")
        idle_callbacks.append(callback)

    application = object.__new__(PubMateGUI)
    application.root = SimpleNamespace(after_idle=after_idle)
    application._running = False
    application._settings_open = False
    application._chooser_pending = False
    application._click_gate = ClickGate()

    with pytest.raises(RuntimeError, match="cannot schedule"):
        application.choose_file()
    assert application._chooser_pending is False

    application.choose_file()
    assert idle_callbacks == [application._show_file_chooser]

    monkeypatch.setattr(
        gui.filedialog,
        "askopenfilename",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("chooser failed")),
    )
    with pytest.raises(RuntimeError, match="chooser failed"):
        idle_callbacks[0]()
    assert application._chooser_pending is False


def test_drop_release_without_press_does_not_schedule_chooser() -> None:
    idle_callbacks: list[object] = []
    application = object.__new__(PubMateGUI)
    application.root = SimpleNamespace(
        after_idle=lambda callback: idle_callbacks.append(callback)
    )
    application.drop_zone = SimpleNamespace(
        winfo_rootx=lambda: 10,
        winfo_rooty=lambda: 20,
        winfo_width=lambda: 100,
        winfo_height=lambda: 50,
    )
    application._running = False
    application._settings_open = False
    application._chooser_pending = False
    application._click_gate = ClickGate()
    event = SimpleNamespace(x_root=30, y_root=40)

    assert application._choose_event(event) == "break"
    assert idle_callbacks == []

    application._drop_press_event(event)
    application._choose_event(event)
    assert idle_callbacks == [application._show_file_chooser]


def test_open_settings_does_not_stack_dialogs(monkeypatch) -> None:
    application = object.__new__(PubMateGUI)
    application.root = object()
    application._running = False
    application._settings_open = False
    application._click_gate = ClickGate()
    nested_results: list[bool] = []

    class FakeSettingsDialog:
        def __init__(self, _parent: object) -> None:
            nested_results.append(application.open_settings())
            self.saved = True

    monkeypatch.setattr(gui, "SettingsDialog", FakeSettingsDialog)

    assert application.open_settings() is True
    assert nested_results == [False]
    assert application._settings_open is False


def test_show_preferences_routes_exceptions_to_tk(monkeypatch) -> None:
    reported: list[tuple[object, object, object]] = []
    application = object.__new__(PubMateGUI)
    application.root = SimpleNamespace(
        report_callback_exception=lambda *info: reported.append(info)
    )
    application.open_settings = lambda: (_ for _ in ()).throw(RuntimeError("boom"))

    assert application._settings_event(None) == "break"

    assert len(reported) == 1
    assert reported[0][0] is RuntimeError
    assert str(reported[0][1]) == "boom"


def test_quit_during_conversion_respects_no(monkeypatch) -> None:
    prompts: list[dict[str, object]] = []
    destroyed: list[bool] = []
    root = SimpleNamespace(destroy=lambda: destroyed.append(True))
    application = object.__new__(PubMateGUI)
    application.root = root
    application._running = True
    monkeypatch.setattr(
        gui.messagebox,
        "askyesno",
        lambda title, message, **kwargs: prompts.append(
            {"title": title, "message": message, **kwargs}
        )
        or False,
    )

    assert application._quit_event() == "break"
    assert destroyed == []
    assert prompts[0]["title"] == "Conversion in progress"
    assert prompts[0]["parent"] is root
    assert prompts[0]["default"] == gui.messagebox.NO


def test_quit_during_conversion_yes_clears_busy_then_destroys(monkeypatch) -> None:
    events: list[object] = []
    root = SimpleNamespace(destroy=lambda: events.append("destroy"))
    application = object.__new__(PubMateGUI)
    application.root = root
    application._running = True
    application._set_running = lambda running: events.append(("running", running))
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *_args, **_kwargs: True)

    application._request_quit()
    assert events == [("running", False), "destroy"]


def test_conversion_error_clears_sparkle_busy_state(monkeypatch, tmp_path: Path) -> None:
    busy_states: list[bool] = []
    statuses: list[str] = []
    application = object.__new__(PubMateGUI)
    application.root = object()
    application.status = SimpleNamespace(set=statuses.append)
    application._drop_widgets = []
    application._append_log = lambda _message: None
    application._reveal_if_populated = lambda _folder: None
    application._clean_old_folders = lambda: None
    monkeypatch.setattr(gui.sparkle, "set_busy", busy_states.append)
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *_args, **_kwargs: None)

    application._set_running(True)
    application._handle_error("failed", tmp_path)

    assert busy_states == [True, False]
    assert application._running is False


def test_conversion_error_clears_busy_before_showing_alert(
    monkeypatch, tmp_path: Path
) -> None:
    events: list[object] = []
    application = object.__new__(PubMateGUI)
    application.root = object()
    application.status = SimpleNamespace(set=lambda _status: None)
    application._append_log = lambda _message: None
    application._reveal_if_populated = lambda _folder: None
    application._clean_old_folders = lambda: None
    application._set_running = lambda running: events.append(("running", running))
    monkeypatch.setattr(
        gui.messagebox,
        "showerror",
        lambda *_args, **_kwargs: events.append("alert"),
    )

    application._handle_error("failed", tmp_path)

    assert events == [("running", False), "alert"]


def test_handle_result_clears_busy(tmp_path: Path) -> None:
    states: list[bool] = []
    application = object.__new__(PubMateGUI)
    application.root = object()
    application.status = SimpleNamespace(set=lambda _status: None)
    application._append_log = lambda _message: None
    application._reveal_if_populated = lambda _folder: None
    application._clean_old_folders = lambda: None
    application._set_running = states.append

    application._handle_result(
        SimpleNamespace(exit_code=0, messages=()),
        tmp_path,
    )

    assert states == [False]


def test_thread_start_failure_clears_busy(monkeypatch, tmp_path: Path) -> None:
    busy_states: list[bool] = []
    application = object.__new__(PubMateGUI)
    application.root = object()
    application.status = SimpleNamespace(set=lambda _status: None)
    application._running = False
    application._drop_widgets = []
    application._document_error = lambda _path: None
    application._append_log = lambda _message: None
    monkeypatch.setattr(gui, "get_saved_email", lambda: "person@example.edu")
    monkeypatch.setattr(gui, "create_conversion_folder", lambda _path: tmp_path)
    monkeypatch.setattr(gui, "load_gui_settings", lambda: {})
    monkeypatch.setattr(gui.sparkle, "set_busy", busy_states.append)

    class BrokenThread:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("cannot start thread")

    monkeypatch.setattr(gui.threading, "Thread", BrokenThread)

    with pytest.raises(RuntimeError, match="cannot start thread"):
        application.start_conversion(tmp_path / "draft.docx")

    assert busy_states == [True, False]
    assert application._running is False


def test_run_starts_sparkle_from_tk_main_loop(monkeypatch) -> None:
    scheduled: list[tuple[int, object]] = []
    root = SimpleNamespace(
        after=lambda delay, callback, *args: scheduled.append((delay, callback)),
        mainloop=lambda: None,
    )
    starts: list[bool] = []
    monkeypatch.setattr(gui, "create_root", lambda: (root, False))
    monkeypatch.setattr(gui, "PubMateGUI", lambda _root, _dnd: object())
    monkeypatch.setattr(gui.sparkle, "start", lambda: starts.append(True) or True)

    assert gui.run() == 0
    assert len(scheduled) == 1
    assert scheduled[0][0] == 0

    scheduled[0][1]()
    assert starts == [True]


def test_macos_menu_registers_native_settings_without_duplicate_entry_or_binding(
    monkeypatch,
) -> None:
    class FakeMenu:
        instances: list["FakeMenu"] = []

        def __init__(self, _parent: object, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.entries: list[tuple[str, dict[str, object]]] = []
            FakeMenu.instances.append(self)

        def add_command(self, **kwargs: object) -> None:
            self.entries.append(("command", kwargs))

        def add_separator(self) -> None:
            self.entries.append(("separator", {}))

        def add_cascade(self, **kwargs: object) -> None:
            self.entries.append(("cascade", kwargs))

    class FakeRoot:
        def __init__(self) -> None:
            self.commands: list[tuple[str, object]] = []
            self.bindings: list[tuple[str, object]] = []
            self.configured_menu: object | None = None
            self.registered: dict[str, object] = {}
            self.tk_calls: list[str] = []
            self.tk = SimpleNamespace(call=self.call)

        def createcommand(self, name: str, callback: object) -> None:
            self.commands.append((name, callback))

        def register(self, callback: object) -> str:
            name = f"pycmd{len(self.registered)}"
            self.registered[name] = callback
            return name

        def call(self, name: str) -> object:
            self.tk_calls.append(name)
            return self.registered[name]()

        def bind_all(self, sequence: str, callback: object) -> None:
            self.bindings.append((sequence, callback))

        def configure(self, **kwargs: object) -> None:
            self.configured_menu = kwargs["menu"]

    root = FakeRoot()
    application = object.__new__(PubMateGUI)
    application.root = root
    application.choose_file = lambda: None
    application.open_conversions_folder = lambda: None
    application.show_about = lambda: None
    settings_events: list[object] = []
    application._settings_event = lambda event: settings_events.append(event) or "break"
    application._open_event = lambda _event: "break"
    monkeypatch.setattr(gui.tk, "Menu", FakeMenu)
    monkeypatch.setattr(gui.sys, "platform", "darwin")
    monkeypatch.setattr(gui.sparkle, "available", lambda: True)

    application._build_menu()

    assert [name for name, _callback in root.commands] == [
        "::tk::mac::ShowPreferences",
        "::tk::mac::Quit",
    ]
    assert "<Command-comma>" not in [sequence for sequence, _callback in root.bindings]
    assert [sequence for sequence, _callback in root.bindings] == ["<Command-o>"]
    assert all(
        entry[1].get("label") != "Settings…"
        for menu in FakeMenu.instances
        for entry in menu.entries
        if entry[0] == "command"
    )
    assert any(
        entry[1].get("label") == "Check for Updates…"
        for menu in FakeMenu.instances
        for entry in menu.entries
        if entry[0] == "command"
    )

    root.commands[0][1]()
    assert settings_events == [None]
    assert root.tk_calls == ["pycmd0"]

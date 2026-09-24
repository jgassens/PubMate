"""Tkinter drop-zone application for PubMate."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Callable

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
except ImportError:
    tk = None
    filedialog = None
    messagebox = None
    scrolledtext = None
    ttk = None

if tk is not None:
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
    except ImportError:
        DND_FILES = None
        TkinterDnD = None
else:
    DND_FILES = None
    TkinterDnD = None

from pmid2endnote.app import ProcessingOptions, ProcessingResult, process_document
from pmid2endnote.conversions import (
    CREATED_MARKER,
    RETENTION_CHOICES,
    clean_old_conversions,
    conversion_output_paths,
    conversions_root,
    create_conversion_folder,
)
from pmid2endnote.settings import (
    get_api_key,
    get_retention_days,
    get_saved_email,
    get_scan_parenthetical_pmids,
    get_skip_reference_section,
    get_write_nbib,
    get_write_report,
    save_gui_settings,
)


def parse_dropped_paths(
    tk_data_string: str,
    splitlist: Callable[[str], tuple[str, ...] | list[str]],
) -> list[Path]:
    """Parse Tk drop data without breaking paths containing spaces or braces."""

    return [Path(value) for value in splitlist(tk_data_string)]


def build_processing_options(
    input_docx: Path,
    folder: Path,
    settings_dict: dict[str, object],
) -> ProcessingOptions:
    """Build the GUI's processing options without requiring Tk."""

    output_paths = conversion_output_paths(input_docx, folder)
    email_value = settings_dict.get("email")
    api_key_value = settings_dict.get("api_key")
    return ProcessingOptions(
        input_docx=input_docx,
        email=str(email_value).strip() if email_value else None,
        api_key=str(api_key_value).strip() if api_key_value else None,
        write_nbib=bool(settings_dict.get("write_nbib", False)),
        write_report=bool(settings_dict.get("write_report", False)),
        scan_parenthetical_pmids=bool(
            settings_dict.get("scan_parenthetical_pmids", False)
        ),
        skip_reference_section=bool(settings_dict.get("skip_reference_section", True)),
        create_backup=False,
        **output_paths,
    )


def reveal_command(folder: Path, platform: str) -> list[str] | None:
    """Return the platform file-manager command, or None for Windows."""

    if platform == "darwin":
        return ["open", str(folder)]
    if platform.startswith("win"):
        return None
    return ["xdg-open", str(folder)]


def email_validation_error(email: str) -> str | None:
    """Return a user-facing validation error for the NCBI email field."""

    value = email.strip()
    if not value:
        return "NCBI email is required."
    if "@" not in value:
        return "Enter a valid NCBI email address containing '@'."
    return None


def load_gui_settings() -> dict[str, object]:
    """Load all settings used to configure a GUI processing run."""

    return {
        "email": get_saved_email(),
        "api_key": get_api_key(),
        "write_nbib": get_write_nbib(),
        "write_report": get_write_report(),
        "retention_days": get_retention_days(),
        "scan_parenthetical_pmids": get_scan_parenthetical_pmids(),
        "skip_reference_section": get_skip_reference_section(),
    }


def create_root() -> tuple[object, bool]:
    """Create the GUI root, falling back when the tkdnd binary cannot load."""

    if TkinterDnD is not None:
        try:
            return TkinterDnD.Tk(), True
        except tk.TclError:
            pass
    return tk.Tk(), False


class ClickGate:
    """Track complete drop-zone clicks and the settings-dialog cooldown."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        cooldown_seconds: float = 0.4,
    ) -> None:
        self._clock = clock
        self._cooldown_seconds = cooldown_seconds
        self._pressed = False
        self._settings_open = False
        self._settings_closed_at: float | None = None

    def press(self) -> None:
        self._pressed = True

    def release(self, *, inside: bool) -> bool:
        matched_press = self._pressed
        self._pressed = False
        return matched_press and inside

    def settings_opened(self) -> None:
        self._pressed = False
        self._settings_open = True

    def settings_closed(self) -> None:
        self._settings_open = False
        self._settings_closed_at = self._clock()

    def should_open(self) -> bool:
        if self._settings_open:
            return False
        if self._settings_closed_at is None:
            return True
        return self._clock() - self._settings_closed_at >= self._cooldown_seconds


class SettingsDialog:
    """Modal editor for PubMate's persistent GUI settings."""

    def __init__(self, parent: object) -> None:
        self.saved = False
        self.window = tk.Toplevel(parent)
        self.window.title("PubMate Settings")
        self.window.resizable(False, False)
        self.window.transient(parent)

        values = load_gui_settings()
        self.email = tk.StringVar(value=values["email"] or "")
        self.api_key = tk.StringVar(value=values["api_key"] or "")
        self.write_nbib = tk.BooleanVar(value=values["write_nbib"])
        self.write_report = tk.BooleanVar(value=values["write_report"])
        self.retention_days = tk.IntVar(value=values["retention_days"])
        self.scan_parenthetical_pmids = tk.BooleanVar(
            value=values["scan_parenthetical_pmids"]
        )
        self.skip_reference_section = tk.BooleanVar(
            value=values["skip_reference_section"]
        )

        frame = ttk.Frame(self.window, padding=18)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="NCBI email (required)").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=4
        )
        email_entry = ttk.Entry(frame, textvariable=self.email, width=38)
        email_entry.grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(frame, text="NCBI API key (optional)").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=4
        )
        ttk.Entry(frame, textvariable=self.api_key, width=38, show="•").grid(
            row=1, column=1, sticky="ew", pady=4
        )

        ttk.Checkbutton(
            frame,
            text="Also write auxiliary .nbib file (debugging)",
            variable=self.write_nbib,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 2))
        ttk.Checkbutton(
            frame,
            text="Also write JSON report (debugging)",
            variable=self.write_report,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=2)

        retention = ttk.LabelFrame(frame, text="Keep converted files for:", padding=8)
        retention.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 6))
        for column, days in enumerate(RETENTION_CHOICES):
            ttk.Radiobutton(
                retention,
                text=f"{days} days",
                value=days,
                variable=self.retention_days,
            ).grid(row=0, column=column, sticky="w", padx=(0, 12))

        ttk.Checkbutton(
            frame,
            text="Scan parenthetical PMIDs like (6426050)",
            variable=self.scan_parenthetical_pmids,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 2))
        ttk.Checkbutton(
            frame,
            text="Skip identifiers after a References heading",
            variable=self.skip_reference_section,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=2)

        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Cancel", command=self._close).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(buttons, text="Save", command=self._save).grid(row=0, column=1)

        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.bind("<Escape>", lambda _event: self._close())
        self.window.bind("<Return>", lambda _event: self._save())
        self.window.grab_set()
        email_entry.focus_set()
        self.window.wait_window()

    def _close(self) -> None:
        try:
            self.window.grab_release()
        except tk.TclError:
            pass
        self.window.destroy()

    def _save(self) -> None:
        error = email_validation_error(self.email.get())
        if error:
            messagebox.showerror("Invalid settings", error, parent=self.window)
            return
        save_gui_settings(
            {
                "email": self.email.get(),
                "api_key": self.api_key.get(),
                "write_nbib": self.write_nbib.get(),
                "write_report": self.write_report.get(),
                "retention_days": self.retention_days.get(),
                "scan_parenthetical_pmids": self.scan_parenthetical_pmids.get(),
                "skip_reference_section": self.skip_reference_section.get(),
            }
        )
        self.saved = True
        self._close()


class PubMateGUI:
    """Single-window drop-zone front end over the processing service."""

    def __init__(self, root: object, dnd_available: bool) -> None:
        self.root = root
        self.root.title("PubMate")
        self.root.geometry("700x500")
        self.root.minsize(560, 400)
        self.status = tk.StringVar(value="Drop or choose a Word document to begin.")
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._running = False
        self._settings_open = False
        self._click_gate = ClickGate()
        self._chooser_pending = False
        self._dnd_available = dnd_available
        self._drop_widgets: list[object] = []

        self._build_menu()
        self._build_ui()
        self._configure_drop_targets()
        self._clean_old_folders()
        self._poll_queue()

    def _build_menu(self) -> None:
        menu_bar = tk.Menu(self.root)
        file_menu = tk.Menu(menu_bar, tearoff=False)
        file_menu.add_command(
            label="Open…",
            accelerator="Command-O" if sys.platform == "darwin" else "Ctrl+O",
            command=self.choose_file,
        )
        file_menu.add_command(
            label="Open Conversions Folder",
            command=self.open_conversions_folder,
        )

        if sys.platform == "darwin":
            application_menu = tk.Menu(menu_bar, name="apple", tearoff=False)
            application_menu.add_command(label="About PubMate", command=self.show_about)
            menu_bar.add_cascade(menu=application_menu)
            self.root.createcommand(
                "::tk::mac::ShowPreferences", lambda: self._settings_event(None)
            )
            self.root.bind_all("<Command-o>", self._open_event)
        else:
            file_menu.add_separator()
            file_menu.add_command(label="Settings…", command=self.open_settings)
            self.root.bind_all("<Control-o>", self._open_event)

        menu_bar.add_cascade(label="File", menu=file_menu)
        self.root.configure(menu=menu_bar)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main = ttk.Frame(self.root, padding=20)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=3)
        main.rowconfigure(2, weight=2)

        self.drop_zone = tk.Frame(
            main,
            borderwidth=2,
            relief="groove",
            background="#f4f4f4",
            cursor="hand2",
        )
        self.drop_zone.grid(row=0, column=0, sticky="nsew")
        self.drop_zone.columnconfigure(0, weight=1)
        self.drop_zone.rowconfigure(0, weight=1)
        self.drop_zone.rowconfigure(3, weight=1)

        primary = tk.Label(
            self.drop_zone,
            text="Drop a Word document (.docx) here",
            font=("TkDefaultFont", 18, "bold"),
            background="#f4f4f4",
        )
        primary.grid(row=1, column=0, padx=24, pady=(24, 5))
        secondary_text = (
            "or click to choose a file"
            if self._dnd_available
            else "Drag-and-drop is unavailable; click to choose a file"
        )
        secondary = tk.Label(
            self.drop_zone,
            text=secondary_text,
            font=("TkDefaultFont", 12),
            foreground="#555555",
            background="#f4f4f4",
        )
        secondary.grid(row=2, column=0, padx=24, pady=(0, 24))
        self._drop_widgets = [self.drop_zone, primary, secondary]
        for widget in self._drop_widgets:
            widget.bind("<ButtonPress-1>", self._drop_press_event)
            widget.bind("<ButtonRelease-1>", self._choose_event)

        ttk.Label(main, textvariable=self.status, anchor="w").grid(
            row=1, column=0, sticky="ew", pady=(12, 6)
        )
        self.log = scrolledtext.ScrolledText(main, height=8, wrap="word", state="disabled")
        self.log.grid(row=2, column=0, sticky="nsew")

    def _configure_drop_targets(self) -> None:
        if not self._dnd_available:
            return
        for widget in self._drop_widgets:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_drop)

    def _on_drop(self, event: object) -> str:
        if self._running:
            return "break"
        paths = parse_dropped_paths(event.data, self.root.tk.splitlist)
        if len(paths) != 1:
            messagebox.showerror(
                "Invalid drop", "Drop one Word .docx file at a time.", parent=self.root
            )
            return "break"
        self.start_conversion(paths[0])
        return "break"

    def _drop_press_event(self, _event: object) -> str:
        self._click_gate.press()
        return "break"

    def _choose_event(self, event: object) -> str:
        if self._click_gate.release(inside=self._release_inside_drop_zone(event)):
            self.choose_file()
        return "break"

    def _open_event(self, _event: object) -> str:
        self.choose_file()
        return "break"

    def _settings_event(self, _event: object) -> str:
        self.open_settings()
        return "break"

    def choose_file(self) -> None:
        """Request the native chooser from a later main-loop iteration."""

        if self._running or self._chooser_pending or not self._click_gate.should_open():
            return
        self._chooser_pending = True
        try:
            self.root.after_idle(self._show_file_chooser)
        except Exception:
            self._chooser_pending = False
            raise

    def _show_file_chooser(self) -> None:
        try:
            if self._running or not self._click_gate.should_open():
                return
            filename = filedialog.askopenfilename(
                parent=self.root,
                title="Choose Word document",
                filetypes=[("Word documents", "*.docx"), ("All files", "*.*")],
            )
            if filename:
                self.start_conversion(Path(filename))
        finally:
            self._chooser_pending = False

    def _release_inside_drop_zone(self, event: object) -> bool:
        left = self.drop_zone.winfo_rootx()
        top = self.drop_zone.winfo_rooty()
        return (
            left <= event.x_root < left + self.drop_zone.winfo_width()
            and top <= event.y_root < top + self.drop_zone.winfo_height()
        )

    def start_conversion(self, input_docx: Path) -> None:
        if self._running:
            return
        error = self._document_error(input_docx)
        if error:
            messagebox.showerror("Invalid document", error, parent=self.root)
            return

        email = get_saved_email()
        if email is None or email_validation_error(email):
            self.open_settings()
            email = get_saved_email()
            if email is None or email_validation_error(email):
                self.status.set("Conversion cancelled: an NCBI email is required.")
                return

        try:
            folder = create_conversion_folder(input_docx)
        except OSError as exc:
            messagebox.showerror(
                "Could not create conversion folder", str(exc), parent=self.root
            )
            return

        options = build_processing_options(input_docx, folder, load_gui_settings())
        self._set_running(True)
        self._append_log(f"Converting {input_docx.name}\n")
        self._append_log(f"Output folder: {folder}\n")
        thread = threading.Thread(
            target=self._run_worker,
            args=(options, folder),
            daemon=True,
        )
        thread.start()

    def _run_worker(self, options: ProcessingOptions, folder: Path) -> None:
        try:
            result = process_document(
                options,
                status_callback=lambda message: self._queue.put(("status", message)),
            )
            self._queue.put(("result", (result, folder)))
        except Exception as exc:
            self._queue.put(("error", (f"Unexpected error: {exc}", folder)))

    def _poll_queue(self) -> None:
        try:
            while True:
                try:
                    kind, payload = self._queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    if kind == "status":
                        message = str(payload)
                        self.status.set(message)
                        self._append_log(message + "\n")
                    elif kind == "result":
                        result, folder = payload
                        self._handle_result(result, folder)
                    elif kind == "error":
                        message, folder = payload
                        self._handle_error(str(message), folder)
                except Exception as exc:
                    self._append_log(f"Unexpected error handling worker update: {exc}\n")
                    self._set_running(False)
        finally:
            self.root.after(100, self._poll_queue)

    def _handle_result(self, result: ProcessingResult, folder: Path) -> None:
        self._set_running(False)
        self.status.set("Finished." if result.exit_code == 0 else "Finished with errors.")
        for message in result.messages:
            self._append_log(message + "\n")
        self._reveal_if_populated(folder)
        self._clean_old_folders()
        if result.exit_code != 0:
            detail = "\n".join(result.messages) or f"Conversion failed ({result.exit_code})."
            messagebox.showerror("PubMate error", detail, parent=self.root)

    def _handle_error(self, message: str, folder: Path) -> None:
        self._set_running(False)
        self.status.set("Unexpected error.")
        self._append_log(message + "\n")
        self._reveal_if_populated(folder)
        self._clean_old_folders()
        messagebox.showerror("PubMate error", message, parent=self.root)

    def _set_running(self, running: bool) -> None:
        self._running = running
        if running:
            self.status.set("Running…")
        cursor = "watch" if running else "hand2"
        for widget in self._drop_widgets:
            widget.configure(cursor=cursor)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clean_old_folders(self) -> None:
        removed = clean_old_conversions(max_age_days=get_retention_days())
        if removed:
            self._append_log(f"Removed {len(removed)} old conversion folder(s)\n")

    def _reveal_if_populated(self, folder: Path) -> None:
        try:
            populated = any(
                child.name != CREATED_MARKER and child.is_file()
                for child in folder.iterdir()
            )
        except OSError:
            return
        if populated:
            self._reveal_folder(folder)

    def _reveal_folder(self, folder: Path) -> None:
        try:
            command = reveal_command(folder, sys.platform)
            if command is None:
                startfile = getattr(os, "startfile")
                startfile(str(folder))
            else:
                subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except (OSError, AttributeError) as exc:
            self._append_log(f"Could not open conversion folder: {exc}\n")

    def open_conversions_folder(self) -> None:
        folder = conversions_root()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Could not open folder", str(exc), parent=self.root)
            return
        self._reveal_folder(folder)

    def open_settings(self) -> bool:
        if self._running or self._settings_open:
            return False
        self._settings_open = True
        self._click_gate.settings_opened()
        try:
            dialog = SettingsDialog(self.root)
            return dialog.saved
        finally:
            self._settings_open = False
            self._click_gate.settings_closed()

    def show_about(self) -> None:
        from pmid2endnote import __version__

        messagebox.showinfo(
            "About PubMate",
            f"PubMate {__version__}\n\nConvert Word PMID/DOI placeholders for EndNote.",
            parent=self.root,
        )

    @staticmethod
    def _document_error(path: Path) -> str | None:
        if path.suffix.lower() != ".docx":
            return "PubMate only accepts one Word .docx file."
        if not path.exists() or not path.is_file():
            return f"The Word document could not be found:\n{path}"
        return None


def run(initial_docx: Path | None = None) -> int:
    """Build and run the PubMate drop-zone window."""

    if tk is None:
        print("Tkinter is not available in this Python installation.", file=sys.stderr)
        return 2
    root, dnd_available = create_root()
    application = PubMateGUI(root, dnd_available)
    if initial_docx is not None:
        root.after(0, application.start_conversion, initial_docx)
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pmid2endnote-gui")
    parser.add_argument("input_docx", nargs="?", type=Path)
    args = parser.parse_args(argv)
    return run(args.input_docx)


if __name__ == "__main__":
    raise SystemExit(main())

"""Tkinter GUI for macOS and other desktop environments."""

from __future__ import annotations

from pathlib import Path
import queue
import subprocess
import sys
import threading

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
except ModuleNotFoundError:
    tk = None
    filedialog = None
    messagebox = None
    scrolledtext = None
    ttk = None

from pmid2endnote.app import ENDNOTE_INSTRUCTIONS, ProcessingOptions, ProcessingResult, process_document
from pmid2endnote.settings import get_saved_email
from pmid2endnote.word import default_enw_path, default_nbib_path, default_output_path, default_report_path


class PMID2EndNoteGUI:
    """Small desktop front end over the production processing service."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("PMID2EndNote")
        self.root.geometry("860x680")
        self.root.minsize(760, 600)

        self.input_docx = tk.StringVar()
        self.email = tk.StringVar(value=get_saved_email() or "")
        self.api_key = tk.StringVar()
        self.output_docx = tk.StringVar()
        self.enw_file = tk.StringVar()
        self.nbib_file = tk.StringVar()
        self.report_file = tk.StringVar()
        self.include_tables = tk.BooleanVar(value=True)
        self.include_comments = tk.BooleanVar(value=True)
        self.include_headers = tk.BooleanVar(value=False)
        self.include_footers = tk.BooleanVar(value=False)
        self.include_footnotes = tk.BooleanVar(value=False)
        self.dry_run = tk.BooleanVar(value=False)
        self.keep_pmid_text = tk.BooleanVar(value=False)
        self.mark_unresolved = tk.BooleanVar(value=False)
        self.scan_parenthetical_pmids = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Choose a .docx file to begin.")

        self._queue: queue.Queue[tuple[str, str | ProcessingResult]] = queue.Queue()
        self._last_output_dir: Path | None = None
        self._run_button: ttk.Button | None = None

        self._build_ui()
        self._poll_queue()

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main = ttk.Frame(self.root, padding=18)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)

        title = ttk.Label(main, text="PMID2EndNote", font=("TkDefaultFont", 22, "bold"))
        title.grid(row=0, column=0, sticky="w")

        subtitle = ttk.Label(
            main,
            text="Convert labeled PubMed PMIDs in a Word document into Clarivate EndNote temporary citations.",
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(4, 16))

        paths = ttk.LabelFrame(main, text="Files", padding=12)
        paths.grid(row=2, column=0, sticky="ew")
        paths.columnconfigure(1, weight=1)

        self._path_row(paths, 0, "Input .docx", self.input_docx, self._choose_input)
        self._path_row(paths, 1, "Output .docx", self.output_docx, self._choose_output_docx)
        self._path_row(paths, 2, "EndNote .enw", self.enw_file, self._choose_enw)
        self._path_row(paths, 3, "Auxiliary .nbib", self.nbib_file, self._choose_nbib)
        self._path_row(paths, 4, "Report .json", self.report_file, self._choose_report)

        options = ttk.LabelFrame(main, text="Options", padding=12)
        options.grid(row=3, column=0, sticky="ew", pady=(12, 12))
        options.columnconfigure(1, weight=1)

        ttk.Label(options, text="NCBI email").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(options, textvariable=self.email).grid(row=0, column=1, sticky="ew", pady=3)

        ttk.Label(options, text="NCBI API key").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(options, textvariable=self.api_key, show="*").grid(row=1, column=1, sticky="ew", pady=3)

        checks = ttk.Frame(options)
        checks.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        for column in range(4):
            checks.columnconfigure(column, weight=1)

        ttk.Checkbutton(checks, text="Tables", variable=self.include_tables).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(checks, text="Comments", variable=self.include_comments).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(checks, text="Headers", variable=self.include_headers).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(checks, text="Footers", variable=self.include_footers).grid(row=0, column=3, sticky="w")
        ttk.Checkbutton(checks, text="Footnotes", variable=self.include_footnotes).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(checks, text="Dry run", variable=self.dry_run).grid(row=1, column=1, sticky="w")
        ttk.Checkbutton(checks, text="Keep PMID text", variable=self.keep_pmid_text).grid(row=1, column=2, sticky="w")
        ttk.Checkbutton(checks, text="Mark unresolved", variable=self.mark_unresolved).grid(
            row=1, column=3, sticky="w"
        )
        ttk.Checkbutton(
            checks,
            text="Parenthetical PMIDs",
            variable=self.scan_parenthetical_pmids,
        ).grid(row=2, column=0, sticky="w")

        output = ttk.LabelFrame(main, text="Run Log", padding=12)
        output.grid(row=4, column=0, sticky="nsew")
        output.columnconfigure(0, weight=1)
        output.rowconfigure(1, weight=1)

        ttk.Label(output, textvariable=self.status).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.log = scrolledtext.ScrolledText(output, height=12, wrap="word")
        self.log.grid(row=1, column=0, sticky="nsew")
        self.log.insert(
            "end",
            ENDNOTE_INSTRUCTIONS.format(
                nbib_file="<file>.references.nbib",
                enw_file="<file>.endnote-import.enw",
                output_docx="<file>.endnote.docx",
            )
            + "\n",
        )
        self.log.configure(state="disabled")

        actions = ttk.Frame(main)
        actions.grid(row=5, column=0, sticky="ew", pady=(14, 0))
        actions.columnconfigure(0, weight=1)

        self._run_button = ttk.Button(actions, text="Run PMID2EndNote", command=self._start_run)
        self._run_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Button(actions, text="Open Output Folder", command=self._open_output_folder).grid(
            row=0, column=2, sticky="e", padx=(8, 0)
        )

    def _path_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        command: object,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Button(parent, text="Choose", command=command).grid(row=row, column=2, padx=(8, 0), pady=3)

    def _choose_input(self) -> None:
        filename = filedialog.askopenfilename(
            title="Choose Word document",
            filetypes=[("Word documents", "*.docx"), ("All files", "*.*")],
        )
        if not filename:
            return
        input_path = Path(filename)
        self.input_docx.set(str(input_path))
        self.output_docx.set(str(default_output_path(input_path)))
        self.enw_file.set(str(default_enw_path(input_path)))
        self.nbib_file.set(str(default_nbib_path(input_path)))
        self.report_file.set(str(default_report_path(input_path)))
        self._last_output_dir = input_path.parent
        self.status.set("Ready to run.")

    def _choose_output_docx(self) -> None:
        self._choose_save_path(self.output_docx, "Choose modified Word output", ".docx")

    def _choose_enw(self) -> None:
        self._choose_save_path(self.enw_file, "Choose EndNote Tagged Import output", ".enw")

    def _choose_nbib(self) -> None:
        self._choose_save_path(self.nbib_file, "Choose auxiliary PubMed/NLM output", ".nbib")

    def _choose_report(self) -> None:
        self._choose_save_path(self.report_file, "Choose report output", ".json")

    def _choose_save_path(self, variable: tk.StringVar, title: str, extension: str) -> None:
        filename = filedialog.asksaveasfilename(
            title=title,
            defaultextension=extension,
            filetypes=[(extension.upper().lstrip(".") + " files", f"*{extension}"), ("All files", "*.*")],
        )
        if filename:
            variable.set(filename)

    def _start_run(self) -> None:
        if not self.input_docx.get().strip():
            messagebox.showerror("Missing document", "Choose an input .docx file first.")
            return
        if not self.email.get().strip():
            messagebox.showerror("Missing email", "Enter the email address required by NCBI E-utilities.")
            return

        self._set_running(True)
        self._clear_log()
        self._append_log("Starting PMID2EndNote...\n")

        options = ProcessingOptions(
            input_docx=Path(self.input_docx.get().strip()),
            email=self.email.get().strip(),
            output_docx=_optional_path(self.output_docx.get()),
            nbib_file=_optional_path(self.nbib_file.get()),
            enw_file=_optional_path(self.enw_file.get()),
            report_file=_optional_path(self.report_file.get()),
            api_key=self.api_key.get().strip() or None,
            include_tables=self.include_tables.get(),
            include_comments=self.include_comments.get(),
            include_headers=self.include_headers.get(),
            include_footers=self.include_footers.get(),
            include_footnotes=self.include_footnotes.get(),
            dry_run=self.dry_run.get(),
            keep_pmid_text=self.keep_pmid_text.get(),
            mark_unresolved=self.mark_unresolved.get(),
            scan_parenthetical_pmids=self.scan_parenthetical_pmids.get(),
        )

        thread = threading.Thread(target=self._run_worker, args=(options,), daemon=True)
        thread.start()

    def _run_worker(self, options: ProcessingOptions) -> None:
        try:
            result = process_document(
                options,
                status_callback=lambda message: self._queue.put(("status", message)),
            )
            self._queue.put(("result", result))
        except Exception as exc:
            self._queue.put(("error", f"Unexpected error: {exc}"))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "status":
                    message = str(payload)
                    self.status.set(message)
                    self._append_log(message + "\n")
                elif kind == "result":
                    assert isinstance(payload, ProcessingResult)
                    self._handle_result(payload)
                elif kind == "error":
                    self._handle_error(str(payload))
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _handle_result(self, result: ProcessingResult) -> None:
        self._set_running(False)
        self._last_output_dir = result.output_docx.parent
        self.status.set("Finished." if result.exit_code == 0 else "Finished with errors.")
        for message in result.messages:
            self._append_log(message + "\n")

        if result.exit_code == 0:
            messagebox.showinfo("PMID2EndNote finished", "\n".join(result.messages[-3:]))
        else:
            messagebox.showerror("PMID2EndNote error", "\n".join(result.messages))

    def _handle_error(self, message: str) -> None:
        self._set_running(False)
        self.status.set("Unexpected error.")
        self._append_log(message + "\n")
        messagebox.showerror("PMID2EndNote error", message)

    def _open_output_folder(self) -> None:
        folder = self._last_output_dir
        if folder is None and self.input_docx.get().strip():
            folder = Path(self.input_docx.get().strip()).parent
        if folder is None:
            messagebox.showinfo("No folder yet", "Choose an input document first.")
            return
        subprocess.run(["open", str(folder)], check=False)

    def _set_running(self, running: bool) -> None:
        if self._run_button is not None:
            self._run_button.configure(state="disabled" if running else "normal")
        if running:
            self.status.set("Running...")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")


def _optional_path(value: str) -> Path | None:
    value = value.strip()
    return Path(value) if value else None


def main() -> int:
    if tk is None:
        print(
            "Tkinter is not available in this Python installation. On macOS, install a "
            "Python build with Tk support, or use macos/PMID2EndNote.command for the "
            "built-in macOS dialog launcher.",
            file=sys.stderr,
        )
        return 2

    root = tk.Tk()
    PMID2EndNoteGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

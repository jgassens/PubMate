"""macOS dialog-based launcher for PubMate.

This module avoids Tkinter so the packaged macOS app can run as a small native
wrapper around the same processing service used by the CLI.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import traceback

from pmid2endnote import sparkle
from pmid2endnote.settings import get_saved_email


APP_NAME = "PubMate"
SELF_TEST_MESSAGE = "PubMate macOS launcher self-test OK"
EMAIL_PROMPT = (
    "Enter the email address required by NCBI E-utilities. "
    "PubMate will remember this for future runs."
)
MISSING_EMAIL_MESSAGE = "PubMate needs an NCBI email address to fetch PubMed records."


def main(argv: list[str] | None = None) -> int:
    """Run the interactive macOS launcher."""

    args = argv if argv is not None else sys.argv[1:]
    if "--version" in args:
        from pmid2endnote import __version__

        print(f"{APP_NAME} {__version__}")
        return 0

    if "--self-test" in args:
        print(SELF_TEST_MESSAGE)
        return 0

    if "--sparkle-self-test" in args:
        print(sparkle.validate_sparkle_runtime())
        return 0

    if shutil.which("osascript") is None:
        print("PubMate macOS launcher requires osascript. Use the CLI instead.", file=sys.stderr)
        return 2

    sparkle_warning = sparkle.initialize_sparkle_updater()
    if sparkle_warning:
        print(sparkle_warning, file=sys.stderr)

    input_docx, launch_error = _docx_from_launch_args(args)
    if launch_error:
        _display_alert("PubMate could not open that file.", launch_error)
        return 1
    if input_docx is None:
        input_docx = _choose_docx()
    if input_docx is None:
        return 0

    saved_email = get_saved_email()
    if saved_email:
        email = saved_email
        print(f"Using saved PubMed email: {email}")
    else:
        email = _prompt_required_email()

    if email is None:
        return 0

    api_key = _prompt_text("Optional: enter an NCBI API key, or leave this blank.", optional=True)
    scan_parenthetical_pmids = _prompt_yes_no(
        "Scan raw parenthetical PMID citations like (6426050) or (6426050, 104929)?",
        default_yes=False,
    )
    skip_reference_section = _prompt_yes_no(
        "Ignore PMIDs/DOIs after a References/Bibliography heading?",
        default_yes=True,
    )

    print("Running PubMate...")
    print(f"Input: {input_docx}")

    try:
        result = process_document(
            _processing_options(
                input_docx=input_docx,
                email=email,
                api_key=api_key or None,
                scan_parenthetical_pmids=scan_parenthetical_pmids,
                skip_reference_section=skip_reference_section,
            ),
            status_callback=lambda message: print(message, flush=True),
        )
    except Exception as exc:
        traceback.print_exc()
        _display_alert("PubMate crashed.", f"{type(exc).__name__}: {exc}")
        return 2

    for message in result.messages:
        print(message)

    if result.exit_code == 0:
        instruction = _endnote_instructions().format(
            enw_file=result.enw_file,
            output_docx=result.output_docx,
            nbib_file=result.nbib_file,
        )
        _display_alert(
            "PubMate finished.",
            (
                f"Created:\n{result.output_docx}\n{result.enw_file}\n{result.report_file}\n\n"
                f"{instruction}"
            ),
        )
    else:
        detail = "\n".join(result.messages) or f"See the report: {result.report_file}"
        _display_alert("PubMate finished with an error.", detail)

    return result.exit_code


def _processing_options(**kwargs: object) -> object:
    from pmid2endnote.app import ProcessingOptions

    return ProcessingOptions(**kwargs)


def process_document(options: object, *, status_callback: object | None = None) -> object:
    from pmid2endnote.app import process_document as run_process_document

    return run_process_document(options, status_callback=status_callback)


def _endnote_instructions() -> str:
    from pmid2endnote.app import ENDNOTE_INSTRUCTIONS

    return ENDNOTE_INSTRUCTIONS


def _choose_docx() -> Path | None:
    script = """
try
  set chosenFile to choose file with prompt "Choose the Word .docx file to process with PubMate"
  return POSIX path of chosenFile
on error number -128
  return ""
end try
"""
    value = _run_applescript(script).strip()
    return Path(value) if value else None


def _docx_from_launch_args(args: list[str]) -> tuple[Path | None, str | None]:
    """Return a document path supplied by Finder/open, or a user-facing error."""

    candidates = [
        arg
        for arg in args
        if arg and not arg.startswith("--") and not arg.startswith("-psn_")
    ]
    if not candidates:
        return None, None
    if len(candidates) > 1:
        return None, "Drop one Word .docx file on PubMate at a time."

    input_docx = Path(candidates[0]).expanduser()
    if input_docx.suffix.lower() != ".docx":
        return None, f"PubMate only accepts Word .docx files:\n{input_docx}"
    if not input_docx.exists():
        return None, f"The dropped file could not be found:\n{input_docx}"
    if not input_docx.is_file():
        return None, f"The dropped item is not a file:\n{input_docx}"
    return input_docx, None


def _prompt_required_email() -> str | None:
    while True:
        email = _prompt_text(EMAIL_PROMPT)
        if email is None:
            return None

        email = email.strip()
        if email:
            return email

        if not _prompt_missing_email_retry():
            return None


def _prompt_text(prompt: str, *, optional: bool = False) -> str | None:
    buttons = '{"Skip", "Continue"}' if optional else '{"Cancel", "Continue"}'
    cancel_result = "" if optional else "__CANCELLED__"
    script = f"""
try
  set dialogResult to display dialog {_applescript_string(prompt)} default answer "" buttons {buttons} default button "Continue"
  return text returned of dialogResult
on error number -128
  return {_applescript_string(cancel_result)}
end try
"""
    value = _run_applescript(script).strip()
    return None if value == "__CANCELLED__" else value


def _prompt_missing_email_retry() -> bool:
    script = f"""
try
  set dialogResult to display alert {_applescript_string(MISSING_EMAIL_MESSAGE)} message "Enter an email address to continue, or close PubMate." buttons {{"Close PubMate", "Enter Email"}} default button "Enter Email" cancel button "Close PubMate"
  return button returned of dialogResult
on error number -128
  return "Close PubMate"
end try
"""
    return _run_applescript(script).strip() == "Enter Email"


def _prompt_yes_no(prompt: str, *, default_yes: bool) -> bool:
    default_button = "Yes" if default_yes else "No"
    cancel_result = "Yes" if default_yes else "No"
    script = f"""
try
  set dialogResult to display dialog {_applescript_string(prompt)} buttons {{"No", "Yes"}} default button {_applescript_string(default_button)}
  return button returned of dialogResult
on error number -128
  return {_applescript_string(cancel_result)}
end try
"""
    return _run_applescript(script).strip() == "Yes"


def _display_alert(title: str, message: str | None = None) -> None:
    if message:
        script = (
            f"display alert {_applescript_string(title)} "
            f"message {_applescript_string(message)}"
        )
    else:
        script = f"display alert {_applescript_string(title)}"
    _run_applescript(script, check=False)


def _run_applescript(script: str, *, check: bool = True) -> str:
    completed = subprocess.run(
        ["osascript"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        stderr = completed.stderr.strip() or "unknown AppleScript error"
        raise RuntimeError(stderr)
    return completed.stdout


def _applescript_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


if __name__ == "__main__":
    raise SystemExit(main())

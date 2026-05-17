"""Command-line interface for PMID2EndNote."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from pmid2endnote.app import ProcessingOptions, process_document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pmid2endnote",
        description=(
            "Scan a Word .docx for PubMed PMIDs and DOI identifiers, create an "
            "EndNote Tagged Import file, and replace identifier text with EndNote "
            "temporary citations."
        ),
    )
    parser.add_argument("input_docx", type=Path, help="Input Microsoft Word .docx file.")
    parser.add_argument(
        "--email",
        help=(
            "Email address required by NCBI E-utilities. If omitted, PMID2EndNote uses "
            "PMID2ENDNOTE_EMAIL or the saved first-run email setting."
        ),
    )
    parser.add_argument("--output", type=Path, help="Modified .docx output path.")
    parser.add_argument("--nbib", type=Path, help="Auxiliary PubMed/NLM .nbib output path.")
    parser.add_argument("--enw", type=Path, help="Canonical EndNote Tagged Import .enw output path.")
    parser.add_argument("--report", type=Path, help="JSON processing report path.")
    parser.add_argument("--api-key", help="Optional NCBI API key. Defaults to NCBI_API_KEY.")
    parser.add_argument(
        "--include-footnotes",
        action="store_true",
        help="Request footnote processing. This implementation records a limitation warning.",
    )
    parser.add_argument("--include-headers", action="store_true", help="Scan and replace in headers.")
    parser.add_argument("--include-footers", action="store_true", help="Scan and replace in footers.")
    parser.add_argument(
        "--include-tables",
        dest="include_tables",
        action="store_true",
        default=True,
        help="Scan and replace in tables. Tables are included by default.",
    )
    parser.add_argument(
        "--no-include-tables",
        dest="include_tables",
        action="store_false",
        help="Skip table text.",
    )
    parser.add_argument(
        "--include-comments",
        dest="include_comments",
        action="store_true",
        default=True,
        help=(
            "Scan Word comments and insert temporary citations at their document anchors. "
            "Comments are included by default."
        ),
    )
    parser.add_argument(
        "--no-include-comments",
        dest="include_comments",
        action="store_false",
        help="Skip Word comments.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the report and planned replacements without writing .docx or .nbib outputs.",
    )
    parser.add_argument(
        "--scan-parenthetical-pmids",
        action="store_true",
        help=(
            "Also scan raw digit-only parenthetical PMID placeholders like "
            "(6426050) or (6426050, 104929). Disabled by default."
        ),
    )
    parser.add_argument(
        "--scan-dois",
        dest="scan_dois",
        action="store_true",
        default=True,
        help="Scan DOI labels and doi.org URLs. Enabled by default.",
    )
    parser.add_argument(
        "--no-scan-dois",
        dest="scan_dois",
        action="store_false",
        help="Disable DOI label and doi.org URL scanning.",
    )
    parser.add_argument(
        "--scan-bare-dois",
        action="store_true",
        help="Also scan bare DOI strings like 10.1000/example. Disabled by default.",
    )
    parser.add_argument(
        "--skip-reference-section",
        dest="skip_reference_section",
        action="store_true",
        default=True,
        help=(
            "Ignore PMID/DOI identifiers after a standalone References/Bibliography "
            "heading. Enabled by default."
        ),
    )
    parser.add_argument(
        "--no-skip-reference-section",
        dest="skip_reference_section",
        action="store_false",
        help="Process PMID/DOI identifiers even after a References/Bibliography heading.",
    )
    parser.add_argument(
        "--backup",
        dest="create_backup",
        action="store_true",
        help=(
            "Also create a safety copy of the input .docx before writing the separate "
            ".endnote.docx output. Disabled by default because the input file is not modified."
        ),
    )
    parser.add_argument(
        "--doi-source",
        default="auto",
        choices=["auto", "pubmed-first", "crossref", "datacite", "content-negotiation"],
        help="DOI metadata lookup strategy. Default: auto.",
    )
    parser.add_argument(
        "--import-format",
        default="enw",
        choices=["enw"],
        help="Canonical EndNote import format. Only enw is supported.",
    )
    parser.add_argument(
        "--keep-pmid-text",
        action="store_true",
        help="Keep the original PMID block before the generated temporary citation.",
    )
    parser.add_argument(
        "--mark-unresolved",
        action="store_true",
        help="Replace unresolved PMID blocks with an explicit unresolved marker.",
    )
    parser.add_argument(
        "--style",
        default="temporary",
        choices=["temporary"],
        help="Citation style to emit. Only EndNote temporary citations are supported.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


def run(args: argparse.Namespace) -> int:
    result = process_document(
        ProcessingOptions(
            input_docx=args.input_docx,
            email=args.email,
            output_docx=args.output,
            nbib_file=args.nbib,
            enw_file=args.enw,
            report_file=args.report,
            api_key=args.api_key,
            include_tables=args.include_tables,
            include_comments=args.include_comments,
            include_headers=args.include_headers,
            include_footers=args.include_footers,
            include_footnotes=args.include_footnotes,
            dry_run=args.dry_run,
            keep_pmid_text=args.keep_pmid_text,
            mark_unresolved=args.mark_unresolved,
            scan_parenthetical_pmids=args.scan_parenthetical_pmids,
            scan_dois=args.scan_dois,
            scan_bare_dois=args.scan_bare_dois,
            skip_reference_section=args.skip_reference_section,
            create_backup=args.create_backup,
            doi_source=args.doi_source,
            import_format=args.import_format,
        )
    )

    stream = sys.stderr if result.exit_code else sys.stdout
    for message in result.messages:
        print(message, file=stream)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

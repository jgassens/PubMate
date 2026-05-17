# PubMate

PubMate is a small, practical bridge between manuscript drafting and Clarivate EndNote. Its first tool, `pmid2endnote`, scans a Microsoft Word `.docx` document for PubMed identifiers, fetches the matching records from NCBI PubMed, and creates a Word document containing EndNote temporary citations that EndNote can later convert into real Cite While You Write citations.

The goal is simple: let authors draft with lightweight citation placeholders, then hand EndNote both the rewritten document and the import records it needs to format the manuscript properly.

> PubMate is for Clarivate EndNote. It does not create Microsoft Word endnotes, and it does not attempt to hand-write EndNote CWYW field codes.

## Current Status

The current working path supports PMIDs plus DOI labels and DOI resolver URLs:

1. Scan a `.docx` for explicit PMID markers.
2. Scan DOI labels and `doi.org` URLs by default.
3. Resolve PMIDs with NCBI E-utilities.
4. Resolve DOIs by checking PubMed first, then falling back to DOI metadata services.
5. Write a canonical EndNote Tagged Import `.endnote-import.enw` file.
6. Replace identifier markers in the Word document with EndNote temporary citations.
7. Write a JSON report describing every identifier, replacement, warning, and error.

By default, PubMate ignores PMIDs and DOIs in the document's final reference section. Once it sees a standalone heading such as `References`, `Bibliography`, `Works Cited`, `Literature Cited`, or `References Cited`, identifiers from that point to the end of the document are reported as skipped and are not fetched, imported, or replaced.

PubMate also supports a macOS-friendly launcher flow and scans Word comments by default. When a PMID appears in a Word comment, the generated EndNote temporary citation is inserted at the comment anchor in the actual document body, because EndNote cannot format citations that live only inside comment balloons.

The input `.docx` is not modified. PubMate writes a separate `.endnote.docx` output file instead. Backup copies are therefore disabled by default and are only created when requested with `--backup`.

`.references.nbib` may also be generated for PubMed records, but it is auxiliary. `.endnote-import.enw` is the file to import into EndNote.

## What PubMate Converts

By default, `pmid2endnote` scans only clearly labeled PMID text:

```text
PMID: 12345678
PMID 12345678
PMID#12345678
PMIDs: 12345678, 23456789
PMIDs 12345678; 23456789
pmid:12345678
```

It intentionally ignores arbitrary digit strings. That keeps it from converting years, page numbers, figure numbers, grant numbers, and other numeric text that happen to look citation-like.

PubMate also scans DOI labels and DOI URLs by default:

```text
DOI: 10.1021/acs.joc.0c00770
doi:10.1038/s41586-020-2649-2
https://doi.org/10.1016/j.cell.2020.04.011
http://dx.doi.org/10.1002/anie.202000000
```

Bare DOI scanning is available with `--scan-bare-dois`.

For manuscripts that use raw PMID placeholders, parenthetical scanning is available as an opt-in mode:

```text
(6426050)
(6426050, 104929)
(6426050;9036716)
```

Use this mode carefully in numeric-heavy documents and review the generated report.

## Reference Section Skipping

Manuscripts often contain PMIDs and DOIs in an existing reference list. Those are bibliographic metadata, not citation placeholders, so converting them would create duplicate citations in the reference section.

PubMate skips reference-section identifiers by default. The skipped region starts at a standalone heading such as:

```text
References
Bibliography
Works Cited
Literature Cited
References Cited
1. References
VII. References
```

The skipped region continues to the end of the document. Skipped identifiers are written to `skipped_identifiers` in the JSON report with their text, normalized ID, source, location, and reason. They are intentionally excluded from PubMed/DOI lookup, `.endnote-import.enw`, `.references.nbib`, Word replacements, and `identifier_statuses`.

If a document has an unusual structure and you really do want to process identifiers after a reference heading, use:

```bash
python -m pmid2endnote manuscript.docx --email name@university.edu --no-skip-reference-section
```

## EndNote Handoff: Why Two Files Are Required

EndNote does not get references from the Word document alone. Temporary citations such as this are only placeholders:

```text
{Ward, 1983, PMID-6416259}
```

For EndNote to format them, the matching references must already exist in the open EndNote library. That is why PubMate creates both:

```text
manuscript.endnote.docx
manuscript.endnote-import.enw
manuscript.references.nbib
```

The `.endnote.docx` file contains temporary citation text. The `.endnote-import.enw` file contains the EndNote import records and is the canonical file to import. The `.references.nbib` file is auxiliary for PubMed records.

After running PubMate:

1. Open EndNote.
2. Open the target EndNote library.
3. Import `manuscript.endnote-import.enw` using the EndNote Import option.
4. Open `manuscript.endnote.docx` in Microsoft Word desktop.
5. Run EndNote > Update Citations and Bibliography.

## Why Temporary Citations Instead of CWYW Fields

EndNote Cite While You Write citations inside Word are not ordinary text. They are managed field-code-backed objects owned by EndNote. Manually generating those fields is brittle and can corrupt documents or create citations EndNote cannot update.

PubMate writes EndNote temporary citations instead:

```text
{Smith, 2024, PMID-12345678}
```

PubMate uses EndNote's "Any Text" temporary citation form:

```text
{Author, Year, citation_key}
```

It does not use the record-number form:

```text
{Author, Year #RecordNumber}
```

That distinction matters. App-controlled keys such as `PMID-6416259` and `DOI-5E8A9C2D4F91` are matching/search text, not printable citation suffixes.

## Installation

Clone the repository:

```bash
git clone https://github.com/jgassens/PubMate.git
cd PubMate
```

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the package:

```bash
pip install -e .
```

For development and tests:

```bash
pip install -e ".[test]"
pytest
```

## Quick Start

Run the CLI on a Word document:

```bash
python -m pmid2endnote manuscript.docx --email name@university.edu
```

For `manuscript.docx`, PubMate writes:

```text
manuscript.references.nbib
manuscript.endnote-import.enw
manuscript.endnote.docx
manuscript.pmid2endnote.report.json
```

Then import the `.endnote-import.enw` file into EndNote using the EndNote Import option, open the generated `.endnote.docx`, and run EndNote > Update Citations and Bibliography.

## CLI Reference

Basic form:

```bash
python -m pmid2endnote input.docx --email user@example.edu
```

Useful options:

```text
--output output.docx
--nbib output.references.nbib
--enw output.endnote-import.enw
--report output.report.json
--api-key NCBI_API_KEY
--include-headers
--include-footers
--include-footnotes
--include-tables
--no-include-tables
--include-comments
--no-include-comments
--dry-run
--scan-parenthetical-pmids
--scan-dois
--no-scan-dois
--scan-bare-dois
--no-skip-reference-section
--backup
--doi-source auto|pubmed-first|crossref|datacite|content-negotiation
--import-format enw
--keep-pmid-text
--mark-unresolved
--style temporary
```

Tables, Word comments, and reference-section skipping are included by default. Headers and footers are opt-in. Footnotes are not safely exposed by `python-docx`; if requested, PubMate records a limitation warning in the report.

The original input document is left untouched. `--backup` is available if you want an extra safety copy, but it is not needed for the normal workflow because PubMate writes changes only to the separate `.endnote.docx` file.

## macOS Launcher

This repository includes a double-clickable launcher:

```text
macos/PMID2EndNote.command
```

The launcher uses built-in macOS dialogs to choose a Word file and enter the PubMed email/API key. It runs the same processing code as the CLI.

Each run also asks whether to scan raw parenthetical PMID placeholders and whether to ignore identifiers after a References/Bibliography heading. The reference-section skip prompt defaults to Yes.

The first time PubMate needs PubMed access, it asks for an email address and saves it here:

```text
~/Library/Application Support/PMID2EndNote/settings.json
```

Future runs reuse that saved email. You can override it from the CLI with `--email`, or set `PMID2ENDNOTE_EMAIL` in your shell.

## macOS App Distribution

For a more Mac-like handoff, PubMate can be packaged as a double-clickable app bundle:

```bash
pip install -e ".[test,macos]"
macos/build_distribution.sh
```

This creates:

```text
dist/PubMate.app
dist/PubMate-<version>-macos-universal2.dmg
```

The app uses native macOS dialogs and the same processing engine as the CLI. The default release build is universal2, so the same DMG runs on Apple Silicon and Intel Macs. For universal builds, use a universal Python runtime such as the python.org framework build; an arm64-only Homebrew Python can only create an Apple Silicon-only app.

The default build is ad-hoc signed for local testing. For public distribution, build with a Developer ID certificate and notarize the DMG:

```bash
MACOS_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" macos/build_distribution.sh
MACOS_NOTARY_PROFILE=pubmate-notary macos/notarize_distribution.sh dist/PubMate-<version>-macos-universal2.dmg
```

The DMG is the primary distribution artifact. The notarization helper also supports App Store Connect API key credentials through `MACOS_NOTARY_KEY`, `MACOS_NOTARY_KEY_ID`, and `MACOS_NOTARY_ISSUER`; those credentials submit to Apple, but the app still needs a Developer ID Application certificate for public notarized distribution.

The packaged app includes Sparkle auto-update support. PubMate checks the GitHub-hosted appcast at:

```text
https://jgassens.github.io/PubMate/appcast.xml
```

When you publish a new version, build and notarize the DMG, upload that DMG to the matching GitHub release, and regenerate the appcast:

```bash
macos/prepare_sparkle_appcast.sh
```

Commit and publish the updated `docs/appcast.xml` through GitHub Pages. Sparkle uses that appcast plus the signed DMG to decide whether installed copies should update.

See [docs/macos-distribution.md](docs/macos-distribution.md) for the full release checklist.

## Word Comments

PubMate scans Word comments by default.

If a comment contains a PMID marker and the comment is attached to a word or phrase in the manuscript, PubMate inserts the generated temporary citation at that comment anchor in the document text.

For example, if a comment attached to the word `stimulation` contains:

```text
PMID: 3139738; PMID: 3126745
```

the output document places the temporary citation next to the anchored manuscript text, not inside the comment balloon:

```text
stimulation {Harth, 1988, PMID-3139738;Wolf, 1988, PMID-3126745}
```

This matters because EndNote scans the document body for temporary citations. It will not reliably format references that exist only inside Word comments.

## Parenthetical PMID Scanning

Parenthetical PMID scanning is disabled by default:

```bash
python -m pmid2endnote manuscript.docx --email name@university.edu --scan-parenthetical-pmids
```

When enabled, PubMate accepts only digit-only parenthetical PMID lists such as:

```text
(6426050)
(6426050, 104929)
(6426050;9036716)
```

It rejects ambiguous parentheticals such as:

```text
(1983)
(12-15)
(Fig. 2)
(6426050a)
(6426050 and 104929)
```

Review the JSON report before using parenthetical mode in a manuscript with lots of numeric parentheticals.

## Unresolved Identifiers

If a PMID cannot be resolved through PubMed, PubMate leaves it unchanged by default and records the issue in the report.

When a multi-PMID block contains both resolved and unresolved PMIDs, PubMate puts only resolved records inside the EndNote temporary citation braces. Unresolved IDs are kept outside the braces:

```text
{Petersen, 1983, PMID-6426050} [unresolved PMID: 999999999]
```

That protects EndNote from leaving an entire multi-citation group unformatted because one item could not be matched.

Use `--mark-unresolved` to explicitly mark unresolved-only blocks in the Word output.

## NCBI/PubMed Behavior

PubMate uses NCBI E-utilities, not screen scraping.

It sends PubMed EFetch requests to:

```text
https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi
```

Requests include:

```text
db=pubmed
id=<comma-separated PMIDs>
tool=pmid2endnote
email=<user email>
api_key=<optional>
```

The app fetches PubMed XML for metadata parsing and MEDLINE text for the auxiliary `.nbib` file. PMIDs are batched, POST is used for EFetch batches, and transient HTTP failures are retried with exponential backoff.

For DOI identifiers, PubMate first searches PubMed’s Article Identifier field. If a DOI resolves to a verified PubMed PMID, the citation key is the PubMed identity, for example `PMID-6416259`. If no PubMed record verifies, PubMate falls back to DOI metadata and generates a safe DOI citation key such as `DOI-5E8A9C2D4F91`.

## Processing Report

Each run writes:

```text
manuscript.pmid2endnote.report.json
```

The report includes:

- input and output paths
- total PMID and identifier occurrences
- unique PMIDs in first-seen document order
- unique identifiers in first-seen document order
- reference-section skip settings and detected heading location
- skipped reference-section identifiers
- resolved and unresolved PMIDs
- per-identifier status information
- per-PMID status information
- replacement records with document locations
- warnings
- errors

If PubMed fetching or import-file validation fails, PubMate writes the report and does not modify the Word document.

## Formatting Limits

The current Word implementation uses `python-docx`. It is intentionally conservative.

What it does well:

- preserves paragraph-level style
- attempts to preserve run-level formatting where possible
- replaces exact PMID blocks only
- skips unsafe field-code-like paragraphs instead of touching them
- inserts comment-derived citations at the comment anchor in document text

Known limits:

- footnotes are not modified in the current implementation
- complex run layouts may not preserve every character-level formatting detail
- existing EndNote field-code content is not rewritten
- heavily customized Word documents should be reviewed carefully after conversion

Always inspect the generated `.endnote.docx` before submission.

## DOI and Unified Import

The mixed-identifier workflow is:

```text
PMID -> PubMed -> EndNote import record
DOI  -> PubMed PMID lookup first -> DOI metadata fallback -> EndNote import record
```

For mixed PMID and DOI documents, PubMate uses `.endnote-import.enw` as the canonical import file because EndNote Tagged Import can carry app-controlled citation keys, PubMed identifiers, DOI fields, URLs, and DOI-only metadata.

The import choice is:

> PMID2EndNote uses `.endnote-import.enw` as the canonical import file because it supports both PubMed PMID records and DOI-only records that do not exist in PubMed. `.references.nbib` may also be generated for PubMed records, but it is auxiliary. Import the `.enw` file into EndNote using the EndNote Import option.

The temporary citation key format is:

```text
{Ward, 1983, PMID-6416259}
{Smith, 2023, DOI-5E8A9C2D4F91}
```

Raw DOI strings will not be used as temporary citation keys.

## Development

Run tests:

```bash
pytest
```

Run one test file:

```bash
pytest tests/test_scanner.py
```

Run the CLI locally:

```bash
python -m pmid2endnote path/to/manuscript.docx --email name@university.edu --dry-run
```

The package layout is:

```text
src/pmid2endnote/
  app.py       shared processing service for CLI and GUI
  cli.py       argparse command-line interface
  scanner.py   PMID and identifier scanning
  pubmed.py    NCBI E-utilities client and PubMed XML parsing
  endnote.py   EndNote temporary citation formatting
  word.py      python-docx scanning and replacement
  report.py    JSON report helpers
  settings.py  saved macOS email settings
```

## Contributing

Contributions are welcome, especially in these areas:

- deeper DOI provider edge-case coverage
- stronger Word formatting preservation
- footnote support
- packaged macOS app distribution
- additional real-world `.docx` fixtures
- EndNote workflow documentation from different EndNote versions

Please keep the main design constraint intact: PubMate should use EndNote-supported temporary citations and import files, not manually generated CWYW field codes.

## License

No license has been selected yet. Until a license is added, all rights are reserved by the repository owner.

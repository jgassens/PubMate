#!/bin/zsh
set -e

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
cd "$PROJECT_DIR"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

export PYTHONPATH="$PROJECT_DIR/src"

SAVED_EMAIL="$("$PYTHON" -c 'from pmid2endnote.settings import get_saved_email; print(get_saved_email() or "")' 2>/dev/null || true)"

INPUT_DOCX="$(osascript <<'APPLESCRIPT'
try
  set chosenFile to choose file with prompt "Choose the Word .docx file to process with PMID2EndNote"
  return POSIX path of chosenFile
on error number -128
  return ""
end try
APPLESCRIPT
)"

if [[ -z "$INPUT_DOCX" ]]; then
  exit 0
fi

if [[ -n "$SAVED_EMAIL" ]]; then
  EMAIL="$SAVED_EMAIL"
  echo "Using saved PubMed email: $EMAIL"
else
  EMAIL="$(osascript <<'APPLESCRIPT'
try
  set dialogResult to display dialog "Enter the email address required by NCBI E-utilities. PMID2EndNote will remember this for future runs." default answer "" buttons {"Cancel", "Continue"} default button "Continue"
  return text returned of dialogResult
on error number -128
  return ""
end try
APPLESCRIPT
  )"
fi

if [[ -z "$EMAIL" ]]; then
  osascript -e 'display alert "PMID2EndNote needs an NCBI email address to fetch PubMed records."'
  exit 1
fi

"$PYTHON" -c 'from pmid2endnote.settings import save_email; import sys; save_email(sys.argv[1])' "$EMAIL" 2>/dev/null || true

API_KEY="$(osascript <<'APPLESCRIPT'
try
  set dialogResult to display dialog "Optional: enter an NCBI API key, or leave this blank." default answer "" buttons {"Skip", "Continue"} default button "Skip"
  return text returned of dialogResult
on error number -128
  return ""
end try
APPLESCRIPT
)"

SCAN_PARENTHESES="$(osascript <<'APPLESCRIPT'
try
  set dialogResult to display dialog "Scan raw parenthetical PMID citations like (6426050) or (6426050, 104929)?" buttons {"No", "Yes"} default button "No"
  return button returned of dialogResult
on error number -128
  return "No"
end try
APPLESCRIPT
)"

echo "Running PMID2EndNote..."
echo "Input: $INPUT_DOCX"
echo

ARGS=("$INPUT_DOCX" "--email" "$EMAIL")

if [[ -n "$API_KEY" ]]; then
  ARGS+=("--api-key" "$API_KEY")
fi

if [[ "$SCAN_PARENTHESES" == "Yes" ]]; then
  ARGS+=("--scan-parenthetical-pmids")
fi

set +e
"$PYTHON" -m pmid2endnote "${ARGS[@]}"
STATUS=$?
set -e
echo
if [[ "$STATUS" -eq 0 ]]; then
  osascript -e 'display alert "PMID2EndNote finished. Open EndNote and import the generated .endnote-import.enw file into the target EndNote library using the EndNote Import option. Then open the generated .endnote.docx in Word desktop and run EndNote > Update Citations and Bibliography."'
else
  osascript -e 'display alert "PMID2EndNote finished with an error. Check the Terminal output and the generated report JSON."'
fi

echo "Press Return to close this window."
read -r
exit "$STATUS"

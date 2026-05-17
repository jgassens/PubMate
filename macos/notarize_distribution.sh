#!/bin/zsh
set -euo pipefail

KEY_PATH="${MACOS_NOTARY_KEY:-}"
KEY_ID="${MACOS_NOTARY_KEY_ID:-${APP_STORE_CONNECT_API_KEY_ID:-}}"
ISSUER_ID="${MACOS_NOTARY_ISSUER:-${APP_STORE_CONNECT_ISSUER_ID:-}}"
PROFILE="${MACOS_NOTARY_PROFILE:-}"
SHOW_HELP=0

POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --key)
      KEY_PATH="$2"
      shift 2
      ;;
    --key-id)
      KEY_ID="$2"
      shift 2
      ;;
    --issuer)
      ISSUER_ID="$2"
      shift 2
      ;;
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    -h|--help)
      SHOW_HELP=1
      break
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

if [[ "$SHOW_HELP" -eq 1 || "${#POSITIONAL[@]}" -lt 1 ]]; then
  cat <<'HELP'
Submit a PubMate DMG to Apple notarization and staple the ticket.

Usage:
  MACOS_NOTARY_PROFILE=profile-name macos/notarize_distribution.sh dist/PubMate-0.1.0-macos-arm64.dmg
  macos/notarize_distribution.sh dist/PubMate-0.1.0-macos-arm64.dmg --key /path/AuthKey_KEYID.p8 --key-id KEYID --issuer ISSUER-UUID

Prerequisites:
  1. Build with a Developer ID Application identity:
       MACOS_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" macos/build_distribution.sh
  2. Either store notarization credentials once:
       xcrun notarytool store-credentials profile-name
     and set MACOS_NOTARY_PROFILE, or provide App Store Connect API key values:
       MACOS_NOTARY_KEY=/path/AuthKey_KEYID.p8
       MACOS_NOTARY_KEY_ID=KEYID
       MACOS_NOTARY_ISSUER=ISSUER-UUID
HELP
  if [[ "$SHOW_HELP" -eq 1 ]]; then
    exit 0
  fi
  exit 2
fi

DMG_PATH="${POSITIONAL[1]}"

if [[ ! -f "$DMG_PATH" ]]; then
  echo "DMG not found: $DMG_PATH" >&2
  exit 1
fi

if [[ -n "$PROFILE" ]]; then
  AUTH_ARGS=(--keychain-profile "$PROFILE")
elif [[ -n "$KEY_PATH" && -n "$KEY_ID" && -n "$ISSUER_ID" ]]; then
  if [[ ! -f "$KEY_PATH" ]]; then
    echo "App Store Connect API key file not found: $KEY_PATH" >&2
    exit 1
  fi
  AUTH_ARGS=(--key "$KEY_PATH" --key-id "$KEY_ID" --issuer "$ISSUER_ID")
else
  echo "Provide MACOS_NOTARY_PROFILE or MACOS_NOTARY_KEY, MACOS_NOTARY_KEY_ID, and MACOS_NOTARY_ISSUER." >&2
  exit 1
fi

SUBMIT_JSON="$(mktemp -t pubmate-notary-submit.XXXXXX.json)"
cleanup() {
  rm -f "$SUBMIT_JSON"
}
trap cleanup EXIT

xcrun notarytool submit "$DMG_PATH" "${AUTH_ARGS[@]}" --wait --output-format json > "$SUBMIT_JSON"
cat "$SUBMIT_JSON"

SUBMISSION_ID="$(/usr/bin/python3 - "$SUBMIT_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
print(data.get("id", ""))
PY
)"
SUBMISSION_STATUS="$(/usr/bin/python3 - "$SUBMIT_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
print(data.get("status", ""))
PY
)"

if [[ "$SUBMISSION_STATUS" != "Accepted" ]]; then
  echo "Notarization failed with status: ${SUBMISSION_STATUS:-unknown}" >&2
  if [[ -n "$SUBMISSION_ID" ]]; then
    echo "Fetching notarization log for $SUBMISSION_ID..." >&2
    xcrun notarytool log "$SUBMISSION_ID" "${AUTH_ARGS[@]}" || true
  fi
  exit 1
fi

xcrun stapler staple "$DMG_PATH"
xcrun stapler validate "$DMG_PATH"
spctl --assess --type open --context context:primary-signature --verbose "$DMG_PATH"

echo "Notarized and stapled: $DMG_PATH"

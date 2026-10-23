#!/bin/zsh
# Collect the signing material a PubMate release needs and store it as GitHub
# Actions secrets for .github/workflows/release-macos.yml.
#
# Run it on the Mac that already builds releases by hand:
#
#   macos/setup_ci_secrets.sh
#
# It reads the Developer ID certificate, the Sparkle signing key and the
# notarization key, checks each one actually works before uploading, and
# never prints a secret value. Re-running it is safe; secrets are replaced.

set -euo pipefail

REPO="${PUBMATE_CI_REPO:-jgassens/PubMate}"
WORKDIR="$(mktemp -d -t pubmate-ci-secrets)"
P12_PASSWORD=""
CLEANUP_PATHS=("$WORKDIR")

cleanup() {
  for path in "${CLEANUP_PATHS[@]}"; do
    [[ -e "$path" ]] || continue
    find "$path" -type f -exec rm -f {} + 2>/dev/null || true
    rm -rf "$path" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

bold() { print -P "%B$1%b"; }
step() { print -P "\n%F{blue}==>%f %B$1%b"; }
ok()   { print -P "  %F{green}ok%f  $1"; }
warn() { print -P "  %F{yellow}!!%f  $1"; }
die()  { print -P "\n%F{red}error%f $1" >&2; exit 1; }

ask() {
  # ask <prompt> <variable-name>
  local prompt="$1" varname="$2" answer=""
  while [[ -z "$answer" ]]; do
    read -r "answer?  $prompt: "
  done
  typeset -g "$varname=$answer"
}

ask_secret() {
  local prompt="$1" varname="$2" answer=""
  while [[ -z "$answer" ]]; do
    read -rs "answer?  $prompt: "
    print ""
  done
  typeset -g "$varname=$answer"
}

# ---------------------------------------------------------------- preflight

step "Checking this machine"

[[ "$(uname -s)" == "Darwin" ]] \
  || die "This has to run on your Mac; the keys live in its Keychain."
ok "macOS"

if ! command -v gh >/dev/null 2>&1; then
  die "The GitHub CLI is not installed. Install it, then re-run:

    brew install gh
    gh auth login"
fi
ok "GitHub CLI installed"

if ! gh auth status >/dev/null 2>&1; then
  die "The GitHub CLI is not signed in. Run this, then re-run this script:

    gh auth login"
fi
ok "GitHub CLI signed in"

if ! gh repo view "$REPO" >/dev/null 2>&1; then
  die "Cannot reach $REPO with your GitHub login."
fi
ok "can reach $REPO"

# ------------------------------------------------------- signing identity

step "Finding your Developer ID certificate"

typeset -a identities
identities=("${(@f)$(security find-identity -v -p codesigning \
  | grep 'Developer ID Application' \
  | sed -E 's/^[[:space:]]*[0-9]+\)[[:space:]]+[0-9A-F]+[[:space:]]+"(.*)"$/\1/')}")

if (( ${#identities[@]} == 0 )); then
  die "No \"Developer ID Application\" certificate found in your Keychain.
Open Xcode > Settings > Accounts and download your certificates first."
fi

if (( ${#identities[@]} == 1 )); then
  SIGN_IDENTITY="${identities[1]}"
else
  bold "  More than one signing certificate found:"
  local i=1
  for identity in "${identities[@]}"; do
    print "    $i) $identity"
    (( i++ ))
  done
  local choice=""
  ask "Which number should releases use?" choice
  SIGN_IDENTITY="${identities[$choice]}"
fi
ok "using: $SIGN_IDENTITY"

# ----------------------------------------------------------- certificate

step "Exporting the certificate and its private key"

print "  A .p12 file holds the certificate plus the private key that signs
  PubMate. Keychain Access has to produce it; macOS will not let a script
  read a private key out without your say-so."
print ""
print "  In Keychain Access:"
print "    1. Left sidebar: click  login"
print "    2. Click the  My Certificates  tab"
print "    3. Click this row:  $SIGN_IDENTITY"
print "    4. Right-click it, choose  Export \"Developer ID Application...\""
print "    5. Save it to your Desktop, keep the format as  .p12"
print "    6. Give it a password and remember it, then allow the Keychain prompt"
print ""

local open_now=""
read -r "open_now?  Open Keychain Access now? [Y/n] "
if [[ -z "$open_now" || "$open_now" == [Yy]* ]]; then
  open -a "Keychain Access" 2>/dev/null || warn "Could not open Keychain Access."
fi

print ""
print "  Waiting for a .p12 to appear on your Desktop. Ctrl-C to stop."

P12_PATH=""
while :; do
  P12_PATH="$(find "$HOME/Desktop" -maxdepth 1 -name '*.p12' -type f 2>/dev/null \
    | head -1 || true)"
  [[ -n "$P12_PATH" ]] && break
  sleep 2
done
ok "found: ${P12_PATH:t}"

ask_secret "Password you just set on that .p12" P12_PASSWORD

# Verify the file and password by doing exactly what the workflow does.
step "Checking the certificate actually signs"

VERIFY_KEYCHAIN="$WORKDIR/verify.keychain-db"
VERIFY_PASSWORD="$(uuidgen)"
security create-keychain -p "$VERIFY_PASSWORD" "$VERIFY_KEYCHAIN" >/dev/null
CLEANUP_PATHS+=("$VERIFY_KEYCHAIN")
security unlock-keychain -p "$VERIFY_PASSWORD" "$VERIFY_KEYCHAIN" >/dev/null

if ! security import "$P12_PATH" -k "$VERIFY_KEYCHAIN" -P "$P12_PASSWORD" \
      -T /usr/bin/codesign >/dev/null 2>&1; then
  security delete-keychain "$VERIFY_KEYCHAIN" 2>/dev/null || true
  die "That .p12 and password did not import.
Either the password is wrong, or the export did not include the private key.
In Keychain Access the certificate must have a key nested under its triangle."
fi

if ! security find-identity -v -p codesigning "$VERIFY_KEYCHAIN" \
     | grep -q 'Developer ID Application'; then
  security delete-keychain "$VERIFY_KEYCHAIN" 2>/dev/null || true
  die "That .p12 imported but holds no Developer ID signing identity.
Re-export from the  My Certificates  tab, not  Certificates."
fi
security delete-keychain "$VERIFY_KEYCHAIN" 2>/dev/null || true
ok "certificate and password verified"

base64 -i "$P12_PATH" > "$WORKDIR/cert.b64"
ok "encoded for upload"

# --------------------------------------------------------- sparkle ed key

step "Reading your Sparkle signing key"

SPARKLE_KEY=""
if SPARKLE_KEY="$(security find-generic-password \
    -s 'https://sparkle-project.org' -a ed25519 -w 2>/dev/null)"; then
  ok "found in your Keychain"
elif SPARKLE_KEY="$(security find-generic-password \
    -s 'https://sparkle-project.org' -w 2>/dev/null)"; then
  ok "found in your Keychain"
else
  warn "Not found in the Keychain under the usual name."
  print "  Sparkle can write it out for you. If you have the key file already,
  give its path. Otherwise find generate_keys in your PubMate checkout and run:
      ./generate_keys -x sparkle_private.txt"
  print ""
  local key_path=""
  ask "Path to a file holding the Sparkle private key" key_path
  key_path="${key_path/#\~/$HOME}"
  [[ -f "$key_path" ]] || die "No such file: $key_path"
  SPARKLE_KEY="$(<"$key_path")"
fi

# The key is a base64 blob on one line; anything else means we grabbed the
# wrong item and the appcast would be signed with nonsense.
SPARKLE_KEY="${SPARKLE_KEY//[[:space:]]/}"
if (( ${#SPARKLE_KEY} < 40 )) || [[ -n "${SPARKLE_KEY//[A-Za-z0-9+\/=]/}" ]]; then
  die "That does not look like a Sparkle private key.
Expected one line of base64. Check the file and re-run."
fi
printf '%s' "$SPARKLE_KEY" > "$WORKDIR/sparkle.key"
ok "Sparkle key looks valid"

# ---------------------------------------------------------- notary api key

step "Finding your notarization key"

NOTARY_P8=""
typeset -a candidates
candidates=("${(@f)$(find "$HOME/private_keys" "$HOME/Downloads" "$HOME/Desktop" \
  -maxdepth 1 -name 'AuthKey_*.p8' -type f 2>/dev/null || true)}")
candidates=("${(@)candidates:#}")

if (( ${#candidates[@]} >= 1 )); then
  NOTARY_P8="${candidates[1]}"
  ok "found: ${NOTARY_P8:t}"
else
  warn "No AuthKey_*.p8 found."
  print "  Notarizing from a server needs an App Store Connect API key. A saved
  notarytool profile on this Mac cannot be reused, so make a key once:"
  print ""
  print "    1. appstoreconnect.apple.com > Users and Access > Integrations"
  print "    2. Team Keys tab > + > name it  PubMate CI  > Access: Developer"
  print "    3. Generate, then Download the .p8 (Apple allows one download)"
  print "    4. Note the Key ID next to it and the Issuer ID above the list"
  print ""
  local p8_path=""
  ask "Path to the downloaded .p8 file" p8_path
  p8_path="${p8_path/#\~/$HOME}"
  [[ -f "$p8_path" ]] || die "No such file: $p8_path"
  NOTARY_P8="$p8_path"
fi

# Apple names the file AuthKey_<KEYID>.p8, so the Key ID comes for free.
NOTARY_KEY_ID="${${NOTARY_P8:t:r}#AuthKey_}"
if [[ -n "${MACOS_NOTARY_KEY_ID:-}" ]]; then
  NOTARY_KEY_ID="$MACOS_NOTARY_KEY_ID"
elif [[ -z "$NOTARY_KEY_ID" || "$NOTARY_KEY_ID" == "${NOTARY_P8:t:r}" ]]; then
  ask "Key ID for that key" NOTARY_KEY_ID
else
  ok "Key ID: $NOTARY_KEY_ID"
fi

NOTARY_ISSUER="${MACOS_NOTARY_ISSUER:-}"
if [[ -z "$NOTARY_ISSUER" ]]; then
  print "  The Issuer ID sits above the key list on that same Apple page."
  print "  It looks like  57246542-96fe-1a63-e053-0824d011072a"
  ask "Issuer ID" NOTARY_ISSUER
fi

step "Checking the notarization key works"
if xcrun notarytool history --key "$NOTARY_P8" --key-id "$NOTARY_KEY_ID" \
     --issuer "$NOTARY_ISSUER" >/dev/null 2>&1; then
  ok "Apple accepted the key"
else
  warn "Apple did not accept that key, ID and issuer combination."
  print "  Upload it anyway and let the release tell you, or Ctrl-C and recheck."
  local carry_on=""
  read -r "carry_on?  Upload it anyway? [y/N] "
  [[ "$carry_on" == [Yy]* ]] || die "Stopped. Nothing was uploaded."
fi

base64 -i "$NOTARY_P8" > "$WORKDIR/notary.b64"

# ----------------------------------------------------------------- upload

step "Storing the secrets in $REPO"

put() {
  # put <secret-name> <file-holding-value>
  # Piped, never passed as an argument, so no value lands in the process list.
  if gh secret set "$1" --repo "$REPO" < "$2" >/dev/null 2>&1; then
    ok "$1"
  else
    die "Could not set $1. Check you have admin rights on $REPO."
  fi
}

printf '%s' "$SIGN_IDENTITY"  > "$WORKDIR/identity"
printf '%s' "$P12_PASSWORD"   > "$WORKDIR/p12pass"
printf '%s' "$NOTARY_KEY_ID"  > "$WORKDIR/keyid"
printf '%s' "$NOTARY_ISSUER"  > "$WORKDIR/issuer"

put MACOS_CERTIFICATE_P12      "$WORKDIR/cert.b64"
put MACOS_CERTIFICATE_PASSWORD "$WORKDIR/p12pass"
put MACOS_CODESIGN_IDENTITY    "$WORKDIR/identity"
put MACOS_NOTARY_KEY_P8        "$WORKDIR/notary.b64"
put MACOS_NOTARY_KEY_ID        "$WORKDIR/keyid"
put MACOS_NOTARY_ISSUER        "$WORKDIR/issuer"
put SPARKLE_ED_PRIVATE_KEY     "$WORKDIR/sparkle.key"

step "Confirming all seven are stored"
typeset -a required missing
required=(
  MACOS_CERTIFICATE_P12 MACOS_CERTIFICATE_PASSWORD MACOS_CODESIGN_IDENTITY
  MACOS_NOTARY_KEY_P8 MACOS_NOTARY_KEY_ID MACOS_NOTARY_ISSUER
  SPARKLE_ED_PRIVATE_KEY
)
STORED="$(gh secret list --repo "$REPO" 2>/dev/null | awk '{print $1}')"
missing=()
for name in "${required[@]}"; do
  print -r -- "$STORED" | grep -qx "$name" || missing+=("$name")
done
if (( ${#missing[@]} )); then
  die "Still missing: ${missing[*]}"
fi
ok "all seven stored"

# ---------------------------------------------------------------- finish

print ""
bold "Done. $REPO can now build, sign and notarize a release."
print ""
print "You can delete the .p12 from your Desktop now:"
print "    rm \"$P12_PATH\""
print ""

local run_now=""
read -r "run_now?  Start the release build now? [y/N] "
if [[ "$run_now" == [Yy]* ]]; then
  gh workflow run release-macos.yml --repo "$REPO" -f publish=true
  print ""
  ok "Started. Watch it with:"
  print "    gh run watch --repo $REPO"
  print "  or at https://github.com/$REPO/actions"
else
  print "When you are ready:"
  print "    gh workflow run release-macos.yml --repo $REPO -f publish=true"
fi

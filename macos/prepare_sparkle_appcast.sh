#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
cd "$PROJECT_DIR"

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python"
  else
    PYTHON="$(command -v python3)"
  fi
fi

VERSION="$("$PYTHON" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
ARCH="$(uname -m)"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-jgassens/PubMate}"
GITHUB_RELEASE_TAG="${GITHUB_RELEASE_TAG:-v$VERSION}"
DMG_PATH="${DMG_PATH:-$PROJECT_DIR/dist/PubMate-$VERSION-macos-$ARCH.dmg}"
UPDATES_DIR="${UPDATES_DIR:-$PROJECT_DIR/dist/sparkle-updates}"
APPCAST_OUTPUT="${APPCAST_OUTPUT:-$PROJECT_DIR/docs/appcast.xml}"
DOWNLOAD_URL_PREFIX="${DOWNLOAD_URL_PREFIX:-https://github.com/$GITHUB_REPOSITORY/releases/download/$GITHUB_RELEASE_TAG}"
RELEASE_NOTES_PATH="${RELEASE_NOTES_PATH:-}"
SPARKLE_GENERATE_APPCAST="${SPARKLE_GENERATE_APPCAST:-}"

if [[ "$DOWNLOAD_URL_PREFIX" != */ ]]; then
  DOWNLOAD_URL_PREFIX="$DOWNLOAD_URL_PREFIX/"
fi

find_generate_appcast() {
  if [[ -n "$SPARKLE_GENERATE_APPCAST" ]]; then
    if [[ -x "$SPARKLE_GENERATE_APPCAST" ]]; then
      print -r -- "$SPARKLE_GENERATE_APPCAST"
      return 0
    fi
    echo "error: SPARKLE_GENERATE_APPCAST is not executable: $SPARKLE_GENERATE_APPCAST" >&2
    return 1
  fi

  if command -v generate_appcast >/dev/null 2>&1; then
    command -v generate_appcast
    return 0
  fi

  local candidates=(
    "$PROJECT_DIR/macos/SparkleSupport/.build/artifacts/sparkle/Sparkle/bin/generate_appcast"
    "$PROJECT_DIR/macos/SparkleSupport/.build/checkouts/Sparkle/generate_appcast"
    "$HOME/Documents/programming/word-history/.build/artifacts/sparkle/Sparkle/bin/generate_appcast"
  )

  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      print -r -- "$candidate"
      return 0
    fi
  done

  echo "error: could not find Sparkle's generate_appcast tool" >&2
  echo "run: swift build --package-path macos/SparkleSupport -c release" >&2
  echo "or set SPARKLE_GENERATE_APPCAST=/path/to/generate_appcast" >&2
  return 1
}

if [[ ! -f "$DMG_PATH" ]]; then
  echo "error: DMG not found: $DMG_PATH" >&2
  echo "build and notarize the DMG before preparing the Sparkle appcast" >&2
  exit 1
fi

SPARKLE_GENERATE_APPCAST="$(find_generate_appcast)"
mkdir -p "$UPDATES_DIR" "${APPCAST_OUTPUT:h}"

ARCHIVE_NAME="${SPARKLE_ARCHIVE_NAME:-${DMG_PATH:t}}"
ARCHIVE_PATH="$UPDATES_DIR/$ARCHIVE_NAME"
ARCHIVE_BASE="${ARCHIVE_NAME:r}"
rm -f "$UPDATES_DIR/appcast.xml"
ditto --norsrc --noextattr "$DMG_PATH" "$ARCHIVE_PATH"

if [[ -n "$RELEASE_NOTES_PATH" ]]; then
  if [[ ! -f "$RELEASE_NOTES_PATH" ]]; then
    echo "error: release notes file not found: $RELEASE_NOTES_PATH" >&2
    exit 1
  fi
  cp "$RELEASE_NOTES_PATH" "$UPDATES_DIR/$ARCHIVE_BASE.${RELEASE_NOTES_PATH:e}"
fi

"$SPARKLE_GENERATE_APPCAST" \
  --download-url-prefix "$DOWNLOAD_URL_PREFIX" \
  "$UPDATES_DIR"

if [[ ! -f "$UPDATES_DIR/appcast.xml" ]]; then
  echo "error: generate_appcast did not create $UPDATES_DIR/appcast.xml" >&2
  exit 1
fi

cp "$UPDATES_DIR/appcast.xml" "$APPCAST_OUTPUT"

echo "Sparkle appcast written to: $APPCAST_OUTPUT"
echo "Upload $ARCHIVE_NAME to: $DOWNLOAD_URL_PREFIX"

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

APP_NAME="PubMate"
BUNDLE_ID="${MACOS_BUNDLE_ID:-org.pubmate.PubMate}"
SIGN_IDENTITY="${MACOS_CODESIGN_IDENTITY:--}"
SPARKLE_ENABLED=1
SPARKLE_FRAMEWORK_PATH="${SPARKLE_FRAMEWORK_PATH:-}"
SPARKLE_FEED_URL="${SPARKLE_FEED_URL:-https://jgassens.github.io/PubMate/appcast.xml}"
SPARKLE_PUBLIC_ED_KEY="${SPARKLE_PUBLIC_ED_KEY:-HK2FMFt1/JlsEm52nLZ7X4cXo1nmLLJpAoRzB3y7tYQ=}"
CREATE_DMG=1
SIGN_APP=1
CLEAN=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-dmg)
      CREATE_DMG=0
      shift
      ;;
    --no-sign)
      SIGN_APP=0
      shift
      ;;
    --no-sparkle)
      SPARKLE_ENABLED=0
      shift
      ;;
    --sparkle-framework)
      SPARKLE_FRAMEWORK_PATH="$2"
      shift 2
      ;;
    --sparkle-feed-url)
      SPARKLE_FEED_URL="$2"
      shift 2
      ;;
    --sparkle-public-ed-key)
      SPARKLE_PUBLIC_ED_KEY="$2"
      shift 2
      ;;
    --identity)
      SIGN_IDENTITY="$2"
      shift 2
      ;;
    --bundle-id)
      BUNDLE_ID="$2"
      shift 2
      ;;
    --no-clean)
      CLEAN=0
      shift
      ;;
    -h|--help)
      cat <<'HELP'
Build a distributable PubMate macOS app bundle.

Usage:
  macos/build_distribution.sh [--no-dmg] [--no-sign] [--no-sparkle] [--identity "Developer ID Application: ..."]

Environment:
  PYTHON                     Python interpreter to build with. Defaults to .venv/bin/python.
  MACOS_CODESIGN_IDENTITY    Signing identity. Defaults to ad-hoc signing with "-".
  MACOS_BUNDLE_ID            Bundle identifier. Defaults to org.pubmate.PubMate.
  SPARKLE_FRAMEWORK_PATH     Optional explicit path to Sparkle.framework.
  SPARKLE_FEED_URL           Appcast URL. Defaults to https://jgassens.github.io/PubMate/appcast.xml.
  SPARKLE_PUBLIC_ED_KEY      Sparkle public EdDSA key.

Outputs:
  dist/PubMate.app
  dist/PubMate-<version>-macos-<arch>.dmg unless --no-dmg is passed.
HELP
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

VERSION="$("$PYTHON" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
ARCH="$(uname -m)"
DIST_DIR="$PROJECT_DIR/dist"
BUILD_ROOT="${PUBMATE_BUILD_ROOT:-/private/tmp/pubmate-build-$VERSION-$ARCH}"
BUILD_DIR="$BUILD_ROOT/build"
STAGE_DIST_DIR="$BUILD_ROOT/dist"
ICON_PATH="$PROJECT_DIR/macos/assets/PubMate.icns"
ENTRYPOINT="$PROJECT_DIR/macos/pubmate_launcher_entry.py"
APP_STAGE_PATH="$STAGE_DIST_DIR/$APP_NAME.app"
APP_PATH="$DIST_DIR/$APP_NAME.app"
DMG_PATH="$DIST_DIR/$APP_NAME-$VERSION-macos-$ARCH.dmg"
DMG_STAGE_PATH="$STAGE_DIST_DIR/$APP_NAME-$VERSION-macos-$ARCH.dmg"

if ! "$PYTHON" -c 'import PyInstaller' >/dev/null 2>&1; then
  cat >&2 <<EOF
PyInstaller is not installed in $PYTHON.

Install the macOS packaging extra first:
  "$PYTHON" -m pip install -e ".[macos]"
EOF
  exit 1
fi

if [[ "$SPARKLE_ENABLED" -eq 1 ]]; then
  if ! "$PYTHON" -c 'import objc, Foundation, AppKit' >/dev/null 2>&1; then
    cat >&2 <<EOF
PyObjC is not installed in $PYTHON.

Sparkle support uses PyObjC to start Sparkle.framework from the packaged app.
Install the macOS packaging extra first:
  "$PYTHON" -m pip install -e ".[macos]"

Or build without Sparkle for local testing:
  macos/build_distribution.sh --no-sparkle
EOF
    exit 1
  fi
fi

if [[ "$SIGN_APP" -eq 1 && "$SIGN_IDENTITY" != "-" ]]; then
  if ! security find-identity -p codesigning -v | grep -F "$SIGN_IDENTITY" >/dev/null; then
    cat >&2 <<EOF
Developer ID signing identity is not installed:
  $SIGN_IDENTITY

Install the Developer ID Application certificate and private key in Keychain, or build a local-only ad-hoc package with:
  macos/build_distribution.sh
EOF
    exit 1
  fi
fi

if [[ "$CLEAN" -eq 1 ]]; then
  rm -rf "$BUILD_ROOT" "$APP_PATH" "$DMG_PATH"
fi

mkdir -p "$DIST_DIR" "$BUILD_DIR"
export PYINSTALLER_CONFIG_DIR="$BUILD_DIR/pyinstaller-cache"
"$PYTHON" "$PROJECT_DIR/macos/make_icon.py" "$ICON_PATH" >/dev/null
xattr -cr "$ICON_PATH" 2>/dev/null || true

clean_macos_metadata() {
  local target="$1"
  find "$target" -name '._*' -delete 2>/dev/null || true
  xattr -cr "$target" 2>/dev/null || true
  find "$target" -exec xattr -c {} + 2>/dev/null || true
}

find_sparkle_framework() {
  if [[ -n "$SPARKLE_FRAMEWORK_PATH" ]]; then
    if [[ -d "$SPARKLE_FRAMEWORK_PATH" ]]; then
      print -r -- "$SPARKLE_FRAMEWORK_PATH"
      return 0
    fi
    echo "error: SPARKLE_FRAMEWORK_PATH does not exist: $SPARKLE_FRAMEWORK_PATH" >&2
    return 1
  fi

  local candidates=(
    "$PROJECT_DIR/macos/vendor/Sparkle.framework"
    "$PROJECT_DIR/macos/SparkleSupport/.build/artifacts/sparkle/Sparkle/Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework"
    "$PROJECT_DIR/macos/SparkleSupport/.build/arm64-apple-macosx/release/Sparkle.framework"
    "$PROJECT_DIR/macos/SparkleSupport/.build/arm64-apple-macosx/debug/Sparkle.framework"
    "$HOME/Documents/programming/word-history/.build/artifacts/sparkle/Sparkle/Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework"
  )

  for candidate in "${candidates[@]}"; do
    if [[ -d "$candidate" ]]; then
      print -r -- "$candidate"
      return 0
    fi
  done

  echo "error: Sparkle.framework was not found" >&2
  echo "run: swift build --package-path macos/SparkleSupport -c release" >&2
  echo "or set SPARKLE_FRAMEWORK_PATH=/path/to/Sparkle.framework" >&2
  return 1
}

sign_target() {
  local target="$1"
  local sign_args=(--force --deep --sign "$SIGN_IDENTITY")
  if [[ "$SIGN_IDENTITY" != "-" ]]; then
    sign_args+=(--options runtime --timestamp)
  fi
  codesign "${sign_args[@]}" "$target"
}

sign_nested_bundles() {
  local root="$1"
  [[ -d "$root" ]] || return 0
  find "$root" -depth \( -name '*.app' -o -name '*.xpc' -o -name '*.framework' \) -print0 | \
    while IFS= read -r -d '' bundle; do
      sign_target "$bundle"
    done
}

"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --icon "$ICON_PATH" \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --paths "$PROJECT_DIR/src" \
  --distpath "$STAGE_DIST_DIR" \
  --workpath "$BUILD_DIR/pyinstaller" \
  --specpath "$BUILD_DIR" \
  "$ENTRYPOINT"

if [[ ! -d "$APP_STAGE_PATH" ]]; then
  echo "Expected app bundle was not created: $APP_STAGE_PATH" >&2
  exit 1
fi

clean_macos_metadata "$APP_STAGE_PATH"

if [[ "$SPARKLE_ENABLED" -eq 1 ]]; then
  SPARKLE_FRAMEWORK_RESOLVED="$(find_sparkle_framework)"
  rm -rf "$APP_STAGE_PATH/Contents/Frameworks/Sparkle.framework"
  ditto --norsrc --noextattr "$SPARKLE_FRAMEWORK_RESOLVED" "$APP_STAGE_PATH/Contents/Frameworks/Sparkle.framework"
  clean_macos_metadata "$APP_STAGE_PATH/Contents/Frameworks/Sparkle.framework"
fi

plist_set_string() {
  local key="$1"
  local value="$2"
  local plist="$APP_STAGE_PATH/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Set :$key $value" "$plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Add :$key string $value" "$plist"
}

plist_set_bool() {
  local key="$1"
  local value="$2"
  local plist="$APP_STAGE_PATH/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Set :$key $value" "$plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Add :$key bool $value" "$plist"
}

plist_set_string "CFBundleShortVersionString" "$VERSION"
plist_set_string "CFBundleVersion" "$VERSION"
plist_set_string "LSMinimumSystemVersion" "13.0"
plist_set_string "NSDocumentsFolderUsageDescription" "PubMate reads the Word document you choose and writes converted output files next to it."
plist_set_string "NSDownloadsFolderUsageDescription" "PubMate can read and write Word, EndNote import, and report files in Downloads when you choose a file there."

if [[ "$SPARKLE_ENABLED" -eq 1 ]]; then
  plist_set_string "SUFeedURL" "$SPARKLE_FEED_URL"
  plist_set_string "SUPublicEDKey" "$SPARKLE_PUBLIC_ED_KEY"
  plist_set_bool "SUEnableAutomaticChecks" "true"
  plist_set_bool "SUAutomaticallyUpdate" "false"
fi

if [[ "$SIGN_APP" -eq 1 ]]; then
  clean_macos_metadata "$APP_STAGE_PATH"
  if [[ "$SPARKLE_ENABLED" -eq 1 ]]; then
    sign_nested_bundles "$APP_STAGE_PATH/Contents/Frameworks/Sparkle.framework"
  fi
  sign_target "$APP_STAGE_PATH"
  codesign --verify --deep --strict --verbose=2 "$APP_STAGE_PATH"
fi

"$APP_STAGE_PATH/Contents/MacOS/$APP_NAME" --self-test >/dev/null
if [[ "$SPARKLE_ENABLED" -eq 1 ]]; then
  "$APP_STAGE_PATH/Contents/MacOS/$APP_NAME" --sparkle-self-test >/dev/null
fi

if [[ "$CREATE_DMG" -eq 1 ]]; then
  DMG_ROOT="$BUILD_DIR/dmg-root"
  rm -rf "$DMG_ROOT" "$DMG_STAGE_PATH"
  mkdir -p "$DMG_ROOT"
  ditto --norsrc --noextattr "$APP_STAGE_PATH" "$DMG_ROOT/$APP_NAME.app"
  ln -s /Applications "$DMG_ROOT/Applications"
  hdiutil create -volname "$APP_NAME" -srcfolder "$DMG_ROOT" -ov -format UDZO "$DMG_STAGE_PATH"
  if [[ "$SIGN_APP" -eq 1 && "$SIGN_IDENTITY" != "-" ]]; then
    codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG_STAGE_PATH"
  fi
fi

rm -rf "$APP_PATH"
ditto --norsrc --noextattr "$APP_STAGE_PATH" "$APP_PATH"
clean_macos_metadata "$APP_PATH"
if [[ "$CREATE_DMG" -eq 1 ]]; then
  cp "$DMG_STAGE_PATH" "$DMG_PATH"
fi
if [[ "$SIGN_APP" -eq 1 ]]; then
  if ! codesign --verify --deep --strict --verbose=2 "$APP_PATH"; then
    echo "Warning: the convenience app copy at $APP_PATH picked up macOS file-provider metadata after copying." >&2
    echo "The staged app was verified before DMG creation; use the DMG for distribution." >&2
  fi
fi

echo "Built: $APP_PATH"
if [[ "$CREATE_DMG" -eq 1 ]]; then
  echo "Built: $DMG_PATH"
fi
echo
if [[ "$SIGN_APP" -eq 1 && "$SIGN_IDENTITY" != "-" ]]; then
  echo "Built with Developer ID signing identity: $SIGN_IDENTITY"
  if [[ "$SPARKLE_ENABLED" -eq 1 ]]; then
    echo "Sparkle appcast URL: $SPARKLE_FEED_URL"
    echo "After notarization, run macos/prepare_sparkle_appcast.sh and publish docs/appcast.xml plus the DMG release asset."
  fi
  echo "Next step for public distribution: notarize the DMG with macos/notarize_distribution.sh."
else
  echo "Ad-hoc-signed builds are for local testing. For public distribution, sign with a Developer ID identity and notarize the DMG."
fi

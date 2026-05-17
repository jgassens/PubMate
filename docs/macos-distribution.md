# macOS Distribution

PubMate can be distributed as a normal double-clickable macOS app bundle:

```text
PubMate.app
```

The app uses native macOS dialogs to choose a Word `.docx`, ask for the PubMed email address the first time it is needed, and run the same processing service as the CLI. It does not rely on Tkinter.

## Build A Local App

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test,macos]"
macos/build_distribution.sh
```

Outputs:

```text
dist/PubMate.app
dist/PubMate-<version>-macos-<arch>.dmg
```

The default build is ad-hoc signed. That is good for local testing and internal handoff, but it is not notarized.

Use the DMG as the primary distribution artifact. The build script stages signing and DMG creation in `/private/tmp` to avoid iCloud/File Provider metadata that can attach to app bundles inside `Documents` folders.

## Test The App

Open the app bundle:

```bash
open dist/PubMate.app
```

Use a disposable Word test document first. A successful run should create these files next to the input document:

```text
<name>.endnote.docx
<name>.endnote-import.enw
<name>.references.nbib
<name>.pmid2endnote.report.json
```

Import the `.endnote-import.enw` file into EndNote using the **EndNote Import** option, then open the `.endnote.docx` in Word and run **EndNote > Update Citations and Bibliography**.

## Build With Developer ID Signing

For public distribution outside your own Mac, build with a Developer ID Application certificate:

```bash
MACOS_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
  macos/build_distribution.sh
```

The build script checks `security find-identity -p codesigning -v` before it starts a Developer ID build, so a missing certificate/private key is reported before the slower package build begins.

Verify the app signature:

```bash
codesign --verify --deep --strict --verbose=2 dist/PubMate.app
```

If your checkout lives in an iCloud/File Provider-backed folder and the convenience `dist/PubMate.app` copy picks up Finder metadata after the build, verify the app inside the generated DMG instead. The DMG is the artifact to upload or hand to users.

## Notarize The DMG

Create a `notarytool` keychain profile once:

```bash
xcrun notarytool store-credentials pubmate-notary
```

Then submit and staple:

```bash
MACOS_NOTARY_PROFILE=pubmate-notary \
  macos/notarize_distribution.sh dist/PubMate-<version>-macos-<arch>.dmg
```

Or use an App Store Connect API key directly:

```bash
MACOS_NOTARY_KEY=/path/to/AuthKey_KEYID.p8 \
MACOS_NOTARY_KEY_ID=KEYID \
MACOS_NOTARY_ISSUER=ISSUER-UUID \
  macos/notarize_distribution.sh dist/PubMate-<version>-macos-<arch>.dmg
```

The script runs:

```text
xcrun notarytool submit --wait
xcrun notarytool log if Apple rejects the submission
xcrun stapler staple
xcrun stapler validate
spctl --assess
```

An App Store Connect API key is only the notarization credential. Public distribution still requires building the app with an installed **Developer ID Application** signing certificate first.

## Sparkle Auto-Updates

PubMate uses Sparkle for direct-distribution updates. The app bundle contains:

```text
Contents/Frameworks/Sparkle.framework
SUFeedURL=https://jgassens.github.io/PubMate/appcast.xml
SUPublicEDKey=<Sparkle EdDSA public key>
```

The build script embeds `Sparkle.framework`, sets the appcast keys in `Info.plist`, signs Sparkle's nested updater components, and runs a packaged `--sparkle-self-test` before creating the DMG.

If Sparkle is not already available locally, resolve it once:

```bash
swift build --package-path macos/SparkleSupport -c release
```

You can also point the build at an explicit framework:

```bash
SPARKLE_FRAMEWORK_PATH=/path/to/Sparkle.framework macos/build_distribution.sh
```

Local test builds can disable Sparkle:

```bash
macos/build_distribution.sh --no-sparkle
```

### Publish A New Update

Sparkle does not read the GitHub repository version by itself. It reads a signed appcast. PubMate's release helper turns the notarized DMG into that appcast and points it at a GitHub release asset.

1. Bump `version` in `pyproject.toml`.
2. Build with Developer ID signing.
3. Notarize and staple the DMG.
4. Upload `dist/PubMate-<version>-macos-<arch>.dmg` to the GitHub release tag `v<version>`.
5. Generate the appcast:

```bash
macos/prepare_sparkle_appcast.sh
```

The helper writes:

```text
docs/appcast.xml
dist/sparkle-updates/
```

Commit and push the updated `docs/appcast.xml`, and make sure GitHub Pages serves it at:

```text
https://jgassens.github.io/PubMate/appcast.xml
```

Sparkle compares the installed app's `CFBundleVersion`/`CFBundleShortVersionString` with the appcast item and offers the newer notarized DMG when the version changes.

## Release Checklist

Before uploading a release artifact:

- Run `pytest`.
- Run `zsh -n macos/PMID2EndNote.command macos/build_distribution.sh macos/notarize_distribution.sh macos/prepare_sparkle_appcast.sh`.
- Build `dist/PubMate.app` and the DMG.
- Open the built app from Finder or `open dist/PubMate.app`.
- Process a disposable Word document.
- Import the generated `.endnote-import.enw` into a test EndNote library.
- Run Word's EndNote **Update Citations and Bibliography** command.
- Confirm the final document has formatted EndNote fields and no visible `PMID-` or `DOI-` key leakage.
- Confirm PMIDs/DOIs in the manuscript reference section were skipped unless `--no-skip-reference-section` was intentionally used.
- Upload the notarized DMG to a GitHub release.
- Run `macos/prepare_sparkle_appcast.sh`, commit `docs/appcast.xml`, and verify the public appcast URL.

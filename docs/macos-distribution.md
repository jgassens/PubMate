# macOS Distribution

PubMate can be distributed as a normal double-clickable macOS app bundle:

```text
PubMate.app
```

The app opens a Tkinter drop-zone window backed by `tkinterdnd2`. Users can
drop one Word `.docx` or click to choose it, and the Settings window stores the
required NCBI email plus the optional API key and scanning preferences. If
Tkinter cannot be imported, the launcher retains the older native macOS dialog
flow as a fallback.

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
dist/PubMate-<version>-macos-universal2.dmg
```

The default build target is `universal2`, so the same DMG works on Apple Silicon and Intel Macs. Universal builds require a universal Python runtime. The build script also scans the finished `.app` and fails if any bundled Mach-O file is missing either the `arm64` or `x86_64` slice. On this Mac, use the python.org framework Python instead of the arm64-only Homebrew Python:

```bash
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -m venv .venv-universal
.venv-universal/bin/python -m pip install -e ".[test,macos]"
```

The default build is ad-hoc signed. That is good for local testing and internal handoff, but it is not notarized.

Use the DMG as the primary distribution artifact. The build script stages signing and DMG creation in `/private/tmp` to avoid iCloud/File Provider metadata that can attach to app bundles inside `Documents` folders.

## Test The App

Open the app bundle:

```bash
open dist/PubMate.app
```

Use a disposable Word test document first. A successful run creates a dated
folder under `~/Library/Application Support/PubMate/Conversions` containing:

```text
<name>.endnote.docx
<name>.endnote-import.enw
```

The app reveals that folder after the run. Settings retain conversion folders
for 7, 14, 30, or 90 days (30 by default). The `.references.nbib` and
`.pmid2endnote.report.json` debugging outputs are off by default and can be
enabled in Settings.

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
xcrun notarytool store-credentials chemdraft-notary
```

Then submit and staple:

```bash
MACOS_NOTARY_PROFILE=chemdraft-notary \
  macos/notarize_distribution.sh dist/PubMate-<version>-macos-universal2.dmg
```

Or use an App Store Connect API key directly:

```bash
MACOS_NOTARY_KEY=/path/to/AuthKey_KEYID.p8 \
MACOS_NOTARY_KEY_ID=KEYID \
MACOS_NOTARY_ISSUER=ISSUER-UUID \
  macos/notarize_distribution.sh dist/PubMate-<version>-macos-universal2.dmg
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
SUEnableAutomaticChecks=true
SUAllowsAutomaticUpdates=true
SUAutomaticallyUpdate=true
SUPromptUserOnFirstLaunch=false
```

The build script embeds `Sparkle.framework` and the universal in-process bridge
`Contents/Frameworks/libPubMateSparkle.dylib`, sets Sparkle defaults in
`Info.plist`, and signs both before creating the DMG. Sparkle performs its own
scheduled checks daily. Automatic installation is enabled by default, while
the choices a user makes in Sparkle's alert—including **Skip This Version** and
the automatic-install checkbox—are respected. If an update is ready while a
conversion is running, relaunch is postponed until the conversion ends.

Release builds always use the appcast URL from `Info.plist`. A feed override is
available only in builds compiled with `PUBMATE_DEBUG_FEED=1`; those debug
builds may read `PUBMATE_SPARKLE_FEED_URL` at runtime and must not be
distributed.

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
4. Upload `dist/PubMate-<version>-macos-universal2.dmg` to the GitHub release tag `v<version>`.
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

Sparkle compares the installed app's `CFBundleVersion`/`CFBundleShortVersionString`
with the appcast item, downloads the newer notarized DMG without consulting the
user, and installs it silently when PubMate exits.

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

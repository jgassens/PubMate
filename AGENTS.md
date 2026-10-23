# PubMate Agent Instructions

## Sparkle Release Rule

PubMate's macOS app updates through Sparkle. Sparkle only offers an update when
the live appcast advertises a version newer than the installed app's
`CFBundleVersion` / `CFBundleShortVersionString`, both of which are sourced from
`pyproject.toml`.

For any commit that changes shipped app behavior, packaging, dependencies, or
user-facing workflow and will be pushed to `main`, increment:

```toml
version = "..."
```

in `pyproject.toml` before committing.

Do not push a releasable app change with the same version number as the current
published GitHub release. If the version does not change, Sparkle will not offer
the new build.

Documentation-only commits may keep the version unchanged only when they do not
need to ship as a new app update.

## Releasing Through GitHub Actions

`.github/workflows/release-macos.yml` performs the whole checklist below on a
GitHub macOS runner. Bump the version, then push the matching tag:

```bash
git tag v$(python3 -c 'import tomllib;print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')
git push origin --tags
```

The workflow refuses to run if the tag and `pyproject.toml` version disagree,
or if that version already has a published DMG. It installs a universal2
Python, runs the tests, signs, notarizes, staples, verifies the app inside the
DMG, publishes the release, regenerates `docs/appcast.xml`, and commits the
appcast to the default branch.

Set those secrets up in one pass by running this on the Mac that already
builds releases by hand:

```bash
macos/setup_ci_secrets.sh
```

It reads the Developer ID certificate, the Sparkle key and the notarization
key, verifies each one works before uploading, and never prints a value.

It needs these repository secrets: `MACOS_CERTIFICATE_P12`,
`MACOS_CERTIFICATE_PASSWORD`, `MACOS_CODESIGN_IDENTITY`,
`MACOS_NOTARY_KEY_P8`, `MACOS_NOTARY_KEY_ID`, `MACOS_NOTARY_ISSUER`, and
`SPARKLE_ED_PRIVATE_KEY`. The two `_P12`/`_P8` values are base64 encoded.

Set `SPARKLE_ED_KEY_FILE` to sign an appcast on a machine whose Keychain has
no Sparkle key; `prepare_sparkle_appcast.sh` otherwise reads the Keychain as
before, so local releases are unchanged.

## Release Checklist For App Changes

Run these by hand only when releasing without the workflow.

1. Bump `pyproject.toml` version.
2. Run tests:

```bash
.venv-universal/bin/pytest -q
```

3. Build the signed macOS app and DMG:

```bash
MACOS_CODESIGN_IDENTITY="Developer ID Application: JEREMIAH JOSEPH GASSENSMITH (C2N7W5247T)" \
  macos/build_distribution.sh
```

The default release target is `universal2`. Do not publish an Apple
Silicon-only DMG unless the user explicitly asks for a single-architecture
emergency build. A proper Sparkle release should be named like:

```text
dist/PubMate-<version>-macos-universal2.dmg
```

4. Notarize and staple the DMG.
5. Create or update the matching GitHub release tag `v<version>`.
6. Upload `dist/PubMate-<version>-macos-universal2.dmg` to that release.
7. Regenerate the Sparkle appcast:

```bash
macos/prepare_sparkle_appcast.sh
```

8. Commit and push the version bump plus updated `docs/appcast.xml`.
9. Verify the live appcast:

```bash
curl -fsSL https://jgassens.github.io/PubMate/appcast.xml
```

The live appcast must contain the new `sparkle:version`, a valid
`sparkle:edSignature`, and an enclosure URL pointing to the matching GitHub
release DMG.

## Packaging Notes

- The distributable artifact is the DMG, not the loose `dist/PubMate.app` copy.
- The loose app copy can pick up local Finder/File Provider metadata after
  copying; verify the app inside the DMG for distribution.
- Universal releases require a universal Python runtime. Use
  `.venv-universal` created from `/Library/Frameworks/Python.framework` or
  another universal2 Python, not the arm64-only Homebrew Python.
- Keep `.references.nbib` auxiliary. The canonical EndNote import file is
  `.endnote-import.enw`.

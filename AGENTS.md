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

## Release Checklist For App Changes

1. Bump `pyproject.toml` version.
2. Run tests:

```bash
.venv/bin/pytest -q
```

3. Build the signed macOS app and DMG:

```bash
MACOS_CODESIGN_IDENTITY="Developer ID Application: JEREMIAH JOSEPH GASSENSMITH (C2N7W5247T)" \
  macos/build_distribution.sh
```

4. Notarize and staple the DMG.
5. Create or update the matching GitHub release tag `v<version>`.
6. Upload `dist/PubMate-<version>-macos-<arch>.dmg` to that release.
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
- Keep `.references.nbib` auxiliary. The canonical EndNote import file is
  `.endnote-import.enw`.

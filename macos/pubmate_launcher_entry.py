"""PyInstaller entry point for the PubMate macOS app."""

from pmid2endnote.macos_launcher import main


if __name__ == "__main__":
    raise SystemExit(main())

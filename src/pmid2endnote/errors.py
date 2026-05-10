"""Application-specific exceptions."""


class PMID2EndNoteError(Exception):
    """Base exception for expected PMID2EndNote failures."""


class InputDocumentError(PMID2EndNoteError):
    """Raised when the input document cannot be read or validated."""


class PubMedFetchError(PMID2EndNoteError):
    """Raised when PubMed records cannot be fetched."""


class PubMedParseError(PMID2EndNoteError):
    """Raised when PubMed XML cannot be parsed."""


class WordProcessingError(PMID2EndNoteError):
    """Raised when a Word document cannot be processed safely."""

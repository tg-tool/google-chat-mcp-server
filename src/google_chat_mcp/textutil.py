"""Text normalization for matching names and keywords."""

import unicodedata


def norm(s: str) -> str:
    """Fold full-/half-width forms (NFKC) and case, so 'ＡＢＣ' / 'abc' and 'ｶﾅ' / 'カナ' match."""
    return unicodedata.normalize("NFKC", s).casefold()

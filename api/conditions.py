"""Hebrew and English condition names, and how to accept either.

The sheet stores the English names (they came from KSP's grade A/B/C/D
descriptions in Phase 2). The chatbot talks to customers in Hebrew. This module
is the only place the two vocabularies meet.
"""

from __future__ import annotations

import unicodedata

# Sheet name -> customer-facing Hebrew. Order matters: responses list conditions
# best-first, which is the order a customer expects to see prices in.
CONDITIONS = [
    {"english": "Like New", "hebrew": "מצב מעולה"},
    {"english": "Intact", "hebrew": "מצב טוב"},
    {"english": "Cracked", "hebrew": "מצב בעייתי"},
    {"english": "Faulty", "hebrew": "לא תקין"},
]

ENGLISH_NAMES = [c["english"] for c in CONDITIONS]
HEBREW_NAMES = [c["hebrew"] for c in CONDITIONS]

# Spellings a caller might reasonably send, beyond the two canonical names.
# Keys are normalised (see _normalise), so case and spacing are already handled.
_ALIASES = {
    # English
    "likenew": "Like New",
    "like-new": "Like New",
    "asnew": "Like New",
    "new": "Like New",
    "excellent": "Like New",
    "intact": "Intact",
    "good": "Intact",
    "wellkept": "Intact",
    "cracked": "Cracked",
    "crack": "Cracked",
    "damaged": "Cracked",
    "cosmeticdamage": "Cracked",
    "faulty": "Faulty",
    "broken": "Faulty",
    "notworking": "Faulty",
    "belowaverage": "Faulty",
    # Hebrew, including the wording KSP itself shows on its trade-in page.
    "מצבמעולה": "Like New",
    "כחדשלחלוטין": "Like New",
    "חדשלחלוטין": "Like New",
    "מצבטוב": "Intact",
    "ללאשברסדק": "Intact",
    "מצבבעייתי": "Cracked",
    "שברסדק": "Cracked",
    "לאתקין": "Faulty",
    "תקול": "Faulty",
}


class UnknownCondition(ValueError):
    """The caller sent something that is not one of the four conditions."""


def _normalise(text):
    """Fold away case, spacing, punctuation and Hebrew niqqud/bidi marks.

    Hebrew text pasted from a browser often carries right-to-left marks that are
    invisible but break a plain string comparison, so they go first.
    """
    text = unicodedata.normalize("NFKC", str(text))
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Mn", "Cf"))
    return "".join(ch for ch in text.lower() if ch.isalnum())


# Built once: every canonical name maps to itself.
_LOOKUP = dict(_ALIASES)
for entry in CONDITIONS:
    _LOOKUP[_normalise(entry["english"])] = entry["english"]
    _LOOKUP[_normalise(entry["hebrew"])] = entry["english"]
_LOOKUP = {_normalise(k): v for k, v in _LOOKUP.items()}

_HEBREW_FOR = {c["english"]: c["hebrew"] for c in CONDITIONS}


def to_english(value):
    """Map a condition given in Hebrew or English onto the sheet's name.

    Raises UnknownCondition if it is not one of the four.
    """
    if value is None or not str(value).strip():
        raise UnknownCondition("condition is empty")

    english = _LOOKUP.get(_normalise(value))
    if english is None:
        raise UnknownCondition(f"unknown condition: {value!r}")
    return english


def to_hebrew(english_name):
    """The customer-facing Hebrew for a sheet condition name."""
    try:
        return _HEBREW_FOR[english_name]
    except KeyError as err:
        raise UnknownCondition(f"unknown condition: {english_name!r}") from err


def is_valid(value):
    try:
        to_english(value)
        return True
    except UnknownCondition:
        return False


def both(english_name):
    """{"hebrew": ..., "english": ...} for one condition."""
    return {"hebrew": to_hebrew(english_name), "english": english_name}

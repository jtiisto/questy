"""Test-name matching shared by the parser and the database."""

from __future__ import annotations

import re

# The model sometimes paraphrases a printed test name ("CHOL/HDL RATIO" for
# "CHOL/HDLC RATIO"). Names this short are left to exact matching: one letter
# separates MCH from MCHC.
_FUZZY_MIN_LEN = 6


def normalize_name(name: str) -> str:
    """Uppercase and drop whitespace and hyphens, so "LDL-CHOLESTEROL",
    "LDL CHOLESTEROL" and "ldl cholesterol" compare equal."""
    return re.sub(r"[\s-]+", "", name.upper())


def names_match(a: str, b: str) -> bool:
    """True if two test names refer to the same test.

    Equal after normalization, or (for names of at least _FUZZY_MIN_LEN
    characters) one is the other with a single letter inserted. Digit
    insertions and substitutions never match: they tell apart tests like
    T3/T4 and APOLIPOPROTEIN A1/B.
    """
    a, b = normalize_name(a), normalize_name(b)
    if a == b:
        return True
    if len(a) > len(b):
        a, b = b, a
    if len(b) != len(a) + 1 or len(a) < _FUZZY_MIN_LEN:
        return False
    for i, c in enumerate(b):
        if b[:i] + b[i + 1:] == a:
            return c.isalpha()
    return False

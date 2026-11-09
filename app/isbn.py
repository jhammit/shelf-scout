"""ISBN helpers."""
from __future__ import annotations

import re

_strip = re.compile(r"[^0-9Xx]")


def clean(raw: str) -> str:
    return _strip.sub("", raw or "").upper()


def isbn10_valid(s: str) -> bool:
    if not re.fullmatch(r"\d{9}[\dX]", s):
        return False
    total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s))
    return total % 11 == 0


def isbn13_check_digit(first12: str) -> str:
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(first12))
    return str((10 - total % 10) % 10)


def isbn13_valid(s: str) -> bool:
    return bool(re.fullmatch(r"97[89]\d{10}", s)) and isbn13_check_digit(s[:12]) == s[12]


def to_isbn13(raw: str) -> str:
    """Normalize ISBN-10 or ISBN-13 to ISBN-13. Returns '' when invalid."""
    s = clean(raw)
    if len(s) == 13 and isbn13_valid(s):
        return s
    if len(s) == 10 and isbn10_valid(s):
        core = "978" + s[:9]
        return core + isbn13_check_digit(core)
    return ""


def to_isbn10(isbn13: str) -> str:
    if not isbn13.startswith("978") or len(isbn13) != 13:
        return ""
    core = isbn13[3:12]
    total = sum((10 - i) * int(c) for i, c in enumerate(core))
    check = (11 - total % 11) % 11
    return core + ("X" if check == 10 else str(check))

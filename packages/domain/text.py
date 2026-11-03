from __future__ import annotations

import re
import unicodedata

MAX_ANSWER_CHARS = 6000
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+?86[-\s]?)?1[3-9]\d{9}|\d{3,4}-\d{7,8}")


def redact_identity(value: str) -> str:
    """Strip phone/email before resume or JD text is shown to an agent."""
    redacted = _EMAIL.sub("[REDACTED_EMAIL]", value)
    return _PHONE.sub("[REDACTED_PHONE]", redacted)


def normalize_text(value: str) -> str:
    """Immutable original-text normalization: NFC + LF. Does not strip substance."""
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def code_point_slice(text: str, start: int, end: int) -> str:
    chars = list(text)
    if start < 0 or end < start or end > len(chars):
        raise ValueError("invalid unicode offsets")
    return "".join(chars[start:end])


def locate_quote(normalized_text: str, quote: str) -> tuple[int, int] | None:
    """Return unique (start, end) code-point offsets for quote, or None if missing/ambiguous."""
    if not quote:
        return None
    haystack = list(normalized_text)
    needle = list(quote)
    matches: list[tuple[int, int]] = []
    n = len(haystack)
    m = len(needle)
    for i in range(0, n - m + 1):
        if haystack[i : i + m] == needle:
            matches.append((i, i + m))
            if len(matches) > 1:
                return None
    if len(matches) != 1:
        return None
    return matches[0]


def assert_quote_match(normalized_text: str, start: int, end: int, quote: str) -> bool:
    try:
        return code_point_slice(normalized_text, start, end) == quote
    except ValueError:
        return False

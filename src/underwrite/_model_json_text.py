"""Keep model-visible JSON text free of raw Unicode control and format characters."""

from __future__ import annotations

import json
import unicodedata

_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})


def has_text_controls(value: str) -> bool:
    """Detect raw characters excluded from model-visible JSON text."""
    return any(unicodedata.category(char) in _UNSAFE_CATEGORIES for char in value)


def escape_text_controls(document: str) -> str:
    """Escape controls in serialized JSON without changing its parsed value."""
    return "".join(
        json.dumps(char, ensure_ascii=True)[1:-1]
        if unicodedata.category(char) in _UNSAFE_CATEGORIES
        else char
        for char in document
    )

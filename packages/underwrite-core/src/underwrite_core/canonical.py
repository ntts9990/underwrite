"""Deterministic canonical JSON serialization and SHA-256 digests.

Reject non-NFC strings, non-finite numbers and integers outside the safe range.
Float formatting follows ECMAScript number serialization, including negative zero.
Pure standard-library functions perform no I/O or clock access."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Final, TypeGuard

CANONICAL_SCHEMA: Final[str] = "underwrite-canonical-json/v1"
SAFE_INT_MAX: Final[int] = 2**53
# ASCII lowercase letters/digits/./_/- only (tagged_digest's domain-separation tag).
_TAG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9._-]+$")

_BACKSLASH: Final[str] = chr(0x5C)
_QUOTE: Final[str] = chr(0x22)
_CONTROL_CHAR_LIMIT: Final[int] = 0x20
# ECMAScript Number::toString layout boundaries (RFC 8785 number serialization).
_ES6_MAX_FIXED_EXPONENT: Final[int] = 21
_ES6_MIN_FIXED_EXPONENT: Final[int] = -6

# RFC 8785 §3.2.2.2 named escapes; every other control char < 0x20 falls
# back to "\u00xx" and everything else is emitted as literal UTF-8.
_NAMED_ESCAPES: Final[dict[str, str]] = {
    _QUOTE: _BACKSLASH + _QUOTE,
    _BACKSLASH: _BACKSLASH + _BACKSLASH,
    chr(0x08): _BACKSLASH + "b",
    chr(0x0C): _BACKSLASH + "f",
    chr(0x0A): _BACKSLASH + "n",
    chr(0x0D): _BACKSLASH + "r",
    chr(0x09): _BACKSLASH + "t",
}


class CanonicalizationError(ValueError):
    """Raised when a value cannot be serialized under CANONICAL_SCHEMA.

    ``args[0]`` is one of: NON_NFC_STRING, NON_UTF8_STRING,
    INT_OUT_OF_SAFE_RANGE, NON_FINITE_NUMBER, UNSUPPORTED_TYPE, INVALID_TAG.
    """


def canonical_bytes(value: object) -> bytes:
    """Serialize ``value`` as underwrite-canonical-json/v1 UTF-8 bytes.

    A string holding a lone surrogate is no sequence of Unicode scalar values, so it has
    no UTF-8 (or UTF-16 sort key) and is not I-JSON (RFC 7493 §2.1, which RFC 8785
    assumes): `CanonicalizationError("NON_UTF8_STRING")`, raised outside the handler so
    no codec exception is its context.
    """
    try:
        return _encode(value).encode("utf-8")
    except UnicodeEncodeError:
        pass
    raise CanonicalizationError("NON_UTF8_STRING")


def digest_bytes(data: bytes) -> str:
    """Return the ``sha256:<hex>`` digest of raw bytes."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def content_digest(value: object) -> str:
    """Return ``digest_bytes(canonical_bytes(value))``."""
    return digest_bytes(canonical_bytes(value))


def tagged_digest(tag: str, data: bytes) -> str:
    """Domain-separated ``sha256:`` digest of ``data`` under ``tag``.

    ``sha256:`` + ``sha256(len(tag_utf8) as 8-byte big-endian || tag_utf8 ||
    data).hexdigest()``. The length-prefixed tag binds ``data`` to the
    purpose it was digested for, so the same bytes digested under two
    different tags -- or two different tags whose concatenation could
    otherwise collide -- never produce the same digest.

    Raises `CanonicalizationError("INVALID_TAG")` unless ``tag`` matches
    ``^[a-z0-9._-]+$`` (ASCII lowercase letters, digits, ``.``, ``_``, ``-``).
    """
    if not _TAG_RE.fullmatch(tag):
        raise CanonicalizationError("INVALID_TAG")
    tag_bytes = tag.encode("ascii")
    header = len(tag_bytes).to_bytes(8, "big")
    return "sha256:" + hashlib.sha256(header + tag_bytes + data).hexdigest()


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    """`isinstance(value, list)`, narrowed to `list[object]` (not
    `list[Unknown]`) so its elements type-check as the `object` `_encode`
    already accepts, with no `typing.cast`."""
    return isinstance(value, list)


def _is_object_dict(value: object) -> TypeGuard[dict[object, object]]:
    """`isinstance(value, dict)`, narrowed to `dict[object, object]` for the
    same reason as `_is_object_list` above."""
    return isinstance(value, dict)


def _encode(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _encode_string(value)
    if isinstance(value, (int, float)):
        return _encode_number(value)
    if _is_object_list(value):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    if _is_object_dict(value):
        return _encode_object(value)
    raise CanonicalizationError("UNSUPPORTED_TYPE")


def _encode_number(value: int | float) -> str:
    if isinstance(value, int):
        return _encode_int(value)
    return _encode_float(value)


def _encode_string(value: str) -> str:
    if unicodedata.normalize("NFC", value) != value:
        raise CanonicalizationError("NON_NFC_STRING")
    return _QUOTE + _escape_string(value) + _QUOTE


def _escape_string(value: str) -> str:
    out: list[str] = []
    for ch in value:
        escape = _NAMED_ESCAPES.get(ch)
        if escape is not None:
            out.append(escape)
        elif ord(ch) < _CONTROL_CHAR_LIMIT:
            out.append(_BACKSLASH + "u00" + format(ord(ch), "02x"))
        else:
            out.append(ch)
    return "".join(out)


def _encode_int(value: int) -> str:
    if abs(value) > SAFE_INT_MAX:
        raise CanonicalizationError("INT_OUT_OF_SAFE_RANGE")
    return str(value)


def _encode_float(value: float) -> str:
    if math.isnan(value) or math.isinf(value):
        raise CanonicalizationError("NON_FINITE_NUMBER")
    rendered = _format_float(value)
    if "." not in rendered and "e" not in rendered and abs(value) > SAFE_INT_MAX:
        raise CanonicalizationError("INT_OUT_OF_SAFE_RANGE")
    return rendered


def _encode_object(value: dict[object, object]) -> str:
    keys: list[str] = []
    for key in value:
        if not isinstance(key, str):
            raise CanonicalizationError("UNSUPPORTED_TYPE")
        keys.append(key)
    parts = [
        _encode_string(key) + ":" + _encode(value[key])
        for key in sorted(keys, key=_member_sort_key)
    ]
    return "{" + ",".join(parts) + "}"


def _member_sort_key(key: str) -> bytes:
    """RFC 8785 §3.2.3 member ordering: compare UTF-16 code unit sequences.

    Comparing the big-endian UTF-16 byte encoding lexicographically is
    equivalent to comparing the code unit arrays numerically, because every
    code unit occupies exactly two bytes.
    """
    return key.encode("utf-16-be")


def _format_float(value: float) -> str:
    """Format a finite float per ECMAScript ``Number::toString`` (RFC 8785).

    ``repr`` already gives the shortest decimal digit string that round
    trips to ``value`` (CPython's float repr); this only re-derives the
    significant digits and decimal exponent from that string and re-lays
    them out per the ES6 rules instead of Python's own formatting choices.
    """
    if value == 0.0:
        return "0"
    negative = value < 0.0
    digits, n = _shortest_digits_and_exponent(-value if negative else value)
    k = len(digits)
    if k <= n <= _ES6_MAX_FIXED_EXPONENT:
        body = digits + "0" * (n - k)
    elif 0 < n <= _ES6_MAX_FIXED_EXPONENT:
        body = digits[:n] + "." + digits[n:]
    elif _ES6_MIN_FIXED_EXPONENT < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        exponent = n - 1
        sign = "+" if exponent >= 0 else "-"
        mantissa = digits[0] if k == 1 else digits[0] + "." + digits[1:]
        body = mantissa + "e" + sign + str(abs(exponent))
    return "-" + body if negative else body


def _shortest_digits_and_exponent(magnitude: float) -> tuple[str, int]:
    """Return ``(digits, n)`` with no leading/trailing zeros in ``digits``,
    such that ``magnitude == int(digits) * 10 ** (n - len(digits))``.
    """
    text = repr(magnitude)
    mantissa, _, exponent_text = text.partition("e")
    exponent = int(exponent_text) if exponent_text else 0
    int_part, _, frac_part = mantissa.partition(".")
    digits = int_part + frac_part
    point = len(int_part) + exponent
    stripped = digits.lstrip("0")
    point -= len(digits) - len(stripped)
    return stripped.rstrip("0"), point

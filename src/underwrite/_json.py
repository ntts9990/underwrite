"""Shared strict JSON decoder for effectful local input boundaries."""

from __future__ import annotations

import json
import math


class DecodeError(ValueError):
    """Source bytes are not strict UTF-8 JSON within the configured depth."""


def _check_depth(text: str, max_depth: int) -> None:
    """Check container nesting before json.loads can exhaust its call stack."""
    depth = 0
    quoted = False
    escaped = False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > max_depth:
                raise DecodeError("DEPTH_LIMIT_EXCEEDED")
        elif character in "]}":
            depth -= 1


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DecodeError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise DecodeError("NON_FINITE_JSON_NUMBER")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise DecodeError("NON_FINITE_JSON_NUMBER")
    return number


def decode_json(data: bytes, max_depth: int) -> object:
    try:
        text = data.decode("utf-8", errors="strict")
        _check_depth(text, max_depth)
        decoded: object = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
        # Escaped lone surrogates survive json.loads but are not Unicode scalars.
        json.dumps(decoded, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return decoded
    except DecodeError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DecodeError("INVALID_JSON_INPUT") from exc

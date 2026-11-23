"""Model-visible JSON escaping preserves parsed values and closes raw controls."""

from __future__ import annotations

import json

from underwrite_core.canonical import canonical_bytes

from underwrite._model_json_text import escape_text_controls, has_text_controls


def test_escape_controls_preserves_json_value_including_astral_format_character() -> None:
    value = {"content": {"next_action": "PRIVATE_CANARY\u202e\u2028\u2029\U000e0001\u0085"}}
    canonical = canonical_bytes(value).decode("utf-8")
    assert has_text_controls(canonical)
    escaped = escape_text_controls(canonical)
    assert not has_text_controls(escaped)
    assert json.loads(escaped) == value
    assert "\\u202e\\u2028\\u2029\\udb40\\udc01\\u0085" in escaped
    assert escape_text_controls(escaped) == escaped

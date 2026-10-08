"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeGuard

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from underwrite_core.boundary import GATE_VOCAB
from underwrite_core.canonical import digest_bytes

from underwrite.instrument.ingest.formats import native_codecs
from underwrite.instrument.ingest.project import Source
from underwrite.instrument.ingest.transport import Ingestor

_ROOT_MARKER = "pyproject.toml"


def _repo_root() -> Path:
    """Find the nearest repository manifest above this test."""
    start = Path(__file__).resolve().parent
    for candidate in (start, *start.parents):
        if (candidate / _ROOT_MARKER).is_file():
            return candidate
    raise RuntimeError(f"could not find {_ROOT_MARKER!r} above {start}")


REPO_ROOT = _repo_root()
SCHEMA_PATH = REPO_ROOT / "contracts" / "observation.v1.schema.json"
GOLDEN_PATH = REPO_ROOT / "fixtures/examples/measurement/bundle.json"


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validator() -> Draft202012Validator:
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate(instance: dict[str, Any]) -> None:
    """Standalone behavior and boundary checks."""
    _validator().validate(instance)  # pyright: ignore[reportUnknownMemberType]


def _minimal_observation(kind: str) -> dict[str, Any]:
    return {
        "schema": "observation.v1",
        "source": {"format": "test.fixture", "format_version": "v1"},
        "kind": kind,
        "payload": {},
        "raw": {"ref": "test://fixture", "hash": "sha256:" + "ab" * 32},
        "normalization": {"nfc": True},
    }


def _is_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    """`isinstance(value, dict)`, narrowed to `dict[str, object]` (mirrors
    underwrite_core.canonical._is_object_dict) so callers type-check with no
    further `Any`."""
    return isinstance(value, dict)


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    """Like `_is_object_dict`, for list-shaped JSON nodes."""
    return isinstance(value, list)


def _count_id_keys(node: object) -> int:
    """Recursive count of every `$id` key anywhere in `node` (dicts + lists)."""
    if _is_object_dict(node):
        count = 1 if "$id" in node else 0
        for value in node.values():
            count += _count_id_keys(value)
        return count
    if _is_object_list(node):
        return sum(_count_id_keys(item) for item in node)
    return 0


# ---------------------------------------------------------------------------
# (a) schema itself is a valid Draft 2020-12 schema
# ---------------------------------------------------------------------------


def test_schema_itself_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(_schema())


# ---------------------------------------------------------------------------
# (b) exactly one $id, matching the house urn pattern
# ---------------------------------------------------------------------------


def test_schema_has_exactly_one_id_matching_house_pattern() -> None:
    schema = _schema()
    assert _count_id_keys(schema) == 1
    assert schema["$id"] == "urn:underwrite:contract:observation.v1"


# ---------------------------------------------------------------------------
# (c) every declared kind validates in a minimal observation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", _schema()["properties"]["kind"]["enum"])
def test_every_declared_kind_validates(kind: str) -> None:
    _validate(_minimal_observation(kind))


def test_declared_observation_kinds_match_the_contract() -> None:
    expected = {
        # OpenInference's 10 span kinds
        "LLM",
        "AGENT",
        "TOOL",
        "RETRIEVER",
        "EMBEDDING",
        "CHAIN",
        "GUARDRAIL",
        "EVALUATOR",
        "PROMPT",
        "RERANKER",
        # This app's 3 extensions
        "eval_run",
        "incident_record",
        "human_correction",
    }
    assert set(_schema()["properties"]["kind"]["enum"]) == expected


# ---------------------------------------------------------------------------
# (d) rejections -- identical shape (mutate a minimal observation, expect
# ValidationError), parametrized over the mutation instead of repeated per-case
# copies of the same three lines.
# ---------------------------------------------------------------------------


def _missing_raw_hash(observation: dict[str, Any]) -> None:
    del observation["raw"]["hash"]


def _wrong_digest_shape(observation: dict[str, Any]) -> None:
    observation["raw"]["hash"] = "sha256:abc"


def _extra_top_level_property(observation: dict[str, Any]) -> None:
    observation["unexpected_field"] = "nope"


def _extra_property_inside_raw(observation: dict[str, Any]) -> None:
    observation["raw"]["unexpected_field"] = "nope"


def _normalization_nfc_false(observation: dict[str, Any]) -> None:
    observation["normalization"]["nfc"] = False


def _unknown_kind(observation: dict[str, Any]) -> None:
    observation["kind"] = "NOT_A_DECLARED_KIND"


def _empty_source_format(observation: dict[str, Any]) -> None:
    observation["source"]["format"] = ""


@pytest.mark.parametrize(
    ("mutate", "validators", "path"),
    [
        (_missing_raw_hash, {"required"}, ("raw",)),
        (_wrong_digest_shape, {"pattern"}, ("raw", "hash")),
        (_extra_top_level_property, {"additionalProperties"}, ()),
        (_extra_property_inside_raw, {"additionalProperties"}, ("raw",)),
        (_normalization_nfc_false, {"const"}, ("normalization", "nfc")),
        (_unknown_kind, {"enum"}, ("kind",)),
        (_empty_source_format, {"minLength", "pattern"}, ("source", "format")),
    ],
)
def test_invalid_observation_is_rejected(
    mutate: Callable[[dict[str, Any]], None], validators: set[str], path: tuple[str, ...]
) -> None:
    observation = _minimal_observation("LLM")
    _validate(observation)
    mutate(observation)
    with pytest.raises(ValidationError) as error:
        _validate(observation)
    assert error.value.validator in validators
    assert tuple(error.value.absolute_path) == path


# ---------------------------------------------------------------------------
# (e) NaN/Inf: the JSON grammar itself has no such literal; the projector's
# enforcement point is `json.dumps(..., allow_nan=False)` raising before a
# non-finite float ever reaches canonicalization.
# ---------------------------------------------------------------------------


def test_nan_payload_cannot_even_serialize_with_allow_nan_false() -> None:
    with pytest.raises(ValueError):
        json.dumps({"x": float("nan")}, allow_nan=False)


def test_inf_payload_cannot_even_serialize_with_allow_nan_false() -> None:
    with pytest.raises(ValueError):
        json.dumps({"x": float("inf")}, allow_nan=False)


# ---------------------------------------------------------------------------
# (f) SURFACE: the registered reader and actual ingest seam project the pinned

# ---------------------------------------------------------------------------


def test_registered_sample_run_surface_validates_without_gate_vocab() -> None:
    raw_bytes = GOLDEN_PATH.read_bytes()
    source = Source("underwrite.evidence-bundle", "v1", f"file://{GOLDEN_PATH.as_posix()}")
    ingestor = Ingestor(SCHEMA_PATH, native_codecs(), max_bytes=len(raw_bytes), max_depth=64)
    observation = ingestor.file(GOLDEN_PATH, source).observation

    _validate(observation)

    assert observation["source"] == {"format": "underwrite.evidence-bundle", "format_version": "v1"}
    assert observation["kind"] == "eval_run"
    assert observation["raw"] == {
        "ref": f"file://{GOLDEN_PATH.as_posix()}",
        "hash": digest_bytes(raw_bytes),
    }
    assert observation["normalization"] == {"nfc": True}
    # Source words may remain in payload, never the projection's structural keys.
    assert not any(key in GATE_VOCAB for key in observation)


# ---------------------------------------------------------------------------
# (g) UNSUPPORTED_FORMAT is a reader concern, not a schema restriction: the

# cannot be faked by a schema-level allowlist).
# ---------------------------------------------------------------------------


def test_format_version_is_not_restricted_to_an_enum_or_const() -> None:
    format_version_schema = _schema()["$defs"]["source"]["properties"]["format_version"]
    assert "enum" not in format_version_schema
    assert "const" not in format_version_schema

"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, TypeGuard

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from underwrite_core.canonical import SAFE_INT_MAX, canonical_bytes, content_digest, digest_bytes

from underwrite.instrument.ingest import project
from underwrite.instrument.ingest.project import Projection, ProjectionError, Source, SourceError
from underwrite.instrument.ingest.schema import ObservationValidationError, SchemaConfigurationError
from underwrite.instrument.ingest.transport import (
    DecodeError,
    Ingestor,
    TransportError,
    UnsupportedFormat,
)


def _root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise AssertionError("repository root unavailable")


SCHEMA = _root() / "contracts/observation.v1.schema.json"
GOLDEN = _root() / "fixtures/examples/transport.json"
SOURCE = Source("underwrite.evidence-bundle", "v1", "urn:sample:run:candidate")


def _is_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def _object(value: object) -> dict[str, object]:
    assert _is_dict(value)
    return value


def _payload_codec(value: object) -> Projection:
    return Projection("eval_run", _object(value))


def _sample_codec(value: object) -> Projection:
    run = _object(value)
    return Projection("eval_run", {"run_id": run["run_id"], "model": run["model"]})


def _ingestor(*, max_bytes: int = 8192, max_depth: int = 32) -> Ingestor:
    return Ingestor(
        SCHEMA,
        {("underwrite.evidence-bundle", "v1"): _payload_codec},
        max_bytes=max_bytes,
        max_depth=max_depth,
    )


def test_actual_golden_file_and_http_have_identical_entire_observation(tmp_path: Path) -> None:
    body = GOLDEN.read_bytes()
    path = tmp_path / "received.json"
    path.write_bytes(body)
    ingest = Ingestor(
        SCHEMA,
        {("underwrite.evidence-bundle", "v1"): _sample_codec},
        max_bytes=len(body),
        max_depth=32,
    )
    source = Source(SOURCE.format, SOURCE.format_version, SOURCE.ref, "s3://archive/run.v1.json")
    local = ingest.file(path, source)
    remote = ingest.http_body(body, "https://receiver.example/envelope", source)
    assert local.transport_locator == str(path)
    assert remote.transport_locator == "https://receiver.example/envelope"
    assert local.observation == remote.observation
    assert canonical_bytes(local.observation) == canonical_bytes(remote.observation)
    assert content_digest(local.observation) == content_digest(remote.observation)
    assert local.observation == {
        "schema": "observation.v1",
        "source": {"format": "underwrite.evidence-bundle", "format_version": "v1"},
        "kind": "eval_run",
        "payload": {"run_id": "candidate", "model": {"spec": "mock:golden"}},
        "raw": {"ref": source.ref, "hash": digest_bytes(body), "locator": source.locator},
        "normalization": {"nfc": True},
    }


def test_source_locator_is_optional_and_changes_observation_identity() -> None:
    ingest = _ingestor()
    absent = ingest.http_body(b"{}", "receipt", SOURCE).observation
    explicit = ingest.http_body(
        b"{}", "receipt", Source(SOURCE.format, SOURCE.format_version, SOURCE.ref, "s3://other")
    ).observation
    assert "locator" not in _object(absent["raw"])
    assert _object(explicit["raw"])["locator"] == "s3://other"
    assert content_digest(absent) != content_digest(explicit)


def test_explicit_none_reference_uses_raw_hash_once(monkeypatch: pytest.MonkeyPatch) -> None:
    hashed: list[bytes] = []

    def counted(data: bytes) -> str:
        hashed.append(data)
        return digest_bytes(data)

    monkeypatch.setattr(project, "digest_bytes", counted)
    source = Source(SOURCE.format, SOURCE.format_version, None)
    result = _ingestor().http_body(b"{}", "receipt", source).observation
    assert result["raw"] == {"ref": digest_bytes(b"{}"), "hash": digest_bytes(b"{}")}
    assert hashed == [b"{}"]


@pytest.mark.parametrize("field", ["format", "format_version", "ref", "locator"])
@pytest.mark.parametrize(
    "value,reason",
    [
        ("", "EMPTY_REFERENCE"),
        (" \n", "EMPTY_REFERENCE"),
        ("\ud800", "INVALID_REFERENCE_UNICODE"),
        ("\udfff", "INVALID_REFERENCE_UNICODE"),
        ("cafe\u0301", "NON_NFC_REFERENCE"),
    ],
)
def test_all_source_metadata_rejects_blank_nonscalar_and_non_nfc(
    field: str, value: str, reason: str
) -> None:
    fields = {
        "format": SOURCE.format,
        "format_version": SOURCE.format_version,
        "ref": "reference",
        "locator": "archive",
    }
    fields[field] = value
    with pytest.raises(SourceError) as caught:
        project.project(Projection("eval_run", {}), Source(**fields), b"{}")
    assert caught.value.args == (reason,)
    if reason == "INVALID_REFERENCE_UNICODE":
        assert isinstance(caught.value.__cause__, UnicodeEncodeError)
    else:
        assert caught.value.__cause__ is None


@pytest.mark.parametrize("field", ["format", "format_version"])
def test_only_explicit_reference_and_locator_allow_none(field: str) -> None:
    fields: dict[str, Any] = {
        "format": SOURCE.format,
        "format_version": SOURCE.format_version,
        "ref": None,
        "locator": None,
    }
    fields[field] = None
    with pytest.raises(SourceError):
        project.project(Projection("eval_run", {}), Source(**fields), b"{}")


def test_reference_positional_argument_remains_required() -> None:
    with pytest.raises(TypeError):
        Source("underwrite.evidence-bundle", "v1")  # pyright: ignore[reportCallIssue]


def test_body_capture_file_shares_limits_and_retains_supplied_label(tmp_path: Path) -> None:
    path = tmp_path / "capture"
    path.write_bytes(b"{}")
    ingest = _ingestor(max_bytes=len(b"{}"))
    result = ingest.http_body_file(path, "received:one", SOURCE)
    assert result == ingest.http_body(b"{}", "received:one", SOURCE)
    assert result.observation == ingest.file(path, SOURCE).observation
    assert result.transport_locator == "received:one"
    path.write_bytes(b"{} ")
    with pytest.raises(TransportError, match="^BYTE_LIMIT_EXCEEDED$"):
        ingest.http_body_file(path, "received:two", SOURCE)
    with pytest.raises(TransportError, match="^SOURCE_READ_FAILED$"):
        ingest.http_body_file(tmp_path / "absent", "received:two", SOURCE)
    with pytest.raises(UnsupportedFormat):
        ingest.http_body_file(tmp_path / "absent", "label", Source("unknown", "v1", "ref"))


@pytest.mark.parametrize(
    "source",
    [
        Source("unknown", "v1", "ref"),
        Source("underwrite.evidence-bundle", "v2", "ref"),
    ],
)
def test_unsupported_pair_is_rejected_before_decode_or_projection(
    source: Source,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("unsupported input reached decoding or projection")

    ingest = _ingestor()
    monkeypatch.setattr(json, "loads", forbidden)
    monkeypatch.setattr(project, "project", forbidden)
    with pytest.raises(UnsupportedFormat) as error:
        ingest.http_body(b"\xff", "receipt", source)
    assert error.value.args == ("UNSUPPORTED_FORMAT",)
    assert isinstance(error.value.__cause__, KeyError)
    with pytest.raises(UnsupportedFormat):
        ingest.file(tmp_path / "missing", source)


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"\xff", "INVALID_JSON_INPUT"),
        (b"{", "INVALID_JSON_INPUT"),
        (b'{"x":1,"x":2}', "DUPLICATE_JSON_KEY"),
        (b'{"x":{"y":1,"y":2}}', "DUPLICATE_JSON_KEY"),
        (b'{"x":NaN}', "NON_FINITE_JSON_NUMBER"),
        (b'{"x":Infinity}', "NON_FINITE_JSON_NUMBER"),
        (b'{"x":-Infinity}', "NON_FINITE_JSON_NUMBER"),
        (b'{"x":1e999}', "NON_FINITE_JSON_NUMBER"),
        (b'{"x":-1e999}', "NON_FINITE_JSON_NUMBER"),
        (b'{"x":"\\ud800"}', "INVALID_JSON_INPUT"),
        (b'{"\\udfff":0}', "INVALID_JSON_INPUT"),
        (b"{} {}", "INVALID_JSON_INPUT"),
        (b'"raw\nnewline"', "INVALID_JSON_INPUT"),
        (b"\xef\xbb\xbf{}", "INVALID_JSON_INPUT"),
    ],
)
def test_malformed_input_never_reaches_codec_or_projector(
    body: bytes,
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> Projection:
        pytest.fail("malformed input reached codec or projector")

    ingest = Ingestor(
        SCHEMA,
        {("underwrite.evidence-bundle", "v1"): forbidden},
        max_bytes=1024,
        max_depth=8,
    )
    monkeypatch.setattr(project, "project", forbidden)
    with pytest.raises(DecodeError) as error:
        ingest.http_body(body, "receipt", SOURCE)
    assert error.value.args == (reason,)
    if reason == "INVALID_JSON_INPUT":
        assert isinstance(error.value.__cause__, (UnicodeError, json.JSONDecodeError))
    else:
        assert error.value.__cause__ is None


def test_exact_container_depth_limit_and_quoted_delimiters(monkeypatch: pytest.MonkeyPatch) -> None:
    ingest = _ingestor(max_depth=2)
    assert ingest.http_body(b'{"x":["{[\\"\\\\"]}', "receipt", SOURCE).observation

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("excessive nesting reached projection")

    monkeypatch.setattr(project, "project", forbidden)
    with pytest.raises(DecodeError) as error:
        ingest.http_body(b'{"x":[{}]}', "receipt", SOURCE)
    assert error.value.args == ("DEPTH_LIMIT_EXCEEDED",)


@pytest.mark.parametrize(
    "body",
    [
        b'{"":[],"next":[]}',
        b'{"x":[0],"y":{},"z":[]}',
        b'{"x":"\\"[[[[[[[[","next":[]}',
        b'{"x":"\\\\","next":{}}',
    ],
)
def test_depth_counts_containers_independently_of_strings_and_siblings(body: bytes) -> None:
    observation = _ingestor(max_depth=2).http_body(body, "receipt", SOURCE).observation
    assert observation["payload"] == json.loads(body)


@pytest.mark.parametrize(
    "body",
    [
        b'{"":[],"next":[{}]}',
        b'{"x":{},"next":[{}]}',
        b'{"x":"\\\\","next":[{}]}',
    ],
)
def test_previous_sibling_cannot_lower_the_next_siblings_depth(body: bytes) -> None:
    with pytest.raises(DecodeError) as error:
        _ingestor(max_depth=2).http_body(body, "receipt", SOURCE)
    assert error.value.args == ("DEPTH_LIMIT_EXCEEDED",)


@settings(max_examples=30, derandomize=True)
@given(st.text(alphabet='a"\\[]{}\n\t', max_size=40))
def test_json_string_escaping_does_not_change_container_depth(text: str) -> None:
    payload: dict[str, object] = {text: text, "nested": {"items": []}}
    body = json.dumps(payload).encode()
    result = _ingestor(max_depth=3).http_body(body, "receipt", SOURCE)
    assert result.observation["payload"] == payload
    with pytest.raises(DecodeError) as error:
        _ingestor(max_depth=2).http_body(body, "receipt", SOURCE)
    assert error.value.args == ("DEPTH_LIMIT_EXCEEDED",)


@pytest.mark.parametrize("limit_name", ["max_bytes", "max_depth"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_limits_must_be_positive_integers(limit_name: str, value: Any) -> None:
    # Any deliberately exercises invalid runtime calls outside the typed API.
    limits = {"max_bytes": 1024, "max_depth": 8}
    limits[limit_name] = value
    with pytest.raises(ValueError) as error:
        Ingestor(SCHEMA, {}, **limits)
    assert error.value.args == ("INGEST_LIMIT_MUST_BE_POSITIVE_INTEGER",)


def test_smallest_positive_limits_accept_one_byte_json() -> None:
    def codec(value: object) -> Projection:
        return Projection("eval_run", {"value": value})

    ingest = Ingestor(
        SCHEMA, {("underwrite.evidence-bundle", "v1"): codec}, max_bytes=1, max_depth=1
    )
    assert ingest.http_body(b"0", "receipt", SOURCE).observation["payload"] == {"value": 0}


def test_byte_limit_is_identical_for_file_and_http(tmp_path: Path) -> None:
    ingest = _ingestor(max_bytes=len(b"{}"))
    path = tmp_path / "input.json"
    path.write_bytes(b"{}")
    assert (
        ingest.file(path, SOURCE).observation == ingest.http_body(b"{}", "http", SOURCE).observation
    )
    path.write_bytes(b"{} ")
    with pytest.raises(TransportError) as error:
        ingest.file(path, SOURCE)
    assert error.value.args == ("BYTE_LIMIT_EXCEEDED",)
    with pytest.raises(TransportError) as error:
        ingest.http_body(b"{} ", "http", SOURCE)
    assert error.value.args == ("BYTE_LIMIT_EXCEEDED",)
    with pytest.raises(TransportError) as error:
        ingest.file(tmp_path / "missing", SOURCE)
    assert error.value.args == ("SOURCE_READ_FAILED",)
    assert isinstance(error.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize("mode", ["file", "http_body_file"])
def test_file_read_is_bounded_at_limit_plus_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    limit = 8
    ingest = _ingestor(max_bytes=limit)
    path = tmp_path / "bounded"
    path.write_bytes(b" " * (limit + 20))
    original_read = os.read
    consumed = 0

    def read(fd: int, count: int) -> bytes:
        nonlocal consumed
        assert 0 < count <= limit + 1 - consumed
        result = original_read(fd, min(count, 3))
        consumed += len(result)
        return result

    monkeypatch.setattr(os, "read", read)
    with pytest.raises(TransportError, match="^BYTE_LIMIT_EXCEEDED$"):
        if mode == "file":
            ingest.file(path, SOURCE)
        else:
            ingest.http_body_file(path, "label", SOURCE)
    assert consumed == limit + 1


def test_nested_unicode_is_normalized_without_changing_raw_hash() -> None:
    body = '{"cafe\u0301":{"items":["A\u030a",{"e\u0301":"가"}]}}'.encode()
    observation = _ingestor().http_body(body, "receipt", SOURCE).observation
    assert observation["payload"] == {"café": {"items": ["Å", {"é": "가"}]}}
    assert _object(observation["raw"])["hash"] == digest_bytes(body)
    assert content_digest(observation) != digest_bytes(body)
    assert observation["normalization"] == {"nfc": True}


def test_payload_ref_is_inert_and_supplementary_unicode_is_valid() -> None:
    body = b'{"$ref":"https://untrusted.invalid/schema","emoji":"\\ud83d\\ude00"}'
    result = _ingestor().http_body(body, "receipt", SOURCE)
    assert result.observation["payload"] == {
        "$ref": "https://untrusted.invalid/schema",
        "emoji": "😀",
    }


def test_nfc_key_collision_is_rejected() -> None:
    with pytest.raises(ProjectionError) as error:
        _ingestor().http_body('{"é":1,"e\u0301":2}'.encode(), "receipt", SOURCE)
    assert error.value.args == ("NFC_KEY_COLLISION",)
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    "source",
    [
        Source("underwrite.evidence-bundle", "v1", "file:///cafe\u0301"),
        Source("underwrite.evidence-bundle", "v1", "ref", "file:///cafe\u0301"),
    ],
)
def test_source_references_are_rejected_instead_of_rewritten(source: Source) -> None:
    with pytest.raises(ProjectionError) as error:
        _ingestor().http_body(b"{}", "receipt", source)
    assert error.value.args == ("NON_NFC_REFERENCE",)


@pytest.mark.parametrize("field", ["ref", "locator"])
@pytest.mark.parametrize("value", [42, [], {}])
def test_invalid_source_reference_type_is_a_typed_projection_failure(
    field: str, value: Any
) -> None:
    # Any deliberately exercises invalid metadata at the runtime boundary.
    source = (
        Source(SOURCE.format, SOURCE.format_version, value)
        if field == "ref"
        else Source(SOURCE.format, SOURCE.format_version, SOURCE.ref, value)
    )
    with pytest.raises(ProjectionError) as error:
        _ingestor().http_body(b"{}", "receipt", source)
    assert error.value.args == ("INVALID_PROJECTION",)
    assert isinstance(error.value.__cause__, TypeError)


@pytest.mark.parametrize("value", [SAFE_INT_MAX + 1, -(SAFE_INT_MAX + 1), float(SAFE_INT_MAX + 2)])
def test_canonical_numeric_rejections_survive_json_decode(value: int | float) -> None:
    with pytest.raises(ProjectionError) as error:
        _ingestor().http_body(json.dumps({"value": value}).encode(), "receipt", SOURCE)
    assert error.value.args == ("INVALID_PROJECTION",)
    assert error.value.__cause__ is not None
    assert error.value.__cause__.args == ("INT_OUT_OF_SAFE_RANGE",)


@pytest.mark.parametrize("value", [SAFE_INT_MAX, -SAFE_INT_MAX, 0.125, False, None])
def test_canonical_numeric_boundary_and_scalar_values_are_preserved(value: object) -> None:
    result = _ingestor().http_body(json.dumps({"value": value}).encode(), "receipt", SOURCE)
    assert result.observation["payload"] == {"value": value}


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"value": float("nan")}, "INVALID_PROJECTION"),
        ({"value": float("inf")}, "INVALID_PROJECTION"),
        ({"value": object()}, "INVALID_PROJECTION"),
        ({"value": "\ud800"}, "INVALID_PROJECTION"),
        ({"value": (1, 2)}, "INVALID_PROJECTION"),
        ({1: "not a string key"}, "NON_STRING_KEY"),
    ],
)
def test_codec_cannot_inject_values_outside_canonical_json(
    payload: dict[object, object],
    reason: str,
) -> None:
    def codec(value: object) -> Projection:
        return Projection("eval_run", _object(payload))

    ingest = Ingestor(
        SCHEMA, {("underwrite.evidence-bundle", "v1"): codec}, max_bytes=32, max_depth=8
    )
    with pytest.raises(ProjectionError) as error:
        ingest.http_body(b"{}", "receipt", SOURCE)
    assert error.value.args == (reason,)
    if reason == "INVALID_PROJECTION":
        assert error.value.__cause__ is not None


def test_schema_is_loaded_once_and_each_projection_is_validated_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    schema_path = tmp_path / "observation.json"
    schema_path.write_bytes(SCHEMA.read_bytes())
    ingest = Ingestor(
        schema_path,
        {("underwrite.evidence-bundle", "v1"): _payload_codec},
        max_bytes=32,
        max_depth=8,
    )
    schema_path.unlink()
    calls: list[object] = []
    # jsonschema's validate method is an untyped third-party boundary.
    original = Draft202012Validator.validate  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

    def counted(self: Draft202012Validator, instance: object, *args: Any, **kwargs: Any) -> None:
        calls.append(instance)
        original(self, instance, *args, **kwargs)

    monkeypatch.setattr(Draft202012Validator, "validate", counted)
    first = ingest.http_body(b"{}", "first", SOURCE)
    second = ingest.http_body(b"{}", "second", SOURCE)
    assert calls == [first.observation, second.observation]


def test_invalid_projected_kind_is_rejected_by_actual_schema() -> None:
    def codec(value: object) -> Projection:
        return Projection("unknown_kind", {})

    ingest = Ingestor(
        SCHEMA, {("underwrite.evidence-bundle", "v1"): codec}, max_bytes=32, max_depth=8
    )
    with pytest.raises(ObservationValidationError) as error:
        ingest.http_body(b"{}", "receipt", SOURCE)
    assert error.value.args == ("INVALID_OBSERVATION",)
    assert isinstance(error.value.__cause__, ValidationError)
    assert list(error.value.__cause__.path) == ["kind"]


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"{", "INVALID_OBSERVATION_SCHEMA"),
        (b"false", "WRONG_OBSERVATION_CONTRACT"),
        (b"{}", "WRONG_OBSERVATION_CONTRACT"),
        (b"[]", "WRONG_OBSERVATION_CONTRACT"),
        (b"\xff", "INVALID_OBSERVATION_SCHEMA"),
    ],
)
def test_wrong_schema_fails_at_configuration(tmp_path: Path, body: bytes, reason: str) -> None:
    path = tmp_path / "schema.json"
    path.write_bytes(body)
    with pytest.raises(SchemaConfigurationError) as error:
        Ingestor(path, {}, max_bytes=32, max_depth=8)
    assert error.value.args == (reason,)
    if reason == "INVALID_OBSERVATION_SCHEMA":
        assert isinstance(error.value.__cause__, (UnicodeError, json.JSONDecodeError))
    else:
        assert error.value.__cause__ is None


@pytest.mark.parametrize(
    ("fragment", "reason"),
    [
        ({"type": "not-a-schema-type"}, "INVALID_OBSERVATION_SCHEMA"),
        ({"$ref": "https://untrusted.invalid/schema"}, "NON_LOCAL_SCHEMA_REFERENCE"),
        ({"$dynamicRef": "https://untrusted.invalid/schema"}, "NON_LOCAL_SCHEMA_REFERENCE"),
        ({"$ref": "other.json#/$defs/local"}, "NON_LOCAL_SCHEMA_REFERENCE"),
        ({"$ref": False}, "NON_LOCAL_SCHEMA_REFERENCE"),
        ({"$id": "urn:other:schema", "$ref": "#"}, "NESTED_SCHEMA_RESOURCE"),
        ({"allOf": [{"$ref": "https://untrusted.invalid/schema"}]}, "NON_LOCAL_SCHEMA_REFERENCE"),
    ],
)
def test_schema_rejects_invalid_types_external_refs_and_nested_resources(
    tmp_path: Path,
    fragment: dict[str, object],
    reason: str,
) -> None:
    schema = _object(json.loads(SCHEMA.read_bytes()))
    _object(schema["$defs"])["injected"] = fragment
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(schema), encoding="utf-8")
    with pytest.raises(SchemaConfigurationError) as error:
        Ingestor(path, {}, max_bytes=32, max_depth=8)
    assert error.value.args == (reason,)
    if reason == "INVALID_OBSERVATION_SCHEMA":
        assert isinstance(error.value.__cause__, SchemaError)
    else:
        assert error.value.__cause__ is None


def test_valid_internal_schema_reference_inside_a_list(tmp_path: Path) -> None:
    schema = _object(json.loads(SCHEMA.read_bytes()))
    schema["allOf"] = [{"$ref": "#/$defs/extra"}]
    _object(schema["$defs"])["extra"] = {"type": "object"}
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(schema), encoding="utf-8")
    ingest = Ingestor(
        path, {("underwrite.evidence-bundle", "v1"): _payload_codec}, max_bytes=32, max_depth=8
    )
    assert ingest.http_body(b"{}", "receipt", SOURCE).observation["payload"] == {}


def test_unresolved_internal_reference_is_a_typed_configuration_error(tmp_path: Path) -> None:
    schema = _object(json.loads(SCHEMA.read_bytes()))
    schema["allOf"] = [{"$ref": "#/$defs/missing"}]
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(schema), encoding="utf-8")
    ingest = Ingestor(
        path, {("underwrite.evidence-bundle", "v1"): _payload_codec}, max_bytes=32, max_depth=8
    )
    with pytest.raises(SchemaConfigurationError) as error:
        ingest.http_body(b"{}", "receipt", SOURCE)
    assert error.value.args == ("OBSERVATION_SCHEMA_EVALUATION_FAILED",)
    assert error.value.__cause__ is not None
    assert not isinstance(error.value.__cause__, ValidationError)


def test_missing_schema_is_configuration_failure(tmp_path: Path) -> None:
    with pytest.raises(SchemaConfigurationError) as error:
        Ingestor(tmp_path / "missing", {}, max_bytes=32, max_depth=8)
    assert error.value.args == ("INVALID_OBSERVATION_SCHEMA",)
    assert isinstance(error.value.__cause__, FileNotFoundError)


def test_codec_registry_is_snapshotted_and_dispatch_preserves_exact_version() -> None:
    def alternate_codec(value: object) -> Projection:
        return Projection("LLM", {"version_specific": value})

    codecs = {
        ("underwrite.evidence-bundle", "v1"): _payload_codec,
        ("underwrite.evidence-bundle", "v2"): alternate_codec,
    }
    ingest = Ingestor(SCHEMA, codecs, max_bytes=32, max_depth=8)
    codecs.clear()
    first = ingest.http_body(b"{}", "receipt", SOURCE).observation
    version_two = Source(SOURCE.format, "v2", SOURCE.ref)
    second = ingest.http_body(b"{}", "receipt", version_two).observation
    assert first["kind"] == "eval_run"
    assert first["payload"] == {}
    assert second["kind"] == "LLM"
    assert second["payload"] == {"version_specific": {}}
    assert second["source"] == {"format": "underwrite.evidence-bundle", "format_version": "v2"}

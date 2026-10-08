"""The one CLI local JSON object reader: fixed reasons, the codes they imply, no echo; and
the one rule by which a command raises those failures and schema refusals as its own error."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from underwrite_core.canonical import content_digest

from underwrite.cli.local_json import LocalJsonError, read_document, read_object, validate
from underwrite.instrument.ingest.schema import SchemaConfigurationError

LIMIT = 64
DEPTH = 3


def _failure(path: Path, max_bytes: int = LIMIT, max_depth: int = DEPTH) -> LocalJsonError:
    with pytest.raises(LocalJsonError) as caught:
        read_object(path, max_bytes, max_depth)
    error = caught.value
    assert error.args == (error.reason,)
    assert error.__cause__ is None and (error.__context__ is None or error.__suppress_context__)
    return error


def test_object_and_its_canonical_digest(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(b'{"b": [1, {"c": null}], "a": "x"}')
    document, digest = read_object(path, LIMIT, DEPTH)
    assert document == {"a": "x", "b": [1, {"c": None}]}
    assert digest == content_digest(document)


def test_exact_byte_and_depth_limits_are_admitted(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    raw = b'{"a":[{"b":1}]}'
    path.write_bytes(raw)
    assert read_object(path, len(raw), DEPTH)[0] == {"a": [{"b": 1}]}
    error = _failure(path, max_bytes=len(raw) - 1)
    assert (error.reason, error.code) == ("BYTE_LIMIT_EXCEEDED", "INPUT_LIMIT_EXCEEDED")
    error = _failure(path, max_depth=DEPTH - 1)
    assert (error.reason, error.code) == ("DEPTH_LIMIT_EXCEEDED", "INPUT_LIMIT_EXCEEDED")


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"", "INVALID_JSON_INPUT"),
        (b'{"secret": ', "INVALID_JSON_INPUT"),
        (b'{"a": 1, "a": 2}', "INVALID_JSON_INPUT"),
        (b'{"a": NaN}', "INVALID_JSON_INPUT"),
        (b'{"a": "\xff"}', "INVALID_JSON_INPUT"),
        (b'{"a": "\\ud800"}', "INVALID_JSON_INPUT"),
        (b"[1]", "OBJECT_REQUIRED"),
        (b'"secret"', "OBJECT_REQUIRED"),
        (b'{"a": 9007199254740993}', "NONCANONICAL_INPUT"),
    ],
)
def test_invalid_input_leaves_the_code_to_the_command(
    raw: bytes, reason: str, tmp_path: Path
) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(raw)
    error = _failure(path)
    assert (error.reason, error.code) == (reason, None)
    assert "secret" not in repr(error)


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("missing", "SOURCE_READ_FAILED"),
        ("unreadable", "SOURCE_READ_FAILED"),
        ("directory", "SOURCE_NOT_REGULAR_FILE"),
        ("fifo", "SOURCE_NOT_REGULAR_FILE"),
    ],
)
def test_source_failures(fault: str, reason: str, tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    if fault == "unreadable":
        if os.geteuid() == 0:
            pytest.fail("the unreadable-file case needs a non-root runner")
        path.write_bytes(b"{}")
        path.chmod(0)
    elif fault == "directory":
        path.mkdir()
    elif fault == "fifo":
        os.mkfifo(path)
    error = _failure(path)
    assert (error.reason, error.code) == (reason, "SOURCE_READ_FAILED")


@pytest.mark.parametrize("flag", ["O_NONBLOCK", "O_NOCTTY"])
def test_platform_without_nonblocking_open(
    flag: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(b"{}")
    monkeypatch.delattr(os, flag)
    error = _failure(path)
    assert (error.reason, error.code) == ("UNSUPPORTED_PLATFORM", "UNSUPPORTED_PLATFORM")


class CommandError(ValueError):
    def __init__(self, code: str, reason: str, location: str) -> None:
        super().__init__(code, reason, location)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"[]", ("INVALID_X", "OBJECT_REQUIRED", "/x")),
        (b"[[[[]]]]", ("INPUT_LIMIT_EXCEEDED", "DEPTH_LIMIT_EXCEEDED", "/x")),
    ],
)
def test_read_document_raises_the_command_s_error_at_its_location(
    raw: bytes, expected: tuple[str, str, str], tmp_path: Path
) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(raw)
    with pytest.raises(CommandError) as caught:
        read_document(path, LIMIT, DEPTH, "/x", "INVALID_X", CommandError)
    assert caught.value.args == expected
    assert caught.value.__suppress_context__


def test_read_document_puts_an_unsupported_platform_at_no_location(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(b"{}")
    assert read_document(path, LIMIT, DEPTH, "/x", "INVALID_X", CommandError)[0] == {}
    monkeypatch.delattr(os, "O_NONBLOCK")
    with pytest.raises(CommandError) as caught:
        read_document(path, LIMIT, DEPTH, "/x", "INVALID_X", CommandError)
    assert caught.value.args == ("UNSUPPORTED_PLATFORM", "UNSUPPORTED_PLATFORM", "")


def test_validate_refuses_as_the_command_and_a_broken_validator_is_configuration() -> None:
    checker = Draft202012Validator({"type": "object"})
    validate(checker, {}, CommandError, "INVALID_X", "/x")
    with pytest.raises(CommandError) as caught:
        validate(checker, [], CommandError, "INVALID_X", "/x")
    assert caught.value.args == ("INVALID_X", "SCHEMA_VALIDATION_FAILED", "/x")
    broken = Draft202012Validator({"$ref": "#/$defs/absent"})
    with pytest.raises(SchemaConfigurationError, match="SCHEMA_EVALUATION_FAILED"):
        validate(broken, {}, CommandError, "INVALID_X", "/x")

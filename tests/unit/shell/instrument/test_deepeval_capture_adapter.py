"""DeepEval buffered accounting retains the existing file adapter's contracts."""

from pathlib import Path
from typing import Any, cast

import pytest

from underwrite.instrument.ingest import deepeval_accounting as adapter
from underwrite.instrument.ingest import deepeval_compat as current
from underwrite.instrument.ingest.transport import TransportError

GOLDEN = (
    Path(__file__).resolve().parents[4]
    / "fixtures/golden/external/deepeval/test_run_20260914_132634.json"
)


def test_buffered_matches_file_output() -> None:
    assert adapter.audit_deepeval_bytes(GOLDEN.read_bytes()) == adapter.audit_deepeval_file(GOLDEN)


@pytest.mark.parametrize("raw", [b"[", b"\xff", b"{}", b'{"x":1,"x":2}'])
def test_buffered_keeps_file_refusals(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "source"
    path.write_bytes(raw)
    with pytest.raises(adapter.DeepevalFileError) as file_error:
        adapter.audit_deepeval_file(path)
    with pytest.raises(adapter.DeepevalFileError) as byte_error:
        adapter.audit_deepeval_bytes(raw)
    assert vars(file_error.value) == vars(byte_error.value)


@pytest.mark.parametrize("axis", ["DEFAULT_MAX_BYTES", "DEFAULT_MAX_DEPTH"])
def test_buffered_enforces_existing_limits(axis: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(current, axis, 1)
    with pytest.raises(adapter.DeepevalFileError) as caught:
        adapter.audit_deepeval_bytes(GOLDEN.read_bytes())
    assert caught.value.code == "INPUT_LIMIT_EXCEEDED"
    assert caught.value.location == "/file"


@pytest.mark.parametrize(
    "reason,code",
    [
        ("UNSUPPORTED_PLATFORM", "UNSUPPORTED_PLATFORM"),
        ("SOURCE_NOT_REGULAR_FILE", "SOURCE_READ_FAILED"),
        ("BYTE_LIMIT_EXCEEDED", "INPUT_LIMIT_EXCEEDED"),
        ("private unknown reason", "SOURCE_READ_FAILED"),
    ],
)
def test_capture_read_reason_is_finite(
    reason: str, code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(path: Path, max_bytes: int) -> bytes:
        del path, max_bytes
        raise TransportError(reason)

    monkeypatch.setattr(adapter, "read_local_file", fail)
    with pytest.raises(adapter.DeepevalFileError) as caught:
        adapter.read_deepeval_file(GOLDEN)
    assert caught.value.code == code
    assert caught.value.reason == (code if reason == "private unknown reason" else reason)
    assert caught.value.location == "/file"


def test_old_source_file_and_bytes_are_identical() -> None:
    raw = GOLDEN.read_bytes()
    assert adapter.audit_deepeval_file(GOLDEN) == adapter.audit_deepeval_bytes(raw)


@pytest.mark.parametrize("version", ["4.1.1", "4.2.8", "latest", "private/version"])
def test_removed_version_refuses_before_ingestion(
    version: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object) -> bytes:
        pytest.fail("removed selector caused source read")

    monkeypatch.setattr(current, "read_deepeval_file", forbidden)
    for operation, argument in (
        (adapter.audit_deepeval_file, GOLDEN),
        (adapter.audit_deepeval_bytes, b"{}"),
    ):
        with pytest.raises(TypeError, match="version"):
            cast(Any, operation)(argument, version=version)


@pytest.mark.parametrize(
    "name", ["ordinary", "flaky_metric", "flaky_case", "score_without_verdict"]
)
def test_actual_field_era_file_and_bytes_parity(name: str) -> None:
    path = GOLDEN.parent.parent / "deepeval-4.2.8" / (name + ".json")
    raw = path.read_bytes()
    expected = adapter.audit_deepeval_file(path)
    assert adapter.audit_deepeval_bytes(raw) == expected
    assert expected["schema"] == "deepeval_accounting.v3"
    assert expected["source"] == {
        "format": "deepeval.test-run",
        "producer_version": None,
        "producer_version_state": "unknown",
        "claims_verified": False,
    }

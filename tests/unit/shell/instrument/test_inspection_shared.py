"""Shared buffers preserve original identity and conservative descriptor accounting."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from _inspection_cases import inspection_contracts as inspection_contracts
from _repo_paths import repo_root
from underwrite_core.canonical import content_digest, digest_bytes

from underwrite.instrument.ingest import inspection, project, transport
from underwrite.instrument.ingest.project import Projection, Source

ROOT = repo_root(Path(__file__).resolve())
pytestmark = pytest.mark.usefixtures("inspection_contracts")


@pytest.mark.parametrize("resource_fault", ["missing", "malformed"])
def test_schema_configuration_failure_precedes_malformed_manifest(
    tmp_path: Path,
    resource_fault: str,
) -> None:
    resource = tmp_path / "trusted/_contracts/artifact_case.v1.schema.json"
    if resource_fault == "missing":
        resource.unlink()
    else:
        resource.write_bytes(b"not a schema")
    manifest = tmp_path / "case.json"
    manifest.write_bytes(b"not a manifest")

    with pytest.raises(inspection.InspectionError) as caught:
        inspection.inspect_artifact(manifest)

    assert caught.value.payload == {
        "schema": "artifact_inspection_error.v1",
        "code": "SCHEMA_CONFIGURATION_ERROR",
        "reason": "INVALID_INSPECTION_SCHEMA",
        "location": "",
        "next_action": "REINSTALL_PACKAGE",
        "retryable": False,
        "exit_code": 2,
    }


def test_buffered_projection_is_single_pass_and_preserves_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Projection("eval_run", {"release": "e\u0301"})
    codec = Mock(return_value=original)
    ingest = transport.Ingestor(
        ROOT / "contracts/observation.v1.schema.json",
        {("test", "v1"): codec},
        max_bytes=100,
        max_depth=8,
    )
    decode = Mock(wraps=transport.decode_json)
    projecting = Mock(wraps=project.project)
    validate = Mock(wraps=ingest._schema.validate)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(transport, "decode_json", decode)
    monkeypatch.setattr(project, "project", projecting)
    monkeypatch.setattr(ingest._schema, "validate", validate)  # pyright: ignore[reportPrivateUsage]
    source = Source("test", "v1", None)
    result, retained = ingest.buffered_projection(b"{}", "buffer", source)
    assert retained is original and retained.payload == {"release": "e\u0301"}
    assert result.observation["payload"] == {"release": "é"}
    for call in (decode, codec, projecting, validate):
        assert call.call_count == 1
    assert ingest.http_body(b"{}", "buffer", source) == result


@pytest.mark.parametrize("raw", [b'{"schema":"run.v1"}', b"not json"])
def test_shared_summary_wraps_exact_c1_facts(raw: bytes) -> None:
    source = Source("underwrite.evidence-bundle", "v1", None)
    summary, diagnostics, projection = inspection.inspect_bytes(raw, source, None)
    report = inspection._report(b"manifest bytes", raw, source, None)  # pyright: ignore[reportPrivateUsage]
    assert report == {
        "schema": "artifact_inspection.v1",
        "manifest_hash": digest_bytes(b"manifest bytes"),
        "source": {"format": "underwrite.evidence-bundle", "format_version": "v1"},
        **summary,
        "diagnostics": diagnostics,
        "limitations": list(inspection.LIMITATIONS),
    }
    assert summary["raw_hash"] == digest_bytes(raw)
    if projection is not None:
        observation = project.project(projection, source, raw)
        assert summary["observation"] == {"kind": "eval_run", "digest": content_digest(observation)}
    else:
        assert summary["observation"] is None


def test_shared_mismatch_has_no_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = Mock(side_effect=AssertionError("must not ingest mismatched bytes"))
    monkeypatch.setattr(inspection, "packaged_ingestor", factory)
    summary, diagnostics, projection = inspection.inspect_bytes(
        b"{}", Source("underwrite.evidence-bundle", "v1", None), "sha256:" + "0" * 64
    )
    assert projection is None and summary["projection"] == "not_created"
    assert diagnostics[0]["reason"] == "EXPECTED_HASH_MISMATCH"
    factory.assert_not_called()


@pytest.mark.parametrize("raw", [b"", b"x", b"xy"])
def test_zero_allowance_checks_regular_descriptor_then_one_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
) -> None:
    (tmp_path / "artifact").write_bytes(raw)
    flags = inspection.constrained_flags()
    parent = os.open(tmp_path, flags[0])
    reading = Mock(wraps=os.read)
    monkeypatch.setattr(os, "read", reading)
    try:
        if raw:
            with pytest.raises(transport.TransportError, match="BYTE_LIMIT_EXCEEDED") as caught:
                inspection.read_at(parent, ["artifact"], 0, flags)
            assert caught.value.attempted_read is True
        else:
            assert inspection.read_at(parent, ["artifact"], 0, flags) == b""
        assert reading.call_count == 1 and reading.call_args.args[1] == 1
        with pytest.raises(OSError):
            os.fstat(reading.call_args.args[0])
        os.fstat(parent)
    finally:
        os.close(parent)


@pytest.mark.parametrize("stage", ["open", "fstat", "partial"])
@pytest.mark.parametrize("allowance", [0, 8])
def test_failure_marker_separates_open_from_attempt_and_closes_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    allowance: int,
) -> None:
    (tmp_path / "artifact").write_bytes(b"secret payload")
    flags = inspection.constrained_flags()
    parent = os.open(tmp_path, flags[0])
    real_open, real_fstat, real_read = os.open, os.fstat, os.read
    opened: list[int] = []

    def opening(*args: Any, **kwargs: Any) -> int:
        if stage == "open":
            raise OSError("private detail")
        fd = real_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def checking(fd: int) -> os.stat_result:
        if stage == "fstat":
            raise OSError("private detail")
        return real_fstat(fd)

    def reading(fd: int, amount: int) -> bytes:
        real_read(fd, min(amount, 1))
        raise OSError("private detail")

    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(os, "fstat", checking)
    monkeypatch.setattr(os, "read", reading)
    try:
        with pytest.raises(transport.TransportError, match="^SOURCE_READ_FAILED$") as caught:
            inspection.read_at(parent, ["artifact"], allowance, flags)
        assert caught.value.attempted_read is (stage != "open")
        assert vars(caught.value) == {"attempted_read": stage != "open"}
        for fd in opened:
            with pytest.raises(OSError):
                real_fstat(fd)
        real_fstat(parent)
    finally:
        os.close(parent)

"""Raw Promptfoo intake shares accounting while retaining manifest guarantees."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest
from _inspection_cases import case, update
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.cli import audit_counts, audit_promptfoo
from underwrite.cli.evidence import run_cli
from underwrite.instrument.evidence import accounting as pure
from underwrite.instrument.ingest import accounting as adapter
from underwrite.instrument.ingest.transport import TransportError

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "fixtures/golden/external/promptfoo/results.json"
ERROR_EXIT = 2


def files(tmp_path: Path, raw: bytes | None = None) -> tuple[Path, Path]:
    manifest = case(tmp_path, GOLDEN.read_bytes() if raw is None else raw)
    update(manifest, "source", {"format": adapter.SOURCE[0], "format_version": adapter.SOURCE[1]})
    return tmp_path / "artifact.json", manifest


def invoke(path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any], str, str]:
    code = audit_promptfoo.main([str(path), "--version", adapter.SOURCE[1], "--json"])
    text = capsys.readouterr()
    return code, json.loads(text.out if code == 0 else text.err), text.out, text.err


def test_raw_and_default_manifest_outputs_are_byte_identical(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact, manifest = files(tmp_path)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    code, report, stdout, stderr = invoke(artifact, capsys)
    assert code == 0 and stderr == ""
    assert stdout.encode() == canonical_bytes(report) + b"\n"
    assert report["raw_hash"] == digest_bytes(artifact.read_bytes())
    assert audit_counts.main([str(manifest), "--json"]) == 0
    old = capsys.readouterr()
    assert old.out == stdout and old.err == ""
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_explicit_manifest_metadata_changes_digest_not_accounting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact, manifest = files(tmp_path)
    _, raw, _, _ = invoke(artifact, capsys)
    update(manifest, "source", {"ref": "source-record", "locator": "local-export"})
    recorded = adapter.audit_case(manifest)
    assert recorded["observation_digest"] != raw["observation_digest"]
    assert {key: value for key, value in recorded.items() if key != "observation_digest"} == {
        key: value for key, value in raw.items() if key != "observation_digest"
    }


def test_expected_hash_checks_remain_an_explicit_manifest_guarantee(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact, manifest = files(tmp_path)
    update(manifest, "artifact", {"expected_hash": digest_bytes(artifact.read_bytes())})
    assert adapter.audit_case(manifest) == adapter.audit_promptfoo_file(
        artifact, version=adapter.SOURCE[1]
    )
    update(manifest, "artifact", {"expected_hash": "sha256:" + "0" * 64})
    assert audit_counts.main([str(manifest), "--json"]) == ERROR_EXIT
    result = capsys.readouterr()
    assert result.out == ""
    error = json.loads(result.err)
    assert error["code"] == "EXPECTED_HASH_MISMATCH"
    assert error["location"] == "/artifact/expected_hash"
    assert invoke(artifact, capsys)[0] == 0


def test_raw_input_is_read_once_without_a_generated_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "raw.json"
    artifact.write_bytes(GOLDEN.read_bytes())
    original = adapter.read_local_file
    calls: list[Path] = []

    def read(path: Path, limit: int) -> bytes:
        calls.append(path)
        return original(path, limit)

    monkeypatch.setattr(adapter, "read_local_file", read)
    adapter.audit_promptfoo_file(artifact, version=adapter.SOURCE[1])
    assert calls == [artifact]
    assert list(tmp_path.iterdir()) == [artifact]


def test_integral_float_counts_use_original_projection_and_preserve_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact, _ = files(tmp_path)
    original_report = adapter.audit_promptfoo_file(artifact, version=adapter.SOURCE[1])
    data = json.loads(artifact.read_bytes())
    for key in ("successes", "failures", "errors"):
        data["results"]["stats"][key] = float(data["results"]["stats"][key])
    artifact.write_text(json.dumps(data), encoding="utf-8")
    original = adapter.audit_promptfoo
    types: list[type[object]] = []

    def count(stats: object, rows: object) -> pure.AccountingResult:
        assert isinstance(stats, dict)
        types.append(type(cast(dict[str, object], stats)["successes"]))
        return original(cast(dict[str, object], stats), rows)

    monkeypatch.setattr(adapter, "audit_promptfoo", count)
    actual = adapter.audit_promptfoo_file(artifact, version=adapter.SOURCE[1])
    assert types == [float]
    for key, expected in original_report.items():
        if key in {"raw_hash", "observation_digest"}:
            assert actual[key] != expected
        else:
            assert actual[key] == expected


@pytest.mark.parametrize("raw", [b"\xff", b"{", b'{"results":1,"results":2}'])
def test_bad_json_has_bounded_raw_file_diagnostics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], raw: bytes
) -> None:
    artifact, _ = files(tmp_path, raw)
    code, error, stdout, stderr = invoke(artifact, capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "INVALID_INPUT" and error["location"] == "/file"
    assert str(tmp_path) not in stderr


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo"])
def test_nonregular_file_refuses_without_waiting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    path = tmp_path / "input"
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    code, error, stdout, _ = invoke(path, capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "SOURCE_READ_FAILED" and error["location"] == "/file"


def test_public_reader_platform_reason_maps_without_registry_key_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unsupported(path: Path, maximum: int) -> bytes:
        del path, maximum
        raise TransportError("UNSUPPORTED_PLATFORM")

    monkeypatch.setattr(adapter, "read_local_file", unsupported)
    code, error, stdout, _ = invoke(tmp_path / "input", capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == error["reason"] == "UNSUPPORTED_PLATFORM"


@pytest.mark.parametrize("axis", ["bytes", "depth", "rows", "output"])
def test_existing_resource_policies_keep_typed_refusals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, axis: str
) -> None:
    artifact, _ = files(tmp_path)
    if axis == "output":
        code = run_cli(
            "audit-promptfoo",
            [str(artifact), "--version", adapter.SOURCE[1], "--json"],
            audit_promptfoo.configure,
            audit_promptfoo.produce,
            "promptfoo_accounting.v1",
            output_max_bytes=1,
        )
        streams = capsys.readouterr()
        assert code == ERROR_EXIT and streams.out == ""
        assert json.loads(streams.err)["code"] == "OUTPUT_LIMIT_EXCEEDED"
        return
    if axis == "rows":
        monkeypatch.setattr(pure, "ROW_LIMIT", 0)
    else:
        monkeypatch.setattr(
            adapter, "DEFAULT_MAX_BYTES" if axis == "bytes" else "DEFAULT_MAX_DEPTH", 1
        )
    code, error, stdout, _ = invoke(artifact, capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == (
        "COMPUTATION_LIMIT_EXCEEDED" if axis == "rows" else "INPUT_LIMIT_EXCEEDED"
    )
    assert error["location"] == "/file"


@pytest.mark.parametrize("value", [-1, 0.5, True, None, "1"])
def test_invalid_count_values_never_become_successful_raw_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], value: object
) -> None:
    data = json.loads(GOLDEN.read_bytes())
    data["results"]["stats"]["successes"] = value
    artifact, _ = files(tmp_path, json.dumps(data).encode())
    code, error, stdout, _ = invoke(artifact, capsys)
    assert code == ERROR_EXIT and stdout == "" and error["code"] == "INVALID_INPUT"


def test_manifest_is_not_auto_detected_as_a_raw_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, manifest = files(tmp_path)
    code, error, stdout, _ = invoke(manifest, capsys)
    assert code == ERROR_EXIT and stdout == "" and error["code"] == "INVALID_INPUT"


def test_no_file_argument_uses_existing_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert audit_promptfoo.main(["--json"]) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == "" and json.loads(streams.err)["code"] == "USAGE_ERROR"


@pytest.mark.parametrize("fault", ["unsafe_integer", "total_overflow"])
def test_numeric_range_and_total_overflow_keep_existing_owned_reasons(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fault: str
) -> None:
    from underwrite_core.canonical import SAFE_INT_MAX

    data = json.loads(GOLDEN.read_bytes())
    data["results"]["stats"] = {
        "successes": SAFE_INT_MAX + (1 if fault == "unsafe_integer" else 0),
        "failures": 1,
        "errors": 0,
    }
    artifact, _ = files(tmp_path, json.dumps(data).encode())
    code, error, stdout, _ = invoke(artifact, capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "INVALID_INPUT"
    assert error["reason"] == (
        "INT_OUT_OF_SAFE_RANGE" if fault == "unsafe_integer" else "COUNT_TOTAL_OVERFLOW"
    )


def test_raw_schema_failure_is_not_an_input_content_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from underwrite.instrument.ingest.schema import SchemaConfigurationError

    artifact, _ = files(tmp_path)

    def fail(*, max_bytes: int, max_depth: int) -> Any:
        del max_bytes, max_depth
        raise SchemaConfigurationError("INVALID_OBSERVATION_SCHEMA")

    monkeypatch.setattr(adapter, "packaged_ingestor", fail)
    code, error, stdout, stderr = invoke(artifact, capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "SCHEMA_CONFIGURATION_ERROR"
    assert error["location"] == "" and str(tmp_path) not in stderr

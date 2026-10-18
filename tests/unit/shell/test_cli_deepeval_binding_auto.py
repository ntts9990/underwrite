"""No-version DeepEval binding keeps owner declarations distinct from source identity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import content_digest, digest_bytes

from underwrite.cli import record_deepeval_bound
from underwrite.cli.evidence import EvidenceError
from underwrite.cli.main import main
from underwrite.instrument.evidence.deepeval_compat import ACCOUNTING_SCHEMA
from underwrite.instrument.ingest import deepeval_binding
from underwrite.instrument.ingest.schema import SchemaConfigurationError
from underwrite.service.records import read_record, read_record_file

ROOT = repo_root(Path(__file__).resolve())
SOURCE = ROOT / "fixtures/examples/deepeval-auto-4.3.1/ordinary.json"
PACK = ROOT / "fixtures/examples/evidence/synthetic-deepeval-4.2.8.bindings.json"
AUTO_PACK = ROOT / "fixtures/examples/evidence/synthetic-deepeval.bindings.v2.json"
ERROR_EXIT = 2


def _v2_pack(path: Path) -> bytes:
    raw = AUTO_PACK.read_bytes()
    path.write_bytes(raw)
    return raw


def test_no_version_bound_audit_uses_v2_pack_and_unknown_producer_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pack_path = tmp_path / "pack.json"
    pack_raw = _v2_pack(pack_path)
    source_raw = SOURCE.read_bytes()
    assert deepeval_binding.audit_deepeval_bound_bytes(source_raw, pack_raw) == (
        deepeval_binding.audit_deepeval_bound_file(SOURCE, pack_path)
    )
    assert main(["audit-deepeval", str(SOURCE), "--bindings", str(pack_path), "--json"]) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    result = json.loads(streams.out)
    assert result["schema"] == "evidence_result.v2"
    assert result["source"]["producer_version"] is None
    assert result["source"]["producer_version_state"] == "unknown"
    assert result["source"]["identity"]["raw_hash"] == digest_bytes(source_raw)
    assert result["declaration_source"] == {
        "format": "deepeval_metric_bindings.v2",
        "raw_hash": digest_bytes(pack_raw),
        "authority": "owner_asserted_unverified",
    }
    assert result["population"]["observed_count"] == len(result["items"])


def test_auto_bound_human_direct_and_record_state_coverage_and_identity_limits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pack = json.loads(AUTO_PACK.read_bytes())
    definition = pack["bindings"][0]["definition"]
    definition["semantic"]["construct"]["definition"] = "PRIVATE_CANARY owner prose"
    definition["semantic_digest"] = content_digest(definition["semantic"])
    pack_path = tmp_path / "private-pack.json"
    pack_path.write_text(json.dumps(pack))
    assert main(["audit-deepeval", str(SOURCE), "--bindings", str(pack_path)]) == 0
    direct = capsys.readouterr()
    assert direct.err == "" and "PRIVATE_CANARY" not in direct.out
    for expected in (
        "Only supplied serialized metricsData rows are counted",
        "expectation population is unknown",
        "Producer version unknown",
        "pack hash identifies only captured declaration bytes",
    ):
        assert expected in direct.out
    assert (
        main(
            [
                "records",
                "audit-deepeval",
                str(SOURCE),
                "--bindings",
                str(pack_path),
                "--root",
                str(tmp_path / "store"),
                "--id",
                "sample",
            ]
        )
        == 0
    )
    retained = capsys.readouterr()
    assert retained.err == "" and "PRIVATE_CANARY" not in retained.out
    for expected in (
        "Only supplied serialized metricsData rows are counted",
        "expectation population is unknown",
        "Producer version unknown",
        "pack hash identifies only captured declaration bytes",
    ):
        assert expected in retained.out


def test_old_pack_on_no_version_refuses_before_direct_source_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(_: Path) -> bytes:
        pytest.fail("TestRun opened after incompatible pack")

    monkeypatch.setattr(deepeval_binding, "audit_deepeval_file", forbidden)
    with pytest.raises(deepeval_binding.DeepevalBindingError) as caught:
        deepeval_binding.audit_deepeval_bound_file(SOURCE, PACK)
    assert (caught.value.code, caught.value.location) == ("UNSUPPORTED_PROFILE", "/bindings/schema")


def test_auto_bound_record_keeps_both_original_buffers_and_success_reader(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pack_path = tmp_path / "pack.json"
    pack_raw = _v2_pack(pack_path)
    source_raw = SOURCE.read_bytes()
    store = tmp_path / "store"
    assert (
        main(
            [
                "records",
                "audit-deepeval",
                str(SOURCE),
                "--bindings",
                str(pack_path),
                "--root",
                str(store),
                "--id",
                "sample",
                "--json",
            ]
        )
        == 0
    )
    streams = capsys.readouterr()
    assert streams.err == ""
    report = json.loads(streams.out)
    assert report["schema"] == "deepeval_bound_record.v2"
    assert "source_format_version" not in report
    assert report["analysis"]["report"]["schema"] == "evidence_result.v2"
    assert read_record_file(store, "sample", "source/deepeval.bin").data == source_raw
    assert read_record_file(store, "sample", "source/bindings.bin").data == pack_raw
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["arguments"]["selection_mode"] == "auto"
    assert "reader_profile" in invocation["arguments"]
    assert "format_version" not in invocation["arguments"]
    metadata = cast(dict[str, Any], read_record(store, "sample").manifest["metadata"])
    assert metadata["selection_mode"] == "auto" and "reader_profile" in metadata
    assert "format_version" not in metadata


def test_auto_bound_record_v1_pack_diagnostic_has_no_applied_reader(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "store"
    assert (
        main(
            [
                "records",
                "audit-deepeval",
                str(SOURCE),
                "--bindings",
                str(PACK),
                "--root",
                str(store),
                "--id",
                "sample",
                "--json",
            ]
        )
        == ERROR_EXIT
    )
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == "deepeval_bound_record.v2"
    assert report["analysis"]["phase"] == "analysis"
    assert report["analysis"]["error"]["location"] == "/bindings/schema"
    assert report["storage"]["state"] == "saved"
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["arguments"]["selection_mode"] == "auto"
    assert "reader_profile" not in invocation["arguments"]
    metadata = cast(dict[str, Any], read_record(store, "sample").manifest["metadata"])
    assert "reader_profile" not in metadata


def test_removed_version_refuses_before_direct_source_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(_: Path) -> dict[str, object]:
        pytest.fail("TestRun opened after removed version selection")

    monkeypatch.setattr(deepeval_binding, "audit_deepeval_file", forbidden)
    with pytest.raises(TypeError, match="version"):
        cast(Any, deepeval_binding.audit_deepeval_bound_file)(SOURCE, AUTO_PACK, version="4.2.8")


def test_auto_bound_record_uses_each_capture_once_after_paths_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, pack, store = tmp_path / "source.json", tmp_path / "pack.json", tmp_path / "store"
    source_raw = SOURCE.read_bytes()
    pack_raw = _v2_pack(pack)
    source.write_bytes(source_raw)
    real_source = record_deepeval_bound.read_deepeval_file
    real_pack = record_deepeval_bound.read_pack_bytes
    reads = {"deepeval": 0, "bindings": 0}

    def captured_source(path: Path) -> bytes:
        reads["deepeval"] += 1
        raw = real_source(path)
        path.write_bytes(b"changed source after read")
        return raw

    def captured_pack(path: Path) -> bytes:
        reads["bindings"] += 1
        raw = real_pack(path)
        path.unlink()
        return raw

    monkeypatch.setattr(record_deepeval_bound, "read_deepeval_file", captured_source)
    monkeypatch.setattr(record_deepeval_bound, "read_pack_bytes", captured_pack)
    report = cast(
        dict[str, Any],
        record_deepeval_bound.run_record_bound_deepeval(source, pack, store, "sample"),
    )
    assert reads == {"deepeval": 1, "bindings": 1}
    assert report["exit_code"] == 0
    result = report["analysis"]["report"]
    assert result["source"]["identity"]["raw_hash"] == digest_bytes(source_raw)
    assert result["declaration_source"]["raw_hash"] == digest_bytes(pack_raw)
    assert read_record_file(store, "sample", "source/deepeval.bin").data == source_raw
    assert read_record_file(store, "sample", "source/bindings.bin").data == pack_raw


@pytest.mark.parametrize("missing", ["deepeval", "bindings"])
def test_auto_bound_record_unavailable_input_keeps_other_complete_source(
    tmp_path: Path, missing: str
) -> None:
    source, pack, store = tmp_path / "source.json", tmp_path / "pack.json", tmp_path / "store"
    source_raw, pack_raw = SOURCE.read_bytes(), _v2_pack(pack)
    source.write_bytes(source_raw)
    (source if missing == "deepeval" else pack).unlink()
    report = cast(
        dict[str, Any],
        record_deepeval_bound.run_record_bound_deepeval(source, pack, store, "sample"),
    )
    assert report["exit_code"] == ERROR_EXIT
    assert report["analysis"]["phase"] == "acquisition"
    assert report["sources"][missing]["state"] == "unavailable"
    other = "bindings" if missing == "deepeval" else "deepeval"
    assert report["sources"][other]["state"] == "captured"
    assert read_record_file(store, "sample", f"source/{other}.bin").data == (
        pack_raw if other == "bindings" else source_raw
    )
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["arguments"]["selection_mode"] == "auto"
    assert "reader_profile" not in invocation["arguments"]


def test_auto_bound_record_complete_invalid_pack_is_saved_without_private_echo(
    tmp_path: Path,
) -> None:
    source, pack, store = SOURCE, tmp_path / "pack.json", tmp_path / "store"
    raw = b"PRIVATE_CANARY invalid pack"
    pack.write_bytes(raw)
    report = cast(
        dict[str, Any],
        record_deepeval_bound.run_record_bound_deepeval(source, pack, store, "sample"),
    )
    assert report["exit_code"] == ERROR_EXIT
    assert report["analysis"]["phase"] == "analysis"
    assert report["storage"]["state"] == "saved"
    assert "PRIVATE_CANARY" not in json.dumps(report)
    assert read_record_file(store, "sample", "source/bindings.bin").data == raw


def test_auto_bound_record_contract_and_output_limit_precede_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = record_deepeval_bound.packaged_validator

    def broken(name: str) -> Any:
        if name == record_deepeval_bound.AUTO_SCHEMA:
            raise SchemaConfigurationError("PRIVATE_SCHEMA_PATH")
        return original(name)

    def forbidden(_: Path) -> bytes:
        pytest.fail("read before selected contract")

    monkeypatch.setattr(record_deepeval_bound, "packaged_validator", broken)
    monkeypatch.setattr(record_deepeval_bound, "read_deepeval_file", forbidden)
    store = tmp_path / "store"
    with pytest.raises(SchemaConfigurationError):
        record_deepeval_bound.run_record_bound_deepeval(SOURCE, AUTO_PACK, store, "sample")
    assert not store.exists()
    monkeypatch.undo()
    monkeypatch.setattr(record_deepeval_bound, "OUTPUT_MAX_BYTES", 1)
    with pytest.raises(EvidenceError) as caught:
        record_deepeval_bound.run_record_bound_deepeval(SOURCE, AUTO_PACK, store, "sample")
    assert caught.value.payload["code"] == "OUTPUT_LIMIT_EXCEEDED"
    assert not store.exists()


def test_auto_bound_record_accounting_contract_fault_precedes_both_acquisitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = record_deepeval_bound.packaged_validator

    def broken(name: str) -> Any:
        if name == ACCOUNTING_SCHEMA:
            raise SchemaConfigurationError("PRIVATE_SCHEMA_PATH")
        return original(name)

    def forbidden(_: Path) -> bytes:
        pytest.fail("source acquired before selected accounting contract")

    monkeypatch.setattr(record_deepeval_bound, "packaged_validator", broken)
    monkeypatch.setattr(record_deepeval_bound, "read_deepeval_file", forbidden)
    monkeypatch.setattr(record_deepeval_bound, "read_pack_bytes", forbidden)
    store = tmp_path / "store"
    with pytest.raises(SchemaConfigurationError):
        record_deepeval_bound.run_record_bound_deepeval(SOURCE, AUTO_PACK, store, "sample")
    assert not store.exists()


def test_auto_bound_record_rejects_pack_hash_mismatch_before_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = record_deepeval_bound.audit_deepeval_bound_bytes

    def mismatched(source: bytes, pack: bytes) -> dict[str, object]:
        report = original(source, pack)
        declaration = cast(dict[str, object], report["declaration_source"])
        return {**report, "declaration_source": {**declaration, "raw_hash": "sha256:" + "0" * 64}}

    monkeypatch.setattr(record_deepeval_bound, "audit_deepeval_bound_bytes", mismatched)
    store = tmp_path / "store"
    with pytest.raises(EvidenceError) as caught:
        record_deepeval_bound.run_record_bound_deepeval(SOURCE, AUTO_PACK, store, "sample")
    assert caught.value.payload["reason"] == "RAW_IDENTITY_MISMATCH"
    assert not store.exists()

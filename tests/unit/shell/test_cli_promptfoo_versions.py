"""Producer declarations stay truthful while obsolete output selectors are refused."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from _inspection_cases import case, update
from _repo_paths import repo_root
from underwrite_core.canonical import canonical_bytes

from underwrite.cli import record_promptfoo
from underwrite.cli.main import main
from underwrite.instrument.evidence.promptfoo_compat import SOURCE_FORMAT
from underwrite.instrument.ingest import accounting as adapter
from underwrite.instrument.ingest.codecs.promptfoo import read_eval_output_v0_124_1
from underwrite.instrument.ingest.schema import SchemaConfigurationError
from underwrite.service.records import read_record, read_record_file

FIXTURES = repo_root(Path(__file__).resolve()) / "fixtures/golden/external"
ERROR_EXIT = 2
STORAGE_EXIT = 3
FRACTIONAL_SCORE = 0.3


def args(path: Path, store: Path) -> list[str]:
    return [
        "records",
        "audit-promptfoo",
        str(path),
        "--root",
        str(store),
        "--id",
        "sample",
        "--json",
    ]


def output(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    streams = capsys.readouterr()
    assert not (streams.out and streams.err)
    return json.loads(streams.out or streams.err)


def test_actual_derivative_counts_and_retained_recovery(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, store = tmp_path / "input.json", tmp_path / "store"
    raw = (FIXTURES / "promptfoo-0.124.1/results.json").read_bytes()
    path.write_bytes(raw)
    expected = adapter.audit_promptfoo_bytes(raw)
    assert adapter.audit_promptfoo_file(path) == expected
    payload = read_eval_output_v0_124_1(json.loads(raw)).payload
    summary = cast(dict[str, Any], payload["results"])
    assert any(
        row["score"] == FRACTIONAL_SCORE and row["success"] is True for row in summary["results"]
    )
    assert expected["producer_stats"] == {"successes": 3, "failures": 1, "errors": 3, "total": 7}
    assert expected["findings"] == []
    assert main(["audit-promptfoo", str(path), "--json"]) == 0
    assert output(capsys) == expected
    assert main(args(path, store)) == 0
    captured = output(capsys)
    assert captured["schema"] == "promptfoo_record.v2"
    assert captured["analysis"]["report"] == expected
    assert expected["schema"] == "promptfoo_accounting.v2"
    assert read_record_file(store, "sample", "source/promptfoo.bin").data == raw
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    metadata = cast(dict[str, Any], read_record(store, "sample").manifest["metadata"])
    for identity in (invocation["arguments"], metadata):
        assert identity["selection_mode"] == "auto"
        assert identity["producer_version"] == "0.124.1"
        assert identity["producer_version_state"] == "declared"
        assert identity["reader_profile"] == expected["reader_profile"]
        assert "format_version" not in identity
    before = read_record(store, "sample")
    assert main(args(path, store)) == STORAGE_EXIT
    assert output(capsys)["analysis"]["report"] == expected
    assert read_record(store, "sample") == before
    path.unlink()
    assert (
        main(
            [
                "records",
                "read-json",
                "--root",
                str(store),
                "--id",
                "sample",
                "--name",
                "analysis/accounting.json",
            ]
        )
        == 0
    )
    assert output(capsys)["content"] == expected


def test_old_raw_input_uses_current_report_and_declared_source_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = FIXTURES / "promptfoo/results.json"
    assert main(["audit-promptfoo", str(path), "--json"]) == 0
    baseline = capsys.readouterr().out
    assert json.loads(baseline) == adapter.audit_promptfoo_bytes(path.read_bytes())
    store = tmp_path / "store"
    report = record_promptfoo.run_record_promptfoo(path, store, "sample")
    assert canonical_bytes(cast(dict[str, Any], report["analysis"])["report"]) + b"\n" == (
        baseline.encode()
    )
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["arguments"]["producer_version"] == "0.123.0"
    assert invocation["arguments"]["producer_version_state"] == "declared"
    assert "format_version" not in invocation["arguments"]


@pytest.mark.parametrize("available", [False, True])
def test_saved_diagnostic_does_not_invent_a_producer_or_applied_reader(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], available: bool
) -> None:
    path, store = tmp_path / "input", tmp_path / "store"
    if available:
        path.write_bytes(b"broken")
    assert main(args(path, store)) == ERROR_EXIT
    report = output(capsys)
    assert report["analysis"]["phase"] == ("analysis" if available else "acquisition")
    assert "source_format_version" not in report
    assert main(["records", "show", "--root", str(store), "--id", "sample", "--json"]) == 0
    metadata = output(capsys)["record"]["metadata"]
    assert (
        main(
            [
                "records",
                "read-json",
                "--root",
                str(store),
                "--id",
                "sample",
                "--name",
                "invocation.json",
            ]
        )
        == 0
    )
    invocation = output(capsys)["content"]["arguments"]
    for identity in (metadata, invocation):
        assert identity["selection_mode"] == "auto"
        assert (
            not {"format_version", "producer_version", "producer_version_state", "reader_profile"}
            & identity.keys()
        )


def test_unknown_profile_does_not_widen_advanced_manifest_route(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = (FIXTURES / "promptfoo-0.124.1/results.json").read_bytes()
    manifest = case(tmp_path, raw)
    update(manifest, "source", {"format": SOURCE_FORMAT, "format_version": "unsupported"})
    assert main(["audit-counts", str(manifest), "--json"]) == ERROR_EXIT
    assert output(capsys)["code"] == "UNSUPPORTED_PROFILE"


@pytest.mark.parametrize("version", ["0.123.0", "0.124.1", "private/version"])
def test_removed_api_and_cli_selection_have_no_input_or_store_effect(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    version: str,
) -> None:
    def forbidden(_: Path) -> bytes:
        pytest.fail("removed selection must not acquire source")

    monkeypatch.setattr(adapter, "read_promptfoo_file", forbidden)
    monkeypatch.setattr(record_promptfoo, "read_promptfoo_file", forbidden)
    path, store = tmp_path / "private", tmp_path / "store"
    for operation, positional in (
        (adapter.audit_promptfoo_file, (path,)),
        (adapter.audit_promptfoo_bytes, (b"{}",)),
        (adapter.audit_promptfoo_row_index_file, (path,)),
        (record_promptfoo.run_record_promptfoo, (path, store, "sample")),
    ):
        with pytest.raises(TypeError, match="version"):
            cast(Any, operation)(*positional, version=version)
    for command in (
        ["audit-promptfoo", str(path), "--json"],
        args(path, store),
    ):
        assert main([*command, "--version", version]) == ERROR_EXIT
        streams = capsys.readouterr()
        assert streams.out == ""
        assert json.loads(streams.err)["code"] == "USAGE_ERROR"
        assert version not in streams.err
    assert not store.exists()


def test_configuration_fault_preempts_source_acquisition(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_: str) -> Any:
        raise SchemaConfigurationError("private detail")

    def forbidden(_: Path) -> bytes:
        pytest.fail("read before contract preload")

    monkeypatch.setattr(record_promptfoo, "packaged_validator", broken)
    monkeypatch.setattr(record_promptfoo, "read_promptfoo_file", forbidden)
    store = tmp_path / "store"
    assert main(args(tmp_path / "input", store)) == ERROR_EXIT
    assert output(capsys)["code"] == "SCHEMA_CONFIGURATION_ERROR" and not store.exists()


@pytest.mark.parametrize("axis", ["bytes", "depth", "rows"])
def test_current_reader_preserves_existing_resource_bounds(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, axis: str
) -> None:
    from underwrite.instrument.evidence import accounting as pure

    if axis == "rows":
        monkeypatch.setattr(pure, "ROW_LIMIT", 1)
    else:
        monkeypatch.setattr(
            adapter, "DEFAULT_MAX_BYTES" if axis == "bytes" else "DEFAULT_MAX_DEPTH", 1
        )
    path = FIXTURES / "promptfoo-0.124.1/results.json"
    assert main(["audit-promptfoo", str(path), "--json"]) == ERROR_EXIT
    report = output(capsys)
    assert report["code"] == (
        "COMPUTATION_LIMIT_EXCEEDED" if axis == "rows" else "INPUT_LIMIT_EXCEEDED"
    )

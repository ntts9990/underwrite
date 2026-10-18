"""One-call minimized Langfuse analysis retains verifiable complete private sources."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root
from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.cli import record_capture
from underwrite.cli import record_langfuse as command
from underwrite.cli.main import main
from underwrite.instrument.ingest.langfuse_accounting import (
    audit_langfuse_row_index_files,
    output_schema,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service.records import RecordError, read_record, read_record_file, verify_record

ERROR_EXIT = 2
STORAGE_EXIT = 3
PARTIAL_FILE_COUNT = 3

ROOT = repo_root(Path(__file__).resolve())
CLI = ROOT / "fixtures/examples/langfuse-cli"
BLOB = ROOT / "fixtures/golden/external/langfuse"
PAIRS = {
    "blob": (
        BLOB / "observations_v2-2026-09-14T06-40-00.json",
        BLOB / "scores-2026-09-14T06-40-00.json",
    ),
    "cli": (CLI / "observations.json", CLI / "scores.json"),
    "cli-body": (CLI / "observations-body.json", CLI / "scores-body.json"),
}


def _inputs(tmp_path: Path, family: str = "cli") -> tuple[Path, Path]:
    paths = (tmp_path / "observations.json", tmp_path / "scores.json")
    for source, path in zip(PAIRS[family], paths, strict=True):
        path.write_bytes(source.read_bytes())
    return paths


def _run(paths: tuple[Path, Path], store: Path, **options: Any) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        command.run_record_langfuse(*paths, store, "sample", view="row-index", **options),
    )


def _argv(paths: tuple[Path, Path], store: Path, *options: str) -> list[str]:
    return [
        "records",
        "audit-langfuse",
        "--observations",
        str(paths[0]),
        "--scores",
        str(paths[1]),
        "--root",
        str(store),
        "--id",
        "sample",
        "--view",
        "row-index",
        *options,
        "--json",
    ]


@pytest.mark.parametrize("family", ["blob", "cli", "cli-body"])
def test_row_cli_retains_exact_bytes_and_reopens_after_original_deletion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], family: str
) -> None:
    paths, store = _inputs(tmp_path, family), tmp_path / "private-store"
    expected = audit_langfuse_row_index_files(*paths)
    raw = [path.read_bytes() for path in paths]
    assert main(_argv(paths, store)) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    result: dict[str, Any] = json.loads(streams.out)
    assert result["schema"] == command.ROW_RECORD_SCHEMA
    assert result["analysis"]["report"] == expected
    assert result["storage"]["state"] == "saved"
    packaged_validator(command.ROW_RECORD_SCHEMA).validate(result)  # pyright: ignore[reportUnknownMemberType]
    manifest = read_record(store, "sample").manifest
    assert manifest["kind"] == "langfuse.row-index"
    assert cast(dict[str, object], manifest["metadata"])["view"] == "row-index"
    invocation = json.loads(read_record_file(store, "sample", "invocation.json").data)
    assert invocation["arguments"]["view"] == "row-index"
    for role, path, original in zip(("observations", "scores"), paths, raw, strict=True):
        identity = digest_bytes(original)
        assert result["sources"][role]["raw_hash"] == identity
        assert result["analysis"]["report"]["sources"][role]["raw_hash"] == identity
        path.unlink()
        selected = read_record_file(store, "sample", f"source/{role}.bin")
        assert selected.data == original and selected.sha256 == identity
    assert main(["records", "verify", "--root", str(store), "--id", "sample", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
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
                "analysis/row-index.json",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["content"] == expected


def test_row_projection_minimizes_analysis_not_private_originals(tmp_path: Path) -> None:
    paths, store = _inputs(tmp_path), tmp_path / "private-store"
    source = json.loads(paths[0].read_bytes())
    source["headers"]["x-private-note"] = "PRIVATE_HEADER_CANARY"
    source["body"]["data"][0]["input"] = {"text": "PRIVATE_INPUT_CANARY"}
    paths[0].write_text(json.dumps(source))
    scores = json.loads(paths[1].read_bytes())
    scores["body"]["data"][3]["value"] = "PRIVATE_SCORE_CANARY"
    paths[1].write_text(json.dumps(scores))
    result = _run(paths, store)
    assert result["exit_code"] == 0
    assert b"PRIVATE_" not in canonical_bytes(result)
    assert b"PRIVATE_" not in read_record_file(store, "sample", "analysis/row-index.json").data
    assert (
        b"PRIVATE_INPUT_CANARY" in read_record_file(store, "sample", "source/observations.bin").data
    )
    assert b"PRIVATE_SCORE_CANARY" in read_record_file(store, "sample", "source/scores.bin").data


def test_captured_buffers_are_analyzed_and_stored_without_source_reread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, store = _inputs(tmp_path), tmp_path / "store"
    originals = [path.read_bytes() for path in paths]
    actual_read = command.read_langfuse_file
    attempts: list[str] = []

    def read(path: Path, role: str) -> bytes:
        attempts.append(role)
        raw = actual_read(path, role)
        path.write_bytes(b"changed after capture")
        return raw

    monkeypatch.setattr(command, "read_langfuse_file", read)
    result = _run(paths, store)
    assert result["exit_code"] == 0
    assert attempts == ["observations", "scores"]
    for role, original in zip(("observations", "scores"), originals, strict=True):
        assert result["analysis"]["report"]["sources"][role]["raw_hash"] == digest_bytes(original)
        assert read_record_file(store, "sample", f"source/{role}.bin").data == original


@pytest.mark.parametrize(
    ("condition", "reason"),
    [("malformed", "INVALID_JSON_INPUT"), ("mixed", "LANGFUSE_AUTO_MIXED_FAMILIES")],
)
def test_invalid_complete_pairs_save_diagnostic_and_exact_sources(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], condition: str, reason: str
) -> None:
    paths, store = _inputs(tmp_path), tmp_path / "store"
    raw = b"\xffPRIVATE_BAD_INPUT" if condition == "malformed" else PAIRS["blob"][1].read_bytes()
    paths[1].write_bytes(raw)
    assert main(_argv(paths, store)) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.err == "" and "PRIVATE_BAD_INPUT" not in streams.out
    result = json.loads(streams.out)
    assert result["analysis"]["phase"] == "analysis"
    assert result["analysis"]["error"]["reason"] == reason
    assert result["storage"]["state"] == "saved"
    assert read_record_file(store, "sample", "source/scores.bin").data == raw
    assert (
        json.loads(read_record_file(store, "sample", "analysis/error.json").data)
        == result["analysis"]["error"]
    )
    assert _run(paths, store)["storage"]["error"]["code"] == "RECORD_EXISTS"


def test_unavailable_role_is_not_claimed_captured(tmp_path: Path) -> None:
    paths, store = _inputs(tmp_path), tmp_path / "store"
    paths[1].unlink()
    result = _run(paths, store)
    assert result["exit_code"] == ERROR_EXIT
    assert result["analysis"]["phase"] == "acquisition"
    assert result["sources"]["scores"]["state"] == "unavailable"
    assert result["storage"]["verification"]["files_checked"] == PARTIAL_FILE_COUNT
    assert verify_record(store, "sample")["valid"] is True


@pytest.mark.parametrize("missing", ["aggregate", "accounting", "row"])
def test_missing_contracts_fail_before_read_or_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    missing_name = {
        "aggregate": command.ROW_RECORD_SCHEMA,
        "accounting": output_schema("accounting"),
        "row": output_schema("row-index"),
    }[missing]
    actual = command.packaged_validator

    def validator(name: str) -> Any:
        if name == missing_name:
            raise SchemaConfigurationError("synthetic contract missing")
        return actual(name)

    def forbidden(*args: object, **kwargs: object) -> Any:
        pytest.fail("configuration failure must precede source reads and storage")

    monkeypatch.setattr(command, "packaged_validator", validator)
    monkeypatch.setattr(command, "read_langfuse_file", forbidden)
    monkeypatch.setattr(command, "store_captured_record", forbidden)
    with pytest.raises(SchemaConfigurationError):
        _run((tmp_path / "obs", tmp_path / "scores"), tmp_path / "store")
    assert not (tmp_path / "store").exists()


def test_invalid_view_rejected_before_effects_through_api_and_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> Any:
        pytest.fail("invalid view must precede effects")

    monkeypatch.setattr(command, "read_langfuse_file", forbidden)
    monkeypatch.setattr(command, "store_captured_record", forbidden)
    paths, store = (tmp_path / "obs", tmp_path / "scores"), tmp_path / "store"
    with pytest.raises(command.EvidenceError) as error:
        command.run_record_langfuse(*paths, store, "sample", view=cast(Any, "unknown"))
    assert error.value.payload["reason"] == "LANGFUSE_RECORD_VIEW_UNSUPPORTED"
    assert main(_argv(paths, store, "--view", "unknown")) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == ""
    assert json.loads(streams.err)["code"] == "USAGE_ERROR"


def test_verification_failure_retains_analysis_and_published_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, store = _inputs(tmp_path), tmp_path / "store"

    def fail(root: Path, identifier: str) -> dict[str, object]:
        raise RecordError("STORE_IO_ERROR")

    monkeypatch.setattr(record_capture, "verify_record", fail)
    result = _run(paths, store)
    assert result["exit_code"] == STORAGE_EXIT
    assert result["analysis"]["state"] == "result"
    assert result["storage"]["published_record"]["record_id"] == "sample"
    assert result["storage"]["error"]["code"] == "STORE_IO_ERROR"


def test_row_record_contract_rejects_accounting_and_false_saved_claims(tmp_path: Path) -> None:
    paths, store = _inputs(tmp_path), tmp_path / "store"
    result = _run(paths, store)
    validator = packaged_validator(command.ROW_RECORD_SCHEMA)
    full = command.run_record_langfuse(*paths, tmp_path / "full", "sample")
    invalid = deepcopy(result)
    invalid["analysis"] = full["analysis"]
    with pytest.raises(ValidationError):
        validator.validate(invalid)  # pyright: ignore[reportUnknownMemberType]
    invalid = deepcopy(result)
    invalid["storage"]["verification"]["files_checked"] = 3
    with pytest.raises(ValidationError):
        validator.validate(invalid)  # pyright: ignore[reportUnknownMemberType]


def test_human_row_output_names_view_and_capabilities_expose_current_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths, store = _inputs(tmp_path), tmp_path / "store"
    assert main(_argv(paths, store)[:-1]) == 0
    assert "Row-index result:" in capsys.readouterr().out
    assert main(["capabilities", "--json"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    records = next(item for item in catalog["commands"] if item["command"] == "records")
    profiles = {item["name"]: item for item in records["profiles"]}
    row_profiles = {
        name: item
        for name, item in profiles.items()
        if name.startswith("audit-langfuse") and name.endswith("|row-index")
    }
    assert set(row_profiles) == {"audit-langfuse|row-index"}
    assert row_profiles["audit-langfuse|row-index"]["output_schemas"] == [command.ROW_RECORD_SCHEMA]


def test_duplicate_id_preserves_first_row_record_and_reports_new_analysis(tmp_path: Path) -> None:
    paths, store = _inputs(tmp_path), tmp_path / "store"
    first = _run(paths, store)
    original = paths[0].read_bytes()
    paths[0].write_bytes(original + b"\n")
    second = _run(paths, store)
    assert second["exit_code"] == STORAGE_EXIT
    assert second["analysis"]["state"] == "result"
    assert second["sources"]["observations"]["raw_hash"] == digest_bytes(original + b"\n")
    assert second["storage"]["error"]["code"] == "RECORD_EXISTS"
    assert second["storage"]["published_record"] is None
    assert read_record_file(store, "sample", "source/observations.bin").data == original
    assert read_record(store, "sample").digest == first["storage"]["manifest_digest"]


@pytest.mark.parametrize(
    "mutation", ["unknown", "missing", "selector_only", "wrong_row", "accounting"]
)
def test_row_record_closed_selector_rejects_unselected_or_impersonated_reports(
    tmp_path: Path, mutation: str
) -> None:
    paths, store = _inputs(tmp_path), tmp_path / "store"
    result = _run(paths, store)
    report = result["analysis"]["report"]
    selected_schema = report["schema"]
    if mutation == "unknown":
        report["schema"] = "unsupported_row_index.v1"
    elif mutation == "missing":
        del report["schema"]
    elif mutation == "selector_only":
        result["analysis"]["report"] = {"schema": selected_schema}
    elif mutation == "wrong_row":
        # A compatible report cannot impersonate the strict historical CLI contract.
        report["schema"] = "langfuse_cli_row_index.v1"
    else:
        full = cast(
            dict[str, Any], command.run_record_langfuse(*paths, tmp_path / "full", "sample")
        )
        result["analysis"] = full["analysis"]
        result["analysis"]["report"]["schema"] = selected_schema
    with pytest.raises(ValidationError):
        packaged_validator(command.ROW_RECORD_SCHEMA).validate(result)  # pyright: ignore[reportUnknownMemberType]

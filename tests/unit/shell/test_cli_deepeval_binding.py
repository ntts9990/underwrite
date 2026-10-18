"""Owner name bindings project captured DeepEval accounting into common evidence."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root
from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import content_digest, digest_bytes

from underwrite.cli import record_capture, record_deepeval_bound
from underwrite.cli.evidence import EvidenceError
from underwrite.cli.main import main
from underwrite.instrument.evidence.deepeval_binding import (
    DeepevalBindingError,
    bind_deepeval_accounting,
)
from underwrite.instrument.ingest import deepeval_binding as binding_adapter
from underwrite.instrument.ingest.deepeval_accounting import (
    audit_deepeval_bytes,
    audit_deepeval_file,
)
from underwrite.instrument.ingest.deepeval_binding import (
    PACK_MAX_BYTES,
    audit_deepeval_bound_bytes,
    audit_deepeval_bound_file,
    decode_validate_pack_bytes,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service import records
from underwrite.service.records import read_record_file, verify_record

ROOT = repo_root(Path(__file__).resolve())
ERROR_EXIT = 2
STORAGE_EXIT = 3
BOUND_FILE_COUNT = 4
REPEATED_ITEM_COUNT = 6
SOURCE = ROOT / "fixtures/golden/external/deepeval-4.2.8/ordinary.json"
LEGACY_SOURCE = ROOT / "fixtures/golden/external/deepeval/test_run_20260914_132634.json"
DEFINITION = ROOT / "fixtures/conformance/ume1/metric-definition-example.v1.json"


def _pack(name: str = "zero") -> dict[str, Any]:
    definition = json.loads(DEFINITION.read_text())
    semantic = definition["semantic"]
    definition["namespace"] = "synthetic"
    definition["metric_id"] = "fixed_score"
    semantic["construct"] = {
        "id": "synthetic_fixed_score",
        "definition": "Numeric score emitted by the synthetic fixed DeepEval fixture metric.",
    }
    semantic["target"] = {
        "kind": "quantity",
        "id": "fixture_metric_output",
        "definition": "The fixture metric's output, without a truth or quality claim.",
    }
    semantic["value"] = {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
        "unit": "score",
        "desirable_direction": "neither",
    }
    semantic["sampling"]["unit"] = "metric_occurrence"
    semantic["sampling"]["expected_population_basis"] = {
        "id": "reported_occurrences",
        "definition": "Metric occurrences present in the captured fixture TestRun.",
    }
    semantic["applicability"] = {
        "id": "reported_metric",
        "definition": "A metric occurrence recorded in the captured fixture TestRun.",
    }
    semantic["missingness"] = {
        "missing": {"id": "unscored", "definition": "The occurrence contains no score or error."},
        "error": {"id": "evaluator_error", "definition": "The occurrence reports an error."},
        "abstained": {"id": "not_used", "definition": "The fixture has no abstention state."},
    }
    semantic["required_coordinates"] = ["case_index", "metric_index"]
    definition["semantic_digest"] = content_digest(semantic)
    return {
        "schema": "deepeval_metric_bindings.v1",
        "source": {"format": "deepeval.test-run", "format_version": "4.2.8"},
        "name_binding_authority": "owner_asserted_stable_name_not_verified",
        "bindings": [{"source_metric_name": name, "definition": definition}],
    }


def _write(path: Path, pack: dict[str, Any]) -> Path:
    path.write_text(json.dumps(pack))
    return path


def test_bound_record_retains_same_buffers_and_survives_source_removal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source.json"
    source.write_bytes(SOURCE.read_bytes())
    bindings = _write(tmp_path / "bindings.json", _pack())
    source_raw, pack_raw = source.read_bytes(), bindings.read_bytes()
    store = tmp_path / "store"
    assert main(
        [
            "records", "audit-deepeval", str(source), "--version", "4.2.8",
            "--bindings", str(bindings), "--root", str(store), "--id", "sample", "--json",
        ]
    ) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    report = json.loads(streams.out)
    assert report["schema"] == "deepeval_bound_record.v1"
    assert report["analysis"]["report"] == audit_deepeval_bound_file(
        source, bindings, version="4.2.8"
    )
    assert report["storage"]["verification"]["files_checked"] == BOUND_FILE_COUNT
    source.unlink()
    bindings.unlink()
    assert verify_record(store, "sample")["valid"] is True
    assert read_record_file(store, "sample", "source/deepeval.bin").data == source_raw
    assert read_record_file(store, "sample", "source/bindings.bin").data == pack_raw
    assert main(["records", "verify", "--root", str(store), "--id", "sample", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    assert main(
        [
            "records", "read-json", "--root", str(store), "--id", "sample",
            "--name", "analysis/evidence_result.json", "--json",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["content"] == report["analysis"]["report"]
    assert str(source) not in streams.out and str(bindings) not in streams.out


def test_bound_record_capability_executes_its_nested_selector(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bindings = _write(tmp_path / "bindings.json", _pack())
    store = tmp_path / "store"
    assert main(["capabilities", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    records_command = next(row for row in report["commands"] if row["command"] == "records")
    selected = next(
        row for row in records_command["profiles"]
        if row["name"] == "audit-deepeval|4.2.8|owner-asserted-metric-bindings"
    )
    assert selected["required_inputs"] == ["file", "version", "bindings", "root", "id"]
    assert selected["input_schemas"] == ["deepeval_metric_bindings.v1"]
    assert selected["output_schemas"] == ["deepeval_bound_record.v1"]
    values = {
        "version": "4.2.8",
        "bindings": str(bindings),
        "root": str(store),
        "id": "sample",
    }
    argv = ["records", selected["name"].split("|", 1)[0], str(SOURCE)]
    for requirement in selected["required_inputs"][1:]:
        argv.extend(("--" + requirement, values[requirement]))
    assert main([*argv, "--json"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    result = json.loads(output.out)
    assert result["schema"] == selected["output_schemas"][0]
    assert result["analysis"]["report"]["source"]["format_version"] == "4.2.8"
    assert result["storage"]["verification"]["files_checked"] == BOUND_FILE_COUNT


def test_bound_bytes_match_file_api_and_apply_pack_limits(tmp_path: Path) -> None:
    bindings = _write(tmp_path / "bindings.json", _pack())
    pack_raw = bindings.read_bytes()
    assert audit_deepeval_bound_bytes(SOURCE.read_bytes(), pack_raw, version="4.2.8") == (
        audit_deepeval_bound_file(SOURCE, bindings, version="4.2.8")
    )
    with pytest.raises(DeepevalBindingError) as oversized:
        decode_validate_pack_bytes(b" " * (PACK_MAX_BYTES + 1))
    assert (oversized.value.code, oversized.value.location) == (
        "INPUT_LIMIT_EXCEEDED", "/bindings"
    )
    with pytest.raises(DeepevalBindingError) as deep:
        decode_validate_pack_bytes(b"[" * 33 + b"0" + b"]" * 33)
    assert (deep.value.code, deep.value.location) == ("INPUT_LIMIT_EXCEEDED", "/bindings")


def test_bound_record_uses_each_captured_buffer_once_after_paths_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.json"
    source.write_bytes(SOURCE.read_bytes())
    bindings = _write(tmp_path / "bindings.json", _pack())
    source_raw, pack_raw = source.read_bytes(), bindings.read_bytes()
    counts = {"bindings": 0, "deepeval": 0}
    pack_reader = record_deepeval_bound.read_pack_bytes
    source_reader = record_deepeval_bound.read_deepeval_file

    def capture_pack(path: Path) -> bytes:
        counts["bindings"] += 1
        raw = pack_reader(path)
        path.unlink()
        return raw

    def capture_source(path: Path) -> bytes:
        counts["deepeval"] += 1
        raw = source_reader(path)
        path.write_bytes(b"mutated private source")
        return raw

    monkeypatch.setattr(record_deepeval_bound, "read_pack_bytes", capture_pack)
    monkeypatch.setattr(record_deepeval_bound, "read_deepeval_file", capture_source)
    store = tmp_path / "store"
    report = cast(
        dict[str, Any],
        record_deepeval_bound.run_record_bound_deepeval(
            source, bindings, store, "sample", version="4.2.8"
        ),
    )
    assert counts == {"bindings": 1, "deepeval": 1}
    assert report["analysis"]["report"] == audit_deepeval_bound_bytes(
        source_raw, pack_raw, version="4.2.8"
    )
    assert report["sources"]["deepeval"]["raw_hash"] == digest_bytes(source_raw)
    assert report["sources"]["bindings"]["raw_hash"] == digest_bytes(pack_raw)
    assert read_record_file(store, "sample", "source/deepeval.bin").data == source_raw
    assert read_record_file(store, "sample", "source/bindings.bin").data == pack_raw


@pytest.mark.parametrize(
    ("pack_state", "source_state", "phase", "location", "captured"),
    [
        ("absent", "absent", "acquisition", "/bindings", 0),
        ("bad", "absent", "acquisition", "/file", 1),
        ("absent", "valid", "acquisition", "/bindings", 1),
        ("bad", "valid", "analysis", "/bindings", 2),
        ("valid", "bad", "analysis", "/file", 2),
        ("oversized", "valid", "acquisition", "/bindings", 1),
    ],
)
def test_bound_record_capture_and_parse_precedence(
    pack_state: str,
    source_state: str,
    phase: str,
    location: str,
    captured: int,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    bindings = tmp_path / "bindings.json"
    store = tmp_path / "store"
    if pack_state == "valid":
        _write(bindings, _pack())
    elif pack_state == "bad":
        bindings.write_bytes(b"private malformed pack")
    elif pack_state == "oversized":
        bindings.write_bytes(b"x" * (PACK_MAX_BYTES + 1))
    if source_state == "valid":
        source.write_bytes(SOURCE.read_bytes())
    elif source_state == "bad":
        source.write_bytes(b"private malformed source")
    report = cast(
        dict[str, Any],
        record_deepeval_bound.run_record_bound_deepeval(
            source, bindings, store, "sample", version="4.2.8"
        ),
    )
    assert report["exit_code"] == ERROR_EXIT
    assert report["analysis"]["phase"] == phase
    assert report["analysis"]["error"]["location"] == location
    assert "private malformed" not in json.dumps(report)
    assert report["storage"]["verification"]["files_checked"] == 2 + captured
    assert sum(item["state"] == "captured" for item in report["sources"].values()) == captured
    assert (store / "records/sample/files/source/bindings.bin").exists() == (
        pack_state in {"valid", "bad"}
    )
    assert (store / "records/sample/files/source/deepeval.bin").exists() == (
        source_state in {"valid", "bad"}
    )


def test_bound_record_retains_semantic_digest_refusal_without_claiming_pack_hash(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    source.write_bytes(SOURCE.read_bytes())
    pack = _pack()
    pack["bindings"][0]["definition"]["semantic_digest"] = "sha256:" + "0" * 64
    bindings = _write(tmp_path / "bindings.json", pack)
    store = tmp_path / "store"
    report = cast(
        dict[str, Any],
        record_deepeval_bound.run_record_bound_deepeval(
            source, bindings, store, "sample", version="4.2.8"
        ),
    )
    assert report["exit_code"] == ERROR_EXIT
    assert report["analysis"]["phase"] == "analysis"
    assert report["analysis"]["error"]["location"] == "/bindings/0"
    assert report["sources"]["bindings"]["raw_hash"] == digest_bytes(bindings.read_bytes())
    assert read_record_file(store, "sample", "source/bindings.bin").data == bindings.read_bytes()


def test_standalone_pack_validation_precedes_source_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindings = tmp_path / "bindings.json"
    bindings.write_bytes(b"private malformed pack")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("source opened before pack validation")

    monkeypatch.setattr(binding_adapter, "audit_deepeval_file", forbidden)
    with pytest.raises(DeepevalBindingError) as caught:
        audit_deepeval_bound_file(tmp_path / "absent", bindings, version="4.2.8")
    assert caught.value.location == "/bindings"


def test_bound_record_refuses_source_identity_mismatch_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.json"
    source.write_bytes(SOURCE.read_bytes())
    bindings = _write(tmp_path / "bindings.json", _pack())
    store = tmp_path / "store"
    original = record_deepeval_bound.audit_deepeval_bound_bytes

    def altered(source_raw: bytes, pack_raw: bytes, *, version: str) -> dict[str, object]:
        report = original(source_raw, pack_raw, version=version)
        cast(dict[str, Any], report["source"])["identity"]["raw_hash"] = "sha256:" + "0" * 64
        return report

    monkeypatch.setattr(record_deepeval_bound, "audit_deepeval_bound_bytes", altered)
    with pytest.raises(EvidenceError) as caught:
        record_deepeval_bound.run_record_bound_deepeval(
            source, bindings, store, "sample", version="4.2.8"
        )
    assert caught.value.payload["reason"] == "RAW_IDENTITY_MISMATCH"
    assert not store.exists()


def test_bound_record_version_and_output_admission_precede_input_or_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, bindings, store = tmp_path / "source", tmp_path / "bindings", tmp_path / "store"
    with pytest.raises(EvidenceError) as version:
        record_deepeval_bound.run_record_bound_deepeval(
            source, bindings, store, "sample", version="4.1.1"
        )
    assert (version.value.payload["code"], version.value.payload["location"]) == (
        "UNSUPPORTED_FORMAT", "/version"
    )
    assert not store.exists()
    source.write_bytes(SOURCE.read_bytes())
    _write(bindings, _pack())

    def forbidden(*args: object) -> None:
        pytest.fail("storage called before output admission")

    monkeypatch.setattr(record_deepeval_bound, "OUTPUT_MAX_BYTES", 1)
    monkeypatch.setattr(record_deepeval_bound, "store_captured_record", forbidden)
    with pytest.raises(EvidenceError) as output:
        record_deepeval_bound.run_record_bound_deepeval(
            source, bindings, store, "sample", version="4.2.8"
        )
    assert output.value.payload["code"] == "OUTPUT_LIMIT_EXCEEDED"
    assert not store.exists()


def test_bound_record_collision_and_verification_failure_keep_storage_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.json"
    source.write_bytes(SOURCE.read_bytes())
    bindings = _write(tmp_path / "bindings.json", _pack())
    store = tmp_path / "store"
    first = cast(
        dict[str, Any],
        record_deepeval_bound.run_record_bound_deepeval(
            source, bindings, store, "sample", version="4.2.8"
        ),
    )
    second = cast(
        dict[str, Any],
        record_deepeval_bound.run_record_bound_deepeval(
            source, bindings, store, "sample", version="4.2.8"
        ),
    )
    assert first["exit_code"] == 0
    assert second["exit_code"] == STORAGE_EXIT and second["analysis"] == first["analysis"]
    assert second["storage"]["error"]["code"] == "RECORD_EXISTS"

    def fail(root: Path, identifier: str) -> dict[str, object]:
        del root, identifier
        raise records.RecordError("STORE_IO_ERROR")

    monkeypatch.setattr(record_capture, "verify_record", fail)
    failed = cast(
        dict[str, Any],
        record_deepeval_bound.run_record_bound_deepeval(
            source, bindings, store, "second", version="4.2.8"
        ),
    )
    assert failed["exit_code"] == STORAGE_EXIT
    assert failed["storage"]["published_record"]["record_id"] == "second"
    assert records.read_record(store, "second").digest == (
        failed["storage"]["published_record"]["manifest_digest"]
    )


def test_capability_profile_matches_bound_and_legacy_entry_points(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["capabilities", "--json"]) == 0
    capabilities = json.loads(capsys.readouterr().out)
    command = next(row for row in capabilities["commands"] if row["command"] == "audit-deepeval")
    bound_profile = next(
        row for row in command["profiles"] if row["output_schemas"] == ["evidence_result.v1"]
    )
    assert bound_profile["required_inputs"] == ["file", "version", "bindings"]
    bindings = _write(tmp_path / "bindings.json", _pack())
    assert (
        main(
            [
                "audit-deepeval",
                str(SOURCE),
                "--version",
                "4.2.8",
                "--bindings",
                str(bindings),
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["schema"] == "evidence_result.v1"
    assert (
        main(["audit-deepeval", str(SOURCE), "--bindings", str(bindings), "--json"]) == ERROR_EXIT
    )
    refused = capsys.readouterr()
    assert not refused.out
    assert json.loads(refused.err)["reason"] == "DEEPEVAL_BINDING_PACK_SCOPE_UNSUPPORTED"
    assert main(["audit-deepeval", str(LEGACY_SOURCE), "--version", "4.1.1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == "deepeval_accounting.v1"


def test_public_source_result_and_legacy_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pack = _pack()
    bindings = _write(tmp_path / "bindings.json", pack)
    original = SOURCE.read_bytes()
    assert main(["audit-deepeval", str(SOURCE), "--version", "4.2.8", "--json"]) == 0
    legacy = capsys.readouterr().out
    assert (
        main(
            [
                "audit-deepeval",
                str(SOURCE),
                "--version",
                "4.2.8",
                "--bindings",
                str(bindings),
                "--json",
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert not output.err
    report = json.loads(output.out)
    assert report == audit_deepeval_bound_file(SOURCE, bindings, version="4.2.8")
    packaged_validator("evidence_result.v1").validate(report)  # pyright: ignore[reportUnknownMemberType]
    assert (
        report["source"]["identity"]["raw_hash"]
        == audit_deepeval_file(SOURCE, version="4.2.8")["raw_hash"]
    )
    assert report["population"] == {
        "unit": "metric_occurrence",
        "observed_count": 3,
        "planned_count": None,
    }
    items = report["items"]
    assert [
        (item["coordinate"]["case_index"], item["coordinate"]["metric_index"]) for item in items
    ] == [(0, 0), (0, 1), (0, 2)]
    assert [item["availability"] for item in items] == ["observed", "observed", "error"]
    assert [item["value"] for item in items] == [1, 0, None]
    assert (
        items[1]["metric"]["semantic_digest"]
        == pack["bindings"][0]["definition"]["semantic_digest"]
    )
    assert pack["bindings"][0]["definition"]["semantic"]["value"] == {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
        "unit": "score",
        "desirable_direction": "neither",
    }
    assert (
        "synthetic fixed DeepEval fixture metric"
        in (pack["bindings"][0]["definition"]["semantic"]["construct"]["definition"])
    )
    assert [item["metric"]["semantic_binding"] for item in items] == ["unbound", "bound", "unbound"]
    assert all(
        item["origin"] == "source_claim" and item["permitted_effect"] == "descriptive"
        for item in items
    )
    assert not report["verification"]
    assert "zero" not in output.out and "RuntimeError" not in output.out
    assert SOURCE.read_bytes() == original
    assert main(["audit-deepeval", str(SOURCE), "--version", "4.2.8", "--json"]) == 0
    assert capsys.readouterr().out == legacy


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("stale", "DEEPEVAL_BINDING_DIGEST_MISMATCH"),
        ("unmatched", "DEEPEVAL_BINDING_NAME_UNMATCHED"),
        ("duplicate", "DEEPEVAL_BINDING_NAME_DUPLICATE"),
        ("method", "DEEPEVAL_BINDING_METHOD_POLICY_UNSUPPORTED"),
        ("range", "DEEPEVAL_BINDING_SCORE_OUT_OF_RANGE"),
    ],
)
def test_typed_refusals_have_no_partial_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], change: str, reason: str
) -> None:
    pack = _pack()
    binding = pack["bindings"][0]
    definition = binding["definition"]
    if change == "stale":
        definition["semantic"]["construct"]["definition"] = "Changed meaning"
    elif change == "unmatched":
        binding["source_metric_name"] = "private-unmatched-name"
    elif change == "duplicate":
        pack["bindings"].append(copy.deepcopy(binding))
    elif change == "method":
        definition["method"] = {
            "id": "method",
            "version": "v1",
            "digest": definition["semantic_digest"],
        }
    else:
        definition["semantic"]["value"]["minimum"] = 0.1
        definition["semantic_digest"] = content_digest(definition["semantic"])
    path = _write(tmp_path / "private.json", pack)
    assert (
        main(
            ["audit-deepeval", str(SOURCE), "--version", "4.2.8", "--bindings", str(path), "--json"]
        )
        == ERROR_EXIT
    )
    output = capsys.readouterr()
    assert not output.out
    error = json.loads(output.err)
    assert error["reason"] == reason
    assert "private-unmatched-name" not in output.err


def test_v1_pack_on_auto_refuses_before_source_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bindings = _write(tmp_path / "bindings.json", _pack())
    assert (
        main(["audit-deepeval", str(tmp_path / "absent"), "--bindings", str(bindings), "--json"])
        == ERROR_EXIT
    )
    error = json.loads(capsys.readouterr().err)
    assert (error["reason"], error["location"]) == (
        "DEEPEVAL_BINDING_PACK_SCOPE_UNSUPPORTED",
        "/bindings/schema",
    )


def test_repeated_name_preserves_ordinals_and_unscored_state() -> None:
    source = json.loads(SOURCE.read_text())
    first = source["testCases"][0]
    second = copy.deepcopy(first)
    second["name"] = "second"
    second["metricsData"] = [
        {"name": "zero", "flaky": False},
        {"name": "zero", "flaky": False, "score": 0},
        {"name": "zero", "flaky": False, "error": "private-error"},
    ]
    source["testCases"].append(second)
    accounting = audit_deepeval_bytes(json.dumps(source).encode(), version="4.2.8")
    result = bind_deepeval_accounting(accounting, _pack())
    items = cast(list[dict[str, Any]], result["items"])
    assert len(items) == REPEATED_ITEM_COUNT
    assert [item["coordinate"] for item in items[3:]] == [
        {"kind": "metric_occurrence", "case_index": 1, "metric_index": index} for index in range(3)
    ]
    assert [item["availability"] for item in items[3:]] == ["missing", "observed", "error"]
    assert [item["value"] for item in items[3:]] == [None, 0, None]
    assert all(item["metric"]["semantic_binding"] == "bound" for item in items[3:])
    source_result = cast(dict[str, Any], result["source"])
    assert source_result["identity"]["raw_hash"] == accounting["raw_hash"]


def test_same_identity_with_different_meaning_refuses() -> None:
    pack = _pack()
    other = copy.deepcopy(pack["bindings"][0])
    other["source_metric_name"] = "positive"
    other["definition"]["semantic"]["construct"]["definition"] = "Other rubric"
    other["definition"]["semantic_digest"] = content_digest(other["definition"]["semantic"])
    pack["bindings"].append(other)
    accounting = audit_deepeval_file(SOURCE, version="4.2.8")
    with pytest.raises(DeepevalBindingError, match="DEEPEVAL_BINDING_IDENTITY_CONFLICT"):
        bind_deepeval_accounting(accounting, pack)


@pytest.mark.parametrize("raw", [b'{"bindings":[],"bindings":[]}', b"[", b"[]"])
def test_strict_pack_decode_refuses(
    tmp_path: Path, raw: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "private.json"
    path.write_bytes(raw)
    assert (
        main(
            ["audit-deepeval", str(SOURCE), "--version", "4.2.8", "--bindings", str(path), "--json"]
        )
        == ERROR_EXIT
    )
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)["schema"] == "evidence_error.v1"


def test_valid_population_over_output_budget_is_typed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = json.loads(SOURCE.read_text())
    source["testCases"][0]["metricsData"] = [
        {"name": "zero", "score": 0, "flaky": False} for _ in range(2048)
    ]
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(source))
    pack = _pack()
    definition = pack["bindings"][0]["definition"]
    definition["namespace"] = "N" + "x" * 127
    definition["metric_id"] = "M" + "x" * 127
    binding_path = _write(tmp_path / "bindings.json", pack)
    assert (
        main(
            [
                "audit-deepeval",
                str(source_path),
                "--version",
                "4.2.8",
                "--bindings",
                str(binding_path),
                "--json",
            ]
        )
        == ERROR_EXIT
    )
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)["code"] == "OUTPUT_LIMIT_EXCEEDED"


@pytest.mark.parametrize("state", ["unscored", "error"])
def test_inverted_bounds_refuse_before_occurrence_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    source = json.loads(SOURCE.read_text())
    metric: dict[str, object] = {"name": "zero", "flaky": False}
    if state == "error":
        metric["error"] = "private evaluator error"
    source["testCases"][0]["metricsData"] = [metric]
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(source))
    pack = _pack()
    definition = pack["bindings"][0]["definition"]
    definition["semantic"]["value"].update({"minimum": 2, "maximum": 1})
    definition["semantic_digest"] = content_digest(definition["semantic"])
    binding_path = _write(tmp_path / "bindings.json", pack)
    with pytest.raises(DeepevalBindingError) as caught:
        audit_deepeval_bound_file(source_path, binding_path, version="4.2.8")
    assert caught.value.reason == "DEEPEVAL_BINDING_BOUNDS_INVALID"
    assert (
        main(
            [
                "audit-deepeval",
                str(source_path),
                "--version",
                "4.2.8",
                "--bindings",
                str(binding_path),
                "--json",
            ]
        )
        == ERROR_EXIT
    )
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)["reason"] == "DEEPEVAL_BINDING_BOUNDS_INVALID"
    assert "private evaluator error" not in output.err


@pytest.mark.parametrize(
    ("schema", "failure", "expected"),
    [
        (
            "deepeval_metric_bindings.v1",
            ValidationError("private validation detail"),
            ("INVALID_INPUT", "DEEPEVAL_BINDING_SCHEMA_INVALID", "/bindings"),
        ),
        (
            "evidence_result.v1",
            ValidationError("private validation detail"),
            ("INTERNAL_ERROR", "DEEPEVAL_BINDING_RESULT_INVALID", "/result"),
        ),
        *[
            (
                schema,
                SchemaConfigurationError("private schema detail"),
                ("SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT", location),
            )
            for schema, location in (
                ("deepeval_metric_bindings.v1", "/bindings"),
                ("evidence_result.v1", "/result"),
            )
        ],
        *[
            (
                schema,
                RuntimeError("private evaluator detail"),
                ("SCHEMA_CONFIGURATION_ERROR", "SCHEMA_EVALUATION_FAILED", location),
            )
            for schema, location in (
                ("deepeval_metric_bindings.v1", "/bindings"),
                ("evidence_result.v1", "/result"),
            )
        ],
    ],
)
def test_public_api_schema_failures_are_finite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    schema: str,
    failure: Exception,
    expected: tuple[str, str, str],
) -> None:
    bindings = _write(tmp_path / "bindings.json", _pack())
    original = binding_adapter.packaged_validator

    class BrokenValidator:
        def validate(self, _value: object) -> None:
            raise failure

    def selected_validator(name: str) -> object:
        if name != schema:
            return original(name)
        if isinstance(failure, SchemaConfigurationError):
            raise failure
        return BrokenValidator()

    monkeypatch.setattr(binding_adapter, "packaged_validator", selected_validator)
    with pytest.raises(DeepevalBindingError) as raised:
        audit_deepeval_bound_file(SOURCE, bindings, version="4.2.8")
    assert (raised.value.code, raised.value.reason, raised.value.location) == expected
    assert "private" not in str(raised.value)

"""The exact goppi pilot adapter is bounded, reconciled, and never echoes answers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from _repo_paths import repo_root
from jsonschema import Draft202012Validator
from underwrite_core.canonical import canonical_bytes

from underwrite.cli import audit_run
from underwrite.instrument.evidence.run_audit import audit_run_inventory
from underwrite.instrument.ingest import goppi_pilot
from underwrite.instrument.ingest.goppi_pilot import GoppiPilotError, load_inventory

ROOT = repo_root(Path(__file__).resolve())
SCOPE = "goppi_repo_grounded_pilot_exchange_not_public_product_contract"
SOURCE_FILE_COUNT = 4
ERROR_EXIT = 2


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _sha(data: bytes | str) -> str:
    raw = data.encode() if isinstance(data, str) else data
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest(seed: str) -> str:
    return _sha(seed)


def _row(index: int, classification: str, *, include_answers: bool) -> dict[str, Any]:
    pilot = f"pilot-{index + 1:03d}"
    answer_text = f"private-answer-{index}"
    return {
        "schema": "goppi.grounded_pilot_exchange_row.v1",
        "group_id": _digest("group"),
        "source_ref": pilot,
        "source_manifest_sha256": _digest(f"manifest-{index}"),
        "study_id": "grounded-extraction",
        "draft_digest": _digest("draft"),
        "evalset_digest": _digest("evalset"),
        "evalset_classification": "synthetic_fixture",
        "benchmark_claims": "prohibited",
        "assignment_digest": _digest("assignment"),
        "harness": {
            "harness_version": _digest("harness"),
            "snapshot_digest": _digest("snapshot"),
            "profile_identity_digest": _digest(f"profile-{index}"),
        },
        "case": {
            "case_id": f"case-{index}",
            "family": "synthetic-family",
            "content_digest": _digest(f"case-{index}"),
            "split": "explore",
        },
        "arm": "baseline",
        "repeat_index": 0,
        "attempt": {
            "attempt_id": f"attempt-{index}",
            "request_digest": _digest(f"request-{index}"),
            "receipt_digest": _digest(f"receipt-{index}"),
            "termination_reason": "completed",
            "cleanup": "complete",
            "authenticated_local_receipt": True,
            "duration_ms": 1.5,
        },
        "outcome": {
            "classification": classification,
            "reason": (
                "runner_cost_or_assistant_failure_can_mask_verifier_pass"
                if classification == "endpoint_failure"
                else f"producer-reason-{classification}"
            ),
            "producer_recorded": True,
            "independently_regraded": False,
        },
        "usage": {
            "status": (
                "reported_cost_limit_exceeded"
                if classification == "endpoint_failure"
                else "observed_within_authorized_limit"
            ),
            "input_tokens": 1,
            "output_tokens": 1,
            "total_tokens": 2,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "pi_reported_cost_usd": 0.001,
        },
        "answer": (
            {
                "final_assistant_answer": answer_text,
                "sha256": _sha(answer_text),
                "source": "authenticated_stdout_terminal_answer",
            }
            if include_answers
            else None
        ),
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def _refresh_manifest(folder: Path) -> None:
    manifest = json.loads((folder / "manifest.json").read_bytes())
    for item in manifest["files"]:
        raw = (folder / item["path"]).read_bytes()
        item.update(bytes=len(raw), sha256=_sha(raw))
    _write_json(folder / "manifest.json", manifest)


def _exchange(
    tmp_path: Path,
    *,
    classifications: tuple[str, ...] = (
        "observed_true",
        "observed_false",
        "infra_missing",
        "unknown",
        "endpoint_failure",
    ),
    include_answers: bool = True,
) -> Path:
    folder = tmp_path / "exchange"
    folder.mkdir(parents=True)
    rows = [
        _row(index, classification, include_answers=include_answers)
        for index, classification in enumerate(classifications)
    ]
    profiles = [row["harness"]["profile_identity_digest"] for row in rows]
    execution = {
        "schema": "goppi.grounded_pilot_exchange_execution.v1",
        "format_scope": SCOPE,
        "status": "complete",
        "generated_at": "2026-10-11T00:00:00Z",
        "include_answers": include_answers,
        "rows": len(rows),
        "groups": [
            {
                "group_id": _digest("group"),
                "evalset_digest": _digest("evalset"),
                "harness_version": _digest("harness"),
                "grouping_axes": ["evalset_digest", "harness_version"],
                "row_count": len(rows),
                "profile_identity_digests": profiles,
                "snapshot_digests": [_digest("snapshot")],
                "distinct_profile_identity_count": len(profiles),
                "distinct_snapshot_count": 1,
                "conditions_compatibility_established": False,
                "pooled_accuracy_or_effect_claimed": False,
                "automatic_comparability_claimed": False,
                "cross_version_pairing_performed": False,
            }
        ],
        "accounting": {
            "unique_attempt_ids": len(rows),
            "durable_attempts": len(rows),
            "authenticated_native_receipts": len(rows),
            "rows_without_native_receipt": 0,
            "observed_duration_count": len(rows),
            "total_observed_duration_ms": 1.5 * len(rows),
            "duration_scope": "sum_of_observed_attempt_durations_not_wall_clock",
            "observed_cost_count": len(rows),
            "total_pi_reported_cost_usd": 0.001 * len(rows),
            "missing_values_are_zero": False,
        },
        "claims": {
            "executed_native_receipts_authenticated": True,
            "producer_scores_authenticated_by_native_receipts": False,
            "producer_scores_independently_regraded": False,
            "model_contacted_by_exporter": False,
            "network_contacted_by_exporter": False,
            "model_credentials_loaded_by_exporter": False,
            "source_projects_modified_by_exporter": False,
            "groups_are_automatically_comparable": False,
            "pooled_accuracy_or_effect_claimed": False,
        },
        "exclusions": [
            "credential_values",
            *([] if include_answers else ["final_assistant_answer"]),
        ],
    }
    _write_json(folder / "execution.json", execution)
    row_bytes = b"".join(
        (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode() for row in rows
    )
    (folder / "rows.jsonl").write_bytes(row_bytes)
    (folder / "schema.json").write_bytes(b"producer schema is hash-only, not executed\n")
    source_inputs = [
        {
            "source_ref": row["source_ref"],
            "manifest_sha256": row["source_manifest_sha256"],
            "project_root_sha256": _digest("project"),
            "artifact_refs": [f"{row['source_ref']}/result.json"],
        }
        for row in rows
    ]
    manifest = {
        "schema": "goppi.grounded_pilot_exchange_manifest.v1",
        "format_scope": SCOPE,
        "status": "complete",
        "generated_at": "2026-10-11T00:00:00Z",
        "private_output": True,
        "manifest_written_last": True,
        "files": [
            {"path": name, "bytes": 0, "sha256": _digest("placeholder")}
            for name in ("execution.json", "rows.jsonl", "schema.json")
        ],
        "source_inputs": source_inputs,
    }
    _write_json(folder / "manifest.json", manifest)
    _refresh_manifest(folder)
    return folder


def _rewrite_rows(folder: Path, mutate: Any) -> None:
    rows = [json.loads(line) for line in (folder / "rows.jsonl").read_text().splitlines()]
    mutate(rows)
    raw = b"".join(
        (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode() for row in rows
    )
    (folder / "rows.jsonl").write_bytes(raw)
    _refresh_manifest(folder)


def _inventory_validator() -> Validator:
    schema = json.loads((ROOT / "contracts/binary_run_inventory.v1.schema.json").read_bytes())
    return cast("Validator", Draft202012Validator(schema))


def _contract_validator(name: str) -> Validator:
    schema = json.loads((ROOT / "contracts" / f"{name}.schema.json").read_bytes())
    return cast("Validator", Draft202012Validator(schema))


def _load(folder: Path) -> dict[str, Any]:
    return cast(dict[str, Any], load_inventory(folder))


def test_three_state_mapping_reconciles_and_never_echoes_raw_answers(tmp_path: Path) -> None:
    inventory = _load(_exchange(tmp_path))
    _inventory_validator().validate(inventory)
    records = cast(list[dict[str, Any]], inventory["groups"][0]["records"])
    assert [record.get("outcome") for record in records[:2]] == [1, 0]
    assert records[2]["missing_reason"] == "infra"
    assert records[3]["missing_reason"] == "unknown"
    assert records[4]["non_binary_classification"] == "endpoint_failure"
    assert "verifier" not in records[4]
    assert "private-answer" not in json.dumps(inventory)
    assert {ref["kind"] for ref in records[0]["source_refs"]} == {
        "case_family",
        "split",
        "material",
        "profile",
        "snapshot",
        "request",
        "receipt",
        "answer",
        "source_manifest",
    }
    group = inventory["groups"][0]
    assert group["planned_units"] is None
    assert all(
        group["context"][key] is False
        for key in (
            "conditions_compatibility_established",
            "pooled_accuracy_or_effect_claimed",
            "automatic_comparability_claimed",
            "cross_version_pairing_performed",
        )
    )


def test_adapter_and_neutral_input_produce_canonically_equal_audits(tmp_path: Path) -> None:
    inventory = _load(_exchange(tmp_path))
    neutral = json.loads(canonical_bytes(inventory))
    assert canonical_bytes(audit_run_inventory(inventory)) == canonical_bytes(
        audit_run_inventory(neutral)
    )


def test_answer_null_is_accepted_when_answers_were_excluded(tmp_path: Path) -> None:
    inventory = _load(_exchange(tmp_path, include_answers=False))
    refs = inventory["groups"][0]["records"][0]["source_refs"]
    answer = next(ref for ref in refs if ref["kind"] == "answer")
    assert answer == {"kind": "answer", "ref": "not_included", "digest": None}


@pytest.mark.parametrize("raw_answer", ["", "e\u0301"])
def test_raw_answer_bytes_need_not_be_nonblank_or_nfc_and_are_never_echoed(
    raw_answer: str, tmp_path: Path
) -> None:
    folder = _exchange(tmp_path, classifications=("observed_true",))

    def mutate(rows: list[dict[str, Any]]) -> None:
        rows[0]["answer"]["final_assistant_answer"] = raw_answer
        rows[0]["answer"]["sha256"] = _sha(raw_answer)

    _rewrite_rows(folder, mutate)
    inventory = _load(folder)
    answer_ref = next(
        ref
        for ref in inventory["groups"][0]["records"][0]["source_refs"]
        if ref["kind"] == "answer"
    )
    assert answer_ref["digest"] == _sha(raw_answer)
    if raw_answer:
        assert raw_answer not in json.dumps(inventory)


def test_source_files_are_read_once_and_producer_schema_is_not_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _exchange(tmp_path)
    original = goppi_pilot.read_descriptor
    calls = 0

    def counted(descriptor: int, maximum: int) -> bytes:
        nonlocal calls
        calls += 1
        return original(descriptor, maximum)

    monkeypatch.setattr(goppi_pilot, "read_descriptor", counted)
    inventory = _load(folder)
    assert calls == SOURCE_FILE_COUNT
    assert {item["name"] for item in inventory["source"]["files"]} == {
        "manifest.json",
        "execution.json",
        "rows.jsonl",
        "schema.json",
    }


def _manifest_fault(folder: Path, fault: str) -> None:
    manifest = json.loads((folder / "manifest.json").read_bytes())
    if fault == "schema":
        manifest["schema"] = "other"
    elif fault == "file_set":
        manifest["files"][0]["path"] = "other.json"
    else:
        manifest["source_inputs"][0]["artifact_refs"] = ["../private"]
    _write_json(folder / "manifest.json", manifest)


def _execution_fault(folder: Path, fault: str) -> None:
    execution = json.loads((folder / "execution.json").read_bytes())
    if fault == "group_count":
        execution["groups"][0]["row_count"] = 1
    else:
        execution["groups"][0]["profile_identity_digests"].pop()
        execution["groups"][0]["distinct_profile_identity_count"] -= 1
    _write_json(folder / "execution.json", execution)
    _refresh_manifest(folder)


def _row_fault(folder: Path, fault: str) -> None:
    def mutate(rows: list[dict[str, Any]]) -> None:
        if fault == "source_manifest":
            rows[0]["source_manifest_sha256"] = _digest("wrong")
        elif fault == "duplicate_attempt":
            rows[1]["attempt"]["attempt_id"] = rows[0]["attempt"]["attempt_id"]
        elif fault == "classification":
            rows[0]["outcome"]["classification"] = "new_state"
        elif fault == "evalset":
            rows[0]["evalset_digest"] = _digest("wrong")
        elif fault == "harness":
            rows[0]["harness"]["harness_version"] = _digest("wrong")
        else:
            rows[0]["answer"]["sha256"] = _digest("wrong")

    _rewrite_rows(folder, mutate)


def _apply_fault(folder: Path, fault: str) -> None:
    if fault == "hash":
        (folder / "execution.json").write_bytes((folder / "execution.json").read_bytes() + b" ")
    elif fault in {"schema", "file_set", "artifact_ref"}:
        _manifest_fault(folder, fault)
    elif fault in {"group_count", "profile"}:
        _execution_fault(folder, fault)
    else:
        _row_fault(folder, fault)


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("hash", "GOPPI_EXCHANGE_FILE_MISMATCH"),
        ("schema", "UNSUPPORTED_GOPPI_EXCHANGE_PROFILE"),
        ("file_set", "INVALID_GOPPI_FILE_SET"),
        ("group_count", "GOPPI_EXCHANGE_GROUP_MISMATCH"),
        ("source_manifest", "GOPPI_EXCHANGE_SOURCE_MISMATCH"),
        ("evalset", "GOPPI_EXCHANGE_GROUP_MISMATCH"),
        ("harness", "GOPPI_EXCHANGE_GROUP_MISMATCH"),
        ("profile", "GOPPI_EXCHANGE_GROUP_MISMATCH"),
        ("duplicate_attempt", "GOPPI_EXCHANGE_SOURCE_MISMATCH"),
        ("artifact_ref", "INVALID_GOPPI_SOURCE_REFS"),
        ("classification", "UNSUPPORTED_GOPPI_CLASSIFICATION"),
        ("answer_digest", "GOPPI_ANSWER_DIGEST_MISMATCH"),
    ],
)
def test_manifest_execution_row_and_reference_mismatches_are_typed(
    fault: str, reason: str, tmp_path: Path
) -> None:
    folder = _exchange(tmp_path)
    _apply_fault(folder, fault)
    with pytest.raises(GoppiPilotError) as exc:
        load_inventory(folder)
    assert exc.value.reason == reason


def test_duplicate_json_keys_and_row_bounds_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _exchange(tmp_path)
    line = (folder / "rows.jsonl").read_bytes().splitlines()[0]
    duplicate = line.replace(b'{"schema":', b'{"schema":"duplicate","schema":', 1) + b"\n"
    (folder / "rows.jsonl").write_bytes(duplicate)
    _refresh_manifest(folder)
    with pytest.raises(GoppiPilotError) as exc:
        load_inventory(folder)
    assert exc.value.reason == "INVALID_GOPPI_EXCHANGE_JSON"

    other = _exchange(tmp_path / "other")
    execution = json.loads((other / "execution.json").read_bytes())
    execution["rows"] = 129
    _write_json(other / "execution.json", execution)
    _refresh_manifest(other)
    with pytest.raises(GoppiPilotError) as exc:
        load_inventory(other)
    assert (exc.value.code, exc.value.reason) == (
        "INPUT_LIMIT_EXCEEDED",
        "GOPPI_EXCHANGE_LIMIT_EXCEEDED",
    )

    bounded = _exchange(tmp_path / "bounded")
    monkeypatch.setattr(goppi_pilot, "ROW_MAX_BYTES", 10)
    with pytest.raises(GoppiPilotError) as exc:
        load_inventory(bounded)
    assert (exc.value.code, exc.value.reason) == (
        "INPUT_LIMIT_EXCEEDED",
        "GOPPI_EXCHANGE_LIMIT_EXCEEDED",
    )


@pytest.mark.parametrize("fault", ["duration", "cost", "execution_time", "manifest_time"])
def test_execution_totals_and_manifest_timestamp_reconcile(fault: str, tmp_path: Path) -> None:
    folder = _exchange(tmp_path)
    if fault == "manifest_time":
        manifest = json.loads((folder / "manifest.json").read_bytes())
        manifest["generated_at"] = "2026-10-11T00:00:01Z"
        _write_json(folder / "manifest.json", manifest)
    else:
        execution = json.loads((folder / "execution.json").read_bytes())
        if fault == "duration":
            execution["accounting"]["total_observed_duration_ms"] += 1
        elif fault == "cost":
            execution["accounting"]["total_pi_reported_cost_usd"] += 1
        else:
            execution["generated_at"] = "2026-10-11T00:00:01Z"
        _write_json(folder / "execution.json", execution)
        _refresh_manifest(folder)
    with pytest.raises(GoppiPilotError) as exc:
        load_inventory(folder)
    expected = (
        "GOPPI_EXCHANGE_SUMMARY_MISMATCH"
        if fault in {"execution_time", "manifest_time"}
        else "GOPPI_EXCHANGE_COUNT_MISMATCH"
    )
    assert exc.value.reason == expected


def test_directory_and_exchange_file_symlinks_are_rejected(tmp_path: Path) -> None:
    folder = _exchange(tmp_path)
    linked = tmp_path / "linked"
    linked.symlink_to(folder, target_is_directory=True)
    with pytest.raises(GoppiPilotError) as exc:
        load_inventory(linked)
    assert exc.value.code == "SOURCE_READ_FAILED"

    target = tmp_path / "schema-target"
    target.write_bytes((folder / "schema.json").read_bytes())
    (folder / "schema.json").unlink()
    (folder / "schema.json").symlink_to(target)
    with pytest.raises(GoppiPilotError) as exc:
        load_inventory(folder)
    assert exc.value.code == "SOURCE_READ_FAILED"


def test_cli_adapter_and_neutral_inventory_emit_the_same_audit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _exchange(tmp_path)
    assert audit_run.main(["--goppi-grounded-pilot-exchange", str(folder), "--json"]) == 0
    first_streams = capsys.readouterr()
    assert first_streams.err == ""
    first = json.loads(first_streams.out)
    _contract_validator("binary_run_audit.v1").validate(first)

    inventory = _load(folder)
    neutral_path = tmp_path / "inventory.json"
    neutral_path.write_bytes(canonical_bytes(inventory))
    assert audit_run.main(["--input", str(neutral_path), "--json"]) == 0
    second_streams = capsys.readouterr()
    assert second_streams.err == ""
    assert json.loads(second_streams.out) == first


def test_cli_adapter_errors_are_atomic_and_do_not_echo_answers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _exchange(tmp_path)
    _row_fault(folder, "answer_digest")
    assert audit_run.main(["--goppi-grounded-pilot-exchange", str(folder), "--json"]) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == "" and "private-answer" not in streams.err
    error = json.loads(streams.err)
    _contract_validator("evidence_error.v1").validate(error)
    assert (error["code"], error["reason"]) == (
        "INVALID_INPUT",
        "GOPPI_ANSWER_DIGEST_MISMATCH",
    )

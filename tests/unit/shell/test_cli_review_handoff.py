"""One handoff call preserves analysis meaning and independent storage outcomes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from drivers.goppi_p7_fixture import build_goppi_p7_fixture
from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import canonical_bytes, content_digest

from underwrite.cli import handoff_review
from underwrite.cli.handoff_limits import GENERATED_NAMES, MAX_SOURCE_FILES
from underwrite.cli.local_json import Validator
from underwrite.cli.main import main
from underwrite.instrument.evidence.goppi_p7_handoff import HandoffMappingError
from underwrite.instrument.evidence.paired_review import evaluate_paired_review
from underwrite.instrument.ingest.schema import packaged_validator
from underwrite.service.records import RECORD_MAX_FILES, RecordError, read_record, verify_record

ANALYSIS_ERROR_EXIT = 2
STORAGE_ERROR_EXIT = 3


def invoke(tmp_path: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, Any]:
    source = tmp_path / "source"
    handoff = build_goppi_p7_fixture().write(source)
    code = main(
        [
            "review-handoff",
            "--handoff",
            str(handoff),
            "--source-root",
            str(source),
            "--json",
            *extra,
        ]
    )
    streams = capsys.readouterr()
    assert streams.err == ""
    document = json.loads(streams.out)
    cast(Validator, packaged_validator("paired_handoff_review.v1")).validate(document)
    assert document["exit_code"] == code
    return code, document


def test_exact_review_reuses_existing_engine_once_without_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, object, object]] = []
    actual = handoff_review.evaluate_paired_review

    def evaluate(input_doc: object, policy_doc: object, context_doc: object) -> Any:
        calls.append((input_doc, policy_doc, context_doc))
        return actual(input_doc, policy_doc, context_doc)

    monkeypatch.setattr(handoff_review, "evaluate_paired_review", evaluate)
    code, report = invoke(tmp_path, capsys)
    assert code == 0 and len(calls) == 1
    evidence, review = evaluate_paired_review(*calls[0])
    assert report["analysis"]["paired_evidence"] == evidence
    assert report["analysis"]["paired_review"] == review
    assert report["analysis"]["summary"]["measurement_status"] == "not_measured"
    assert report["context_provenance"]["conditions_supplied_by_producer"] is False
    assert report["storage"] == {"state": "not_requested"}
    assert {p.name for p in tmp_path.iterdir()} == {"source"}
    assert report["findings"][0]["code"] == "PRODUCER_POLICY_INCLUDED_FLAG_CONTRADICTION"


def test_record_captures_the_same_source_and_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "store"
    code, report = invoke(
        tmp_path, capsys, "--record-root", str(store), "--record-id", "review.001"
    )
    assert code == 0 and report["storage"]["state"] == "saved"
    record = read_record(store, "review.001")
    assert record.digest == report["storage"]["manifest_digest"]
    assert verify_record(store, "review.001")["valid"] is True
    stored = store / "records/review.001/files"
    assert (
        json.loads((stored / "underwrite/evidence.json").read_text())
        == report["analysis"]["paired_evidence"]
    )
    assert (
        json.loads((stored / "underwrite/review.json").read_text())
        == report["analysis"]["paired_review"]
    )
    identities = {
        item["name"]: item for item in cast(list[dict[str, Any]], record.manifest["files"])
    }
    for item in report["source"]["files"]:
        assert identities[item["name"]]["sha256"] == item["sha256"]
    assert all(item["source_locator"] is None for item in identities.values())
    assert cast(dict[str, Any], record.manifest["selection"])["excluded"]
    assert cast(dict[str, Any], record.manifest["provenance"])["verified"] is False
    assert MAX_SOURCE_FILES + len(GENERATED_NAMES) == RECORD_MAX_FILES
    invocation = json.loads((stored / "underwrite/invocation.json").read_text())
    assert invocation["analysis_exit_code"] == 0
    assert invocation["scope"] == "analysis_phase"
    assert invocation["storage_outcome_location"] == "outer_cli_envelope"


@pytest.mark.parametrize("store_requested", [False, True])
def test_acquisition_error_is_an_envelope_and_stores_only_actual_diagnostics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], store_requested: bool
) -> None:
    store = tmp_path / "store"
    extra = ["--record-root", str(store), "--record-id", "failure.001"] if store_requested else []
    code = main(["review-handoff", "--handoff", str(tmp_path / "missing"), "--json", *extra])
    streams = capsys.readouterr()
    assert streams.err == ""
    report = json.loads(streams.out)
    cast(Validator, packaged_validator("paired_handoff_review.v1")).validate(report)
    assert code == report["exit_code"] == ANALYSIS_ERROR_EXIT
    assert report["analysis"]["phase"] == "acquisition"
    assert report["source"] is report["scope"] is report["context_provenance"] is None
    assert report["findings"] == []
    assert str(tmp_path) not in streams.out
    if store_requested:
        files = cast(list[dict[str, Any]], read_record(store, "failure.001").manifest["files"])
        assert {item["name"] for item in files} == {
            "underwrite/error.json",
            "underwrite/invocation.json",
        }
        assert all(item["role"] == "log" for item in files)
        invocation = json.loads(
            (store / "records/failure.001/files/underwrite/invocation.json").read_text()
        )
        assert invocation["analysis_exit_code"] == ANALYSIS_ERROR_EXIT
        assert invocation["scope"] == "analysis_phase"
    else:
        assert not store.exists()


def test_unexpected_error_never_echoes_source_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "private/authentication-key.json"

    def fail_capture(*args: object, **kwargs: object) -> Any:
        raise RuntimeError(str(secret))

    monkeypatch.setattr(handoff_review, "capture_goppi_p7_handoff", fail_capture)
    code = main(["review-handoff", "--handoff", str(tmp_path), "--json"])
    streams = capsys.readouterr()
    assert streams.err == ""
    assert str(secret) not in streams.out
    report = json.loads(streams.out)
    assert code == ANALYSIS_ERROR_EXIT
    assert report["analysis"]["error"]["code"] == "INTERNAL_ERROR"
    assert report["analysis"]["error"]["reason"] == "INTERNAL_ERROR"


@pytest.mark.parametrize("store_requested", [False, True])
def test_output_refusal_returns_compact_envelope_and_only_diagnostic_record(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    store_requested: bool,
) -> None:
    store = tmp_path / "store"
    monkeypatch.setattr(handoff_review, "MAX_OUTPUT_BYTES", 4096)
    args = ("--record-root", str(store), "--record-id", "bounded.001") if store_requested else ()
    code, report = invoke(tmp_path, capsys, *args)
    assert code == ANALYSIS_ERROR_EXIT
    assert report["analysis"]["phase"] == "analysis"
    assert report["analysis"]["error"]["code"] == "OUTPUT_LIMIT_EXCEEDED"
    assert report["source"] is report["scope"] is None
    if store_requested:
        files = cast(list[dict[str, Any]], read_record(store, "bounded.001").manifest["files"])
        assert {f["name"] for f in files} == {"underwrite/error.json", "underwrite/invocation.json"}
    else:
        assert not store.exists()


def test_payload_schema_refusal_returns_compact_aggregate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def malformed_summary(*args: object) -> dict[str, str]:
        return {"unexpected": "value"}

    monkeypatch.setattr(handoff_review, "_summary", malformed_summary)
    code, report = invoke(tmp_path, capsys)
    assert code == ANALYSIS_ERROR_EXIT
    assert report["analysis"]["error"]["reason"] == "SCHEMA_VALIDATION_FAILED"
    assert report["analysis"]["phase"] == "analysis"
    assert report["source"] is report["context_provenance"] is None


def test_output_budget_includes_emitter_newline_before_publication(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, report = invoke(tmp_path, capsys)
    monkeypatch.setattr(handoff_review, "MAX_OUTPUT_BYTES", len(canonical_bytes(report)))
    code, refused = invoke(tmp_path, capsys)
    assert code == ANALYSIS_ERROR_EXIT
    assert refused["analysis"]["error"]["code"] == "OUTPUT_LIMIT_EXCEEDED"
    assert refused["storage"]["state"] == "not_requested"


def test_saved_contract_cannot_claim_failed_readback_is_saved(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, report = invoke(
        tmp_path, capsys, "--record-root", str(tmp_path / "store"), "--record-id", "verified.001"
    )
    verification = report["storage"]["verification"]
    verification["valid"] = False
    verification["findings"] = [
        {"code": "FILE_MISSING", "name": "mapping/manifest.json", "record_id": None}
    ]
    cast(Validator, packaged_validator("record_verification.v1")).validate(verification)
    with pytest.raises(ValidationError):
        cast(Validator, packaged_validator("paired_handoff_review.v1")).validate(report)


@pytest.mark.parametrize("analysis_fails", [False, True])
def test_storage_error_preserves_independent_analysis(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    analysis_fails: bool,
) -> None:
    if analysis_fails:

        def bad_mapping(value: object) -> Any:
            raise HandoffMappingError("INVALID_INPUT", "HANDOFF_TRANSITION_MISMATCH", "/mapping")

        monkeypatch.setattr(handoff_review, "verify_goppi_p7_mapping", bad_mapping)

    def fail_save(*args: object) -> Any:
        raise RecordError("STORE_IO_ERROR")

    monkeypatch.setattr(handoff_review, "save_captured_record", fail_save)
    code, report = invoke(
        tmp_path, capsys, "--record-root", str(tmp_path / "store"), "--record-id", "review.001"
    )
    assert code == STORAGE_ERROR_EXIT
    assert report["storage"]["state"] == "error"
    assert report["storage"]["published_record"] is None
    assert report["analysis"]["state"] == ("error" if analysis_fails else "result")
    if not analysis_fails:
        assert report["analysis"]["summary"]["baseline"]["observed_one"] == 1


def test_post_publication_error_reports_published_identity_without_echoing_exception(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "private/store-diagnostic"

    def fail_readback(*args: object, **kwargs: object) -> Any:
        raise RuntimeError(str(secret))

    monkeypatch.setattr(handoff_review, "verify_record", fail_readback)
    store = tmp_path / "store"
    code, report = invoke(
        tmp_path, capsys, "--record-root", str(store), "--record-id", "published.001"
    )
    assert code == STORAGE_ERROR_EXIT
    assert report["analysis"]["state"] == "result"
    assert report["storage"]["state"] == "error"
    assert report["storage"]["error"]["code"] == "INTERNAL_ERROR"
    assert report["storage"]["published_record"] == {
        "record_id": "published.001",
        "manifest_digest": read_record(store, "published.001").digest,
    }
    assert str(secret) not in json.dumps(report)


def test_source_change_after_capture_never_changes_saved_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = handoff_review.capture_goppi_p7_handoff

    def capture_then_change(*args: object, **kwargs: Any) -> Any:
        result = capture(*args, **kwargs)  # type: ignore[arg-type]
        path = tmp_path / "source/study/paired-analysis-input/paired-input.json"
        path.write_text('"changed after capture"')
        return result

    monkeypatch.setattr(handoff_review, "capture_goppi_p7_handoff", capture_then_change)
    store = tmp_path / "store"
    code, report = invoke(
        tmp_path, capsys, "--record-root", str(store), "--record-id", "stable.001"
    )
    assert code == 0
    stored = json.loads((store / "records/stable.001/files/mapping/paired-input.json").read_text())
    assert content_digest(stored) == report["analysis"]["paired_evidence"]["input_digest"]


def test_existing_id_is_conflict_never_reconciled_as_new_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "store"
    options = ("--record-root", str(store), "--record-id", "exists.001")
    _, first = invoke(tmp_path, capsys, *options)
    code, second = invoke(tmp_path, capsys, *options)
    assert code == STORAGE_ERROR_EXIT and second["analysis"]["state"] == "result"
    assert second["storage"]["error"]["code"] == "RECORD_EXISTS"
    assert second["storage"]["published_record"] is None
    assert read_record(store, "exists.001").digest == first["storage"]["manifest_digest"]


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--record-root", "store"],
        ["--handoff", "h", "--record-id", "id"],
        ["--handoff", "h", "--input", "input.json"],
    ],
)
def test_option_errors_keep_existing_stderr_contract(
    args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["review-handoff", *args, "--json"]) == ANALYSIS_ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == ""
    assert json.loads(streams.err)["code"] == "USAGE_ERROR"


def test_schema_rejects_exit_and_state_contradictions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, report = invoke(tmp_path, capsys)
    validator = cast(Validator, packaged_validator("paired_handoff_review.v1"))
    report["exit_code"] = 3
    with pytest.raises(ValidationError):
        validator.validate(report)
    report["exit_code"] = 0
    report["analysis"]["error"] = {"code": "INVALID_INPUT"}
    with pytest.raises(ValidationError):
        validator.validate(report)


def test_human_output_starts_with_results_and_limits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "source"
    handoff = build_goppi_p7_fixture().write(root)
    assert main(["review-handoff", "--handoff", str(handoff), "--source-root", str(root)]) == 0
    text = capsys.readouterr().out
    assert "Observed outcome 1" in text and "not_measured" in text and "unknown" in text
    assert "Next:" in text
    assert "same_batch/explore" in text and "2 paired cases" in text
    assert "planned 4; reported 4; missing 0; unreported 0" in text

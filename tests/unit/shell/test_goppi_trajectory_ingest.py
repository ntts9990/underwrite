"""Consumer capture/admission tests using an explicitly non-verifying protocol stub."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from drivers.goppi_trajectory_verifier_stub import verifier_response

from underwrite.instrument.ingest import goppi_trajectory as app
from underwrite.instrument.ingest import local_capture
from underwrite.instrument.ingest.goppi_verifier import VerifierError
from underwrite.instrument.ingest.local_capture import CaptureError
from underwrite.instrument.ingest.schema import SchemaConfigurationError

FIXTURE = Path(__file__).parents[3] / "fixtures/conformance/goppi-trajectory/exchange"
PRIVATE_DIRECTORY_MODE = 0o700


class StubVerifier:
    """Protocol composition only; does not implement native producer verification."""

    def __init__(self, mutate: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.snapshots: list[Path] = []
        self.mutate = mutate

    def verify(self, snapshot: Path) -> bytes:
        self.snapshots.append(snapshot)
        assert snapshot.stat().st_mode & 0o777 == PRIVATE_DIRECTORY_MODE
        for path in snapshot.rglob("*"):
            assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
        value = verifier_response(snapshot)
        if self.mutate:
            self.mutate(value)
        return json.dumps(value).encode()


@pytest.fixture
def exchange(tmp_path: Path) -> Path:
    return Path(shutil.copytree(FIXTURE, tmp_path / "exchange"))


def test_snapshot_is_private_cleaned_and_captured_once(
    exchange: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = local_capture.read_at
    reads: list[tuple[int, tuple[str, ...]]] = []

    def observed(parent: int, parts: list[str], limit: int, flags: tuple[int, int]) -> bytes:
        reads.append((os.fstat(parent).st_ino, tuple(parts)))
        return original(parent, parts, limit, flags)

    monkeypatch.setattr(local_capture, "read_at", observed)
    verifier = StubVerifier()
    report = cast(dict[str, Any], app.audit_goppi_trajectory(exchange, verifier=verifier))
    assert report["schema"] == "goppi_trajectory_review.v1"
    assert report["trust_scope"]["native_verification"] == "local_goppi_same_byte_snapshot"
    assert len(reads) == len(app.MEMBERS) * 2
    assert len(set(reads)) == len(reads)
    assert not verifier.snapshots[0].exists()


@pytest.mark.parametrize(
    "kind", ["extra", "missing", "symlink", "hardlink", "fifo", "directory_link"]
)
def test_exact_topology_refused_before_process(exchange: Path, kind: str) -> None:
    leaf = exchange / "paired/rows.jsonl"
    if kind == "extra":
        (exchange / "extra").write_text("private")
    elif kind == "missing":
        leaf.unlink()
    elif kind == "directory_link":
        directory = exchange / "paired"
        moved = exchange.parent / "moved"
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    else:
        moved = exchange.parent / "rows"
        leaf.rename(moved)
        if kind == "symlink":
            leaf.symlink_to(moved)
        elif kind == "hardlink":
            os.link(moved, leaf)
        else:
            os.mkfifo(leaf)
    verifier = StubVerifier()
    with pytest.raises(app.TrajectoryInputError, match="TRAJECTORY_COMPLETE_HANDOFF_REQUIRED"):
        app.audit_goppi_trajectory(exchange, verifier=verifier)
    assert verifier.snapshots == []


def test_replaced_file_during_capture_rejected(
    exchange: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = local_capture.read_at
    changed = False

    def replacing(parent: int, parts: list[str], limit: int, flags: tuple[int, int]) -> bytes:
        nonlocal changed
        data = original(parent, parts, limit, flags)
        if not changed:
            changed = True
            leaf = exchange.joinpath(*parts)
            replacement = exchange.parent / "replacement"
            replacement.write_bytes(data)
            replacement.replace(leaf)
        return data

    monkeypatch.setattr(local_capture, "read_at", replacing)
    with pytest.raises(CaptureError, match="CAPTURE_TOPOLOGY_CHANGED"):
        app.audit_goppi_trajectory(exchange, verifier=StubVerifier())


@pytest.mark.parametrize(
    "field,value",
    [
        ("code", "COMMAND_HELP"),
        ("exit_code", True),
        ("status", "failure"),
        ("schema", "goppi.command_result.v2"),
        ("merge_authorized", True),
    ],
)
def test_nonverification_envelope_rejected(exchange: Path, field: str, value: object) -> None:
    verifier = StubVerifier(lambda response: response.__setitem__(field, value))
    with pytest.raises(app.TrajectoryInputError, match="GOPPI_VERIFIER_RESPONSE_UNSUPPORTED"):
        app.audit_goppi_trajectory(exchange, verifier=verifier)
    assert not verifier.snapshots[0].exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("row_count", True),
        ("schema", "goppi.paired_run_verification_result.v1"),
        ("producer_projection_independently_verified", True),
        ("source_local_authentication_independently_reverified", True),
        ("independent_provenance_established", True),
    ],
)
def test_false_trust_or_protocol_rejected(exchange: Path, field: str, value: object) -> None:
    verifier = StubVerifier(lambda response: response["data"].__setitem__(field, value))
    with pytest.raises(app.TrajectoryInputError, match="GOPPI_VERIFIER_RESPONSE_UNSUPPORTED"):
        app.audit_goppi_trajectory(exchange, verifier=verifier)


@pytest.mark.parametrize(
    "field,value",
    [
        ("input", "/wrong-private-path"),
        ("paired_output", "/wrong-private-path"),
        ("row_count", 0),
        ("manifest_digest", "sha256:" + "0" * 64),
        ("cycle_status", "completed"),
    ],
)
def test_stale_response_binding_rejected(exchange: Path, field: str, value: object) -> None:
    verifier = StubVerifier(lambda response: response["data"].__setitem__(field, value))
    with pytest.raises(app.TrajectoryInputError, match="GOPPI_VERIFIER_BINDING_MISMATCH") as error:
        app.audit_goppi_trajectory(exchange, verifier=verifier)
    assert "private" not in str(error.value)


@pytest.mark.parametrize("raw", [b'{"schema":1,"schema":2}', b"not json", b"[]"])
def test_malformed_response(exchange: Path, raw: bytes) -> None:
    class BadVerifier:
        def verify(self, snapshot: Path) -> bytes:
            return raw

    with pytest.raises(app.TrajectoryInputError):
        app.audit_goppi_trajectory(exchange, verifier=BadVerifier())


def test_snapshot_mutation_refused_and_cleaned(exchange: Path) -> None:
    snapshots: list[Path] = []

    class MutatingVerifier:
        def verify(self, snapshot: Path) -> bytes:
            snapshots.append(snapshot)
            response = verifier_response(snapshot)
            with (snapshot / "paired/rows.jsonl").open("ab") as stream:
                stream.write(b" ")
            return json.dumps(response).encode()

    with pytest.raises(app.TrajectoryInputError, match="GOPPI_VERIFIER_SNAPSHOT_CHANGED"):
        app.audit_goppi_trajectory(exchange, verifier=MutatingVerifier())
    assert not snapshots[0].exists()


def test_process_failure_is_cleaned(exchange: Path) -> None:
    snapshots: list[Path] = []

    class RefusingVerifier:
        def verify(self, snapshot: Path) -> bytes:
            snapshots.append(snapshot)
            raise VerifierError("INVALID_INPUT", "GOPPI_VERIFIER_REFUSED")

    with pytest.raises(app.TrajectoryInputError, match="GOPPI_VERIFIER_REFUSED"):
        app.audit_goppi_trajectory(exchange, verifier=RefusingVerifier())
    assert not snapshots[0].exists()


def test_interruption_preserves_signal_and_cleans_snapshot(exchange: Path) -> None:
    snapshots: list[Path] = []
    interrupted = KeyboardInterrupt()

    class InterruptedVerifier:
        def verify(self, snapshot: Path) -> bytes:
            snapshots.append(snapshot)
            raise interrupted

    with pytest.raises(KeyboardInterrupt) as error:
        app.audit_goppi_trajectory(exchange, verifier=InterruptedVerifier())
    assert error.value is interrupted
    assert len(snapshots) == 1
    assert not snapshots[0].exists()


def test_schema_configuration_precedes_source_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def failed(name: str) -> None:
        raise SchemaConfigurationError("TEST_CONFIGURATION_FAILURE")

    monkeypatch.setattr(app, "packaged_validator", failed)
    with pytest.raises(SchemaConfigurationError):
        app.audit_goppi_trajectory(Path("/missing"), verifier=StubVerifier())


def test_final_output_limit_before_return(exchange: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "MAX_REPORT_BYTES", 1)
    with pytest.raises(app.TrajectoryInputError, match="TRAJECTORY_REPORT_LIMIT"):
        app.audit_goppi_trajectory(exchange, verifier=StubVerifier())


def test_unit_cumulative_span_limit_precedes_process(
    exchange: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "MAX_SPANS_PER_UNIT", 4)
    verifier = StubVerifier()
    with pytest.raises(app.TrajectoryInputError, match="TRAJECTORY_SPAN_LIMIT"):
        app.audit_goppi_trajectory(exchange, verifier=verifier)
    assert verifier.snapshots == []


def _edit(exchange: Path, relative: str, change: Callable[[dict[str, Any]], None]) -> None:
    path = exchange / relative
    value = json.loads(path.read_bytes())
    change(value)
    path.write_text(json.dumps(value))


def test_unavailable_capture_is_not_zero_through_application(exchange: Path) -> None:
    def change(trajectory: dict[str, Any]) -> None:
        unit = trajectory["units"][0]
        unit.update(
            source_availability="unsupported",
            unavailable_reason="profile_not_supported",
            capture_completeness="incomplete",
            capture_profile=None,
            context=None,
        )
        for case in unit["cases"]:
            case.update(spans=[], verifier_observation=None)
        trajectory["availability_counts"]["retained"] -= len(unit["cases"])
        trajectory["availability_counts"]["unsupported"] += len(unit["cases"])

    _edit(exchange, "trajectory/trajectory.json", change)
    report = cast(dict[str, Any], app.audit_goppi_trajectory(exchange, verifier=StubVerifier()))
    assert report["capture_counts"]["unsupported"] > 0
    assert any(c["baseline"]["tool_status_counts"] is None for c in report["comparisons"])


def test_closed_only_zero_rows_through_application(exchange: Path) -> None:
    def close(execution: dict[str, Any]) -> None:
        for stage in execution["stages"]:
            stage.update(
                execution_status="closed",
                exported_row_count=0,
                reason_code="budget_stop",
                classification="indeterminate",
            )
        execution["claims"]["outcome_harness_binding"] = "not_applicable_no_evaluated_stages"

    _edit(exchange, "paired/execution.json", close)
    _edit(exchange, "paired/manifest.json", lambda d: d.update(row_count=0))
    (exchange / "paired/rows.jsonl").write_bytes(b"")
    execution = json.loads((exchange / "paired/execution.json").read_bytes())

    def no_capture(trajectory: dict[str, Any]) -> None:
        trajectory.update(units=[], closed_stages=[s["stage"] for s in execution["stages"]])
        trajectory["availability_counts"] = dict.fromkeys(trajectory["availability_counts"], 0)

    _edit(exchange, "trajectory/trajectory.json", no_capture)
    report = cast(dict[str, Any], app.audit_goppi_trajectory(exchange, verifier=StubVerifier()))
    assert report["comparisons"] == []
    assert report["populations"]["paired_rows"] == 0
    assert report["stages"]


@pytest.mark.parametrize(
    "limit",
    ["MAX_FAMILY_BYTES", "MAX_MANIFEST_BYTES", "MAX_DOCUMENT_BYTES", "MAX_ROWS", "MAX_UNITS"],
)
def test_resource_refusal_precedes_process(
    exchange: Path, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    monkeypatch.setattr(app, limit, 1)
    verifier = StubVerifier()
    with pytest.raises((app.TrajectoryInputError, CaptureError)) as error:
        app.audit_goppi_trajectory(exchange, verifier=verifier)
    assert error.value.code == "INPUT_LIMIT_EXCEEDED"
    assert verifier.snapshots == []


def test_snapshot_extra_file_is_refused_and_cleanup(exchange: Path) -> None:
    snapshots: list[Path] = []

    class AddingVerifier:
        def verify(self, snapshot: Path) -> bytes:
            snapshots.append(snapshot)
            response = verifier_response(snapshot)
            (snapshot / "extra").write_text("secret")
            return json.dumps(response).encode()

    with pytest.raises(app.TrajectoryInputError, match="GOPPI_VERIFIER_SNAPSHOT_CHANGED"):
        app.audit_goppi_trajectory(exchange, verifier=AddingVerifier())
    assert not snapshots[0].exists()


@pytest.mark.parametrize("field", ["output", "manifest_digest", "availability_counts"])
def test_companion_response_binding(exchange: Path, field: str) -> None:
    def mutate(response: dict[str, Any]) -> None:
        companion = response["data"]["trajectory"]
        if field == "availability_counts":
            companion[field]["retained"] = 0
        else:
            companion[field] = "sha256:" + "0" * 64 if field == "manifest_digest" else "/wrong"

    with pytest.raises(app.TrajectoryInputError, match="GOPPI_VERIFIER_BINDING_MISMATCH"):
        app.audit_goppi_trajectory(exchange, verifier=StubVerifier(mutate))


def test_integer_typed_response_does_not_accept_float_exit(exchange: Path) -> None:
    verifier = StubVerifier(lambda response: response.__setitem__("exit_code", 0.0))
    with pytest.raises(app.TrajectoryInputError, match="GOPPI_VERIFIER_RESPONSE_UNSUPPORTED"):
        app.audit_goppi_trajectory(exchange, verifier=verifier)


def test_bare_paired_directory_requires_complete_handoff(exchange: Path) -> None:
    verifier = StubVerifier()
    with pytest.raises(app.TrajectoryInputError) as error:
        app.audit_goppi_trajectory(exchange / "paired", verifier=verifier)
    assert (error.value.code, error.value.reason, error.value.location) == (
        "INVALID_INPUT",
        "TRAJECTORY_COMPLETE_HANDOFF_REQUIRED",
        "/exchange",
    )
    assert verifier.snapshots == []


def test_source_decode_failure_preserves_facade_error(exchange: Path) -> None:
    (exchange / "paired/execution.json").write_bytes(b"not json")
    verifier = StubVerifier()
    with pytest.raises(CaptureError) as caught:
        app.audit_goppi_trajectory(exchange, verifier=verifier)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "INVALID_INPUT",
        "CAPTURE_INVALID_JSON",
        "/exchange/paired/execution.json",
    )
    assert verifier.snapshots == []


def test_post_native_semantic_failure_preserves_facade_error(exchange: Path) -> None:
    _edit(exchange, "trajectory/trajectory.json", lambda value: value.update(schema="unsupported"))
    verifier = StubVerifier()
    from underwrite.instrument.evidence.goppi_trajectory import TrajectoryError

    with pytest.raises(TrajectoryError):
        app.audit_goppi_trajectory(exchange, verifier=verifier)
    assert len(verifier.snapshots) == 1


def test_captured_analysis_survives_original_removal(exchange: Path) -> None:
    captured = app.capture_goppi_trajectory(exchange)
    originals = {name: item.data for name, item in captured.items()}
    shutil.rmtree(exchange)
    verifier = StubVerifier()
    outcome = app.audit_captured_goppi_trajectory(captured, verifier=verifier)
    assert outcome.report is not None and outcome.error is None
    assert {name: item.data for name, item in captured.items()} == originals
    history = outcome.verification_history
    assert history == {
        "schema": app.HISTORY_SCHEMA,
        "state": "verified",
        "source_members": [
            {"name": name, "raw_hash": captured[name].sha256, "bytes": len(originals[name])}
            for name in app.MEMBERS
        ],
        "integrity_scope": "self_consistent_content_closure",
        "authenticity": "content_digest_only",
        "source_authentication_independently_verified": False,
        "producer_projection_independently_verified": False,
    }
    assert len(verifier.snapshots) == 1


def test_capture_mapping_and_analysis_mapping_cannot_be_replaced(exchange: Path) -> None:
    captured = app.capture_goppi_trajectory(exchange)
    with pytest.raises(TypeError):
        cast(dict[str, local_capture.CapturedBytes], captured)[app.MEMBERS[0]] = captured[
            app.MEMBERS[1]
        ]
    mutable = dict(captured)

    class ReplacingCallerVerifier(StubVerifier):
        def verify(self, snapshot: Path) -> bytes:
            mutable.clear()
            return super().verify(snapshot)

    outcome = app.audit_captured_goppi_trajectory(mutable, verifier=ReplacingCallerVerifier())
    assert not mutable
    assert outcome.report is not None
    assert len(cast(list[object], outcome.verification_history["source_members"])) == len(
        app.MEMBERS
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("logical_name", "wrong"),
        ("sha256", "sha256:" + "0" * 64),
        ("size", True),
        ("size", 0),
        ("data", bytearray(b"{}")),
    ],
)
def test_forged_capture_identity_raises_before_process(
    exchange: Path, field: str, value: object
) -> None:
    from dataclasses import replace

    captured = dict(app.capture_goppi_trajectory(exchange))
    name = app.MEMBERS[0]
    captured[name] = replace(captured[name], **{field: value})
    verifier = StubVerifier()
    with pytest.raises(app.TrajectoryInputError, match="TRAJECTORY_CAPTURE_IDENTITY_INVALID"):
        app.audit_captured_goppi_trajectory(captured, verifier=verifier)
    assert not verifier.snapshots


@pytest.mark.parametrize("change", ["missing", "extra", "wrong_type"])
def test_captured_input_requires_exact_members(exchange: Path, change: str) -> None:
    captured = dict(app.capture_goppi_trajectory(exchange))
    if change == "missing":
        captured.pop(app.MEMBERS[0])
    elif change == "extra":
        captured["extra"] = captured[app.MEMBERS[0]]
    else:
        captured[app.MEMBERS[0]] = cast(local_capture.CapturedBytes, object())
    verifier = StubVerifier()
    with pytest.raises(app.TrajectoryInputError):
        app.audit_captured_goppi_trajectory(captured, verifier=verifier)
    assert not verifier.snapshots


@pytest.mark.parametrize("limit", ["MAX_DOCUMENT_BYTES", "MAX_FAMILY_BYTES", "MAX_MANIFEST_BYTES"])
def test_captured_input_rechecks_original_byte_limits(
    exchange: Path, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    captured = app.capture_goppi_trajectory(exchange)
    monkeypatch.setattr(app, limit, 1)
    verifier = StubVerifier()
    with pytest.raises((CaptureError, app.TrajectoryInputError)) as caught:
        app.audit_captured_goppi_trajectory(captured, verifier=verifier)
    assert caught.value.code == "INPUT_LIMIT_EXCEEDED"
    assert not verifier.snapshots


@pytest.mark.parametrize("kind", ["json", "shape", "rows", "spans"])
def test_safe_source_diagnostic_has_no_native_verification(
    exchange: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    if kind == "json":
        (exchange / "paired/execution.json").write_bytes(b"invalid")
    elif kind == "shape":
        (exchange / "trajectory/trajectory.json").write_bytes(b"[]")
    elif kind == "rows":
        monkeypatch.setattr(app, "MAX_ROWS", 1)
    else:
        monkeypatch.setattr(app, "MAX_SPANS_PER_UNIT", 1)
    verifier = StubVerifier()
    outcome = app.audit_captured_goppi_trajectory(
        app.capture_goppi_trajectory(exchange), verifier=verifier
    )
    assert outcome.report is None and outcome.error is not None
    assert outcome.verification_history["state"] == "not_performed"
    assert outcome.verification_history["integrity_scope"] is None
    assert outcome.verification_history["authenticity"] is None
    assert not verifier.snapshots


def test_post_native_diagnostic_preserves_verified_history(exchange: Path) -> None:
    _edit(exchange, "trajectory/trajectory.json", lambda value: value.update(schema="unsupported"))
    verifier = StubVerifier()
    outcome = app.audit_captured_goppi_trajectory(
        app.capture_goppi_trajectory(exchange), verifier=verifier
    )
    assert outcome.report is None and outcome.error is not None
    assert outcome.verification_history["state"] == "verified"
    assert len(verifier.snapshots) == 1
    with pytest.raises(type(outcome.error)) as caught:
        app.audit_goppi_trajectory(exchange, verifier=StubVerifier())
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        outcome.error.code,
        outcome.error.reason,
        outcome.error.location,
    )


@pytest.mark.parametrize(
    "reason", ["GOPPI_VERIFIER_REFUSED", "GOPPI_VERIFIER_TIMEOUT", "GOPPI_VERIFIER_NOT_FOUND"]
)
def test_captured_analysis_never_retains_verifier_failures(exchange: Path, reason: str) -> None:
    class FailedVerifier:
        def verify(self, snapshot: Path) -> bytes:
            raise VerifierError("INVALID_INPUT", reason)

    with pytest.raises(app.TrajectoryInputError, match=reason):
        app.audit_captured_goppi_trajectory(
            app.capture_goppi_trajectory(exchange), verifier=FailedVerifier()
        )


def test_captured_analysis_does_not_retain_internal_failures(exchange: Path) -> None:
    error = RuntimeError("unexpected process fault")

    class FailedVerifier:
        def verify(self, snapshot: Path) -> bytes:
            raise error

    with pytest.raises(RuntimeError) as caught:
        app.audit_captured_goppi_trajectory(
            app.capture_goppi_trajectory(exchange), verifier=FailedVerifier()
        )
    assert caught.value is error


def test_history_contract_is_loaded_before_source_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    original = app.packaged_validator
    loaded: list[str] = []

    def validator(name: str) -> object:
        loaded.append(name)
        if name == app.HISTORY_SCHEMA:
            raise SchemaConfigurationError("MISSING_HISTORY_CONTRACT")
        return original(name)

    def forbidden_capture(exchange: Path) -> None:
        pytest.fail("source capture must follow all required contract loads")

    monkeypatch.setattr(app, "packaged_validator", validator)
    monkeypatch.setattr(app, "_capture", forbidden_capture)
    with pytest.raises(SchemaConfigurationError, match="MISSING_HISTORY_CONTRACT"):
        app.capture_goppi_trajectory(Path("/not-read"))
    assert app.HISTORY_SCHEMA in loaded


@pytest.mark.parametrize("limit", ["count", "total", "family"])
def test_captured_input_checks_aggregate_limits(
    exchange: Path, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    from dataclasses import replace

    captured = app.capture_goppi_trajectory(exchange)
    if limit == "family":
        monkeypatch.setattr(app, "MAX_FAMILY_BYTES", max(item.size for item in captured.values()))
        expected = "TRAJECTORY_FAMILY_BYTE_LIMIT"
    else:
        field = "max_files" if limit == "count" else "max_total_bytes"
        monkeypatch.setattr(app, "CAPTURE_POLICY", replace(app.CAPTURE_POLICY, **{field: 1}))
        expected = (
            "CAPTURE_FILE_COUNT_EXCEEDED" if limit == "count" else "CAPTURE_TOTAL_LIMIT_EXCEEDED"
        )
    with pytest.raises((CaptureError, app.TrajectoryInputError), match=expected):
        app.audit_captured_goppi_trajectory(captured, verifier=StubVerifier())


def test_captured_analysis_output_failure_is_not_source_diagnostic(
    exchange: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = app.capture_goppi_trajectory(exchange)
    monkeypatch.setattr(app, "MAX_REPORT_BYTES", 1)
    with pytest.raises(app.TrajectoryInputError, match="TRAJECTORY_REPORT_LIMIT"):
        app.audit_captured_goppi_trajectory(captured, verifier=StubVerifier())


def test_analysis_requires_exactly_one_outcome() -> None:
    error = app.TrajectoryInputError("INVALID_INPUT", "TEST")
    with pytest.raises(ValueError, match="TRAJECTORY_ANALYSIS_REQUIRES_ONE_OUTCOME"):
        app.TrajectoryAnalysis(None, None, {})
    with pytest.raises(ValueError, match="TRAJECTORY_ANALYSIS_REQUIRES_ONE_OUTCOME"):
        app.TrajectoryAnalysis({}, error, {})


@pytest.mark.parametrize(
    "code,reason,state,expected",
    [
        ("INVALID_INPUT", "CAPTURE_INVALID_JSON", "not_performed", True),
        ("INVALID_INPUT", "CAPTURE_INVALID_JSON", "verified", False),
        ("INPUT_LIMIT_EXCEEDED", "TRAJECTORY_SPAN_LIMIT", "not_performed", True),
        ("INVALID_INPUT", "TRAJECTORY_SPAN_LIMIT", "not_performed", False),
        ("INVALID_INPUT", "TRAJECTORY_BINDING_MISMATCH", "verified", True),
        ("INVALID_INPUT", "TRAJECTORY_BINDING_MISMATCH", "not_performed", False),
        ("UNSUPPORTED_FORMAT", "TRAJECTORY_UNSUPPORTED_VALUE", "verified", True),
        ("INVALID_INPUT", "TRAJECTORY_UNSUPPORTED_VALUE", "verified", False),
        ("INVALID_INPUT", "GOPPI_VERIFIER_REFUSED", "verified", False),
        ("INVALID_INPUT", "GOPPI_VERIFIER_REFUSED", "not_performed", False),
        ("INVALID_INPUT", "UNKNOWN_REASON", "not_performed", False),
        ("INVALID_INPUT", "TRAJECTORY_VALUE_INVALID", "refused", False),
    ],
)
def test_diagnostic_history_admission_is_stage_and_code_specific(
    code: str, reason: str, state: str, expected: bool
) -> None:
    assert app.trajectory_diagnostic_history_matches(code, reason, state) is expected

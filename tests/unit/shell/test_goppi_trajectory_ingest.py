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

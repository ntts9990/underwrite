"""Same-buffer trajectory retention and typed reopen across real filesystem boundaries.

Controlled verifier ports test composition, not native Goppi content conformance.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from drivers.goppi_trajectory_verifier_stub import verifier_response
from jsonschema import Draft202012Validator
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.cli import main, record_capture, records
from underwrite.cli import record_trajectory as command
from underwrite.cli.evidence import EvidenceError
from underwrite.cli.local_json import Validator
from underwrite.instrument.evidence.goppi_trajectory import MEMBERS
from underwrite.instrument.ingest import goppi_trajectory as ingest
from underwrite.instrument.ingest.goppi_verifier import VerifierError
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.service.records import RecordError, read_record, read_record_file, verify_record

DIAGNOSTIC_EXIT = 2
STORAGE_EXIT = 3

FIXTURE = Path(__file__).parents[3] / "fixtures/conformance/goppi-trajectory/exchange"


@pytest.mark.parametrize("record", [False, True])
def test_missing_verifier_points_to_executable_option_through_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], record: bool
) -> None:
    argv = ["records", "audit-trajectory"] if record else ["audit-trajectory"]
    argv.extend([
        "--exchange", str(FIXTURE), "--goppi-executable", str(tmp_path / "missing"), "--json"
    ])
    if record:
        argv.extend(["--root", str(tmp_path / "store"), "--id", "missing-verifier"])
    assert main.main(argv) == DIAGNOSTIC_EXIT
    streams = capsys.readouterr()
    assert streams.out == ""
    error = json.loads(streams.err)
    assert error["code"] == "SOURCE_READ_FAILED"
    assert error["reason"] == "GOPPI_VERIFIER_UNAVAILABLE"
    assert error["location"] == "/goppi_executable"
    assert error["next_action"] == "CORRECT_ARGUMENTS"
    assert not (tmp_path / "store").exists()


@pytest.mark.parametrize(
    "failure",
    ["raise SystemExit(7)", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"],
)
def test_failed_verifier_process_never_publishes_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    executable = tmp_path / "failed-verifier"
    executable.write_text(f"#!{sys.executable}\n{failure}\n")
    executable.chmod(0o700)
    store = tmp_path / "store"
    assert main.main([
        "records", "audit-trajectory", "--exchange", str(FIXTURE),
        "--goppi-executable", str(executable), "--root", str(store),
        "--id", "failed-process", "--json",
    ]) == DIAGNOSTIC_EXIT
    streams = capsys.readouterr()
    assert streams.out == ""
    error = json.loads(streams.err)
    assert error["code"] == "INVALID_INPUT"
    assert error["reason"] == "GOPPI_VERIFIER_REFUSED"
    assert not store.exists()


class Stub:
    """This response fixture deliberately does not implement producer verification."""

    def __init__(self, action: Callable[[Path], object] | None = None) -> None:
        self.action = action
        self.calls = 0

    def verify(self, snapshot: Path) -> bytes:
        self.calls += 1
        if self.action is not None:
            self.action(snapshot)
        return json.dumps(verifier_response(snapshot)).encode()


@pytest.fixture
def exchange(tmp_path: Path) -> Path:
    return Path(shutil.copytree(FIXTURE, tmp_path / "exchange"))


def _run(exchange: Path, root: Path, verifier: Stub | None = None) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        command.run_record_trajectory(exchange, root, "sample", verifier=verifier or Stub()),
    )


def _assert_schema(value: dict[str, Any]) -> None:
    cast(Validator, packaged_validator(value["schema"])).validate(value)


def test_same_buffer_capture_survives_source_removal_and_no_native_on_reopen(
    exchange: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    originals = {name: (exchange / name).read_bytes() for name in MEMBERS}
    stub = Stub(lambda _: shutil.rmtree(exchange))
    root = tmp_path / "store"
    result = _run(exchange, root, stub)
    _assert_schema(result)
    assert result["exit_code"] == 0
    assert stub.calls == 1
    manifest = read_record(root, "sample").manifest
    assert manifest["rerun_verification"] == {"status": "not_performed", "reference": None}
    assert manifest["provenance"] == {"verified": False, "attestation_ref": None}
    assert manifest["kind"] == command.KIND
    assert len(cast(list[Any], manifest["files"])) == len(MEMBERS) + 3

    def forbidden(*_: object, **__: object) -> None:
        pytest.fail("Reopen must not resolve or invoke a producer")

    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(command, "GoppiCliVerifier", forbidden)
    monkeypatch.setattr(command, "capture_goppi_trajectory", forbidden)
    reopened = cast(dict[str, Any], command.run_read_trajectory(root, "sample"))
    _assert_schema(reopened)
    assert reopened["analysis"] == result["analysis"]
    assert reopened["native_verification_history"] == result["native_verification_history"]
    assert reopened["native_verification_performed_now"] is False
    assert reopened["current_storage_verification"]["valid"] is True
    for name, raw in originals.items():
        restored = read_record_file(
            root, "sample", f"source/{name}", max_bytes=ingest.trajectory_member_limits()[name]
        )
        assert restored.data == raw
    assert (
        records.main(
            [
                "read-json",
                "--root",
                str(root),
                "--id",
                "sample",
                "--name",
                command.RESULT_NAME,
                "--json",
            ]
        )
        == 0
    )
    raw_record = canonical_bytes(result)
    assert str(exchange).encode() not in raw_record
    assert b"underwrite-trajectory-" not in raw_record


@pytest.mark.parametrize("stage", ["before_native", "after_native"])
def test_diagnostic_saved_and_reopened_with_honest_history(
    exchange: Path, tmp_path: Path, stage: str
) -> None:
    if stage == "before_native":
        (exchange / "paired/rows.jsonl").write_bytes(b"private malformed payload")
    else:
        path = exchange / "trajectory/trajectory.json"
        document = json.loads(path.read_bytes())
        document["call_relationships"] = "invented"
        path.write_bytes(canonical_bytes(document))
    stub = Stub()
    root = tmp_path / "store"
    result = _run(exchange, root, stub)
    _assert_schema(result)
    assert result["exit_code"] == DIAGNOSTIC_EXIT
    assert result["analysis"]["state"] == "error"
    expected = "not_performed" if stage == "before_native" else "verified"
    assert result["native_verification_history"]["state"] == expected
    assert stub.calls == (0 if stage == "before_native" else 1)
    reopened = cast(dict[str, Any], command.run_read_trajectory(root, "sample"))
    assert reopened["analysis"] == result["analysis"]
    assert reopened["native_verification_history"]["state"] == expected
    assert b"private malformed" not in canonical_bytes(result)
    assert (
        read_record_file(root, "sample", "source/paired/rows.jsonl").data
        == (exchange / "paired/rows.jsonl").read_bytes()
    )


@pytest.mark.parametrize(
    "code,reason",
    [
        ("SOURCE_READ_FAILED", "GOPPI_VERIFIER_UNAVAILABLE"),
        ("SOURCE_READ_FAILED", "GOPPI_VERIFIER_TIMEOUT"),
        ("OUTPUT_LIMIT_EXCEEDED", "GOPPI_VERIFIER_OUTPUT_LIMIT"),
        ("INVALID_INPUT", "GOPPI_VERIFIER_REFUSED"),
        ("SOURCE_READ_FAILED", "GOPPI_VERIFIER_PROCESS_FAILED"),
    ],
)
def test_port_failure_never_creates_store_or_consumes_id(
    exchange: Path, tmp_path: Path, code: str, reason: str
) -> None:
    def fail(_: Path) -> None:
        raise VerifierError(code, reason)

    root = tmp_path / "store"
    with pytest.raises(EvidenceError):
        _run(exchange, root, Stub(fail))
    assert not root.exists()


@pytest.mark.parametrize(
    "problem",
    ["missing", "extra", "symlink", "unsafe_id", "snapshot_mutation", "contract", "output"],
)
def test_prepublication_failures_leave_no_store(
    exchange: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
) -> None:
    root = tmp_path / "store"
    stub = Stub()
    identifier = "sample"
    if problem == "missing":
        (exchange / MEMBERS[0]).unlink()
    elif problem == "extra":
        (exchange / "private.txt").write_text("not selected")
    elif problem == "symlink":
        target = exchange / MEMBERS[0]
        target.unlink()
        target.symlink_to(FIXTURE / MEMBERS[0])
    elif problem == "unsafe_id":
        identifier = "../unsafe"
    elif problem == "snapshot_mutation":
        stub = Stub(lambda snapshot: (snapshot / "paired/schema.json").write_bytes(b"{}"))
    elif problem == "output":
        monkeypatch.setattr(command, "OUTPUT_MAX_BYTES", 1)
    else:

        def fail(_: str) -> None:
            raise SchemaConfigurationError("MISSING_CONTRACT")

        monkeypatch.setattr(command, "packaged_validator", fail)
    with pytest.raises((EvidenceError, SchemaConfigurationError)):
        command.run_record_trajectory(exchange, root, identifier, verifier=stub)
    assert not root.exists()


def test_duplicate_record_preserves_original_and_analysis(exchange: Path, tmp_path: Path) -> None:
    root = tmp_path / "store"
    first = _run(exchange, root)
    second = _run(exchange, root)
    _assert_schema(second)
    assert second["exit_code"] == STORAGE_EXIT
    assert second["analysis"] == first["analysis"]
    assert second["storage"]["error"]["code"] == "RECORD_EXISTS"
    assert read_record(root, "sample").digest == first["storage"]["manifest_digest"]


def test_postpublication_failure_keeps_known_identity(
    exchange: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: object) -> None:
        raise RecordError("STORE_IO_ERROR")

    monkeypatch.setattr(record_capture, "verify_record", fail)
    root = tmp_path / "store"
    result = _run(exchange, root)
    _assert_schema(result)
    assert result["exit_code"] == STORAGE_EXIT
    assert (
        result["storage"]["published_record"]["manifest_digest"]
        == read_record(root, "sample").digest
    )
    assert command.run_read_trajectory(root, "sample")["native_verification_performed_now"] is False


def test_blocked_storage_returns_analysis_without_false_absence_claim(
    exchange: Path, tmp_path: Path
) -> None:
    root = tmp_path / "store"
    root.write_text("occupied")
    result = _run(exchange, root)
    assert result["exit_code"] == STORAGE_EXIT
    assert result["analysis"]["state"] == "result"
    assert result["storage"]["published_record"] is None
    assert root.read_text() == "occupied"


def _rewrite(root: Path, name: str, change: Callable[[dict[str, Any]], object]) -> None:
    """Make internally hash-consistent corruption; typed binding must still reject it."""
    directory = root / "records/sample"
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    if name == "manifest":
        change(manifest)
    else:
        path = directory / "files" / name
        document = json.loads(path.read_bytes())
        change(document)
        # Preserve deliberately invalid number types; canonicalization would erase 1.0.
        raw = json.dumps(document, separators=(",", ":")).encode() + b"\n"
        path.write_bytes(raw)
        entry = next(item for item in manifest["files"] if item["name"] == name)
        entry.update(bytes=len(raw), sha256=digest_bytes(raw))
    manifest_path.write_bytes(canonical_bytes(manifest))


CORRUPTIONS: list[tuple[str, Callable[[dict[str, Any]], object]]] = [
    (
        command.HISTORY_NAME,
        lambda d: d["source_members"][0].update(bytes=float(d["source_members"][0]["bytes"])),
    ),
    (
        command.RESULT_NAME,
        lambda d: d["input_members"][0].update(bytes=float(d["input_members"][0]["bytes"])),
    ),
    ("manifest", lambda d: d.update(kind="different.kind")),
    ("manifest", lambda d: d["selection"].update(excluded=["invented"])),
    ("manifest", lambda d: d["metadata"].update(layout="future.v2")),
    ("manifest", lambda d: d["files"][0].update(role="log")),
    ("manifest", lambda d: d["files"].pop()),
    ("manifest", lambda d: d["files"][0].update(source_locator="invented")),
    (command.INVOCATION_NAME, lambda d: d.update(analysis_state="error")),
    (
        command.HISTORY_NAME,
        lambda d: d["source_members"][0].update(raw_hash="sha256:" + "0" * 64),
    ),
    (
        command.HISTORY_NAME,
        lambda d: d["source_members"].__setitem__(0, deepcopy(d["source_members"][1])),
    ),
    (command.HISTORY_NAME, lambda d: d.update(source_authentication_independently_verified=0)),
    (
        command.HISTORY_NAME,
        lambda d: d.update(state="not_performed", integrity_scope=None, authenticity=None),
    ),
    (command.RESULT_NAME, lambda d: d["input_members"][0].update(sha256="sha256:" + "0" * 64)),
    (
        command.RESULT_NAME,
        lambda d: d["input_members"].__setitem__(0, deepcopy(d["input_members"][1])),
    ),
    (
        command.RESULT_NAME,
        lambda d: d["comparisons"][0]["baseline"]["outcome"].update(scored_value=0),
    ),
    (
        command.RESULT_NAME,
        lambda d: d["trust_scope"].update(
            native_verification="not_performed",
            integrity_scope="not_checked",
            authenticity="not_checked",
        ),
    ),
]


@pytest.mark.parametrize(("name", "change"), CORRUPTIONS)
def test_reopen_refuses_hash_consistent_misbound_record(
    exchange: Path,
    tmp_path: Path,
    name: str,
    change: Callable[[dict[str, Any]], object],
) -> None:
    root = tmp_path / "store"
    _run(exchange, root)
    _rewrite(root, name, change)
    with pytest.raises(EvidenceError):
        command.run_read_trajectory(root, "sample")


def test_reopen_checks_unselected_sources(exchange: Path, tmp_path: Path) -> None:
    root = tmp_path / "store"
    _run(exchange, root)
    (root / "records/sample/files/source/paired/schema.json").write_text("{}")
    with pytest.raises(EvidenceError):
        command.run_read_trajectory(root, "sample")


def test_reopen_binds_each_read_to_original_manifest(
    exchange: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "store"
    _run(exchange, root)
    original = command.read_record_file
    calls = 0

    def replace(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            _rewrite(root, "manifest", lambda m: m.update(label="replacement"))
        return result

    monkeypatch.setattr(command, "read_record_file", replace)
    with pytest.raises(EvidenceError):
        command.run_read_trajectory(root, "sample")


def _large_exchange(exchange: Path, factor: int = 12) -> None:
    """Scale actual producer-shaped inputs; stub does not verify manifest closure."""
    rows = [json.loads(line) for line in (exchange / "paired/rows.jsonl").read_text().splitlines()]
    expanded: list[dict[str, Any]] = []
    for repetition in range(factor):
        for row in rows:
            item = deepcopy(row)
            item["case_id"] += f"-copy-{repetition}-" + "x" * 800
            expanded.append(item)
    (exchange / "paired/rows.jsonl").write_bytes(
        b"".join(canonical_bytes(row) + b"\n" for row in expanded)
    )
    path = exchange / "trajectory/trajectory.json"
    trajectory = json.loads(path.read_bytes())
    for unit in trajectory["units"]:
        original = unit["cases"]
        unit["cases"] = []
        for repetition in range(factor):
            for case in original:
                item = deepcopy(case)
                item["row_key"]["case_id"] += f"-copy-{repetition}-" + "x" * 800
                if item["verifier_observation"]["status"] == "observed":
                    item["verifier_observation"]["case_id"] = item["row_key"]["case_id"]
                for span in item["spans"]:
                    span["span_id"] = digest_bytes(
                        f"{span['span_id']}:{repetition}".encode()
                    ).removeprefix("sha256:")
                unit["cases"].append(item)
    trajectory["availability_counts"] = {
        k: v * factor for k, v in trajectory["availability_counts"].items()
    }
    path.write_bytes(canonical_bytes(trajectory))
    path = exchange / "paired/execution.json"
    execution = json.loads(path.read_bytes())
    for stage in execution["stages"]:
        stage["exported_row_count"] *= factor
        stage["planned_case_count"] *= factor
    for key in ("row_count", "planned_stage_case_count_total"):
        execution["counts"][key] *= factor
    path.write_bytes(canonical_bytes(execution))
    path = exchange / "paired/manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["row_count"] = len(expanded)
    path.write_bytes(canonical_bytes(manifest))
    _validate_scaled_source(exchange, execution, expanded, trajectory)


def _validate_scaled_source(
    exchange: Path,
    execution: dict[str, Any],
    rows: list[dict[str, Any]],
    trajectory: dict[str, Any],
) -> None:
    paired_schema = json.loads((exchange / "paired/schema.json").read_bytes())["$defs"]
    trajectory_schema = json.loads((exchange / "trajectory/schema.json").read_bytes())["documents"]
    cast(Validator, Draft202012Validator(paired_schema["execution"])).validate(execution)
    row_validator = cast(Validator, Draft202012Validator(paired_schema["row"]))
    for row in rows:
        row_validator.validate(row)
    cast(Validator, Draft202012Validator(trajectory_schema["trajectory"])).validate(trajectory)


def test_actual_large_analysis_retains_and_reopens_without_generic_limit_change(
    exchange: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _large_exchange(exchange)
    root = tmp_path / "store"
    result = _run(exchange, root)
    assert result["exit_code"] == 0, result["analysis"]
    raw = canonical_bytes(result["analysis"]["report"])
    assert 2 * 1024 * 1024 < len(raw) < command.MAX_REPORT_BYTES
    _assert_schema(result)
    shutil.rmtree(exchange)
    reopened = cast(dict[str, Any], command.run_read_trajectory(root, "sample"))
    assert reopened["analysis"] == result["analysis"]
    assert command.main_read(["--root", str(root), "--id", "sample", "--json"]) == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["analysis"] == result["analysis"]
    with pytest.raises(RecordError, match="INPUT_LIMIT_EXCEEDED"):
        read_record_file(root, "sample", command.RESULT_NAME)
    assert (
        records.main(
            [
                "read-json",
                "--root",
                str(root),
                "--id",
                "sample",
                "--name",
                command.RESULT_NAME,
                "--json",
            ]
        )
        == DIAGNOSTIC_EXIT
    )
    diagnostic = json.loads(capsys.readouterr().err)
    assert diagnostic["code"] == "INPUT_LIMIT_EXCEEDED"
    assert verify_record(root, "sample")["valid"] is True


@pytest.mark.parametrize("native_state", ["not_performed", "verified"])
def test_diagnostic_history_cannot_claim_wrong_analysis_stage(
    exchange: Path,
    tmp_path: Path,
    native_state: str,
) -> None:
    root = tmp_path / "store"
    if native_state == "not_performed":
        (exchange / "paired/rows.jsonl").write_text("malformed")
    else:
        path = exchange / "trajectory/trajectory.json"
        value = json.loads(path.read_bytes())
        value["call_relationships"] = "invented"
        path.write_bytes(canonical_bytes(value))
    _run(exchange, root)

    def change(history: dict[str, Any]) -> None:
        if native_state == "not_performed":
            history.update(
                state="verified",
                integrity_scope="self_consistent_content_closure",
                authenticity="content_digest_only",
            )
        else:
            history.update(state="not_performed", integrity_scope=None, authenticity=None)

    _rewrite(root, command.HISTORY_NAME, change)
    with pytest.raises(EvidenceError, match="TRAJECTORY_RECORD_BINDING_MISMATCH"):
        command.run_read_trajectory(root, "sample")


def test_stored_report_budget_includes_exactly_one_newline(
    exchange: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = _run(exchange, tmp_path / "reference")
    framed_size = len(canonical_bytes(reference["analysis"]["report"])) + 1
    monkeypatch.setattr(command, "MAX_REPORT_BYTES", framed_size)
    assert _run(exchange, tmp_path / "exact")["exit_code"] == 0
    monkeypatch.setattr(command, "MAX_REPORT_BYTES", framed_size - 1)
    with pytest.raises(EvidenceError, match="OUTPUT_LIMIT_EXCEEDED"):
        _run(exchange, tmp_path / "overflow")
    assert not (tmp_path / "overflow").exists()

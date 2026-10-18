"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
import os
import socket
import urllib.request
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from _repo_paths import repo_root
from jsonschema import Draft202012Validator
from underwrite_core.canonical import content_digest

from underwrite.cli import measure, measurement_flow
from underwrite.cli.main import main

ROOT = repo_root(Path(__file__).resolve())
FIXTURES = ROOT / "fixtures/examples/event-measurement"
ERROR_EXIT = 2


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def test_measure_help_states_native_boundary(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["measure", "--help"])
    assert caught.value.code == 0
    output = capsys.readouterr()
    assert output.err == ""
    help_text = " ".join(output.out.split())
    for phrase in (
        "underwrite.evidence-bundle v1",
        "binary-event-directional.v1",
        "rows and availability records require epoch=null and repeat=0",
        "External observation profiles are unsupported",
        "UNSUPPORTED_PROFILE with a valid policy",
        "no read is produced",
        "Exit 0 means a read was produced, not approval",
    ):
        assert phrase in help_text


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_bytes())


def test_real_measure_fixtures(capsys: pytest.CaptureFixture[str]) -> None:
    paths = [FIXTURES / "read-input.json", FIXTURES / "policy.json"]
    before = [path.read_bytes() for path in paths]
    assert main(_args()) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    read = json.loads(captured.out)
    assert read["schema"] == "read.v2"
    assert "score" not in read and "confidence" not in read
    assert read["sources"] == {
        "observations": [content_digest(_load("read-input.json"))],
        "policy": content_digest(_load("policy.json")),
    }
    assert before == [path.read_bytes() for path in paths]


def _args(observation: Path | None = None, policy: Path | None = None) -> list[str]:
    return [
        "measure",
        "--observation",
        str(observation or FIXTURES / "read-input.json"),
        "--policy",
        str(policy or FIXTURES / "policy.json"),
        "--json",
    ]


def _error(capsys: pytest.CaptureFixture[str], code: str) -> dict[str, Any]:
    output = capsys.readouterr()
    assert output.out == ""
    payload = json.loads(output.err)
    schema = json.loads((ROOT / "contracts/measurement_error.v1.schema.json").read_bytes())
    cast("Validator", Draft202012Validator(schema)).validate(payload)
    assert payload["code"] == code and payload["exit_code"] == ERROR_EXIT
    assert "secret" not in output.err
    return payload


def test_unconsumed_cost_is_not_a_probability_or_inferential_budget(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observation = _load("read-input.json")
    for reading in observation["payload"]["readings"]:
        for row in reading["rows"]:
            row["payload"]["cost"] = 1e300
            row["payload"]["private_note"] = "secret-unused-input"
    path = tmp_path / "observation.json"
    path.write_text(json.dumps(observation))
    assert main(_args(observation=path)) == 0
    output = capsys.readouterr()
    assert output.err == "" and "secret-unused-input" not in output.out


@pytest.mark.parametrize(
    "raw",
    [
        b'{"secret":1,"\\u0073ecret":2}',
        b'{"x":NaN}',
        b'{"x":1e999}',
        b'{"x":"\\ud800"}',
        b"\xff",
        b"[]",
        b"{",
    ],
)
@pytest.mark.parametrize("location", ["observation", "policy"])
def test_strict_decode(
    raw: bytes,
    location: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "secret.json"
    path.write_bytes(raw)
    assert main(_args(**{location: path})) == ERROR_EXIT
    error = _error(capsys, "INVALID_" + location.upper())
    assert error["location"] == "/" + location


@pytest.mark.parametrize("fault", ["missing", "directory", "fifo"])
def test_descriptor_failures(
    fault: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "secret"
    if fault == "directory":
        path.mkdir()
    elif fault == "fifo":
        os.mkfifo(path)
    assert main(_args(observation=path)) == ERROR_EXIT
    _error(capsys, "SOURCE_READ_FAILED")


@pytest.mark.parametrize("kind", ["byte", "depth", "output"])
def test_limits_emit_no_partial_output(
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = FIXTURES / "read-input.json"
    if kind == "byte":
        monkeypatch.setattr(measurement_flow, "OBSERVATION_MAX_BYTES", 1)
    elif kind == "depth":
        path = tmp_path / "deep.json"
        path.write_text(
            "[" * (measure.OBSERVATION_MAX_DEPTH + 1)
            + "0"
            + "]" * (measure.OBSERVATION_MAX_DEPTH + 1)
        )
    else:
        monkeypatch.setattr(measure, "OUTPUT_MAX_BYTES", 1)
    assert main(_args(observation=path)) == ERROR_EXIT
    _error(capsys, "OUTPUT_LIMIT_EXCEEDED" if kind == "output" else "INPUT_LIMIT_EXCEEDED")


def test_inventory_limit_is_a_schema_refusal_before_computation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy = _load("policy.json")
    policy["expected_case_ids"] = [f"case-{index}" for index in range(2001)]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("measurement computation reached for oversized requested cells")

    monkeypatch.setattr(measurement_flow, "assemble_event_read", forbidden)
    assert main(_args(policy=path)) == ERROR_EXIT
    error = _error(capsys, "INVALID_POLICY")
    assert (error["reason"], error["location"]) == (
        "SCHEMA_VALIDATION_FAILED",
        "/policy",
    )


def test_output_limit_includes_newline(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(_args()) == 0
    output = capsys.readouterr().out
    monkeypatch.setattr(measure, "OUTPUT_MAX_BYTES", len(output.encode()))
    assert main(_args()) == 0
    assert capsys.readouterr().out == output
    monkeypatch.setattr(measure, "OUTPUT_MAX_BYTES", len(output.encode()) - 1)
    assert main(_args()) == ERROR_EXIT
    _error(capsys, "OUTPUT_LIMIT_EXCEEDED")


@pytest.mark.parametrize("legacy", ["measurement_policy.v1", "measurement_policy.v2"])
def test_v1_policy_requires_explicit_migration(
    legacy: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _load("policy.json")
    policy["schema"] = legacy
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy))
    assert main(_args(policy=path)) == ERROR_EXIT
    error = _error(capsys, "INVALID_POLICY")
    assert error["reason"] == "LEGACY_POLICY_REQUIRES_MIGRATION"
    assert error["next_action"] == "PROVIDE_COMPLETE_POLICY"


@pytest.mark.parametrize("fault", ["invalid-policy", "bool-number", "profile"])
def test_invalid_inputs_before_computation(
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy = _load("policy.json")
    if fault == "invalid-policy":
        del policy["diagnostics"]
    elif fault == "noncanonical":
        policy["subject"]["subject_id"] = "e\u0301"
    elif fault == "bool-number":
        policy["diagnostics"]["all_k"] = True
    else:
        policy["profile"] = "secret"
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("statistics called for invalid policy")

    monkeypatch.setattr(measurement_flow, "assemble_event_read", forbidden)
    assert main(_args(policy=path)) == ERROR_EXIT
    _error(capsys, "UNSUPPORTED_PROFILE" if fault == "profile" else "INVALID_POLICY")


def test_one_observation_budget_is_inconclusive_not_successful_absence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _load("policy.json")
    policy["eprocess"]["max_observations"] = 1
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy))
    assert main(_args(policy=path)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["verdict"] == "warn"
    assert output["reason_codes"] == ["observation_budget_exhausted"]
    assert output["directional"]["consumed_count"] == 1


def test_usage_and_unexpected_exception_are_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["measure", "--secret"]) == ERROR_EXIT
    _error(capsys, "USAGE_ERROR")

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret input must not escape")

    monkeypatch.setattr(measurement_flow, "assemble_event_read", broken)
    assert main(_args()) == ERROR_EXIT
    _error(capsys, "INTERNAL_ERROR")


def test_human_escapes_identity_and_success_does_not_access_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy = _load("policy.json")
    policy["subject"]["subject_id"] = "label\n\x1b[31m"
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("measurement attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    assert main(_args(policy=path)[:-1]) == 0
    out = capsys.readouterr()
    assert out.err == "" and "exit 0 is not approval" in out.out
    assert "\\n\\u001b[31m" in out.out and "\x1b" not in out.out
    assert "sources:" in out.out and "diagnostics:" in out.out


def test_missing_expected_outcome_cannot_authorize_inference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observation = _load("read-input.json")
    selector = observation["payload"]["measurement_semantics"]["outcome"]
    for reading in observation["payload"]["readings"]:
        if all(reading.get(key) == selector[key] for key in ("instrument", "metric_id")):
            reading["rows"].pop()
    path = tmp_path / "obs.json"
    path.write_text(json.dumps(observation))
    assert main(_args(observation=path)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["verdict"] == "not_measured"
    assert "outcome_population_incomplete" in output["reason_codes"]
    assert output["directional"]["e_upper"] is None


def test_case_limit_rejects_before_statistics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from underwrite.measurement import event_read
    from underwrite.measurement.read_input import MAX_CASES

    observation = _load("read-input.json")
    row = observation["payload"]["readings"][1]["rows"][0]
    observation["payload"]["availability"] = []
    observation["payload"]["readings"][1]["rows"] = [
        dict(row, case_id=f"case-{index}", sequence=index) for index in range(2 * MAX_CASES + 1)
    ]
    path = tmp_path / "obs.json"
    path.write_text(json.dumps(observation))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("oversize population reached statistics")

    monkeypatch.setattr(event_read, "run_directional", forbidden)
    assert main(_args(observation=path)) == ERROR_EXIT
    _error(capsys, "INPUT_LIMIT_EXCEEDED")


def test_unrecognized_pure_error_is_not_echoed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        error = measure.ReadInputError("INVALID_POLICY", "INVALID_IDENTITY", "/policy")
        error.reason = "secret"
        raise error

    monkeypatch.setattr(measurement_flow, "assemble_event_read", broken)
    assert main(_args()) == ERROR_EXIT
    _error(capsys, "INTERNAL_ERROR")


def test_policy_whitespace_does_not_change_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_args()) == 0
    expected = capsys.readouterr().out
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(_load("policy.json"), indent=7))
    assert main(_args(policy=path)) == 0
    assert capsys.readouterr().out == expected


def test_unsupported_platform_maps_to_safe_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delattr(os, "O_NONBLOCK")
    assert main(_args()) == ERROR_EXIT
    error = _error(capsys, "UNSUPPORTED_PLATFORM")
    assert error["next_action"] == "USE_SUPPORTED_PLATFORM"


@pytest.mark.parametrize(
    "fault",
    ["missing", "invalid-json", "wrong-id", "remote", "nested-id", "invalid-type", "unresolved"],
)
def test_current_packaged_contract_failure_cannot_retrieve_or_use_cwd(
    fault: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from underwrite.instrument.ingest import schema as schema_owner

    root = tmp_path / "trusted"
    folder = root / "_contracts"
    folder.mkdir(parents=True)
    for name in ("read.v2", "measurement_policy.v3", "observation.v1"):
        filename = name + ".schema.json"
        (folder / filename).write_bytes((ROOT / "contracts" / filename).read_bytes())
    path = folder / "measurement_policy.v3.schema.json"
    document = json.loads(path.read_bytes())
    if fault == "missing":
        path.unlink()
    elif fault == "invalid-json":
        path.write_text("secret{")
    else:
        document.update(
            {
                "wrong-id": {"$id": "urn:secret"},
                "remote": {"$ref": "https://secret.invalid/schema"},
                "nested-id": {"$defs": {"hidden": {"$id": "urn:secret"}}},
                "invalid-type": {"type": "secret"},
                "unresolved": {"$ref": "#/$defs/secret"},
            }[fault]
        )
        path.write_text(json.dumps(document))
    (tmp_path / path.name).write_bytes((ROOT / "contracts" / path.name).read_bytes())
    monkeypatch.chdir(tmp_path)

    def resources(name: str) -> Path:
        assert name == "underwrite"
        return root

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("schema attempted external retrieval")

    monkeypatch.setattr(schema_owner, "files", resources)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    assert main(_args()) == ERROR_EXIT
    _error(capsys, "SCHEMA_CONFIGURATION_ERROR")


def test_unknown_event_error_is_not_echoed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        error = measure.EventReadError("INVALID_POLICY", "INVALID_POLICY_FIELDS", "/policy")
        error.reason = "secret"  # type: ignore[assignment]
        raise error

    monkeypatch.setattr(measurement_flow, "assemble_event_read", broken)
    assert main(_args()) == ERROR_EXIT
    _error(capsys, "INTERNAL_ERROR")


def test_all_failures_with_perfect_forecasts_do_not_pass(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observation = _load("read-input.json")
    declaration = observation["payload"]["measurement_semantics"]
    for reading in observation["payload"]["readings"]:
        if all(
            reading.get(key) == declaration["outcome"][key] for key in ("instrument", "metric_id")
        ):
            for row in reading["rows"]:
                row["outcome"] = 0
        if all(
            reading.get(key) == declaration["prediction"][key]
            for key in ("instrument", "metric_id")
        ):
            for row in reading["rows"]:
                row["score"] = 0
    path = tmp_path / "failure-observation.json"
    path.write_text(json.dumps(observation))
    assert main(_args(observation=path)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["verdict"] == "fail"
    assert output["decision"]["status"] == "unfavorable"
    assert output["diagnostics"]["brier"]["value"] == 0
    assert output["diagnostics"]["ece"]["value"] == 0


@pytest.mark.parametrize(
    ("observation_raw", "policy_raw", "expected"),
    [
        (
            b"{",
            None,
            ("INVALID_OBSERVATION", "INVALID_JSON_INPUT", "/observation", ["observation"]),
        ),
        (
            b"{}",
            None,
            ("SOURCE_READ_FAILED", "SOURCE_READ_FAILED", "/policy", ["observation", "policy"]),
        ),
        (None, b"{", ("SOURCE_READ_FAILED", "SOURCE_READ_FAILED", "/observation", ["observation"])),
        (None, None, ("SOURCE_READ_FAILED", "SOURCE_READ_FAILED", "/observation", ["observation"])),
        (
            b"{}",
            b"{}",
            (
                "INVALID_OBSERVATION",
                "SCHEMA_VALIDATION_FAILED",
                "/observation",
                ["observation", "policy"],
            ),
        ),
    ],
)
def test_measure_lazy_input_failure_precedence(
    observation_raw: bytes | None,
    policy_raw: bytes | None,
    expected: tuple[str, str, str, list[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, reason, location, roles = expected
    observation, policy = tmp_path / "observation", tmp_path / "policy"
    for path, raw in ((observation, observation_raw), (policy, policy_raw)):
        if raw is not None:
            path.write_bytes(raw)
    consumed: list[str] = []
    original = measure.read_document

    def tracked(
        path: Path,
        max_bytes: int,
        max_depth: int,
        location: str,
        invalid: str,
        error: Any,
    ) -> tuple[dict[str, Any], str]:
        consumed.append(path.name)
        return original(path, max_bytes, max_depth, location, invalid, error)

    monkeypatch.setattr(measure, "read_document", tracked)
    assert main(_args(observation, policy)) == ERROR_EXIT
    payload = _error(capsys, code)
    assert (payload["reason"], payload["location"]) == (reason, location)
    assert consumed == roles


def test_buffer_measurement_matches_cli_and_consumes_each_role_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from underwrite.cli.local_json import decode_document

    consumed: list[measure.InputRole] = []

    def reader(role: measure.InputRole) -> tuple[dict[str, Any], str]:
        consumed.append(role)
        filename = "read-input.json" if role == "observation" else "policy.json"
        return decode_document(
            (FIXTURES / filename).read_bytes(),
            *measure.input_limits(role),
            "/" + role,
            "INVALID_" + role.upper(),
            measure.MeasurementError,
        )

    result = measure.read_measurement(reader)
    assert consumed == ["observation", "policy"]
    assert main(_args()) == 0
    assert result == json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("stage", ["construction", "evaluation"])
def test_shared_measurement_preserves_configuration_faults(
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumed: list[measure.InputRole] = []

    def reader(role: measure.InputRole) -> tuple[dict[str, Any], str]:
        consumed.append(role)
        document = _load("read-input.json" if role == "observation" else "policy.json")
        return document, content_digest(document)

    def broken(*args: object, **kwargs: object) -> Any:
        raise measure.SchemaConfigurationError("SCHEMA_EVALUATION_FAILED")

    monkeypatch.setattr(
        measurement_flow,
        "measurement_validators" if stage == "construction" else "validate",
        broken,
    )
    with pytest.raises(measure.SchemaConfigurationError):
        measure.read_measurement(reader)
    assert consumed == ["observation", "policy"]


@pytest.mark.parametrize(
    "schema", [[], {}, None, True, 3, "measurement_policy.v99", "../../secret"]
)
def test_unknown_policy_schema_is_finite_refusal_before_computation(
    schema: object,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _load("policy.json")
    policy["schema"] = schema
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("unsupported policy schema reached measurement")

    monkeypatch.setattr(measurement_flow, "assemble_event_read", forbidden)
    assert main(_args(policy=path)) == ERROR_EXIT
    error = _error(capsys, "UNSUPPORTED_PROFILE")
    assert (error["reason"], error["location"]) == ("UNSUPPORTED_POLICY_PROFILE", "/policy")

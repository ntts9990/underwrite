"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
import os
import socket
import sys
import urllib.request
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from _repo_paths import repo_root
from jsonschema import Draft202012Validator, ValidationError
from underwrite_core.canonical import content_digest

from underwrite.cli import measure
from underwrite.cli.main import main

ROOT = repo_root(Path(__file__).resolve())
FIXTURES = ROOT / "fixtures/examples/measurement"
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
        "binary-calibration-two-stage.v1",
        "rows and availability records require epoch=null and repeat=0",
        "External observation profiles are unsupported",
        "UNSUPPORTED_PROFILE with a valid policy",
        "no read is produced",
        "Exit 0 means a read was produced, not approval",
    ):
        assert phrase in help_text


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_bytes())


@pytest.mark.parametrize(
    "observation,policy,verdict,score,state",
    [
        ("read-input.json", "policy.json", "pass", 1, "full"),
        ("read-input.json", "policy-source-2.json", "warn", 1, "partial"),
        ("read-input.json", "policy-quorum2.json", "not_measured", None, "below_quorum"),
        ("observation-below-quorum.json", "policy.json", "not_measured", None, "below_quorum"),
    ],
)
def test_real_measure_fixtures(
    observation: str,
    policy: str,
    verdict: str,
    score: float | None,
    state: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = [FIXTURES / observation, FIXTURES / policy]
    before = [p.read_bytes() for p in paths]
    assert (
        main(["measure", "--observation", str(paths[0]), "--policy", str(paths[1]), "--json"]) == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    read = json.loads(captured.out)
    assert (read["verdict"], read["score"], read["availability"]["state"]) == (
        verdict,
        score,
        state,
    )
    assert read["schema"] == "read.v1" and read["escalate"] is True
    assert read["sources"] == {
        "observations": [content_digest(_load(observation))],
        "policy": content_digest(_load(policy)),
    }
    assert before == [p.read_bytes() for p in paths]
    if verdict == "not_measured":
        assert "below_quorum" in read["reason_codes"]


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


def test_consumed_cost_overflow_has_safe_observation_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observation, policy = _load("read-input.json"), _load("policy.json")
    selector = policy["inputs"]["eprocess"]
    for reading in observation["payload"]["readings"]:
        if all(reading.get(key) == value for key, value in selector.items()):
            for row in reading["rows"]:
                row["payload"]["cost"] = sys.float_info.max
                row["payload"]["private_note"] = "secret-overflow-input"
    policy["eprocess"]["max_cost"] = None
    paths = [tmp_path / "observation.json", tmp_path / "policy.json"]
    for path, document in zip(paths, (observation, policy), strict=True):
        path.write_text(json.dumps(document), encoding="utf-8")
    before = [path.read_bytes() for path in paths]
    assert main(_args(*paths)) == ERROR_EXIT
    error = _error(capsys, "INVALID_OBSERVATION")
    assert error["reason"] == "COST_OVERFLOW"
    assert error["location"] == "/observation"
    assert before == [path.read_bytes() for path in paths]


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
        monkeypatch.setattr(measure, "OBSERVATION_MAX_BYTES", 1)
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


def test_v1_policy_requires_explicit_migration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _load("policy.json")
    policy["schema"] = "measurement_policy.v1"
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy))
    assert main(_args(policy=path)) == ERROR_EXIT
    error = _error(capsys, "INVALID_POLICY")
    assert error["reason"] == "COMPLETE_V2_POLICY_REQUIRED"
    assert error["next_action"] == "PROVIDE_COMPLETE_POLICY"


@pytest.mark.parametrize("fault", ["invalid-policy", "noncanonical", "bool-number", "profile"])
def test_invalid_inputs_before_computation(
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy = _load("policy.json")
    if fault == "invalid-policy":
        del policy["panel"]
    elif fault == "noncanonical":
        policy["subject"]["label"] = "e\u0301"
    elif fault == "bool-number":
        policy["quorum"] = True
    else:
        policy["profile"] = "secret"
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("statistics called for invalid policy")

    monkeypatch.setattr(measure, "assemble_read", forbidden)
    assert main(_args(policy=path)) == ERROR_EXIT
    _error(capsys, "UNSUPPORTED_PROFILE" if fault == "profile" else "INVALID_POLICY")


def test_first_cost_budget_is_error_not_successful_absence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observation, policy = _load("read-input.json"), _load("policy.json")
    for row in observation["payload"]["readings"][1]["rows"]:
        row["payload"]["cost"] = 2
    policy["eprocess"]["max_cost"] = 1
    obs, pol = tmp_path / "observation.json", tmp_path / "policy.json"
    obs.write_text(json.dumps(observation))
    pol.write_text(json.dumps(policy))
    assert main(_args(obs, pol)) == ERROR_EXIT
    error = _error(capsys, "INVALID_POLICY")
    assert error["reason"] == "NO_TRIALS"


def _assets(tmp_path: Path) -> Path:
    root = tmp_path / "trusted"
    folder = root / "_contracts"
    folder.mkdir(parents=True)
    for name in ("read.v1", "measurement_policy.v2", "statistical_seed.v1"):
        filename = name + ".schema.json"
        (folder / filename).write_bytes((ROOT / "contracts" / filename).read_bytes())
    return root


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "invalid-json",
        "wrong-id",
        "http",
        "file",
        "urn",
        "nested-id",
        "dynamic-remote",
        "dynamic-local",
        "wrong-position",
        "subject-pointer",
        "subject-sibling",
        "subject-missing",
        "dependent-subject",
        "invalid-type",
        "unresolved",
        "seed-missing-const",
        "seed-boolean",
        "seed-dependent",
    ],
)
def test_hostile_trusted_resources_fail_without_retrieval_or_cwd_repair(
    fault: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _assets(tmp_path)
    path = root / "_contracts/measurement_policy.v2.schema.json"
    doc = json.loads(path.read_bytes())
    root_changes = {
        "wrong-id": {"$id": "urn:secret"},
        "http": {"$ref": "https://secret.invalid/schema"},
        "file": {"$ref": "file:///secret"},
        "urn": {"$ref": "urn:secret"},
        "nested-id": {"$defs": {"x": {"$id": "urn:secret"}}},
        "dynamic-local": {"$dynamicRef": "#"},
        "dynamic-remote": {"$dynamicRef": "https://secret.invalid"},
        "wrong-position": {"$ref": "urn:underwrite:contract:statistical_seed.v1"},
        "invalid-type": {"type": "secret"},
        "unresolved": {"$ref": "#/$defs/secret"},
    }
    doc.update(root_changes.get(fault, {}))
    if fault == "subject-pointer":
        doc["properties"]["subject"]["$ref"] = "urn:underwrite:contract:read.v1#/$defs/secret"
    elif fault == "subject-sibling":
        doc["properties"]["subject"]["maxProperties"] = 0
    elif fault in {"subject-missing", "dependent-subject"}:
        path = root / "_contracts/read.v1.schema.json"
        doc = json.loads(path.read_bytes())
        if fault == "subject-missing":
            del doc["$defs"]["subject"]
        else:
            doc["$defs"]["subject"]["$ref"] = "#/$defs/subject"
    elif fault.startswith("seed-"):
        path = root / "_contracts/statistical_seed.v1.schema.json"
        doc = json.loads(path.read_bytes())
        if fault == "seed-missing-const":
            del doc["const"]
        else:
            doc.update(
                {"seed-boolean": {"const": True}, "seed-dependent": {"$ref": "#/$defs/subject"}}[
                    fault
                ]
            )
    if fault == "missing":
        path.unlink()
    else:
        path.write_text("secret{" if fault == "invalid-json" else json.dumps(doc))
    # Valid files in CWD cannot repair a broken package.
    (tmp_path / "measurement_policy.v2.schema.json").write_bytes(
        (ROOT / "contracts/measurement_policy.v2.schema.json").read_bytes()
    )
    monkeypatch.chdir(tmp_path)

    def resources(name: str) -> Path:
        assert name == "underwrite"
        return root

    monkeypatch.setattr(measure, "files", resources)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("schema attempted external retrieval")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    assert main(_args()) == ERROR_EXIT
    _error(capsys, "SCHEMA_CONFIGURATION_ERROR")


def test_localization_inherits_owner_without_mutation_or_stale_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _assets(tmp_path)

    def resources(name: str) -> Path:
        assert name == "underwrite"
        return root

    monkeypatch.setattr(measure, "files", resources)
    paths = list((root / "_contracts").iterdir())
    before = {p: p.read_bytes() for p in paths}
    _, validator = measure.measurement_validators()
    validator.validate(_load("policy.json"))
    assert before == {p: p.read_bytes() for p in paths}
    owner = root / "_contracts/read.v1.schema.json"
    schema = json.loads(owner.read_bytes())
    schema["$defs"]["subject"]["properties"]["subject_id"]["const"] = "changed-owner"
    owner.write_text(json.dumps(schema))
    _, changed = measure.measurement_validators()
    with pytest.raises(ValidationError):
        changed.validate(_load("policy.json"))


def test_usage_and_unexpected_exception_are_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["measure", "--secret"]) == ERROR_EXIT
    _error(capsys, "USAGE_ERROR")

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret input must not escape")

    monkeypatch.setattr(measure, "assemble_read", broken)
    assert main(_args()) == ERROR_EXIT
    _error(capsys, "INTERNAL_ERROR")


def test_human_escapes_identity_and_success_does_not_access_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy = _load("policy.json")
    policy["subject"]["label"] = "label\n\x1b[31m"
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
    assert "sources:" in out.out and "calibration:" in out.out


@pytest.mark.parametrize("missing", ["eprocess", "calibration"])
def test_instrument_quorum_cannot_hide_a_missing_method(
    missing: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observation, policy = _load("read-input.json"), _load("policy.json")
    if missing == "calibration":
        observation["payload"]["readings"][1]["rows"][0]["score"] = None
    else:
        policy["inputs"]["eprocess"]["metric_id"] = "absent-metric"
    obs, pol = tmp_path / "obs.json", tmp_path / "pol.json"
    obs.write_text(json.dumps(observation))
    pol.write_text(json.dumps(policy))
    assert main(_args(obs, pol)) == 0
    output = capsys.readouterr()
    assert output.err == ""
    read = json.loads(output.out)
    assert read["verdict"] == "not_measured" and read["score"] is None
    assert read["availability"]["state"] == "full" and read["escalate"] is True
    assert "signal_not_measured" in read["reason_codes"]
    assert "below_quorum" not in read["reason_codes"]
    if missing == "eprocess":
        assert read["jury"]["e_value"] is None
        assert read["calibration"]["ece"]["availability"] == "measured"
    else:
        assert read["eprocess"]["availability"] == "measured"


def test_case_limit_rejects_before_statistics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from underwrite.measurement import eprocess
    from underwrite.measurement.read_input import MAX_CASES

    observation = _load("read-input.json")
    row = observation["payload"]["readings"][1]["rows"][0]
    observation["payload"]["availability"] = []
    observation["payload"]["readings"][1]["rows"] = [
        dict(row, case_id=f"case-{index}", sequence=index) for index in range(MAX_CASES + 1)
    ]
    path = tmp_path / "obs.json"
    path.write_text(json.dumps(observation))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("oversize population reached statistics")

    monkeypatch.setattr(eprocess, "run_stream", forbidden)
    assert main(_args(observation=path)) == ERROR_EXIT
    _error(capsys, "INPUT_LIMIT_EXCEEDED")


def test_unrecognized_pure_error_is_not_echoed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        error = measure.ReadInputError("INVALID_POLICY", "NO_TRIALS", "/policy")
        error.reason = "secret"
        raise error

    monkeypatch.setattr(measure, "assemble_read", broken)
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

"""``underwrite accept``: goldens rebuilt from real ``measure`` reads, every decision exits 0,
every ``acceptance_error.v1`` code exits 2 with a schema-valid envelope and no stdout."""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol, cast, get_args

import pytest
from _repo_paths import repo_root
from jsonschema import Draft202012Validator
from underwrite_core.canonical import canonical_bytes, content_digest

from underwrite.acceptance import decision as dc
from underwrite.acceptance.claims import parse_claims
from underwrite.cli import accept, local_json
from underwrite.cli.local_json import LocalJsonError
from underwrite.cli.main import main
from underwrite.cli.measure import measurement_validators
from underwrite.instrument.ingest.application import NEXT_ACTIONS
from underwrite.instrument.ingest.schema import SchemaConfigurationError

ROOT = repo_root(Path(__file__).resolve())
GOLDEN = ROOT / "fixtures/examples/acceptance"
MEASUREMENT = ROOT / "fixtures/examples/measurement"
ERROR_EXIT = 2
PASSING = ("read.json", "read-latency.json")
NOT_MEASURED = ("read.json", "read-not-measured.json")


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...

    def is_valid(self, instance: object) -> bool: ...


def schema(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "contracts" / f"{name}.schema.json").read_bytes())


def validator(name: str) -> Validator:
    return cast(Validator, Draft202012Validator(schema(name)))


def load(name: str) -> dict[str, Any]:
    return json.loads((GOLDEN / name).read_bytes())


def args(
    candidate: Path | None = None,
    claims: Path | None = None,
    reads: tuple[Path | str, ...] = PASSING,
    *extra: str,
) -> list[str]:
    argv = ["accept", "--candidate", str(candidate or GOLDEN / "candidate.json")]
    argv += ["--claims", str(claims or GOLDEN / "claims.json")]
    for read in reads:
        argv += ["--read", str(GOLDEN / read if isinstance(read, str) else read)]
    return [*argv, "--json", *extra]


def write(tmp_path: Path, name: str, document: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def decision(capsys: pytest.CaptureFixture[str], argv: list[str]) -> dict[str, Any]:
    assert main(argv) == 0
    output = capsys.readouterr()
    assert output.err == ""
    produced = json.loads(output.out)
    validator("acceptance_decision.v1").validate(produced)
    return produced


def error(capsys: pytest.CaptureFixture[str], code: str) -> dict[str, Any]:
    output = capsys.readouterr()
    assert output.out == ""
    payload = json.loads(output.err)
    validator("acceptance_error.v1").validate(payload)
    assert (payload["code"], payload["exit_code"]) == (code, ERROR_EXIT)
    assert "secret" not in output.err
    return payload


def sealed_candidate(claims: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {**load("candidate.json"), "claims_digest": parse_claims(claims).digest, **changes}


# --- goldens -----------------------------------------------------------------------------

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]


def assert_measurement_golden(actual: dict[str, Json], expected: dict[str, Json]) -> None:
    """Standalone behavior and boundary checks."""
    actual, expected = deepcopy(actual), deepcopy(expected)
    actual_signals, expected_signals = actual["signals"], expected["signals"]
    assert isinstance(actual_signals, list) and isinstance(expected_signals, list)
    sections = [
        (actual["eprocess"], expected["eprocess"], ("e_lower", "e_upper", "e_value")),
        (actual["jury"], expected["jury"], ("e_value",)),
        (actual_signals[0], expected_signals[0], ("e_value",)),
    ]
    for produced, golden, fields in sections:
        if golden is None:
            continue
        assert isinstance(produced, dict) and isinstance(golden, dict)
        if golden["availability"] == "measured":
            for field in fields:
                value, reference = produced.pop(field), golden.pop(field)
                assert type(value) is float and type(reference) is float
                assert math.isfinite(value) and math.isfinite(reference)
                assert math.isclose(value, reference, rel_tol=1e-9, abs_tol=0)
    # JSON encoding distinguishes numeric types (including int versus float),
    # while sorting only removes irrelevant object member order.
    assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)


def test_measurement_golden_accepts_independently_observed_linux_values() -> None:
    expected: dict[str, Json] = load("read.json")
    actual = deepcopy(expected)
    eprocess, jury, signals = actual["eprocess"], actual["jury"], actual["signals"]
    assert isinstance(eprocess, dict) and isinstance(jury, dict) and isinstance(signals, list)
    signal = signals[0]
    assert isinstance(signal, dict)
    eprocess.update(
        e_lower=0.41666666666666635, e_upper=0.9166666666666667, e_value=0.6666666666666665
    )
    jury["e_value"] = signal["e_value"] = 0.6666666666666665
    assert_measurement_golden(actual, expected)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("eprocess", "e_value", 0.67),
        ("eprocess", "e_value", math.inf),
        ("eprocess", "e_value", math.nan),
        ("eprocess", "e_value", 1),
        ("availability", "state", "partial"),
        ("sources", "policy", "sha256:" + "0" * 64),
        ("", "score", math.nextafter(1.0, 2.0)),
        ("", "score", 1.0),
    ],
)
def test_measurement_golden_rejects_drift_outside_the_numerical_contract(
    section: str, field: str, value: Json
) -> None:
    expected: dict[str, Json] = load("read.json")
    actual = deepcopy(expected)
    target = actual[section] if section else actual
    assert isinstance(target, dict)
    target[field] = value
    with pytest.raises(AssertionError):
        assert_measurement_golden(actual, expected)


@pytest.mark.parametrize("name", ["quality", "latency"])
def test_derived_policies_change_only_the_subject(name: str) -> None:
    source = json.loads((MEASUREMENT / "policy.json").read_bytes())
    derived = load(f"policy-{name}.json")
    assert derived == {**source, "subject": {"conditions": {}, "subject_id": f"suite.{name}"}}


@pytest.mark.parametrize(
    ("read", "observation", "policy"),
    [
        ("read.json", "read-input.json", "quality"),
        ("read-latency.json", "read-input.json", "latency"),
        ("read-not-measured.json", "observation-below-quorum.json", "latency"),
    ],
)
def test_golden_reads_are_what_measure_writes(
    read: str, observation: str, policy: str, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["measure", "--observation", str(MEASUREMENT / observation)]
    assert main([*argv, "--policy", str(GOLDEN / f"policy-{policy}.json"), "--json"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    produced: dict[str, Json] = json.loads(output.out)
    assert output.out.encode() == canonical_bytes(produced) + b"\n"
    assert_measurement_golden(produced, load(read))


def test_golden_claims_bind_their_policies_and_the_candidate_seals_them() -> None:
    claims = load("claims.json")
    for basis in claims["required_claims"].values():
        for claim in basis:
            policy = load(f"policy-{claim['claim_id']}.json")
            assert claim["subject_id"] == policy["subject"]["subject_id"]
            assert claim["policy_digest"] == content_digest(policy)
    assert load("candidate.json")["claims_digest"] == parse_claims(claims).digest


@pytest.mark.parametrize(
    ("reads", "golden", "classification", "next_state", "reasons"),
    [
        (PASSING, "accept.json", "screened", "screened", ["stage_screened"]),
        (
            NOT_MEASURED,
            "accept-not-measured.json",
            "indeterminate",
            "proposed",
            ["claim_not_measured"],
        ),
    ],
)
def test_goldens_are_the_command_output(
    reads: tuple[str, ...],
    golden: str,
    classification: str,
    next_state: str,
    reasons: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = [path.read_bytes() for path in GOLDEN.glob("*.json")]
    assert main(args(reads=reads)) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out == (GOLDEN / "expected" / golden).read_text(encoding="utf-8")
    produced = json.loads(output.out)
    validator("acceptance_decision.v1").validate(produced)
    assert (produced["classification"], produced["next_state"]) == (classification, next_state)
    assert produced["reason_codes"] == reasons
    assert produced["inputs"]["read_digests"] == sorted(
        content_digest(load(read)) for read in reads
    )
    assert before == [path.read_bytes() for path in GOLDEN.glob("*.json")]


def test_not_measured_golden_explains_the_gap_per_claim() -> None:
    claims = {c["claim_id"]: c["read"] for c in load("expected/accept-not-measured.json")["claims"]}
    assert claims["quality"]["verdict"] == "pass"
    gap = claims["latency"]
    assert gap["verdict"] == "not_measured" and gap["escalate"] is True
    assert gap["reason_codes"] == load("read-not-measured.json")["reason_codes"]


def test_human_mode_explains_each_claim_and_claims_no_authority(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(args(reads=NOT_MEASURED)[:-1]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    lines = output.out.splitlines()
    assert "not approval" in lines[0] and "merge_authorized is false" in lines[0]
    assert "classification: indeterminate (proposed -> proposed)" in lines
    assert any(line.startswith("claim latency (validity, suite.latency): ") for line in lines)
    assert any('"not_measured"' in line and "below_quorum" in line for line in lines)
    assert 'selection: {"case_count": 3, "confirm_fraction": 0.2}' in lines


def test_missing_claim_read_is_named_in_human_mode(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(args(reads=("read.json",))[:-1]) == 0
    assert "claim latency (validity, suite.latency): no read supplied" in capsys.readouterr().out


def test_a_rejected_decision_exits_zero(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    candidate = write(
        tmp_path,
        "candidate.json",
        {**load("candidate.json"), "hard_failures": ["write_outside_harness"]},
    )
    produced = decision(capsys, args(candidate))
    assert (produced["classification"], produced["next_state"]) == ("rejected", "rejected")
    assert produced["merge_authorized"] is False and produced["human_review"] == "required"


def test_accept_is_listed_in_the_top_level_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    assert "accept" in capsys.readouterr().out


# --- the read-count bound ------------------------------------------------------------------


def test_read_bound_is_the_claims_contract_bound() -> None:
    claims = schema("declared_claims.v1")
    assert accept.MAX_READS == claims["$defs"]["basis_claims"]["maxItems"]
    assert accept.MAX_READS == schema("acceptance_decision.v1")["properties"]["claims"]["maxItems"]


def test_the_bound_itself_is_admitted(capsys: pytest.CaptureFixture[str]) -> None:
    reads = ("read.json",) * accept.MAX_READS
    assert main(args(reads=reads)) == ERROR_EXIT  # reaches decide: one claim, many reads
    assert error(capsys, "DUPLICATE_CLAIM")["location"] == "/reads/1/subject/subject_id"


# --- bounded validation cost (tests/gates/test_unique_items_bounded.py) -------------------


def unsortable_unique_checks(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Lengths of the arrays jsonschema's uniqueItems could not sort: its pairwise path."""
    seen: list[int] = []
    original = Draft202012Validator.VALIDATORS["uniqueItems"]

    def spy(validator: object, unique: object, instance: object, schema: object) -> object:
        if isinstance(instance, list):
            try:
                sorted(cast(list[Any], instance))
            except TypeError:
                seen.append(len(cast(list[Any], instance)))
        return original(validator, unique, instance, schema)

    monkeypatch.setitem(Draft202012Validator.VALIDATORS, "uniqueItems", spy)
    return seen


MIXED = [index if index % 2 else f"c{index}" for index in range(20_000)]


def test_mixed_selection_is_refused_before_the_uniqueness_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = unsortable_unique_checks(monkeypatch)
    claims = load("claims.json")
    claims["selection"]["case_ids"] = MIXED
    assert main(args(claims=write(tmp_path, "claims.json", claims))) == ERROR_EXIT
    payload = error(capsys, "INVALID_CLAIMS")
    assert (payload["reason"], payload["location"]) == ("SCHEMA_VALIDATION_FAILED", "/claims")
    assert seen == []


def _scalar_arrays(node: object, path: tuple[str | int, ...] = ()) -> list[tuple[str | int, ...]]:
    if isinstance(node, dict):
        items = cast(dict[str, object], node).items()
        return [found for key, value in items for found in _scalar_arrays(value, (*path, key))]
    if isinstance(node, list):
        values = cast(list[object], node)
        nested = [
            found for i, value in enumerate(values) for found in _scalar_arrays(value, (*path, i))
        ]
        return nested + ([path] if all(not isinstance(v, (dict, list)) for v in values) else [])
    return []


@pytest.mark.parametrize("path", _scalar_arrays(load("read.json")), ids=str)
def test_mixed_read_array_is_refused_before_the_uniqueness_check(
    path: tuple[str | int, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen = unsortable_unique_checks(monkeypatch)
    read = load("read.json")
    parent: Any = read
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = MIXED
    assert main(args(reads=(write(tmp_path, "read.json", read),))) == ERROR_EXIT
    payload = error(capsys, "INVALID_READ")
    assert (payload["reason"], payload["location"]) == ("SCHEMA_VALIDATION_FAILED", "/reads/0")
    assert seen == []


# --- refusals, one per acceptance_error.v1 code ---------------------------------------------

Scenario = Callable[[Path, pytest.MonkeyPatch], list[str]]


def _read_with(tmp_path: Path, **changes: Any) -> Path:
    return write(tmp_path, "secret-read.json", {**load("read.json"), **changes})


def _authority(tmp_path: Path, _: pytest.MonkeyPatch) -> list[str]:
    claims = load("claims.json")
    claims["selection"]["nested"] = [{"review_action": "secret"}]
    return args(claims=write(tmp_path, "claims.json", claims))


def _invalid_candidate(tmp_path: Path, _: pytest.MonkeyPatch) -> list[str]:
    return args(write(tmp_path, "candidate.json", {**load("candidate.json"), "kind": "secret"}))


def _invalid_claims(tmp_path: Path, _: pytest.MonkeyPatch) -> list[str]:
    claims = load("claims.json")
    del claims["selection"]
    return args(claims=write(tmp_path, "claims.json", claims))


def _native_decision(tmp_path: Path, _: pytest.MonkeyPatch) -> list[str]:
    native = write(tmp_path, "foreign.json", {"schema": "foreign.decision.v1"})
    return args(reads=("read.json", native))


def _leaked_selection(tmp_path: Path, _: pytest.MonkeyPatch) -> list[str]:
    claims = load("claims.json")
    claims["selection"]["confirm_fraction"] = 0.999
    candidate = write(tmp_path, "candidate.json", sealed_candidate(claims))
    return args(candidate, write(tmp_path, "claims.json", claims))


def _candidate_mismatch(tmp_path: Path, _: pytest.MonkeyPatch) -> list[str]:
    changed = {**load("candidate.json"), "candidate_id": "prompt-v3"}
    return args(write(tmp_path, "candidate.json", changed))


def _claims_digest(tmp_path: Path, _: pytest.MonkeyPatch) -> list[str]:
    changed = {**load("candidate.json"), "claims_digest": "sha256:" + "0" * 64}
    return args(write(tmp_path, "candidate.json", changed))


def _unexpected(tmp_path: Path, _: pytest.MonkeyPatch) -> list[str]:
    subject: dict[str, object] = {"subject_id": "suite.secret", "conditions": {}}
    return args(reads=(_read_with(tmp_path, subject=subject),))


def _duplicate(tmp_path: Path, _: pytest.MonkeyPatch) -> list[str]:
    return args(reads=("read.json", "read.json"))


def _policy_mismatch(tmp_path: Path, _: pytest.MonkeyPatch) -> list[str]:
    sources = {**load("read.json")["sources"], "policy": "sha256:" + "0" * 64}
    return args(reads=(_read_with(tmp_path, sources=sources),))


def _conditioned(tmp_path: Path, _: pytest.MonkeyPatch) -> list[str]:
    subject = {"subject_id": "suite.quality", "conditions": {"model": "secret"}}
    return args(reads=(_read_with(tmp_path, subject=subject),))


def _inconsistent(tmp_path: Path, _: pytest.MonkeyPatch) -> list[str]:
    # Schema-valid (the loss reason is only constrained by the producer), but a full panel
    # never reports a lost required instrument.
    return args(reads=(_read_with(tmp_path, reason_codes=["required_instrument_missing"]),))


def _terminal(tmp_path: Path, _: pytest.MonkeyPatch) -> list[str]:
    changed = {**load("candidate.json"), "current_state": "confirmed"}
    return args(write(tmp_path, "candidate.json", changed))


def _count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    def unreachable(*_: object) -> None:
        pytest.fail("a file was read before the read count was checked")

    monkeypatch.setattr(local_json, "read_object", unreachable)
    missing = tuple(tmp_path / f"secret-{index}.json" for index in range(accept.MAX_READS + 1))
    return args(tmp_path / "secret.json", tmp_path / "secret.json", missing)


def _input_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(accept, "CLAIMS_MAX_BYTES", 1)
    return args()


def _output_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(accept, "OUTPUT_MAX_BYTES", 1)
    return args()


def _source(tmp_path: Path, _: pytest.MonkeyPatch) -> list[str]:
    return args(reads=("read.json", tmp_path / "secret.json"))


def _schema_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    def broken(name: str) -> None:
        raise SchemaConfigurationError("secret")

    monkeypatch.setattr(accept, "packaged_validator", broken)
    return args()


def _usage(tmp_path: Path, _: pytest.MonkeyPatch) -> list[str]:
    return args()[:-1] + ["--secret"]


def _platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    def unsupported(*_: object) -> None:
        raise LocalJsonError("UNSUPPORTED_PLATFORM")

    monkeypatch.setattr(local_json, "read_object", unsupported)
    return args()


def _internal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    real = dc.decide

    def authorizing(*inputs: Any) -> dict[str, Any]:
        return {**real(*inputs), "merge_authorized": True}

    monkeypatch.setattr(dc, "decide", authorizing)
    return args()


# code -> (scenario, reason, location, next_action); None = not asserted beyond the schema.
REFUSALS: dict[str, tuple[Scenario, str | None, str, str]] = {
    "AUTHORITY_FIELD_REFUSED": (_authority, None, "/claims", "CORRECT_CLAIMS"),
    "INVALID_CANDIDATE": (
        _invalid_candidate,
        "SCHEMA_VALIDATION_FAILED",
        "/candidate",
        "CORRECT_CANDIDATE",
    ),
    "INVALID_CLAIMS": (_invalid_claims, "SCHEMA_VALIDATION_FAILED", "/claims", "CORRECT_CLAIMS"),
    "INVALID_READ": (_native_decision, "SCHEMA_VALIDATION_FAILED", "/reads/1", "CORRECT_READ"),
    "SELECTION_LEAKS_CONFIRM": (
        _leaked_selection,
        None,
        "/claims/selection/case_ids",
        "CORRECT_CLAIMS",
    ),
    "CANDIDATE_MISMATCH": (
        _candidate_mismatch,
        None,
        "/candidate/candidate_id",
        "CORRECT_CANDIDATE",
    ),
    "CLAIMS_DIGEST_MISMATCH": (
        _claims_digest,
        None,
        "/candidate/claims_digest",
        "CORRECT_CANDIDATE",
    ),
    "UNEXPECTED_CLAIM": (_unexpected, None, "/reads/0/subject/subject_id", "CORRECT_READ"),
    "DUPLICATE_CLAIM": (_duplicate, None, "/reads/1/subject/subject_id", "CORRECT_READ"),
    "READ_POLICY_MISMATCH": (_policy_mismatch, None, "/reads/0/sources/policy", "CORRECT_READ"),
    "READ_NOT_AGGREGATE": (_conditioned, None, "/reads/0/subject/conditions", "CORRECT_READ"),
    "READ_AVAILABILITY_INCONSISTENT": (
        _inconsistent,
        None,
        "/reads/0/availability",
        "CORRECT_READ",
    ),
    "INVALID_STATE_FOR_EVALUATION": (
        _terminal,
        None,
        "/candidate/current_state",
        "CORRECT_CANDIDATE",
    ),
    "READ_COUNT_EXCEEDED": (_count, None, "/reads", "REDUCE_INPUT_SIZE"),
    "INPUT_LIMIT_EXCEEDED": (_input_limit, "BYTE_LIMIT_EXCEEDED", "/claims", "REDUCE_INPUT_SIZE"),
    "OUTPUT_LIMIT_EXCEEDED": (_output_limit, None, "", "REDUCE_INPUT_SIZE"),
    "SOURCE_READ_FAILED": (
        _source,
        "SOURCE_READ_FAILED",
        "/reads/1",
        NEXT_ACTIONS["SOURCE_READ_FAILED"],
    ),
    "SCHEMA_CONFIGURATION_ERROR": (
        _schema_configuration,
        None,
        "",
        NEXT_ACTIONS["SCHEMA_CONFIGURATION_ERROR"],
    ),
    "USAGE_ERROR": (_usage, "INVALID_ARGUMENTS_SEE_HELP", "", NEXT_ACTIONS["USAGE_ERROR"]),
    "UNSUPPORTED_PLATFORM": (_platform, None, "", NEXT_ACTIONS["UNSUPPORTED_PLATFORM"]),
    "INTERNAL_ERROR": (_internal, "SCHEMA_VALIDATION_FAILED", "", NEXT_ACTIONS["INTERNAL_ERROR"]),
}


def test_every_contract_code_has_a_cli_scenario() -> None:
    assert list(REFUSALS) == schema("acceptance_error.v1")["properties"]["code"]["enum"]


@pytest.mark.parametrize("code", list(REFUSALS))
def test_each_refusal_exits_two_with_its_envelope(
    code: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario, reason, location, action = REFUSALS[code]
    assert main(scenario(tmp_path, monkeypatch)) == ERROR_EXIT
    payload = error(capsys, code)
    assert (payload["location"], payload["next_action"]) == (location, action)
    if reason is not None:
        assert payload["reason"] == reason


def test_claims_digest_refusal_shows_the_computed_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_claims_digest(tmp_path, monkeypatch)) == ERROR_EXIT
    payload = error(capsys, "CLAIMS_DIGEST_MISMATCH")
    assert payload["claims_digest"] == load("candidate.json")["claims_digest"]


def test_terminal_state_refusal_shows_the_attempted_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_terminal(tmp_path, monkeypatch)) == ERROR_EXIT
    context = error(capsys, "INVALID_STATE_FOR_EVALUATION")["context"]
    assert context == {
        "current_state": "confirmed",
        "attempted_classification": "screened",
        "reason_codes": ["stage_screened"],
    }


@pytest.mark.parametrize("key", sorted(dc.AUTHORITY_KEYS))
@pytest.mark.parametrize("target", ["candidate", "claims"])
def test_authority_fields_are_refused_before_the_schema(
    key: str, target: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document = {"secret": [{"deeper": {key: False}}]}  # schema-invalid in every other way
    path = write(tmp_path, f"{target}.json", document)
    argv = args(path) if target == "candidate" else args(claims=path)
    assert main(argv) == ERROR_EXIT
    payload = error(capsys, "AUTHORITY_FIELD_REFUSED")
    assert payload["location"] == f"/{target}"


def test_an_observation_passed_as_a_read_is_an_invalid_read(
    capsys: pytest.CaptureFixture[str],
) -> None:

    assert main(args(reads=(MEASUREMENT / "read-input.json",))) == ERROR_EXIT
    assert error(capsys, "INVALID_READ")["location"] == "/reads/0"


def test_an_invalid_read_fails_before_a_later_read_is_decoded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "secret.json"
    assert main(args(reads=(MEASUREMENT / "read-input.json", missing))) == ERROR_EXIT
    payload = error(capsys, "INVALID_READ")
    assert (payload["reason"], payload["location"]) == ("SCHEMA_VALIDATION_FAILED", "/reads/0")


@pytest.mark.parametrize(
    ("scenario", "code"),
    [
        (_invalid_candidate, "INVALID_CANDIDATE"),
        (_invalid_claims, "INVALID_CLAIMS"),
        (_authority, "AUTHORITY_FIELD_REFUSED"),
    ],
)
def test_candidate_and_claims_refusals_precede_opening_any_read(
    scenario: Callable[[Path, pytest.MonkeyPatch], list[str]],
    code: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = scenario(tmp_path, monkeypatch)
    missing = str(tmp_path / "secret-missing-read.json")
    flags = ["", *argv[:-1]]
    argv = [missing if flag == "--read" else item for flag, item in zip(flags, argv, strict=True)]
    assert missing in argv
    assert main(argv) == ERROR_EXIT
    error(capsys, code)


def test_read_with_another_seed_is_an_invalid_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed = schema("statistical_seed.v1")["const"]
    assert load("read.json")["seed"] == seed
    assert main(args(reads=(_read_with(tmp_path, seed=seed + 1),))) == ERROR_EXIT
    assert error(capsys, "INVALID_READ")["reason"] == "SCHEMA_VALIDATION_FAILED"


def test_inconsistent_read_is_schema_valid_so_decide_refuses_it() -> None:
    read = {**load("read.json"), "reason_codes": ["required_instrument_missing"]}
    measurement_validators()[0].validate(read)
    assert not dc.consistent_availability(read)


def test_a_case_id_the_split_would_refuse_is_refused_by_the_reader_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    claims = load("claims.json")
    decomposed = unicodedata.normalize("NFD", "case-é")  # schema-valid, not NFC
    claims["selection"]["case_ids"] = [decomposed]

    def unreachable(*_: object, **__: object) -> None:
        pytest.fail("the split kernel saw a case id canonical decoding refuses")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(accept, "assign_split", unreachable)
        assert main(args(claims=write(tmp_path, "claims.json", claims))) == ERROR_EXIT
    payload = error(capsys, "INVALID_CLAIMS")
    assert (payload["reason"], payload["location"]) == ("NONCANONICAL_INPUT", "/claims")


DEEP = b"[" * (accept.DOCUMENT_MAX_DEPTH + 1) + b"]" * (accept.DOCUMENT_MAX_DEPTH + 1)


@pytest.mark.parametrize(
    ("raw", "code", "reason"),
    [
        (b'{"secret": ', "INVALID_CANDIDATE", "INVALID_JSON_INPUT"),
        (b'{"secret": 1, "\\u0073ecret": 2}', "INVALID_CANDIDATE", "INVALID_JSON_INPUT"),
        (b'["secret"]', "INVALID_CANDIDATE", "OBJECT_REQUIRED"),
        (b'{"a": 9007199254740993}', "INVALID_CANDIDATE", "NONCANONICAL_INPUT"),
        (DEEP, "INPUT_LIMIT_EXCEEDED", "DEPTH_LIMIT_EXCEEDED"),
    ],
)
def test_file_decode_failures_keep_measure_s_reasons(
    raw: bytes, code: str, reason: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "candidate.json"
    path.write_bytes(raw)
    assert main(args(path)) == ERROR_EXIT
    payload = error(capsys, code)
    assert (payload["reason"], payload["location"]) == (reason, "/candidate")


def test_directory_is_not_a_regular_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(args(claims=tmp_path)) == ERROR_EXIT
    payload = error(capsys, "SOURCE_READ_FAILED")
    assert (payload["reason"], payload["location"]) == ("SOURCE_NOT_REGULAR_FILE", "/claims")


def test_output_limit_includes_the_newline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(args()) == 0
    output = capsys.readouterr().out
    monkeypatch.setattr(accept, "OUTPUT_MAX_BYTES", len(output.encode()))
    assert main(args()) == 0
    assert capsys.readouterr().out == output
    monkeypatch.setattr(accept, "OUTPUT_MAX_BYTES", len(output.encode()) - 1)
    assert main(args()) == ERROR_EXIT
    error(capsys, "OUTPUT_LIMIT_EXCEEDED")


def test_unexpected_failure_is_an_internal_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing(*_: object, **__: object) -> None:
        raise RuntimeError("secret")

    monkeypatch.setattr(accept, "assign_split", failing)
    assert main(args()) == ERROR_EXIT
    assert error(capsys, "INTERNAL_ERROR")["reason"] == "INTERNAL_ERROR"


# --- next actions ------------------------------------------------------------------------


def test_every_code_has_exactly_one_next_action_owner() -> None:
    codes = schema("acceptance_error.v1")["properties"]["code"]["enum"]
    refusals = set(get_args(dc.RefusalCode))
    for code in codes:
        assert (code in accept._ACTIONS) != (code in refusals), code  # pyright: ignore[reportPrivateUsage]


def test_next_actions_are_the_contract_s_and_the_shared_table_s() -> None:
    table = accept._ACTIONS  # pyright: ignore[reportPrivateUsage]
    assert {code: table[code] for code in NEXT_ACTIONS} == NEXT_ACTIONS
    actions = schema("acceptance_error.v1")["properties"]["next_action"]["enum"]
    assert set(actions) == set(table.values()) | set(dc.REFUSAL_ACTIONS.values())

"""Standalone behavior and boundary checks."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import CanonicalizationError, canonical_bytes

jsonschema = pytest.importorskip(
    "jsonschema", reason="contract validation uses the shell dependency; the tests job runs this"
)

ROOT = repo_root(Path(__file__).resolve())
READ = ROOT / "contracts/read.v1.schema.json"
SEED_OWNER = ROOT / "contracts/statistical_seed.v1.schema.json"
DIGEST = "sha256:" + "ab" * 32
POLICY_DIGEST = "sha256:" + "cd" * 32


def _json(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))


def _seed() -> int:
    return int(_json(SEED_OWNER)["const"])


def _resolved_schema() -> dict[str, Any]:
    """read.v1 with its seed `$ref` replaced by the owner's constraints (no resolver needed)."""
    schema = _json(READ)
    owner = {k: v for k, v in _json(SEED_OWNER).items() if not k.startswith("$")}
    properties = dict(cast("dict[str, Any]", schema["properties"]))
    properties["seed"] = owner
    return {**schema, "properties": properties}


def _validate(read: dict[str, Any]) -> None:
    jsonschema.validate(read, _resolved_schema())


def _measured_signal(
    method: str = "beta_mixture_eprocess", instrument: str = "deepeval"
) -> dict[str, Any]:
    return {
        "method": method,
        "instrument": instrument,
        "availability": "measured",
        "verdict": "pass",
        "score": 0.96,
        "confidence": 1.0,
        "e_value": 28.44,
        "null_id": "binary:p=0.5",
        "reason_codes": [],
    }


def _unmeasured_signal() -> dict[str, Any]:
    return {
        "method": "krippendorff_alpha",
        "instrument": "judge",
        "availability": "not_measured",
        "verdict": None,
        "score": None,
        "confidence": 0.0,
        "e_value": None,
        "null_id": None,
        "reason_codes": ["signal_not_measured"],
    }


def _eprocess() -> dict[str, Any]:
    return {
        "method": "beta_mixture_eprocess",
        "availability": "measured",
        "e_value": 28.44,
        "e_upper": 56.77,
        "e_lower": 0.11,
        "pass_e": 20.0,
        "stop_reason": "threshold",
        "stopped_sequence": 8,
        "observations_used": 8,
        "observations_total": 10,
        "trials_used": 8,
        "cost_used": 0.0,
        "max_observations": None,
        "max_cost": None,
        "null_id": "binary:p=0.5",
        "reason_codes": [],
    }


def _measure(value: float | None, **extra: Any) -> dict[str, Any]:
    measured = value is not None
    return {
        "availability": "measured" if measured else "not_measured",
        "value": value,
        "n": 40 if measured else 3,
        "reason_codes": [] if measured else ["bin_below_min_n"],
        **extra,
    }


def _availability(state: str) -> dict[str, Any]:
    required = ["deepeval", "judge", "retrieval"]
    missing = {"full": [], "partial": ["retrieval"], "below_quorum": ["judge", "retrieval"]}[state]
    return {
        "required_instruments": required,
        "quorum": 2,
        "available": [name for name in required if name not in missing],
        "missing": missing,
        "state": state,
        "triggered": state != "full",
    }


def _read(verdict: str) -> dict[str, Any]:
    """A representative read for each of the four verdicts, built from the contract's own facts."""
    measured = verdict != "not_measured"
    state = {"pass": "full", "warn": "partial", "fail": "full", "not_measured": "below_quorum"}[
        verdict
    ]
    return {
        "schema": "read.v1",
        "subject": {
            "subject_id": "agent:demo:1.4.0",
            "label": "demo agent",
            "conditions": {"locale": "ko"},
        },
        "verdict": verdict,
        "score": {"pass": 0.96, "warn": 0.96, "fail": 0.12, "not_measured": None}[verdict],
        "escalate": state != "full",
        "signals": [_measured_signal(), _unmeasured_signal()]
        if measured
        else [_unmeasured_signal()],
        "eprocess": _eprocess() if measured else None,
        "jury": {
            "combine": "mean",
            "availability": "measured" if measured else "not_measured",
            "e_value": 28.44 if measured else None,
            "disagreement": 0.0 if measured else None,
            "members": 1 if measured else 0,
            "independence": None,
            "reason_codes": [] if measured else ["below_quorum"],
        },
        "calibration": {
            "brier": _measure(0.04 if measured else None),
            "ece": _measure(None, bins=10, min_bin_n=25),
            "pass_k": _measure(0.81 if measured else None, k=3),
        },
        "availability": _availability(state),
        "strata": [
            {
                "key": {"locale": "ko"},
                "n": 40,
                "availability": "measured",
                "verdict": "pass",
                "score": 0.95,
                "reason_codes": [],
            }
            if measured
            else {
                "key": {"locale": "ko"},
                "n": 0,
                "availability": "not_measured",
                "verdict": None,
                "score": None,
                "reason_codes": ["no_observations"],
            },
            {
                "key": {"locale": "en"},
                "n": 2,
                "availability": "not_measured",
                "verdict": None,
                "score": None,
                "reason_codes": ["cell_below_min_n"],
            },
        ],
        "seed": _seed(),
        "sources": {"observations": [DIGEST], "policy": POLICY_DIGEST},
        "reason_codes": [] if measured else ["below_quorum"],
    }


def _with(read: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """Copy `read` with the dotted `path` set to `value` (`.` walks dicts, digits index lists)."""
    out = copy.deepcopy(read)
    node: Any = out
    parts = path.split(".")
    for part in parts[:-1]:
        node = cast("Any", node[int(part)] if isinstance(node, list) else node[part])
    last = parts[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return out


def _without(read: dict[str, Any], path: str) -> dict[str, Any]:
    out = copy.deepcopy(read)
    node: Any = out
    parts = path.split(".")
    for part in parts[:-1]:
        node = cast("Any", node[int(part)] if isinstance(node, list) else node[part])
    del node[parts[-1]]
    return out


# --- the contract itself ---


def test_schema_is_valid_and_declares_no_default() -> None:
    schema = _json(READ)
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    # That `properties.seed` is exactly the owner's `$ref` is tests/gates/
    # test_statistical_seed_contract.py::test_present_future_read_contract_references_seed_owner.
    assert "default" not in json.dumps(schema)


def test_not_measured_is_never_a_sibling_of_warn_in_any_enum() -> None:
    """Standalone behavior and boundary checks."""

    def enums(node: object) -> list[list[str]]:
        found: list[list[str]] = []
        if isinstance(node, dict):
            typed = cast("dict[str, Any]", node)
            if isinstance(typed.get("enum"), list):
                found.append([str(v) for v in cast("list[Any]", typed["enum"])])
            for value in typed.values():
                found.extend(enums(value))
        elif isinstance(node, list):
            for item in cast("list[Any]", node):
                found.extend(enums(item))
        return found

    text = READ.read_text(encoding="utf-8")
    assert '"not_measured"' in text and '"warn"' in text
    assert all(not ({"warn", "not_measured"} <= set(values)) for values in enums(_json(READ)))


# --- four honest shapes ---


@pytest.mark.parametrize("verdict", ["pass", "warn", "fail", "not_measured"])
def test_each_honest_read_shape_validates(verdict: str) -> None:
    _validate(_read(verdict))


def test_availability_is_always_present_and_triggered_false_is_a_recorded_fact() -> None:
    read = _read("pass")
    assert read["availability"]["triggered"] is False
    _validate(read)
    with pytest.raises(jsonschema.ValidationError):
        _validate(_without(read, "availability"))
    with pytest.raises(jsonschema.ValidationError):
        _validate(_without(read, "availability.triggered"))


def test_seed_is_read_from_the_owner_and_any_other_value_is_rejected() -> None:
    read = _read("pass")
    assert read["seed"] == _json(SEED_OWNER)["const"]
    for wrong in (_seed() + 1, 13, str(_seed()), None, float(_seed()) + 0.5):
        with pytest.raises(jsonschema.ValidationError):
            _validate(_with(read, "seed", wrong))


# --- dishonest shapes ---


@pytest.mark.parametrize(
    ("verdict", "path", "value"),
    [
        ("pass", "verdict", "promote"),
        ("pass", "verdict", "approve"),
        ("pass", "verdict", "not_measured"),
        ("not_measured", "score", 0),
        ("not_measured", "score", 0.0),
        ("not_measured", "verdict", "warn"),
        ("not_measured", "reason_codes", []),
        ("not_measured", "availability.triggered", False),
        ("not_measured", "availability.state", "partial"),
        ("not_measured", "availability", _availability("partial")),
        ("warn", "availability.triggered", False),
        ("warn", "escalate", False),
        ("warn", "verdict", "pass"),
        ("pass", "availability.missing", ["retrieval"]),
        ("pass", "availability.state", "below_quorum"),
        ("pass", "score", 1.5),
        ("pass", "score", None),
        ("pass", "signals.1.score", 0),
        ("pass", "signals.1.verdict", "fail"),
        ("pass", "signals.0.e_value", -1),
        ("pass", "signals.0.null_id", ""),
        ("pass", "eprocess.stop_reason", "absorbed"),
        ("pass", "eprocess.availability", "not_measured"),
        ("pass", "eprocess.pass_e", 0.5),
        ("pass", "jury.combine", "product"),
        ("pass", "jury.members", 0),
        ("pass", "calibration.ece.value", 0),
        ("pass", "calibration.ece.reason_codes", []),
        ("pass", "strata.1.score", 0),
        ("pass", "strata.1.verdict", "warn"),
        ("pass", "strata.0.key", {}),
        ("pass", "sources.observations", []),
        ("pass", "sources.observations", ["sha256:short"]),
        ("pass", "sources.policy", "cd" * 32),
        ("pass", "reason_codes", ["looks_fine"]),
        ("pass", "subject.subject_id", "agent demo"),
    ],
)
def test_a_dishonest_field_is_rejected(verdict: str, path: str, value: Any) -> None:
    with pytest.raises(jsonschema.ValidationError):
        _validate(_with(_read(verdict), path, value))


@pytest.mark.parametrize(
    "path",
    [
        "sources",
        "sources.policy",
        "signals.1.availability",
        "eprocess.e_lower",
        "jury.independence",
        "strata.0.availability",
        "reason_codes",
        "escalate",
        "seed",
    ],
)
def test_a_missing_required_field_is_rejected(path: str) -> None:
    with pytest.raises(jsonschema.ValidationError):
        _validate(_without(_read("pass"), path))


@pytest.mark.parametrize(
    "extra",
    [
        {"classification": "confirmed"},
        {"decision": "Approve"},
        {"adjudication": "adjudicated"},
        {"acceptance": {"state": "indeterminate"}},
    ],
)
def test_acceptance_and_adjudication_fields_do_not_belong_to_a_read(extra: dict[str, Any]) -> None:
    with pytest.raises(jsonschema.ValidationError):
        _validate({**_read("pass"), **extra})


def test_an_absorbed_stream_is_not_measured_with_its_reason_and_zero_wealth() -> None:
    read = _read("warn")
    absorbed = {
        **_eprocess(),
        "stop_reason": "absorbed",
        "availability": "not_measured",
        "e_value": 0,
        "e_upper": 0,
        "e_lower": 0,
        "reason_codes": ["wealth_absorbed"],
    }
    _validate(_with(read, "eprocess", absorbed))
    with pytest.raises(jsonschema.ValidationError):
        _validate(_with(read, "eprocess", {**absorbed, "reason_codes": []}))
    with pytest.raises(jsonschema.ValidationError):
        _validate(_with(read, "eprocess", {**absorbed, "e_value": 0.5}))
    with pytest.raises(jsonschema.ValidationError):  # the mixture of 0.5 and 0 is not 0
        _validate(_with(read, "eprocess", {**absorbed, "e_upper": 0.5}))


def test_a_partial_panel_caps_pass_to_warn_and_leaves_fail_alone() -> None:
    partial_fail = _with(_with(_read("warn"), "verdict", "fail"), "score", 0.12)
    _validate(partial_fail)
    with pytest.raises(jsonschema.ValidationError):
        _validate(_with(_read("warn"), "verdict", "pass"))


@pytest.mark.parametrize("state", ["full", "partial"])
@pytest.mark.parametrize("reason", ["signal_not_measured", "wealth_absorbed", "no_observations"])
def test_quorum_met_does_not_manufacture_a_measured_panel(state: str, reason: str) -> None:
    read = _read("not_measured")
    read["availability"] = _availability(state)
    read["reason_codes"] = [reason]
    _validate(read)
    for path, value in [
        ("score", 0),
        ("escalate", False),
        ("reason_codes", []),
        ("reason_codes", ["required_instrument_missing"]),
        ("reason_codes", ["below_quorum"]),
        ("reason_codes", [reason, "below_quorum"]),
    ]:
        with pytest.raises(jsonschema.ValidationError):
            _validate(_with(read, path, value))


@pytest.mark.parametrize(
    "changes",
    [
        {"escalate": False},
        {"reason_codes": ["no_observations"]},
        {"escalate": False, "reason_codes": ["no_observations"]},
    ],
    ids=["false-escalation", "alternative-cause", "both"],
)
def test_legacy_below_quorum_schema_acceptance_is_preserved(
    changes: dict[str, Any],
) -> None:
    """Standalone behavior and boundary checks."""
    read = _read("not_measured")
    read.update(changes)
    _validate(read)
    invalid: list[tuple[str, Any]] = [("score", 0), ("reason_codes", [])]
    for path, value in invalid:
        with pytest.raises(jsonschema.ValidationError):
            _validate(_with(read, path, value))


def test_a_product_fusion_must_state_its_independence_evidence() -> None:
    read = _read("pass")
    with pytest.raises(jsonschema.ValidationError):
        _validate(_with(read, "jury.combine", "product"))
    stated = _with(
        _with(read, "jury.combine", "product"), "jury.independence", "disjoint case sets"
    )
    _validate(stated)
    with pytest.raises(jsonschema.ValidationError):
        _validate(_with(read, "jury.independence", "not needed for the mean"))


# --- non-finite numbers ---


def _reject_non_finite(constant: str) -> float:
    raise ValueError(f"NON_FINITE_JSON_NUMBER: {constant}")


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_number_never_reaches_validation_or_sealing(literal: str) -> None:
    text = json.dumps(_read("pass")).replace('"score": 0.96', f'"score": {literal}', 1)
    assert literal in text
    with pytest.raises(ValueError, match="NON_FINITE_JSON_NUMBER"):
        json.loads(text, parse_constant=_reject_non_finite)
    lenient = json.loads(
        text
    )  # the stdlib default admits it, which is why the sealing layer must not
    with pytest.raises(CanonicalizationError, match="NON_FINITE_NUMBER"):
        canonical_bytes(lenient)

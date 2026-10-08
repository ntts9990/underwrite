"""Standalone behavior and boundary checks."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st
from underwrite_core.boundary import scan_strings

from underwrite.instrument.evidence.controlled import (
    AuditReason,
    ConditionDelta,
    ConditionIssue,
    audit_conditions,
)


def _info() -> dict[str, object]:
    return {
        "suite": "support-cases",
        "prompt_version": "prompt-a",
        "model": "model-a",
        "gen_params": {
            "temperature": 0.1,
            "top_p": 0.95,
            "max_tokens": 2048,
            "think": "off",
            "seed": None,
            "num_ctx": None,
            "timeout_s": 60.0,
        },
    }


def _gen(info: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], info["gen_params"])


def _set(info: dict[str, object], field: str, value: object) -> None:
    if field.startswith("gen_params."):
        _gen(info)[field.removeprefix("gen_params.")] = value
    else:
        info[field] = value


CHANGES: dict[str, object] = {
    "prompt_version": "prompt-b",
    "model": "model-b",
    "gen_params.temperature": 0.7,
    "gen_params.top_p": 0.8,
    "gen_params.max_tokens": 4096,
    "gen_params.think": "high",
    "gen_params.seed": 7,
    "gen_params.num_ctx": 8192,
    "gen_params.timeout_s": 120.0,
}


def test_prompt_comparison_is_descriptive_and_preserves_inputs() -> None:
    baseline, candidate = _info(), _info()
    candidate["prompt_version"] = "prompt-b"
    before = deepcopy((baseline, candidate))

    audit = audit_conditions(baseline, candidate)

    assert audit.eligible
    assert audit.changed_axes == ("prompt",)
    assert audit.differences == (ConditionDelta("prompt", "prompt-a", "prompt-b"),)
    assert audit.suite_mismatch is None
    assert audit.unverifiable == ()
    assert audit.reasons == ()
    assert scan_strings(asdict(audit)) == []
    assert (baseline, candidate) == before


@given(st.sets(st.sampled_from(tuple(CHANGES))))
def test_only_one_changed_axis_is_eligible(fields: set[str]) -> None:
    baseline, candidate = _info(), _info()
    for field in fields:
        _set(candidate, field, CHANGES[field])

    audit = audit_conditions(baseline, candidate)
    reverse = audit_conditions(candidate, baseline)

    assert audit.eligible == (len(fields) == 1)
    assert set(audit.changed_axes) == {
        "prompt" if field == "prompt_version" else field for field in fields
    }
    assert reverse.changed_axes == audit.changed_axes
    assert reverse.eligible == audit.eligible
    assert reverse.reasons == audit.reasons
    assert reverse.differences == tuple(
        ConditionDelta(delta.axis, delta.candidate, delta.baseline) for delta in audit.differences
    )
    if not fields:
        assert audit.reasons == (AuditReason.NO_VARIED_AXIS,)
    elif len(fields) > 1:
        assert audit.reasons == (AuditReason.MULTIPLE_VARIED_AXES,)
    else:
        assert audit.reasons == ()


def test_identical_recorded_conditions_isolate_nothing() -> None:
    """Standalone behavior and boundary checks."""
    audit = audit_conditions(_info(), _info())

    assert not audit.eligible
    assert audit.reasons == (AuditReason.NO_VARIED_AXIS,)
    assert audit.changed_axes == () and audit.differences == ()
    assert audit.suite_mismatch is None
    assert audit.unverifiable == ()


@pytest.mark.parametrize("prompt_changed", [False, True])
def test_suite_difference_is_never_an_allowed_axis(prompt_changed: bool) -> None:
    baseline, candidate = _info(), _info()
    candidate["suite"] = "other-cases"
    if prompt_changed:
        candidate["prompt_version"] = "prompt-b"

    audit = audit_conditions(baseline, candidate)

    assert not audit.eligible
    assert audit.suite_mismatch == ConditionDelta("suite", "support-cases", "other-cases")
    assert "suite" not in audit.changed_axes
    assert audit.reasons == (AuditReason.SUITE_MISMATCH,)


@pytest.mark.parametrize("field", ["suite", "gen_params", *CHANGES])
@pytest.mark.parametrize("sides", [("baseline",), ("candidate",), ("baseline", "candidate")])
def test_missing_metadata_never_establishes_equality(field: str, sides: tuple[str, ...]) -> None:
    infos = {"baseline": _info(), "candidate": _info()}
    infos["candidate"]["prompt_version"] = "prompt-b"
    for side in sides:
        if field.startswith("gen_params."):
            del _gen(infos[side])[field.removeprefix("gen_params.")]
        else:
            del infos[side][field]

    audit = audit_conditions(infos["baseline"], infos["candidate"])

    assert not audit.eligible
    assert audit.reasons == (AuditReason.MISSING_CONDITIONS,)
    assert {(issue.side, issue.field, issue.reason) for issue in audit.unverifiable} == {
        (side, field, "missing") for side in sides
    }
    assert ("prompt" if field == "prompt_version" else field) not in audit.changed_axes
    assert audit.suite_mismatch is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("suite", None),
        ("suite", ""),
        ("prompt_version", " "),
        ("model", 1),
        ("model", {}),
        ("gen_params", None),
        ("gen_params", "unknown"),
        ("gen_params", []),
        ("gen_params", {1: 2}),
        ("gen_params.seed", True),
        ("gen_params.seed", 1.5),
        ("gen_params.num_ctx", False),
        ("gen_params.temperature", True),
        ("gen_params.temperature", None),
        ("gen_params.temperature", float("nan")),
        ("gen_params.top_p", float("inf")),
        ("gen_params.timeout_s", float("-inf")),
        ("gen_params.max_tokens", "2048"),
        ("gen_params.think", False),
    ],
)
def test_unknown_metadata_is_distinct_from_missing(field: str, value: object) -> None:
    baseline, candidate = _info(), _info()
    candidate["prompt_version"] = "prompt-b"
    _set(baseline, field, value)

    audit = audit_conditions(baseline, candidate)

    assert not audit.eligible
    assert audit.unverifiable == (ConditionIssue("baseline", field, "unknown"),)
    assert audit.reasons == (AuditReason.UNKNOWN_CONDITIONS,)


@pytest.mark.parametrize("field", ["gen_params.seed", "gen_params.num_ctx"])
def test_explicit_nullable_parameter_remains_a_known_setting(field: str) -> None:
    baseline, candidate = _info(), _info()
    _set(candidate, field, 7)

    audit = audit_conditions(baseline, candidate)

    assert audit.eligible
    assert audit.differences == (ConditionDelta(field, None, 7),)
    assert audit.unverifiable == ()


def test_bool_does_not_hide_a_numeric_change() -> None:
    baseline, candidate = _info(), _info()
    candidate["prompt_version"] = "prompt-b"
    _gen(baseline)["seed"] = 1
    _gen(candidate)["seed"] = True

    audit = audit_conditions(baseline, candidate)

    assert not audit.eligible
    assert audit.unverifiable == (ConditionIssue("candidate", "gen_params.seed", "unknown"),)


def test_equivalent_numeric_encodings_are_not_extra_axes() -> None:
    baseline, candidate = _info(), _info()
    candidate["prompt_version"] = "prompt-b"
    _gen(baseline)["temperature"] = 1
    _gen(candidate)["temperature"] = 1.0

    assert audit_conditions(baseline, candidate).changed_axes == ("prompt",)
    assert audit_conditions(baseline, candidate).eligible


@pytest.mark.parametrize("field", CHANGES)
def test_each_supported_axis_retains_the_recorded_values(field: str) -> None:
    baseline, candidate = _info(), _info()
    _set(candidate, field, CHANGES[field])
    before = (
        _gen(baseline)[field.removeprefix("gen_params.")]
        if field.startswith("gen_params.")
        else baseline[field]
    )

    audit = audit_conditions(baseline, candidate)

    assert audit.eligible
    assert len(audit.differences) == 1
    assert audit.differences[0].baseline == before
    assert audit.differences[0].candidate == CHANGES[field]


@pytest.mark.parametrize("seed", [0, None, -1])
def test_explicit_seed_settings_do_not_disappear_through_truthiness(seed: int | None) -> None:
    baseline, candidate = _info(), _info()
    candidate["prompt_version"] = "prompt-b"
    _gen(baseline)["seed"] = _gen(candidate)["seed"] = seed

    assert audit_conditions(baseline, candidate).eligible


@pytest.mark.parametrize("same_value", [True, False])
def test_unknown_generation_parameter_is_not_silently_ignored(same_value: bool) -> None:
    baseline, candidate = _info(), _info()
    candidate["prompt_version"] = "prompt-b"
    _gen(baseline)["frequency_penalty"] = 0.0
    _gen(candidate)["frequency_penalty"] = 0.0 if same_value else 0.5

    audit = audit_conditions(baseline, candidate)

    assert not audit.eligible
    assert audit.unverifiable == (
        ConditionIssue("baseline", "gen_params.frequency_penalty", "unknown"),
        ConditionIssue("candidate", "gen_params.frequency_penalty", "unknown"),
    )
    assert audit.reasons == (AuditReason.UNKNOWN_CONDITIONS,)


def test_unrelated_run_record_fields_do_not_become_axes() -> None:
    baseline, candidate = _info(), _info()
    candidate["prompt_version"] = "prompt-b"
    baseline["duration_s"], candidate["duration_s"] = 1.0, 9.0
    baseline["run_id"], candidate["run_id"] = "a", "b"

    assert audit_conditions(baseline, candidate).eligible


def test_all_known_blockers_remain_visible_when_conditions_are_incomplete() -> None:
    baseline, candidate = _info(), _info()
    candidate.update(suite="other-cases", prompt_version="prompt-b", model="model-b")
    del _gen(baseline)["seed"]
    _gen(candidate)["temperature"] = None

    audit = audit_conditions(baseline, candidate)

    assert not audit.eligible
    assert set(audit.reasons) == {
        AuditReason.MISSING_CONDITIONS,
        AuditReason.UNKNOWN_CONDITIONS,
        AuditReason.SUITE_MISMATCH,
        AuditReason.MULTIPLE_VARIED_AXES,
    }
    assert audit.changed_axes == ("prompt", "model")
    assert audit.unverifiable == (
        ConditionIssue("baseline", "gen_params.seed", "missing"),
        ConditionIssue("candidate", "gen_params.temperature", "unknown"),
    )

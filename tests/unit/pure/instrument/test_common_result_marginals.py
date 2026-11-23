"""Marginal-only comparisons of validated common-result projections."""

from __future__ import annotations

from copy import deepcopy
from typing import cast

import pytest

from underwrite.instrument.evidence.common_result import (
    MARGINAL_INELIGIBILITY_REASONS,
    POPULATION_UNIT_MISMATCH,
    RESULT_FORMAT_MISMATCH,
    RESULT_NOT_ANALYZED,
    compare_result_marginals,
)


def _projection(
    *,
    format_name: str = "evidence_result.v2",
    state: str = "analyzed",
    unit: str = "case",
    observed_count: int = 4,
    planned_count: int | None = 5,
) -> dict[str, object]:
    analyzed = state == "analyzed"
    return {
        "schema": "common_result_inspection_actions.v3",
        "selected_result": {"format": format_name, "raw_hash": "sha256:" + "0" * 64},
        "declared_processing": {"state": state, "reason": None, "next_action": "none"},
        "declared_population": (
            {"unit": unit, "observed_count": observed_count, "planned_count": planned_count}
            if analyzed
            else None
        ),
        "availability_counts": (
            {"observed": 2, "missing": 1, "error": 1, "abstained": 0, "total": 4}
            if analyzed
            else None
        ),
        "item_next_action_counts": (
            {
                "none": 2,
                "inspect_source": 1,
                "supply_plan": 0,
                "supply_applicability": 0,
                "supply_definition": 0,
                "supply_label": 0,
                "rerun_source": 1,
            }
            if analyzed
            else None
        ),
        "declared_semantic_binding_counts": (
            {"unbound": 3, "bound": 1} if analyzed else None
        ),
    }


def test_comparison_returns_closed_candidate_minus_baseline_marginals() -> None:
    baseline = _projection()
    candidate = _projection(observed_count=6, planned_count=8)
    candidate["availability_counts"] = {
        "observed": 4, "missing": 0, "error": 1, "abstained": 1, "total": 6
    }
    candidate["item_next_action_counts"] = {
        "none": 3,
        "inspect_source": 1,
        "supply_plan": 1,
        "supply_applicability": 0,
        "supply_definition": 0,
        "supply_label": 0,
        "rerun_source": 1,
    }
    candidate["declared_semantic_binding_counts"] = {"unbound": 2, "bound": 4}

    comparison = compare_result_marginals(baseline, candidate)

    assert set(comparison) == {
        "state",
        "reason",
        "population_unit",
        "observed_population_count_delta",
        "planned_population_count_delta",
        "availability_counts",
        "item_next_action_counts",
        "declared_semantic_binding_counts",
    }
    assert comparison == {
        "state": "computed",
        "reason": None,
        "population_unit": "case",
        "observed_population_count_delta": 2,
        "planned_population_count_delta": 3,
        "availability_counts": {
            "baseline": {"observed": 2, "missing": 1, "error": 1, "abstained": 0, "total": 4},
            "candidate": {"observed": 4, "missing": 0, "error": 1, "abstained": 1, "total": 6},
            "delta": {"observed": 2, "missing": -1, "error": 0, "abstained": 1, "total": 2},
        },
        "item_next_action_counts": {
            "baseline": {
                "none": 2,
                "inspect_source": 1,
                "supply_plan": 0,
                "supply_applicability": 0,
                "supply_definition": 0,
                "supply_label": 0,
                "rerun_source": 1,
            },
            "candidate": {
                "none": 3,
                "inspect_source": 1,
                "supply_plan": 1,
                "supply_applicability": 0,
                "supply_definition": 0,
                "supply_label": 0,
                "rerun_source": 1,
            },
            "delta": {
                "none": 1,
                "inspect_source": 0,
                "supply_plan": 1,
                "supply_applicability": 0,
                "supply_definition": 0,
                "supply_label": 0,
                "rerun_source": 0,
            },
        },
        "declared_semantic_binding_counts": {
            "baseline": {"unbound": 3, "bound": 1},
            "candidate": {"unbound": 2, "bound": 4},
            "delta": {"unbound": -1, "bound": 3},
        },
    }


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("format", RESULT_FORMAT_MISMATCH),
        ("processing", RESULT_NOT_ANALYZED),
        ("unit", POPULATION_UNIT_MISMATCH),
    ],
)
def test_ineligible_comparisons_return_no_partial_marginals(
    mutation: str, reason: str
) -> None:
    baseline = _projection()
    candidate = _projection()
    if mutation == "format":
        selected = candidate["selected_result"]
        assert isinstance(selected, dict)
        selected["format"] = "evidence_result.v1"
    elif mutation == "processing":
        candidate = _projection(state="insufficient")
    else:
        candidate = _projection(unit="row")

    assert compare_result_marginals(baseline, candidate) == {
        "state": "not_computed",
        "reason": reason,
        "population_unit": None,
        "observed_population_count_delta": None,
        "planned_population_count_delta": None,
        "availability_counts": None,
        "item_next_action_counts": None,
        "declared_semantic_binding_counts": None,
    }


def test_ineligibility_reasons_are_closed_and_ordered_by_eligibility_check() -> None:
    assert MARGINAL_INELIGIBILITY_REASONS == (
        RESULT_FORMAT_MISMATCH,
        RESULT_NOT_ANALYZED,
        POPULATION_UNIT_MISMATCH,
    )
    assert MARGINAL_INELIGIBILITY_REASONS == (
        "RESULT_FORMAT_MISMATCH",
        "RESULT_NOT_ANALYZED",
        "POPULATION_UNIT_MISMATCH",
    )


def test_planned_delta_is_null_if_either_declared_plan_is_null() -> None:
    assert compare_result_marginals(
        _projection(planned_count=None), _projection(planned_count=8)
    )["planned_population_count_delta"] is None


def test_swapping_sides_negates_all_computed_deltas_without_mutating_inputs() -> None:
    baseline = _projection()
    candidate = _projection(observed_count=6, planned_count=8)
    candidate["availability_counts"] = {
        "observed": 3, "missing": 2, "error": 1, "abstained": 0, "total": 6
    }
    originals = deepcopy((baseline, candidate))

    forward = compare_result_marginals(baseline, candidate)
    reverse = compare_result_marginals(candidate, baseline)

    forward_observed = forward["observed_population_count_delta"]
    reverse_observed = reverse["observed_population_count_delta"]
    forward_planned = forward["planned_population_count_delta"]
    reverse_planned = reverse["planned_population_count_delta"]
    assert isinstance(forward_observed, int) and isinstance(reverse_observed, int)
    assert isinstance(forward_planned, int) and isinstance(reverse_planned, int)
    assert forward_observed == -reverse_observed
    assert forward_planned == -reverse_planned
    for field in (
        "availability_counts",
        "item_next_action_counts",
        "declared_semantic_binding_counts",
    ):
        forward_counts = forward[field]
        reverse_counts = reverse[field]
        assert isinstance(forward_counts, dict)
        assert isinstance(reverse_counts, dict)
        assert forward_counts["baseline"] == reverse_counts["candidate"]
        assert forward_counts["candidate"] == reverse_counts["baseline"]
        reverse_delta = cast(dict[str, object], reverse_counts)["delta"]
        assert isinstance(reverse_delta, dict)
        assert forward_counts["delta"] == {
            key: -value for key, value in cast(dict[str, int], reverse_delta).items()
        }
    assert (baseline, candidate) == originals


@pytest.mark.parametrize("invalid_value", [True, 1.0])
def test_histogram_values_must_be_strict_integers(invalid_value: object) -> None:
    candidate = _projection()
    counts = candidate["availability_counts"]
    assert isinstance(counts, dict)
    counts["observed"] = invalid_value

    with pytest.raises(ValueError, match="histogram values must be integers"):
        compare_result_marginals(_projection(), candidate)


def test_histogram_key_sets_must_match_exactly() -> None:
    candidate = _projection()
    counts = candidate["item_next_action_counts"]
    assert isinstance(counts, dict)
    del cast(dict[str, object], counts)["rerun_source"]

    with pytest.raises(ValueError, match="histogram keys must match"):
        compare_result_marginals(_projection(), candidate)

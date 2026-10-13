"""Draft policy choices stay explicit and assumption declarations stay unknown."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from typing import Any, overload

import pytest
from _event_acceptance import inputs

from underwrite.measurement.event_policy import EventPolicyError, prepare_event_policy
from underwrite.measurement_contract.semantics import ASSUMPTIONS, parse_semantics, semantic_digest


def _prepare(ids: Any = None, **choices: Any) -> dict[str, Any]:
    obs, policy = inputs([1, 0])
    options: dict[str, Any] = {
        "subject_id": "consumer",
        "p0": 0.3,
        "ece_bins": 9,
        "min_bin_n": 7,
        "all_k": 4,
        "max_observations": 12,
        **choices,
    }
    return prepare_event_policy(
        parse_semantics(obs["payload"]["measurement_semantics"]),
        policy["expected_case_ids"] if ids is None else ids,
        **options,
    )


def test_explicit_choices_immutable_inputs_and_fresh_unknown_assumptions() -> None:
    obs, source_policy = inputs([1, 0])
    semantics = parse_semantics(obs["payload"]["measurement_semantics"])
    ids = source_policy["expected_case_ids"]
    original = deepcopy(ids)
    policy = _prepare(ids)
    assert ids == original
    assert policy["expected_case_ids"] is not ids
    assert policy["semantic_digest"] == semantic_digest(semantics)
    assert policy["diagnostics"] == {"ece_bins": 9, "min_bin_n": 7, "all_k": 4}
    assert policy["eprocess"] == {
        "p0": 0.3,
        "alpha_total": 0.05,
        "prior_a": 1,
        "prior_b": 1,
        "max_observations": 12,
    }
    assert set(policy["assumptions"]) == set(ASSUMPTIONS)
    assert set(policy["assumptions"].values()) == {"unknown"}
    policy["assumptions"]["conditional_mean_model"] = "declared"
    assert set(_prepare()["assumptions"].values()) == {"unknown"}


@pytest.mark.parametrize("ids", ["abc", b"abc", [], ["x", "x"], [True], [""], list(range(2))])
def test_invalid_roster_refused(ids: Any) -> None:
    with pytest.raises(EventPolicyError, match="INVALID_POLICY_FIELDS"):
        _prepare(ids)


def test_generic_policy_population_limit_is_not_baseline_limit() -> None:
    ids = [f"case-{i}" for i in range(2000)]
    assert _prepare(ids)["expected_case_ids"] == ids
    with pytest.raises(EventPolicyError):
        _prepare([*ids, "extra"])


def test_over_limit_roster_is_rejected_before_accessing_items() -> None:
    class OversizedRoster(Sequence[str]):
        def __len__(self) -> int:
            return 2001

        @overload
        def __getitem__(self, index: int) -> str: ...

        @overload
        def __getitem__(self, index: slice) -> Sequence[str]: ...

        def __getitem__(self, index: int | slice) -> str | Sequence[str]:
            raise AssertionError("over-limit roster must not be traversed")

    with pytest.raises(EventPolicyError, match="INVALID_POLICY_FIELDS"):
        _prepare(OversizedRoster())


@pytest.mark.parametrize(
    ("field", "value"),
    [("p0", x) for x in (0, 1, float("nan"), float("inf"), True)]
    + [("ece_bins", x) for x in (0, 129, True)]
    + [("min_bin_n", x) for x in (0, True)]
    + [("all_k", x) for x in (0, 2001, True)]
    + [("max_observations", x) for x in (0, True, 2**53)]
    + [("subject_id", "")],
)
def test_invalid_explicit_choices_refused(field: str, value: Any) -> None:
    with pytest.raises(EventPolicyError, match="INVALID_POLICY_FIELDS"):
        _prepare(**{field: value})

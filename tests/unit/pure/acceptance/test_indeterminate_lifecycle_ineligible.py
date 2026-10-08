"""Standalone behavior and boundary checks."""

from __future__ import annotations

import ast
import copy
import itertools
from pathlib import Path
from typing import Any, get_args

import pytest
from _repo_paths import repo_root

from underwrite.acceptance import lifecycle as lc
from underwrite.acceptance.classify import CLASSIFICATIONS
from underwrite.acceptance.lifecycle import LifecycleError, project_lifecycle

ROOT = repo_root(Path(__file__))
# Rows: current state; columns: CLASSIFICATIONS order; None = refused.
TABLE: dict[str, tuple[str | None, ...]] = {
    "proposed": ("screened", None, "proposed", "rejected"),
    "screened": ("screened", "confirmed", "screened", "rejected"),
    "confirmed": (None, None, None, None),
    "rejected": (None, None, None, None),
}


@pytest.mark.parametrize(
    ("current", "column"), list(itertools.product(TABLE, range(len(CLASSIFICATIONS))))
)
def test_projection_table(current: Any, column: int) -> None:
    classification = CLASSIFICATIONS[column]
    expected = TABLE[current][column]
    if expected is None:
        with pytest.raises(LifecycleError) as caught:
            project_lifecycle(current, classification)
        assert caught.value.code == "INVALID_CLASSIFICATION_FOR_STATE"
        assert (caught.value.current, caught.value.classification) == (current, classification)
    else:
        assert project_lifecycle(current, classification) == expected


def test_table_covers_every_state_and_classification() -> None:
    assert tuple(TABLE) == lc.STATES == get_args(lc.State)
    assert CLASSIFICATIONS == ("screened", "confirmed", "indeterminate", "rejected")


@pytest.mark.parametrize("current", ["proposed", "screened"])
def test_indeterminate_preserves_the_current_state(current: Any) -> None:
    assert project_lifecycle(current, "indeterminate") == current


def test_proposed_cannot_jump_to_confirmed() -> None:
    with pytest.raises(LifecycleError) as caught:
        project_lifecycle("proposed", "confirmed")
    assert (caught.value.code, caught.value.current, caught.value.classification) == (
        "INVALID_CLASSIFICATION_FOR_STATE",
        "proposed",
        "confirmed",
    )


# classification is carried with the terminal state it could not move.
NATIVE_INVALID_LIFECYCLE = [
    ("confirmed", "confirmed"),
    ("confirmed", "rejected"),
    ("rejected", "confirmed"),
    ("rejected", "rejected"),
]


@pytest.mark.parametrize(("current", "attempted"), NATIVE_INVALID_LIFECYCLE)
def test_native_invalid_lifecycle_cases(current: Any, attempted: Any) -> None:
    with pytest.raises(LifecycleError) as caught:
        project_lifecycle(current, attempted)
    assert caught.value.code == "INVALID_CLASSIFICATION_FOR_STATE"
    assert (caught.value.current, caught.value.classification) == (current, attempted)


def test_lifecycle_ineligible_is_not_a_state() -> None:
    assert "lifecycle_ineligible" not in lc.STATES
    assert "lifecycle_ineligible" not in set(lc.TRANSITIONS.values())
    with pytest.raises(LifecycleError) as caught:
        project_lifecycle("lifecycle_ineligible", "screened")  # type: ignore[arg-type]
    assert caught.value.code == "UNKNOWN_STATE"


@pytest.mark.parametrize(
    ("current", "classification", "code"),
    [
        ("Proposed", "screened", "UNKNOWN_STATE"),
        ("indeterminate", "screened", "UNKNOWN_STATE"),
        ("proposed", "pass", "UNKNOWN_CLASSIFICATION"),
        ("screened", "Confirmed", "UNKNOWN_CLASSIFICATION"),
        ("screened", "lifecycle_ineligible", "UNKNOWN_CLASSIFICATION"),
    ],
)
def test_unknown_terms_are_refused(current: Any, classification: Any, code: str) -> None:
    with pytest.raises(LifecycleError) as caught:
        project_lifecycle(current, classification)
    error = caught.value
    assert (error.code, error.current, error.classification) == (code, current, classification)
    assert str(error) == f"{code}: {current} x {classification}"


def test_projection_does_not_mutate_the_table() -> None:
    before = copy.deepcopy(dict(lc.TRANSITIONS))
    for current, classification in itertools.product(lc.STATES, CLASSIFICATIONS):
        try:
            project_lifecycle(current, classification)
        except LifecycleError:
            pass
    assert dict(lc.TRANSITIONS) == before


def test_acceptance_reads_no_clock() -> None:
    """Standalone behavior and boundary checks."""
    for path in sorted((ROOT / "src/underwrite/acceptance").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {(node.module or "").split(".")[0]}
            else:
                continue
            assert not names & {"datetime", "time", "calendar", "zoneinfo"}, path

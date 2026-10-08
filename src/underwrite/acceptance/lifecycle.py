"""Project classifications onto a durable acceptance lifecycle.

Only declared transition pairs are allowed. A refused transition is an error,
not a synthetic lifecycle state. Terminal states cannot be reclassified."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal

from underwrite.acceptance.classify import CLASSIFICATIONS, Classification

State = Literal["proposed", "screened", "confirmed", "rejected"]
LifecycleErrorCode = Literal[
    "UNKNOWN_STATE", "UNKNOWN_CLASSIFICATION", "INVALID_CLASSIFICATION_FOR_STATE"
]

STATES: Final[tuple[State, ...]] = ("proposed", "screened", "confirmed", "rejected")
TRANSITIONS: Final[Mapping[tuple[State, Classification], State]] = {
    ("proposed", "screened"): "screened",
    ("proposed", "indeterminate"): "proposed",
    ("proposed", "rejected"): "rejected",
    ("screened", "screened"): "screened",
    ("screened", "confirmed"): "confirmed",
    ("screened", "indeterminate"): "screened",
    ("screened", "rejected"): "rejected",
}


class LifecycleError(ValueError):
    """A projection that needs a separate evaluation path; carries both inputs."""

    def __init__(self, code: LifecycleErrorCode, current: object, classification: object) -> None:
        self.code = code
        self.current = current
        self.classification = classification
        super().__init__(f"{code}: {current} x {classification}")


def project_lifecycle(current: State, classification: Classification) -> State:
    """The next state for ``current`` under ``classification``, or a refusal."""
    if current not in STATES:
        raise LifecycleError("UNKNOWN_STATE", current, classification)
    if classification not in CLASSIFICATIONS:
        raise LifecycleError("UNKNOWN_CLASSIFICATION", current, classification)
    following = TRANSITIONS.get((current, classification))
    if following is None:
        raise LifecycleError("INVALID_CLASSIFICATION_FOR_STATE", current, classification)
    return following

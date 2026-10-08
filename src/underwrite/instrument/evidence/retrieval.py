"""Project supplied retrieval facts without asserting retrieval execution.

Capability limits belong to the declared evidence profile. Unsupported metric
methods and incomplete retrieval evidence remain explicitly unmeasured.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final, Literal, cast

MetricName = Literal["recall_at_k", "mrr", "ndcg_at_k"]
QualityReason = Literal["no_relevance_population", "missing_scores"]
_METRIC_NAMES: Final = ("recall_at_k", "mrr", "ndcg_at_k")
_CAPABILITIES: Final = {
    "factual": ("L1", True, None),
    "linkable_reasoning": ("L2", True, None),
    "predictive": ("L3", False, "future_prediction_beyond_local_corpus"),
    "creative": ("L4", False, "creative_generation_beyond_local_corpus"),
}


class RetrievalEvidenceError(ValueError):
    """Malformed supplied facts cannot produce partial retrieval evidence."""


class UnsupportedRetrievalProfile(RetrievalEvidenceError):
    """An unknown capability or metric profile has no implicit fallback."""


@dataclass(frozen=True)
class RetrievalContext:
    """Supplied source/corpus/method identity for one observation population."""

    source_ref: str
    source_digest: str
    corpus_id: str
    corpus_digest: str
    method_id: str
    method_version: str


@dataclass(frozen=True)
class CapabilityProfile:
    """A source-declared capability profile, with no action recommendation."""

    version: str
    task_class: str
    subtype: str | None
    capability_level: str
    system_capability_ceiling: str
    in_scope: bool
    out_of_scope_reason: str | None
    signals: tuple[str, ...]


@dataclass(frozen=True)
class MetricObservation:
    """A named source value or explicit absence under its recorded method."""

    name: MetricName
    value: int | float | None
    reason: str | None
    method: str


@dataclass(frozen=True)
class RetrievalObservation:
    """Materialized source scores, not hits, documents or retrieval instructions."""

    query_id: str
    task_id: str
    source_case_id: str
    k: int
    expected_labels: int
    scores: tuple[MetricObservation, ...]
    capability: CapabilityProfile
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalQueryEvidence:
    """Owned source facts; the quality view cannot endorse a zero-label score."""

    query_id: str
    task_id: str
    source_case_id: str
    k: int
    expected_labels: int
    source_scores: tuple[MetricObservation, ...]
    capability: CapabilityProfile
    limitations: tuple[str, ...]

    @property
    def quality_reason(self) -> QualityReason | None:
        return _quality_reason(self)

    @property
    def quality_state(self) -> Literal["observed", "not_measured"]:
        """Availability of supplied quality observations, not their verification."""
        return _quality_state(self)

    @property
    def ranking_quality(self) -> tuple[MetricObservation, ...]:
        """Descriptive quality observations, never a pass/failure classification.

        With no relevance population all quality values are unavailable. Source
        zeros/other supplied values and their original reasons remain separately
        accessible in source_scores. Otherwise individual absences stay intact.
        """
        return _ranking_quality(self)


def _quality_reason(record: RetrievalQueryEvidence) -> QualityReason | None:
    if record.expected_labels == 0:
        return "no_relevance_population"
    if all(score.value is None for score in record.source_scores):
        return "missing_scores"
    return None


def _quality_state(record: RetrievalQueryEvidence) -> Literal["observed", "not_measured"]:
    return "not_measured" if record.quality_reason is not None else "observed"


def _ranking_quality(record: RetrievalQueryEvidence) -> tuple[MetricObservation, ...]:
    """Withhold every value when no relevance population can support one.

    The withholding is total rather than per-score: with no labels there is nothing
    any metric could have been computed against, so a surviving supplied number would
    be a score nobody earned. The originals stay readable in source_scores.
    """
    if record.expected_labels != 0:
        return record.source_scores
    return tuple(
        replace(score, value=None, reason="no_relevance_population")
        for score in record.source_scores
    )


@dataclass(frozen=True)
class RetrievalEvidence:
    """A source population; observed means received facts, not verified quality."""

    context: RetrievalContext
    records: tuple[RetrievalQueryEvidence, ...]
    limitations: tuple[str, ...]

    @property
    def population(self) -> int:
        return len(self.records)

    @property
    def state(self) -> Literal["observed", "not_measured"]:
        return _state(self)

    @property
    def reason(self) -> Literal["empty_population"] | None:
        return _reason(self)


def _state(evidence: RetrievalEvidence) -> Literal["observed", "not_measured"]:
    return "observed" if evidence.records else "not_measured"


def _reason(evidence: RetrievalEvidence) -> Literal["empty_population"] | None:
    return None if evidence.records else "empty_population"


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise RetrievalEvidenceError(f"INVALID_TEXT: {field}")
    if unicodedata.normalize("NFC", value) != value:
        raise RetrievalEvidenceError(f"NONCANONICAL_TEXT: {field}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RetrievalEvidenceError(f"INVALID_TEXT: {field}") from exc
    return value


def _texts(value: object, field: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise RetrievalEvidenceError(f"INVALID_TUPLE: {field}")
    return tuple(_text(item, field) for item in cast(tuple[object, ...], value))


def _context(value: object) -> RetrievalContext:
    if type(value) is not RetrievalContext:
        raise RetrievalEvidenceError("INVALID_CONTEXT")
    for field in (
        "source_ref",
        "source_digest",
        "corpus_id",
        "corpus_digest",
        "method_id",
        "method_version",
    ):
        _text(getattr(value, field), field)
    for digest in (value.source_digest, value.corpus_digest):
        if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
            raise RetrievalEvidenceError("INVALID_DIGEST")
    return replace(value)


def _capability(value: object) -> CapabilityProfile:
    if type(value) is not CapabilityProfile:
        raise RetrievalEvidenceError("INVALID_CAPABILITY")
    if type(value.version) is not str or value.version != "query-task-capability-v1":
        raise UnsupportedRetrievalProfile("UNSUPPORTED_CAPABILITY_PROFILE")
    for field in ("task_class", "capability_level", "system_capability_ceiling"):
        _text(getattr(value, field), field)
    if value.subtype is not None:
        _text(value.subtype, "subtype")
    if value.out_of_scope_reason is not None:
        _text(value.out_of_scope_reason, "out_of_scope_reason")
    expected = _CAPABILITIES.get(value.task_class)
    if expected is None or type(value.in_scope) is not bool:
        raise RetrievalEvidenceError("INVALID_CAPABILITY")
    if (value.capability_level, value.in_scope, value.out_of_scope_reason) != expected:
        raise RetrievalEvidenceError("INCOHERENT_CAPABILITY")
    if value.system_capability_ceiling != "L2":
        raise RetrievalEvidenceError("INCOHERENT_CAPABILITY_CEILING")
    _subtype(value)
    return replace(value, signals=_texts(value.signals, "signals"))


def _subtype(value: CapabilityProfile) -> None:
    if value.task_class == "linkable_reasoning":
        if value.subtype not in ("bridging", "comparative", "quantitative", "summarizing"):
            raise RetrievalEvidenceError("INVALID_CAPABILITY_SUBTYPE")
    elif value.subtype is not None:
        raise RetrievalEvidenceError("INVALID_CAPABILITY_SUBTYPE")


def _metric(value: object) -> MetricObservation:
    if type(value) is not MetricObservation:
        raise RetrievalEvidenceError("INVALID_METRIC")
    _text(value.name, "metric.name")
    if value.name not in _METRIC_NAMES:
        raise RetrievalEvidenceError("UNKNOWN_METRIC")
    _text(value.method, "metric.method")
    if value.name == "ndcg_at_k" and value.method != "expectation-label-ndcg-v1":
        raise UnsupportedRetrievalProfile("UNSUPPORTED_NDCG_METHOD")
    if value.value is None:
        _text(value.reason, "metric.reason")
    else:
        if type(value.value) not in (int, float) or value.reason is not None:
            raise RetrievalEvidenceError("INVALID_SCORE")
        try:
            finite = math.isfinite(value.value)
        except OverflowError as exc:
            raise RetrievalEvidenceError("UNREPRESENTABLE_SCORE") from exc
        if not finite:
            raise RetrievalEvidenceError("NONFINITE_SCORE")
    return replace(value)


def _scores(value: object) -> tuple[MetricObservation, ...]:
    if type(value) is not tuple:
        raise RetrievalEvidenceError("INVALID_TUPLE: scores")
    scores = tuple(_metric(item) for item in cast(tuple[object, ...], value))
    names = tuple(score.name for score in scores)
    if len(scores) != len(_METRIC_NAMES) or set(names) != set(_METRIC_NAMES):
        raise RetrievalEvidenceError("METRIC_POPULATION_MISMATCH")
    return tuple(sorted(scores, key=lambda score: _METRIC_NAMES.index(score.name)))


def _record(value: object) -> RetrievalQueryEvidence:
    if type(value) is not RetrievalObservation:
        raise RetrievalEvidenceError("INVALID_RECORD")
    for field in ("query_id", "task_id", "source_case_id"):
        _text(getattr(value, field), field)
    if type(value.k) is not int or value.k <= 0:
        raise RetrievalEvidenceError("INVALID_COUNT: k")
    if type(value.expected_labels) is not int or value.expected_labels < 0:
        raise RetrievalEvidenceError("INVALID_COUNT: expected_labels")
    return RetrievalQueryEvidence(
        query_id=value.query_id,
        task_id=value.task_id,
        source_case_id=value.source_case_id,
        k=value.k,
        expected_labels=value.expected_labels,
        source_scores=_scores(value.scores),
        capability=_capability(value.capability),
        limitations=_texts(value.limitations, "limitations"),
    )


def project_retrieval(
    records: Sequence[RetrievalObservation], *, context: RetrievalContext
) -> RetrievalEvidence:
    """Validate and copy one supplied corpus/method/run without external effects.

    Only exact list/tuple populations and the declared frozen record types are
    accepted. All facts are validated before any evidence is returned; digest
    syntax and internally coherent declarations do not verify their contents.
    """
    owned_context = _context(context)
    if type(records) not in (list, tuple):
        raise RetrievalEvidenceError("INVALID_POPULATION")
    owned = tuple(_record(record) for record in records)
    if len({record.query_id for record in owned}) != len(owned):
        raise RetrievalEvidenceError("DUPLICATE_QUERY")
    return RetrievalEvidence(
        owned_context,
        tuple(sorted(owned, key=lambda record: record.query_id)),
        ("supplied_provenance_not_independently_verified",),
    )

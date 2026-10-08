"""Standalone behavior and boundary checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, asdict, fields, replace
from typing import Any, cast

import pytest
from underwrite_core.boundary import scan_strings

from underwrite.instrument.evidence.retrieval import (
    CapabilityProfile,
    MetricObservation,
    RetrievalContext,
    RetrievalEvidence,
    RetrievalEvidenceError,
    RetrievalObservation,
    UnsupportedRetrievalProfile,
    project_retrieval,
)


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _context() -> RetrievalContext:
    return RetrievalContext(
        source_ref="conformance:query-rows",
        source_digest=_digest(b"conformance source rows"),
        corpus_id="conformance:two-label-corpus",
        corpus_digest=_digest(b"explicit conformance corpus: label-a,label-b"),
        method_id="sample-retrieval-scoring",
        method_version="local-conformance-v1",
    )


def _profile() -> CapabilityProfile:
    return CapabilityProfile(
        "query-task-capability-v1", "factual", None, "L1", "L2", True, None, ()
    )


def _record(query_id: str = "query-1") -> RetrievalObservation:
    return RetrievalObservation(
        query_id=query_id,
        task_id="task-1",
        source_case_id="case-1",
        k=2,
        expected_labels=2,
        scores=(
            MetricObservation("recall_at_k", 1.0, None, "source-recall-v1"),
            MetricObservation("mrr", 1.0, None, "source-first-rank-v1"),
            MetricObservation("ndcg_at_k", 1.2262943855309167, None, "expectation-label-ndcg-v1"),
        ),
        capability=_profile(),
        limitations=("analytic_conformance_not_live_retrieval",),
    )


def _score(
    record: RetrievalObservation, value: object, reason: object = None
) -> RetrievalObservation:
    metric = replace(record.scores[0], value=cast(float, value), reason=cast(str | None, reason))
    return replace(record, scores=(metric, *record.scores[1:]))


def _boundary_document(evidence: RetrievalEvidence) -> dict[str, Any]:
    # The scanner consumes JSON dict/list, not Python DTO tuples.
    return json.loads(json.dumps(asdict(evidence), allow_nan=False))


def test_boundary_probe_traverses_nested_source_claims() -> None:
    record = replace(_record(), limitations=("approved",))
    document = _boundary_document(project_retrieval([record], context=_context()))
    leaks = scan_strings(document)
    assert any(
        leak.path == "$.records[0].limitations[0]" and leak.term == "approved" for leak in leaks
    )


def test_projection_sorts_owned_source_facts_without_recomputing() -> None:
    rows = [_record("query-z"), _record("query-a")]
    context = _context()
    evidence = project_retrieval(rows, context=context)

    assert evidence == project_retrieval(tuple(reversed(rows)), context=context)
    assert evidence.state == "observed"
    assert evidence.reason is None
    assert evidence.population == len(rows)
    assert tuple(row.query_id for row in evidence.records) == ("query-a", "query-z")
    assert evidence.context == context and evidence.context is not context
    assert evidence.limitations == ("supplied_provenance_not_independently_verified",)
    row = evidence.records[0]
    assert (row.task_id, row.source_case_id, row.k, row.expected_labels) == (
        "task-1",
        "case-1",
        2,
        2,
    )
    assert row.source_scores == rows[1].scores
    assert row.source_scores is not rows[1].scores
    assert row.source_scores[0] is not rows[1].scores[0]
    assert row.capability == rows[1].capability and row.capability is not rows[1].capability
    assert row.ranking_quality == row.source_scores
    assert (row.quality_state, row.quality_reason) == ("observed", None)
    assert row.limitations == rows[1].limitations
    assert scan_strings(_boundary_document(evidence)) == []


def test_empty_population_is_not_measured_but_requires_context() -> None:
    evidence = project_retrieval([], context=_context())
    assert (evidence.state, evidence.reason, evidence.population, evidence.records) == (
        "not_measured",
        "empty_population",
        0,
        (),
    )
    assert "supplied_provenance_not_independently_verified" in evidence.limitations
    with pytest.raises(RetrievalEvidenceError, match="INVALID_CONTEXT"):
        project_retrieval([], context=cast(RetrievalContext, None))


def test_no_relevance_population_preserves_source_zero_but_not_quality() -> None:
    source = replace(
        _record(),
        expected_labels=0,
        scores=tuple(replace(score, value=0.0) for score in _record().scores),
    )
    evidence = project_retrieval([source], context=_context())
    row = evidence.records[0]
    assert evidence.state == "observed"  # Received source rows, not a quality verdict.
    assert (row.quality_state, row.quality_reason) == ("not_measured", "no_relevance_population")
    assert [metric.value for metric in row.source_scores] == [0.0, 0.0, 0.0]
    assert [(metric.value, metric.reason) for metric in row.ranking_quality] == [
        (None, "no_relevance_population")
    ] * 3
    assert [metric.method for metric in row.ranking_quality] == [
        metric.method for metric in row.source_scores
    ]
    # The computed view is not a second stored, independently authored score list.
    assert "ranking_quality" not in {field.name for field in fields(row)}


def test_no_hits_zero_and_absent_scores_remain_distinct() -> None:
    no_hits = replace(_record(), scores=tuple(replace(s, value=0) for s in _record().scores))
    missing = replace(
        _record("missing"),
        scores=tuple(
            replace(s, value=None, reason="upstream_unavailable") for s in _record().scores
        ),
    )
    evidence = project_retrieval([no_hits, missing], context=_context())
    assert (evidence.population, evidence.state) == (2, "observed")
    assert (evidence.records[0].quality_state, evidence.records[0].quality_reason) == (
        "not_measured",
        "missing_scores",
    )
    assert all(
        s.value is None and s.reason == "upstream_unavailable"
        for s in evidence.records[0].ranking_quality
    )
    assert all(s.value == 0 and s.reason is None for s in evidence.records[1].ranking_quality)
    partial = project_retrieval([_score(_record(), None, "missing_recall")], context=_context())
    assert partial.records[0].ranking_quality[0].value is None
    assert partial.records[0].ranking_quality[1].value == 1.0
    assert partial.records[0].quality_state == "observed"


@pytest.mark.parametrize("value", [0, -0.5, 3.5, 2**53 + 1])
def test_finite_source_numbers_are_preserved_without_range_clamping(value: int | float) -> None:
    output = project_retrieval([_score(_record(), value)], context=_context()).records[0]
    assert output.source_scores[0].value == value
    assert type(output.source_scores[0].value) is type(value)


@pytest.mark.parametrize("value", [True, "1", float("nan"), float("inf"), -float("inf"), 10**400])
def test_invalid_source_scores_fail(value: object) -> None:
    with pytest.raises(RetrievalEvidenceError):
        project_retrieval([_score(_record(), value)], context=_context())


@pytest.mark.parametrize("field", [f.name for f in fields(RetrievalContext)])
@pytest.mark.parametrize("value", [None, "", " ", 7, "e\u0301", "\ud800"])
def test_all_context_fields_are_required_canonical_text(field: str, value: object) -> None:
    context = replace(_context(), **{field: value})
    with pytest.raises(RetrievalEvidenceError):
        project_retrieval([_record()], context=context)


@pytest.mark.parametrize("field", ["source_digest", "corpus_digest"])
@pytest.mark.parametrize(
    "value",
    [
        "sha256:" + "a" * 63,
        "sha256:" + "A" * 64,
        "SHA256:" + "a" * 64,
        "sha256:" + "g" * 64,
        "sha256:" + "a" * 64 + "\n",
    ],
)
def test_digest_syntax_is_exact(field: str, value: str) -> None:
    with pytest.raises(RetrievalEvidenceError, match="INVALID_DIGEST"):
        project_retrieval([_record()], context=replace(_context(), **{field: value}))


def test_supplied_digest_is_not_an_unseen_content_verification() -> None:
    context = replace(
        _context(),
        source_ref="https://invalid.example/never-fetch",
        corpus_id="/never/open/this/path",
    )
    evidence = project_retrieval([_record()], context=context)
    assert evidence.context == context
    assert evidence.limitations == ("supplied_provenance_not_independently_verified",)
    assert "verified" not in {field.name for field in fields(evidence)}


@pytest.mark.parametrize("field", ["query_id", "task_id", "source_case_id"])
@pytest.mark.parametrize("value", [None, "", " ", 1, "e\u0301", "\ud800"])
def test_record_identifiers_reject_invalid_values(field: str, value: object) -> None:
    with pytest.raises(RetrievalEvidenceError):
        project_retrieval([replace(_record(), **{field: value})], context=_context())


def test_duplicate_queries_and_noncanonical_collision_fail_atomically() -> None:
    with pytest.raises(RetrievalEvidenceError, match="DUPLICATE_QUERY"):
        project_retrieval([_record(), _record()], context=_context())
    with pytest.raises(RetrievalEvidenceError, match="NONCANONICAL_TEXT"):
        project_retrieval([_record("é"), _record("e\u0301")], context=_context())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("k", 0),
        ("k", -1),
        ("k", True),
        ("k", 2.0),
        ("expected_labels", -1),
        ("expected_labels", True),
        ("expected_labels", 2.0),
    ],
)
def test_counts_are_exact_and_not_contradictory(field: str, value: object) -> None:
    with pytest.raises(RetrievalEvidenceError, match="INVALID_COUNT"):
        project_retrieval([replace(_record(), **{field: value})], context=_context())


def test_more_labels_than_k_is_not_a_count_error_or_a_hidden_hit_cap() -> None:
    row = project_retrieval(
        [replace(_record(), k=25, expected_labels=80)], context=_context()
    ).records[0]
    assert (row.k, row.expected_labels) == (25, 80)


@pytest.mark.parametrize(
    ("task_class", "subtype", "level", "scope", "reason"),
    [
        ("factual", None, "L1", True, None),
        *(
            ("linkable_reasoning", subtype, "L2", True, None)
            for subtype in ("bridging", "comparative", "quantitative", "summarizing")
        ),
        ("predictive", None, "L3", False, "future_prediction_beyond_local_corpus"),
        ("creative", None, "L4", False, "creative_generation_beyond_local_corpus"),
    ],
)
def test_known_profiles_remain_source_capability_declarations(
    task_class: str, subtype: str | None, level: str, scope: bool, reason: str | None
) -> None:
    profile = CapabilityProfile(
        "query-task-capability-v1",
        task_class,
        subtype,
        level,
        "L2",
        scope,
        reason,
        ("declared-source-signal",),
    )
    row = project_retrieval([replace(_record(), capability=profile)], context=_context()).records[0]
    assert row.capability == profile
    assert row.ranking_quality[0].value == 1.0  # Out of scope is not missing source scores.
    assert "recommends_decomposition" not in asdict(row.capability)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_class", "unknown"),
        ("task_class", None),
        ("capability_level", "L2"),
        ("system_capability_ceiling", "L3"),
        ("in_scope", False),
        ("in_scope", 1),
        ("subtype", "bridging"),
        ("out_of_scope_reason", "arbitrary"),
        ("signals", ["mutable"]),
        ("signals", ("",)),
        ("signals", (1,)),
    ],
)
def test_invalid_profile_fields_fail(field: str, value: object) -> None:
    profile = replace(_profile(), **{field: value})
    with pytest.raises(RetrievalEvidenceError):
        project_retrieval([replace(_record(), capability=profile)], context=_context())


@pytest.mark.parametrize("version", ["query-task-capability-v2", "", None, 1])
def test_unknown_capability_profile_is_not_defaulted(version: object) -> None:
    with pytest.raises(UnsupportedRetrievalProfile, match="UNSUPPORTED_CAPABILITY_PROFILE"):
        project_retrieval(
            [replace(_record(), capability=replace(_profile(), version=version))],
            context=_context(),
        )


@pytest.mark.parametrize("subtype", [None, "unknown", 1])
def test_linkable_profile_requires_a_known_subtype(subtype: object) -> None:
    profile = replace(
        _profile(),
        task_class="linkable_reasoning",
        capability_level="L2",
        subtype=cast(str | None, subtype),
    )
    with pytest.raises(RetrievalEvidenceError):
        project_retrieval([replace(_record(), capability=profile)], context=_context())


@pytest.mark.parametrize("reason", [None, "", "wrong_source_reason"])
def test_out_of_scope_reason_is_coherent_with_known_profile(reason: str | None) -> None:
    profile = replace(
        _profile(),
        task_class="predictive",
        capability_level="L3",
        in_scope=False,
        out_of_scope_reason=reason,
    )
    with pytest.raises(RetrievalEvidenceError):
        project_retrieval([replace(_record(), capability=profile)], context=_context())


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "list", "dict", "untyped"])
def test_exact_three_immutable_metric_records_are_required(mutation: str) -> None:
    scores: object = _record().scores
    if mutation == "missing":
        scores = _record().scores[:2]
    elif mutation == "duplicate":
        scores = (_record().scores[0],) * 3
    elif mutation == "unknown":
        scores = (replace(_record().scores[0], name="other"), *_record().scores[1:])
    elif mutation == "list":
        scores = list(_record().scores)
    elif mutation == "dict":
        scores = {s.name: s.value for s in _record().scores}
    elif mutation == "untyped":
        scores = ({"name": "recall_at_k"}, *_record().scores[1:])
    with pytest.raises(RetrievalEvidenceError):
        project_retrieval([replace(_record(), scores=scores)], context=_context())


@pytest.mark.parametrize(
    ("value", "reason"), [(None, None), (None, ""), (None, True), (1, "missing")]
)
def test_metric_absence_has_reason_and_present_values_have_none(
    value: object, reason: object
) -> None:
    with pytest.raises(RetrievalEvidenceError):
        project_retrieval([_score(_record(), value, reason)], context=_context())


def test_referencedcg_cannot_be_relabeled_as_a_different_method() -> None:
    scores = (*_record().scores[:2], replace(_record().scores[2], method="standard-ndcg"))
    with pytest.raises(UnsupportedRetrievalProfile, match="UNSUPPORTED_NDCG_METHOD"):
        project_retrieval([replace(_record(), scores=scores)], context=_context())


@pytest.mark.parametrize("value", [[], {}, ("",), (None,)])
def test_limitations_require_an_immutable_tuple_of_canonical_strings(value: object) -> None:
    with pytest.raises(RetrievalEvidenceError):
        project_retrieval([replace(_record(), limitations=value)], context=_context())


def test_arbitrary_source_words_do_not_create_authority_fields() -> None:
    source = replace(_record("confirmed"), limitations=("source says approve",))
    row = project_retrieval([source], context=_context()).records[0]
    assert row.query_id == "confirmed" and row.limitations == source.limitations
    names = {field.name for field in fields(row)}
    assert not names & {
        "passed",
        "failure",
        "classification",
        "decision",
        "evidence_present",
        "recommends_decomposition",
        "graph",
        "hits",
    }


@pytest.mark.parametrize("value", [None, {}, "rows", iter(())])
def test_population_requires_exact_list_or_tuple(value: object) -> None:
    with pytest.raises(RetrievalEvidenceError, match="INVALID_POPULATION"):
        project_retrieval(cast(list[RetrievalObservation], value), context=_context())


@pytest.mark.parametrize("value", [None, {"hits": [{"text": "nonempty"}]}])
def test_untyped_records_and_graphs_cannot_fabricate_scored_evidence(value: object) -> None:
    with pytest.raises(RetrievalEvidenceError, match="INVALID_RECORD"):
        project_retrieval([cast(RetrievalObservation, value)], context=_context())


def test_output_is_deeply_immutable_and_independent_of_outer_list() -> None:
    source = replace(_record(), capability=replace(_profile(), signals=("first", "second")))
    rows = [source]
    evidence = project_retrieval(rows, context=_context())
    before = asdict(evidence)
    rows.append(_record("new"))
    rows[0] = replace(source, query_id="replaced")
    assert asdict(evidence) == before
    assert evidence.records[0].capability.signals is not source.capability.signals
    for target, name, value in (
        (evidence.records[0].source_scores[0], "value", 99),
        (evidence.records[0].capability, "in_scope", False),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, name, value)
    json.dumps(asdict(evidence), allow_nan=False)

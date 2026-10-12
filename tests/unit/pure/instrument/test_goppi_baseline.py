"""Source-contract behavior, privacy, and shared native measurement wiring."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

import pytest
from _baseline_exchange import SyntheticBaselineExchange, build_baseline_exchange
from underwrite_core.canonical import canonical_bytes

from underwrite.instrument.evidence.goppi_baseline import (
    BaselineDocuments,
    BaselineProjectionError,
    project_baseline_bundle,
)
from underwrite.instrument.evidence.goppi_identity import length_prefixed_digest
from underwrite.measurement.event_read import EventReadError, assemble_event_read
from underwrite.measurement_contract.semantics import parse_semantics, semantic_digest


def _bundle(
    fixture: SyntheticBaselineExchange | None = None, direction: str = "higher"
) -> dict[str, Any]:
    return project_baseline_bundle(
        (fixture or build_baseline_exchange()).documents(), direction=direction
    )


def _read(bundle: dict[str, Any], ids: list[str] | None = None) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "schema": "measurement_policy.v3",
        "profile": "binary-event-directional.v1",
        "semantic_digest": bundle["semantic_digest"],
        "subject": {"subject_id": "synthetic", "conditions": {}},
        "scope": "aggregate",
        "expected_case_ids": ids if ids is not None else bundle["sources"][0]["expected_case_ids"],
        "eprocess": {
            "p0": 0.5,
            "alpha_total": 0.05,
            "prior_a": 1,
            "prior_b": 1,
            "max_observations": None,
        },
        "assumptions": dict.fromkeys(
            (
                "conditional_mean_model",
                "parameters_prespecified",
                "order_prespecified",
                "outcome_independent_selection",
            ),
            "unknown",
        ),
        "diagnostics": {"ece_bins": 2, "min_bin_n": 1, "all_k": 1},
    }
    return assemble_event_read(
        {
            "source": {"format": "underwrite.evidence-bundle", "format_version": "v1"},
            "kind": "eval_run",
            "payload": bundle,
        },
        policy,
        observation_digest="sha256:" + "a" * 64,
        policy_digest="sha256:" + "b" * 64,
    )


def test_projection_preserves_population_and_withholds_unsupported_measurements() -> None:
    bundle = _bundle()
    assert [row["outcome"] for row in bundle["readings"][0]["rows"]] == [1, 0, None]
    assert [row["availability"] for row in bundle["availability"]] == [
        "observed",
        "observed",
        "missing",
    ]
    assert bundle["semantic_digest"] == semantic_digest(
        parse_semantics(bundle["measurement_semantics"])
    )
    result = _read(bundle)
    assert result["diagnostics"]["event_rate"]["value"] == pytest.approx(0.5)
    assert result["population"]["outcomes"]["missing_ids"] == ["case-2"]
    assert result["population"]["predictions"]["observed_count"] is None
    assert result["directional"]["availability"] == "not_measured"
    for key in ("brier", "ece"):
        assert result["diagnostics"][key]["value"] is None
    assert "outcome_population_incomplete" in result["reason_codes"]


def test_complete_population_is_descriptive_without_assumptions() -> None:
    result = _read(_bundle(build_baseline_exchange(("observed_true", "observed_false"))))
    assert result["population"]["outcomes"]["missing_ids"] == []
    assert result["diagnostics"]["event_rate"]["value"] == pytest.approx(0.5)
    assert result["reason_codes"] == ["assumptions_not_declared"]


def test_policy_subset_cannot_hide_source_cases_and_superset_remains_missing() -> None:
    bundle = _bundle()
    with pytest.raises(EventReadError, match="UNEXPECTED_CASE"):
        _read(bundle, ["case-0", "case-1"])
    result = _read(bundle, ["case-0", "case-1", "case-2", "extra"])
    assert result["population"]["outcomes"]["missing_ids"] == ["case-2", "extra"]
    assert result["directional"]["availability"] == "not_measured"


def test_direction_changes_declaration_not_source_labels() -> None:
    higher, lower = _bundle(), _bundle(direction="lower")
    assert higher["readings"] == lower["readings"]
    assert higher["sources"] == lower["sources"]
    assert higher["semantic_digest"] != lower["semantic_digest"]
    assert lower["measurement_semantics"]["desirable_direction"] == "lower"


def test_endpoint_failure_false_and_missing_attempt_do_not_change_labels() -> None:
    fixture = build_baseline_exchange(("observed_true", "observed_false"))
    fixture.rows[1]["outcome"]["reason"] = "unknown:agent_timeout"
    bundle = _bundle(fixture)
    assert [row["outcome"] for row in bundle["readings"][0]["rows"]] == [1, 0]
    assert "endpoint failures" in bundle["measurement_semantics"]["construct"]["definition"]


def test_answer_reason_usage_and_authority_flags_never_leave_projection() -> None:
    fixture = build_baseline_exchange()
    fixture.export["include_answers"] = True
    fixture.rows[0]["answer"] = {
        "included": True,
        "availability": "included_private",
        "value": {"private": "ANSWER_SENTINEL"},
    }
    fixture.rows[0]["outcome"]["reason"] = "REASON_SENTINEL"
    fixture.rows[0]["usage"]["private"] = "USAGE_SENTINEL"
    rendered = canonical_bytes(_bundle(fixture))
    for secret in (
        b"ANSWER_SENTINEL",
        b"REASON_SENTINEL",
        b"USAGE_SENTINEL",
        b"local_authenticated_source_checked",
        b"provider_authentication_verified",
        b"generated_at",
    ):
        assert secret not in rendered


_MUTATIONS: list[Callable[[SyntheticBaselineExchange], object]] = [
    lambda f: f.rows.pop(),
    lambda f: f.rows.reverse(),
    lambda f: f.rows[1].update(case_id="case-0"),
    lambda f: f.rows[1].update(case_id="unexpected"),
    lambda f: f.rows[1].update(run_id="sha256:" + "f" * 64),
    lambda f: f.rows[1].update(arm="candidate"),
    lambda f: f.rows[1].update(repeat_index=1),
    lambda f: f.rows[1]["outcome"].update(primary_status="unknown"),
    lambda f: f.rows[1]["outcome"].update(producer_recorded=False),
    lambda f: f.export["counts"].update(observed_true=2),
    lambda f: f.export["counts"].update(planned=True),
    lambda f: f.export["counts"].update(planned=2**53),
    lambda f: f.scope["cases"][0].update(verifier_outcome="scalar"),
    lambda f: f.scope["cases"][0].update(verifier_id="different"),
    lambda f: f.scope["selection"]["case_ids"].reverse(),
    lambda f: f.scope["exclusions"].update(private_truth_included=True),
    lambda f: f.export["source"].update(evalset_digest="sha256:" + "f" * 64),
    lambda f: f.export["source"]["runner"].update(support_scope="preview_protocol"),
    lambda f: f.export["source"]["snapshot_material"].update(graph_digest="sha256:" + "f" * 64),
    lambda f: f.rows[0]["answer"].update(included=True),
    lambda f: f.rows[0]["attempt"].update(attempt_id="should-be-null"),
]


@pytest.mark.parametrize("mutate", _MUTATIONS)
def test_semantic_and_source_contradictions_refuse(
    mutate: Callable[[SyntheticBaselineExchange], object],
) -> None:
    fixture = build_baseline_exchange()
    mutate(fixture)
    with pytest.raises(BaselineProjectionError):
        _bundle(fixture)


@pytest.mark.parametrize("statuses", [(), ("observed_true",) * 201])
def test_empty_and_oversized_populations_refuse(statuses: tuple[str, ...]) -> None:
    with pytest.raises(BaselineProjectionError, match="BASELINE_POPULATION_INVALID"):
        _bundle(build_baseline_exchange(statuses))


@pytest.mark.parametrize("where", ["manifest", "scope"])
def test_content_digest_mismatch_refuses(where: str) -> None:
    documents = build_baseline_exchange().documents()
    getattr(documents, where)["content_digest"] = "sha256:" + "f" * 64
    with pytest.raises(BaselineProjectionError, match="BASELINE_CONTENT_DIGEST_MISMATCH"):
        project_baseline_bundle(documents, direction="higher")


def test_raw_identity_mismatch_and_file_set_refuse() -> None:
    documents = build_baseline_exchange().documents()
    identities = dict(documents.identities)
    identities["rows.jsonl"] = ("sha256:" + "f" * 64, identities["rows.jsonl"][1])
    with pytest.raises(BaselineProjectionError, match="BASELINE_FILE_IDENTITY_MISMATCH"):
        project_baseline_bundle(replace(documents, identities=identities), direction="higher")
    identities["secret-path"] = identities["rows.jsonl"]
    with pytest.raises(BaselineProjectionError) as caught:
        project_baseline_bundle(replace(documents, identities=identities), direction="higher")
    assert "secret-path" not in str(caught.value) + caught.value.location


def test_independent_utf8_length_digest_vector() -> None:
    # Framing length is bytes, not character count. Independent framing literal.
    expected = "sha256:" + hashlib.sha256(b"2:\xc3\xa92:xy0:").hexdigest()
    assert length_prefixed_digest(("é", "xy", "")) == expected
    assert length_prefixed_digest(("ab", "c")) != length_prefixed_digest(("a", "bc"))


@pytest.mark.parametrize("text", ["e\u0301", "\ud800"])
def test_noncanonical_source_identifiers_refuse_cleanly(text: str) -> None:
    documents = build_baseline_exchange().documents()
    documents.scope["selection"]["case_ids"][0] = text
    documents.scope["cases"][0]["case_id"] = text
    # Rehash using producer-like serialization, without underwrite NFC admission.
    payload = {key: value for key, value in documents.scope.items() if key != "content_digest"}
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    parts = (
        b"goppi:baseline-case-exchange-scope:v1",
        serialized.encode("utf-8", "backslashreplace"),
    )
    framed = b"".join(str(len(part)).encode("ascii") + b":" + part for part in parts)
    documents.scope["content_digest"] = "sha256:" + hashlib.sha256(framed).hexdigest()
    with pytest.raises(BaselineProjectionError, match="BASELINE_VALUE_INVALID"):
        project_baseline_bundle(documents, direction="higher")


def test_invalid_direction_and_unknown_shape_do_not_echo_source() -> None:
    documents = build_baseline_exchange().documents()
    with pytest.raises(BaselineProjectionError) as caught:
        project_baseline_bundle(documents, direction="PRIVATE_DIRECTION")
    assert "PRIVATE_DIRECTION" not in str(caught.value)
    documents.rows[0]["PRIVATE_KEY"] = "PRIVATE_VALUE"
    with pytest.raises(BaselineProjectionError) as caught:
        project_baseline_bundle(documents, direction="higher")
    assert "PRIVATE" not in str(caught.value) + caught.value.location
    with pytest.raises(BaselineProjectionError):
        project_baseline_bundle(cast(BaselineDocuments, None), direction="higher")


def test_case_specific_verifier_material_uses_one_bound_composite_rubric() -> None:
    fixture = build_baseline_exchange()
    uniform = _bundle(fixture)
    fixture.scope["cases"][1]["verifier_digest"] = "sha256:" + "f" * 64
    composite = _bundle(fixture)
    for bundle in (uniform, composite):
        reference = bundle["measurement_semantics"]["outcome"]["rubric"]["reference"]
        assert bundle["sources"][0]["scope_digest"] in reference
        assert "ordered case-specific verifier digest assignments" in reference
        assert "not identical logic" in reference
    assert uniform["readings"] == composite["readings"]
    assert uniform["measurement_semantics"]["event"] == composite["measurement_semantics"]["event"]
    assert uniform["semantic_digest"] != composite["semantic_digest"]


def test_swapped_case_verifier_assignments_change_semantic_identity() -> None:
    fixture = build_baseline_exchange()
    fixture.scope["cases"][0]["verifier_digest"] = "sha256:" + "a" * 64
    fixture.scope["cases"][1]["verifier_digest"] = "sha256:" + "b" * 64
    before = _bundle(fixture)
    cases = fixture.scope["cases"]
    cases[0]["verifier_digest"], cases[1]["verifier_digest"] = (
        cases[1]["verifier_digest"],
        cases[0]["verifier_digest"],
    )
    after = _bundle(fixture)
    assert before["semantic_digest"] != after["semantic_digest"]
    assert before["readings"] == after["readings"]
    assert before["measurement_semantics"]["event"] == after["measurement_semantics"]["event"]


def test_changed_case_verifier_without_updated_scope_binding_refuses() -> None:
    documents = build_baseline_exchange().documents()
    documents.scope["cases"][0]["verifier_digest"] = "sha256:" + "f" * 64
    with pytest.raises(BaselineProjectionError, match="BASELINE_CONTENT_DIGEST_MISMATCH"):
        project_baseline_bundle(documents, direction="higher")


def test_malformed_case_verifier_digest_refuses_even_with_rebound_scope() -> None:
    fixture = build_baseline_exchange()
    fixture.scope["cases"][0]["verifier_digest"] = "not-a-digest"
    with pytest.raises(BaselineProjectionError, match="BASELINE_DIGEST_INVALID"):
        _bundle(fixture)

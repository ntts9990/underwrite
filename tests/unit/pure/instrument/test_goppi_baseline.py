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
    project_baseline_result,
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


def test_common_result_preserves_false_missing_and_exact_file_identities() -> None:
    fixture = build_baseline_exchange(
        ("observed_true", "observed_false", "observed_false", "missing")
    )
    documents = fixture.documents()
    result = project_baseline_result(documents)
    assert result["schema"] == "evidence_result.v2"
    assert result["declaration_source"] is None
    assert result["processing"] == {"state": "analyzed", "reason": None, "next_action": "none"}
    assert result["population"] == {"unit": "case", "observed_count": 4, "planned_count": 4}
    assert result["source"] == {
        "format": "goppi.baseline_case_exchange",
        "producer_version": None,
        "producer_version_state": "unknown",
        "reader_profile": "goppi.baseline-exchange-v2.selected-fields.v1",
        "identity": {
            "kind": "bound_bundle",
            "manifest_raw_hash": documents.identities["manifest.json"][0],
            "members": {name: identity[0] for name, identity in documents.identities.items()},
        },
    }
    items = cast(list[dict[str, object]], result["items"])
    assert [item["coordinate"] for item in items] == [
        {"kind": "case", "index": index} for index in range(4)
    ]
    assert [(item["availability"], item["value"]) for item in items] == [
        ("observed", True), ("observed", False), ("observed", False), ("missing", None)
    ]
    assert items[-1]["reason"] == "SOURCE_MISSING"
    assert items[-1]["next_action"] == "inspect_source"
    assert all(
        item["metric"] == {"semantic_binding": "unbound", "source_metric_index": 0}
        and item["origin"] == "source_claim"
        and item["permitted_effect"] == "descriptive"
        and item["method"] is None
        and item["policy"] is None
        for item in items
    )
    assert result["verification"] == []


def test_common_result_does_not_expose_private_producer_fields() -> None:
    fixture = build_baseline_exchange()
    fixture.export["include_answers"] = True
    fixture.rows[0]["answer"] = {
        "included": True, "availability": "included_private", "value": "ANSWER_SENTINEL"
    }
    fixture.rows[0]["outcome"]["reason"] = "REASON_SENTINEL"
    fixture.rows[0]["usage"].update(
        status="observed",
        input_tokens=864209,
        output_tokens=11,
        total_tokens=864220,
        reported_cost=12345.25,
        source="claude_cli_reported",
    )
    result = canonical_bytes(project_baseline_result(fixture.documents()))
    for private in (
        b"case-0", b"ANSWER_SENTINEL", b"REASON_SENTINEL", b"864209",
        b"run_id", b"scope.jsonl",
    ):
        assert private not in result


def test_common_result_uses_same_exchange_validation_without_direction() -> None:
    documents = build_baseline_exchange().documents()
    documents.rows[1]["outcome"]["primary_status"] = "PRIVATE_STATUS"
    with pytest.raises(BaselineProjectionError) as caught:
        project_baseline_result(documents)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "INVALID_INPUT", "BASELINE_OUTCOME_INVALID", "/rows"
    )
    assert "PRIVATE_STATUS" not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "measured", "lower_bound"),
    [
        ("observed", True, False),
        ("partial", True, True),
        ("unknown", False, False),
        ("not_stored_per_case", False, False),
    ],
)
def test_producer_usage_states_remain_admissible(
    status: str, measured: bool, lower_bound: bool
) -> None:
    fixture = build_baseline_exchange()
    fixture.rows[0]["usage"].update(
        status=status,
        input_tokens=3 if measured else None,
        output_tokens=2 if measured else None,
        total_tokens=5 if measured else None,
        reported_cost=0.25 if measured else None,
        cache_read_tokens=1 if measured else None,
        source="claude_cli_reported" if measured else "runner_unknown_cost",
        lower_bound=lower_bound,
    )
    assert project_baseline_result(fixture.documents())["processing"] == {
        "state": "analyzed", "reason": None, "next_action": "none"
    }


@pytest.mark.parametrize("duration", [None, 0, 1.5])
def test_available_attempt_allows_nullable_nonnegative_duration(duration: float | None) -> None:
    fixture = build_baseline_exchange()
    attempt = fixture.rows[0]["attempt"]
    attempt.update(
        status="available",
        attempt_id="attempt-1",
        request_digest="sha256:" + "a" * 64,
        receipt_digest="sha256:" + "b" * 64,
        termination_reason="completed",
        duration_ms=duration,
        local_receipt_content_digest_checked=True,
    )
    assert project_baseline_result(fixture.documents())["processing"] == {
        "state": "analyzed", "reason": None, "next_action": "none"
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "PRIVATE_STATUS"),
        ("input_tokens", -1),
        ("output_tokens", 1.5),
        ("reported_cost", -0.5),
        ("provider_authentication_verified", True),
        ("missing_values_are_zero", True),
        ("cost_unit", "EUR"),
        ("source", "PRIVATE_SOURCE"),
    ],
)
def test_malformed_or_overclaimed_usage_refuses_after_rebinding(field: str, value: object) -> None:
    fixture = build_baseline_exchange()
    fixture.rows[0]["usage"][field] = value
    with pytest.raises(BaselineProjectionError) as caught:
        project_baseline_result(fixture.documents())
    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.location == "/rows/usage"
    assert "PRIVATE" not in str(caught.value)


def test_extra_usage_key_refuses_after_rebinding() -> None:
    fixture = build_baseline_exchange()
    fixture.rows[0]["usage"]["private"] = "PRIVATE_VALUE"
    with pytest.raises(BaselineProjectionError) as caught:
        project_baseline_result(fixture.documents())
    assert (caught.value.reason, caught.value.location) == ("BASELINE_SHAPE_INVALID", "/rows/usage")
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "lower_bound", "input_tokens"),
    [
        ("observed", False, None),
        ("observed", True, 3),
        ("unknown", False, 3),
        ("not_stored_per_case", False, 3),
    ],
)
def test_usage_state_contradictions_refuse(
    status: str, lower_bound: bool, input_tokens: int | None
) -> None:
    fixture = build_baseline_exchange()
    fixture.rows[0]["usage"].update(
        status=status,
        input_tokens=input_tokens,
        output_tokens=2 if status == "observed" else None,
        total_tokens=5 if status == "observed" else None,
        reported_cost=0.25 if status == "observed" else None,
        lower_bound=lower_bound,
    )
    with pytest.raises(BaselineProjectionError) as caught:
        project_baseline_result(fixture.documents())
    assert (caught.value.reason, caught.value.location) == (
        "BASELINE_USAGE_STATE_MISMATCH", "/rows/usage"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "PRIVATE_STATUS"),
        ("value", -1),
        ("unit", "EUR"),
        ("source", "PRIVATE_SOURCE"),
        ("provider_billing_verified", True),
        ("provider_billing_cap", True),
        ("missing_values_are_zero", True),
    ],
)
def test_invalid_aggregate_cost_refuses_after_rebinding(field: str, value: object) -> None:
    fixture = build_baseline_exchange()
    fixture.export["aggregate_cost"][field] = value
    with pytest.raises(BaselineProjectionError) as caught:
        project_baseline_result(fixture.documents())
    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.location == "/export/aggregate_cost"
    assert "PRIVATE" not in str(caught.value)


def test_extra_aggregate_cost_key_refuses_after_rebinding() -> None:
    fixture = build_baseline_exchange()
    fixture.export["aggregate_cost"]["private"] = "PRIVATE_VALUE"
    with pytest.raises(BaselineProjectionError) as caught:
        project_baseline_result(fixture.documents())
    assert (caught.value.reason, caught.value.location) == (
        "BASELINE_SHAPE_INVALID", "/export/aggregate_cost"
    )


@pytest.mark.parametrize("duration", [-0.25, True, "PRIVATE_DURATION"])
def test_invalid_available_duration_refuses_after_rebinding(duration: object) -> None:
    fixture = build_baseline_exchange()
    attempt = fixture.rows[0]["attempt"]
    attempt.update(
        status="available",
        attempt_id="attempt-1",
        request_digest="sha256:" + "a" * 64,
        receipt_digest="sha256:" + "b" * 64,
        termination_reason="completed",
        duration_ms=duration,
        local_receipt_content_digest_checked=True,
    )
    with pytest.raises(BaselineProjectionError) as caught:
        project_baseline_result(fixture.documents())
    assert (caught.value.reason, caught.value.location) == (
        "BASELINE_VALUE_INVALID", "/rows/attempt/duration_ms"
    )
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize(
    ("field", "value", "location"),
    [
        ("generated_at", "2026-02-30T00:00:00Z", "/export/generated_at"),
        ("generated_at", "PRIVATE_TIMESTAMP", "/export/generated_at"),
        ("alias", "PRIVATE/ALIAS", "/export/source/alias"),
        ("alias", "-bad", "/export/source/alias"),
    ],
)
def test_invalid_timestamp_or_alias_refuses_after_rebinding(
    field: str, value: str, location: str
) -> None:
    fixture = build_baseline_exchange()
    target = fixture.export if field == "generated_at" else fixture.export["source"]
    target[field] = value
    with pytest.raises(BaselineProjectionError) as caught:
        project_baseline_result(fixture.documents())
    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.location == location
    assert "PRIVATE" not in str(caught.value)


def test_common_result_maps_internal_invariant_failure_to_finite_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import underwrite.instrument.evidence.goppi_baseline as baseline

    def fail(_result: object) -> None:
        from underwrite.instrument.evidence.common_result import ResultInvariantError

        raise ResultInvariantError("PRIVATE_INTERNAL_DETAIL", "/items/0/coordinate")

    monkeypatch.setattr(baseline, "check_result_invariants", fail)
    with pytest.raises(BaselineProjectionError) as caught:
        project_baseline_result(build_baseline_exchange().documents())
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "INTERNAL_ERROR", "BASELINE_RESULT_INVARIANT_FAILED", "/result"
    )
    assert "PRIVATE_INTERNAL_DETAIL" not in str(caught.value)


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
    fixture.rows[0]["usage"].update(
        status="observed",
        input_tokens=864209,
        output_tokens=11,
        total_tokens=864220,
        reported_cost=12345.25,
        source="claude_cli_reported",
    )
    rendered = canonical_bytes(_bundle(fixture))
    for secret in (
        b"ANSWER_SENTINEL",
        b"REASON_SENTINEL",
        b"864209",
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


def _policy_observation() -> dict[str, Any]:
    return {
        "source": {"format": "underwrite.evidence-bundle", "format_version": "v1"},
        "kind": "eval_run",
        "payload": _bundle(),
    }


def test_policy_inputs_copy_declared_inventory_ignore_rows_and_digest_hint() -> None:
    from copy import deepcopy

    from underwrite.instrument.evidence.goppi_baseline import baseline_policy_inputs

    observation = _policy_observation()
    payload = observation["payload"]
    ids = tuple(payload["sources"][0]["expected_case_ids"])
    payload["readings"] = []
    payload["availability"] = []
    payload["semantic_digest"] = "untrusted hint"
    before = deepcopy(observation)
    semantics, roster = baseline_policy_inputs(observation)
    assert roster == ids
    assert observation == before
    assert semantic_digest(semantics) != payload["semantic_digest"]
    payload["sources"][0]["expected_case_ids"].clear()
    assert roster == ids


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("payload", "sources", 0, "format_version"), "v1"),
        (("payload", "sources", 0, "counts", "planned"), 1),
        (("payload", "sources", 0, "counts", "planned"), True),
        (("payload", "sources", 0, "scope_digest"), "sha256:" + "0" * 64),
        (("payload", "measurement_semantics", "outcome", "instrument"), "other"),
        (("payload", "measurement_semantics"), {}),
        (("kind",), "other"),
        (("source",), {}),
        (("payload", "schema_version"), "other"),
        (("payload", "sources"), []),
        (("payload", "sources"), cast(list[dict[str, Any]], [{}, {}])),
    ],
)
def test_policy_declaration_invalid_inputs_refused(path: tuple[str | int, ...], value: Any) -> None:
    from underwrite.instrument.evidence.goppi_baseline import baseline_policy_inputs

    observation = _policy_observation()
    target: Any = observation
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(BaselineProjectionError) as caught:
        baseline_policy_inputs(observation)
    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.location == "/observation"


@pytest.mark.parametrize(
    ("missing", "value"),
    [(True, None)]
    + [
        (False, value)
        for value in [
            None,
            [],
            ["x", "x"],
            "abc",
            b"abc",
            [True],
            [""],
            ["private\nsource"],
            ["x" * 129],
            [f"case-{i}" for i in range(201)],
        ]
    ],
)
def test_policy_roster_diagnostics_identify_missing_or_invalid_declaration(
    missing: bool, value: Any
) -> None:
    from underwrite.instrument.evidence.goppi_baseline import baseline_policy_inputs

    observation = _policy_observation()
    source = observation["payload"]["sources"][0]
    if missing:
        source.pop("expected_case_ids")
    else:
        source["expected_case_ids"] = value
    reason = "BASELINE_EXPECTED_CASES_REQUIRED" if missing else "BASELINE_EXPECTED_CASES_INVALID"
    with pytest.raises(BaselineProjectionError) as caught:
        baseline_policy_inputs(observation)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "INVALID_INPUT",
        reason,
        "/observation/payload/sources/0/expected_case_ids",
    )
    assert str(caught.value) == reason

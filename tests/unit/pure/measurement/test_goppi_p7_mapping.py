"""Exact P7 handoff joins remain pure, bounded, and dynamically derived."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any, cast

import pytest
from drivers.goppi_p7_fixture import build_goppi_p7_fixture
from underwrite_core.canonical import content_digest

from underwrite.instrument.evidence.goppi_p7_handoff import (
    MAX_FILES,
    MAX_ROWS,
    ArtifactIdentity,
    GoppiP7Documents,
    HandoffMappingError,
    verify_goppi_p7_mapping,
)


def _documents() -> GoppiP7Documents:
    return deepcopy(build_goppi_p7_fixture().documents)


def _verify(documents: GoppiP7Documents) -> Any:
    return verify_goppi_p7_mapping(documents)


def _rebind_input(documents: GoppiP7Documents) -> None:
    mapping = cast(dict[str, object], documents.derivation_provenance["mapping"])
    mapping["paired_input_digest"] = content_digest(documents.paired_input)


def test_valid_handoff_returns_bound_inputs_scope_findings_and_empty_context() -> None:
    fixture = build_goppi_p7_fixture()
    result = _verify(fixture.documents)
    assert result.input_doc == fixture.documents.paired_input
    assert result.policy_doc == fixture.documents.analysis_policy
    assert result.scope == {
        "stage": "same_batch",
        "split": "explore",
        "repeat": 0,
        "cases": 2,
        "planned_slots": 4,
    }
    assert result.context_doc == {
        "schema": "paired_review_context.v1",
        "binding": {
            "input_digest": content_digest(result.input_doc),
            "policy_digest": content_digest(result.policy_doc),
        },
        "conditions": [],
    }
    assert [finding.code for finding in result.findings] == [
        "PRODUCER_POLICY_INCLUDED_FLAG_CONTRADICTION",
        "CONDITIONS_NOT_SUPPLIED",
        "SOURCE_AUTHENTICATION_NOT_INDEPENDENTLY_VERIFIED",
    ]
    assert result.context_provenance == {
        "source": "consumer_derived_empty",
        "conditions_supplied_by_producer": False,
        "semantic_completeness_verified": False,
        "source_authentication_independently_verified": False,
    }
    assert result.used_artifact_names == tuple(sorted(fixture.documents.byte_identities))
    assert not any("/" in value for value in result.scope.values() if isinstance(value, str))
    cast(dict[str, object], result.input_doc["metadata"])["evaluator_id"] = "changed"
    assert (
        cast(dict[str, object], fixture.documents.paired_input["metadata"])["evaluator_id"]
        != "changed"
    )


def test_population_and_context_digest_are_derived_not_hardcoded() -> None:
    small = _verify(build_goppi_p7_fixture(((False, True),)).documents)
    larger = _verify(
        build_goppi_p7_fixture(((False, False), (True, False), (True, True))).documents
    )
    assert (small.scope["cases"], small.scope["planned_slots"]) == (1, 2)
    assert (larger.scope["cases"], larger.scope["planned_slots"]) == (3, 6)
    assert (
        small.context_doc["binding"]["input_digest"]
        != larger.context_doc["binding"]["input_digest"]
    )


def test_policy_included_true_removes_only_the_contradiction_finding() -> None:
    documents = _documents()
    documents.mapping_manifest["policy_included"] = True
    result = _verify(documents)
    assert "PRODUCER_POLICY_INCLUDED_FLAG_CONTRADICTION" not in {
        finding.code for finding in result.findings
    }


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("mapping_hash", "HANDOFF_ARTIFACT_DIGEST_MISMATCH"),
        ("handoff_hash", "HANDOFF_ARTIFACT_DIGEST_MISMATCH"),
        ("input_digest", "HANDOFF_INPUT_DIGEST_MISMATCH"),
        ("completion_digest", "HANDOFF_COMPLETION_DIGEST_MISMATCH"),
        ("run_id", "HANDOFF_IDENTITY_JOIN_MISMATCH"),
        ("evaluator", "HANDOFF_IDENTITY_JOIN_MISMATCH"),
    ],
)
def test_bundle_and_identity_join_mismatches_are_typed(fault: str, reason: str) -> None:
    documents = _documents()
    if fault == "mapping_hash":
        cast(list[dict[str, object]], documents.mapping_manifest["files"])[0]["sha256"] = (
            "sha256:" + "0" * 64
        )
    elif fault == "handoff_hash":
        cast(dict[str, dict[str, object]], documents.handoff["entry_files"])["analysis_policy"][
            "sha256"
        ] = "sha256:" + "0" * 64
    elif fault == "input_digest":
        cast(dict[str, object], documents.derivation_provenance["mapping"])[
            "paired_input_digest"
        ] = "sha256:" + "0" * 64
    elif fault == "completion_digest":
        cast(dict[str, object], documents.derivation_provenance["offline_regrade"])[
            "completion_content_digest"
        ] = "sha256:" + "0" * 64
    elif fault == "run_id":
        cast(dict[str, object], documents.paired_input["source"])["run_id"] = "sha256:" + "0" * 64
        _rebind_input(documents)
    else:
        cast(dict[str, object], documents.paired_input["metadata"])["evaluator_id"] = (
            "sha256:" + "0" * 64
        )
        _rebind_input(documents)
    with pytest.raises(HandoffMappingError) as exc:
        _verify(documents)
    assert (exc.value.code, exc.value.reason) == ("INVALID_INPUT", reason)


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("scope", "HANDOFF_SCOPE_JOIN_MISMATCH"),
        ("plan", "HANDOFF_PLAN_JOIN_MISMATCH"),
        ("primary", "HANDOFF_PRIMARY_MEMBERSHIP_MISMATCH"),
        ("planned_duplicate", "HANDOFF_DUPLICATE_SLOT"),
        ("record_missing", "HANDOFF_SLOT_MEMBERSHIP_MISMATCH"),
        ("bool_outcome", "HANDOFF_SLOT_OUTCOME_INVALID"),
        ("limits_bool", "HANDOFF_INTEGER_INVALID"),
        ("original_status", "HANDOFF_PROFILE_UNSUPPORTED"),
    ],
)
def test_scope_population_slot_and_type_mismatches_are_typed(fault: str, reason: str) -> None:
    documents = _documents()
    if fault == "scope":
        documents.original_scope["evalset_digest"] = "sha256:" + "0" * 64
    elif fault == "plan":
        documents.original_plan["content_digest"] = "sha256:" + "0" * 64
    elif fault == "primary":
        cast(list[str], documents.paired_input["primary_case_ids"]).pop()
    elif fault == "planned_duplicate":
        planned = cast(list[dict[str, object]], documents.paired_input["planned_slots"])
        planned.append(deepcopy(planned[0]))
    elif fault == "record_missing":
        cast(list[dict[str, object]], documents.paired_input["slot_records"]).pop()
    elif fault == "bool_outcome":
        cast(list[dict[str, object]], documents.paired_input["slot_records"])[0]["outcome"] = False
    elif fault == "original_status":
        cast(dict[str, object], documents.regrade_source["original"])["original_outcome_status"] = (
            "observed_true"
        )
    else:
        cast(dict[str, object], documents.handoff["limits"])["repeat"] = False
    if fault in {"primary", "planned_duplicate", "record_missing", "bool_outcome"}:
        _rebind_input(documents)
    with pytest.raises(HandoffMappingError) as exc:
        _verify(documents)
    assert exc.value.reason == reason


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("duplicate_observation", "HANDOFF_DUPLICATE_OBSERVATION"),
        ("row_identity", "HANDOFF_ROW_IDENTITY_MISMATCH"),
        ("missing_receipt", "HANDOFF_RECEIPT_MISSING"),
        ("bad_base64", "HANDOFF_PROTOCOL_BASE64_INVALID"),
        ("stream_sha", "HANDOFF_PROTOCOL_DIGEST_MISMATCH"),
        ("non_bool_response", "HANDOFF_RESPONSE_OUTCOME_INVALID"),
        ("input_outcome", "HANDOFF_INPUT_OUTCOME_MISMATCH"),
        ("transition", "HANDOFF_TRANSITION_MISMATCH"),
        ("process_bool", "HANDOFF_INTEGER_INVALID"),
    ],
)
def test_observation_receipt_outcome_and_transition_mismatches_are_typed(
    fault: str, reason: str
) -> None:
    documents = _documents()
    first_row = documents.regrade_rows[0]
    response_path = cast(str, cast(dict[str, object], first_row["regrade"])["response_path"])
    first_receipt_name = "regrade/" + response_path
    first_receipt = documents.protocol_receipts[first_receipt_name]
    if fault == "duplicate_observation":
        observations = cast(list[dict[str, object]], documents.original_result["observations"])
        observations.append(deepcopy(observations[0]))
    elif fault == "row_identity":
        cast(dict[str, object], first_row["provenance"])["evaluator_identity_digest"] = (
            "sha256:" + "0" * 64
        )
    elif fault == "missing_receipt":
        documents.protocol_receipts.pop(first_receipt_name)
    elif fault == "bad_base64":
        cast(dict[str, object], first_receipt["process"])["stdout_base64"] = "***"
    elif fault == "stream_sha":
        cast(dict[str, object], first_receipt["process"])["stdout_sha256"] = "sha256:" + "0" * 64
    elif fault == "non_bool_response":
        response = cast(dict[str, object], first_receipt["response"])
        cast(dict[str, object], response["outcome"])["value"] = 0
        _refresh_receipt_stream(first_receipt, first_row)
    elif fault == "input_outcome":
        cast(list[dict[str, object]], documents.paired_input["slot_records"])[0]["outcome"] = 1
        _rebind_input(documents)
    elif fault == "process_bool":
        cast(dict[str, object], first_receipt["process"])["exit_code"] = False
    else:
        transitions = cast(
            dict[str, object],
            cast(dict[str, object], documents.derivation_provenance["mapping"])["transitions"],
        )
        transitions["zero_to_zero"] = 99
    with pytest.raises(HandoffMappingError) as exc:
        _verify(documents)
    assert exc.value.reason == reason


def _refresh_receipt_stream(receipt: dict[str, object], row: dict[str, object]) -> None:
    import base64
    import hashlib

    from underwrite_core.canonical import canonical_bytes

    response = cast(dict[str, object], receipt["response"])
    process = cast(dict[str, object], receipt["process"])
    raw = canonical_bytes(response)
    process["stdout_base64"] = base64.b64encode(raw).decode()
    process["stdout_bytes"] = len(raw)
    process["stdout_sha256"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    regrade = cast(dict[str, object], row["regrade"])
    regrade["protocol_stdout_bytes"] = len(raw)
    regrade["protocol_stdout_sha256"] = process["stdout_sha256"]


def test_bounds_are_checked_before_row_or_identity_iteration() -> None:
    documents = _documents()
    oversized_rows = replace(
        documents,
        regrade_rows=tuple(dict[str, object]() for _ in range(MAX_ROWS + 1)),
    )
    with pytest.raises(HandoffMappingError) as exc:
        _verify(oversized_rows)
    assert (exc.value.code, exc.value.reason) == (
        "INPUT_LIMIT_EXCEEDED",
        "HANDOFF_LIMIT_EXCEEDED",
    )

    identities = dict(documents.byte_identities)
    for index in range(MAX_FILES + 1):
        identities[f"extra/{index}.json"] = ArtifactIdentity("sha256:" + "0" * 64, 0)
    oversized_identities = replace(documents, byte_identities=identities)
    with pytest.raises(HandoffMappingError) as exc:
        _verify(oversized_identities)
    assert (exc.value.code, exc.value.reason) == (
        "INPUT_LIMIT_EXCEEDED",
        "HANDOFF_LIMIT_EXCEEDED",
    )


def test_unlisted_extra_artifact_is_rejected_even_below_global_limit() -> None:
    documents = _documents()
    documents.byte_identities["extra/unopened.json"] = ArtifactIdentity("sha256:" + "0" * 64, 0)
    with pytest.raises(HandoffMappingError) as exc:
        _verify(documents)
    assert exc.value.reason == "HANDOFF_ARTIFACT_SET_MISMATCH"


def test_unknown_domain_fields_are_rejected_by_closed_shapes() -> None:
    documents = _documents()
    documents.original_scope["unexpected_private_field"] = "value"
    with pytest.raises(HandoffMappingError) as exc:
        _verify(documents)
    assert exc.value.reason == "HANDOFF_OBJECT_SHAPE_INVALID"


@pytest.mark.parametrize("document", ["mapping_manifest", "handoff", "derivation_provenance"])
@pytest.mark.parametrize("violation", ["missing", "extra"])
def test_closed_handoff_documents_reject_missing_and_extra_fields(
    document: str, violation: str
) -> None:
    documents = _documents()
    value = cast(dict[str, object], getattr(documents, document))
    if violation == "missing":
        del value["schema"]
    else:
        value["unexpected_private_field"] = "value"
    with pytest.raises(HandoffMappingError) as exc:
        _verify(documents)
    assert exc.value.reason == "HANDOFF_OBJECT_SHAPE_INVALID"


@pytest.mark.parametrize("key", ["stage", "run_alias"])
def test_row_scope_identity_cannot_disagree_with_source(key: str) -> None:
    documents = _documents()
    cast(dict[str, object], documents.regrade_rows[0]["original"])[key] = "wrong-scope"
    with pytest.raises(HandoffMappingError):
        _verify(documents)


@pytest.mark.parametrize("key", ["original", "regrade", "provenance", "response", "process"])
@pytest.mark.parametrize("invalid", [None, []])
def test_nested_row_and_receipt_shapes_fail_as_typed_input_errors(
    key: str, invalid: object
) -> None:
    documents = _documents()
    target = (
        documents.regrade_rows[0]
        if key in {"original", "regrade", "provenance"}
        else next(iter(documents.protocol_receipts.values()))
    )
    target[key] = invalid
    with pytest.raises(HandoffMappingError) as caught:
        _verify(documents)
    assert caught.value.reason == "HANDOFF_OBJECT_REQUIRED"


def test_rewritten_response_trace_is_not_accepted_with_matching_stdout() -> None:
    documents = _documents()
    receipt = next(iter(documents.protocol_receipts.values()))
    cast(dict[str, object], receipt["response"])["trace_digest"] = "sha256:" + "0" * 64
    _refresh_receipt_stream(receipt, documents.regrade_rows[0])
    with pytest.raises(HandoffMappingError, match="HANDOFF_RESPONSE_TRACE_MISMATCH"):
        _verify(documents)


def test_paired_population_is_bounded_before_canonicalization() -> None:
    from underwrite.instrument.evidence.goppi_p7_handoff import MAX_SLOTS

    documents = _documents()
    documents.paired_input["planned_slots"] = [object()] * (MAX_SLOTS + 1)
    with pytest.raises(HandoffMappingError) as caught:
        _verify(documents)
    assert caught.value.code == "INPUT_LIMIT_EXCEEDED"


def test_cyclic_paired_value_does_not_escape_as_recursion_error() -> None:
    documents = _documents()
    cycle: list[object] = []
    cycle.append(cycle)
    planned = cast(list[dict[str, object]], documents.paired_input["planned_slots"])
    planned[0]["outcome"] = cycle
    with pytest.raises(HandoffMappingError):
        _verify(documents)


@pytest.mark.parametrize("key", ["verifier_id", "verifier_digest"])
def test_response_verifier_unhashable_value_is_typed(key: str) -> None:
    documents = _documents()
    receipt = next(iter(documents.protocol_receipts.values()))
    response = cast(dict[str, object], receipt["response"])
    cast(dict[str, object], response["verifier"])[key] = []
    _refresh_receipt_stream(receipt, documents.regrade_rows[0])
    with pytest.raises(HandoffMappingError):
        _verify(documents)

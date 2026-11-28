"""Pinned synthetic CQ fixture and selected-row/receipt regressions."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import canonical_bytes

from underwrite.instrument.evidence import goppi_cq_provenance as cq
from underwrite.instrument.evidence.goppi_identity import length_prefixed_digest

ROOT = Path(__file__).resolve().parents[4]
FIXTURE = ROOT / "tests/fixtures/cq-provenance-v1.json.raw"
EXCHANGE = ROOT / "fixtures/conformance/goppi-trajectory/exchange"
FIXTURE_SHA256 = "163372080ad8589ab2a0a56fbf7a7ed415d31c9e25321c0f61aba4e1373e9e58"
MEMBER_PINS = {
    "paired/execution.json": (
        4595,
        "c84382fd7e9c9900f3074f50fee91a239969d4e1384c63a9bf3214f828979e54",
    ),
    "paired/manifest.json": (
        798,
        "d437983b3ced8fa3d63dfe35183ef8e6005506b65aa7f58b1081d064476e8074",
    ),
    "paired/rows.jsonl": (
        187384,
        "13c3e066e7ea45849f18118d0f81f0a49998d43adfbbfec7ac4b3541702ae554",
    ),
    "paired/schema.json": (
        12306,
        "0e76595668255eecea653090b66d9948626fa55a01c2f947c1b5463440fe1c81",
    ),
    "trajectory/manifest.json": (
        622,
        "350e037a4b56b0266e4712f1f73589c91cd228bb40bcbd77a984c1dded8a07c5",
    ),
    "trajectory/schema.json": (
        11253,
        "56501dee20f0b2afc28066c6d8a2b8af04dbe31d62b46bd26107377be66cd8dd",
    ),
    "trajectory/trajectory.json": (
        234824,
        "99d6d54ba2bbeb2309129d621cd87be52597ed5d08c5818f6394dc318f4aedbe",
    ),
}
EXPECTED = {
    "changed_axes": ["criterion"],
    "classification": "criterion_only",
    "agent_improvement": "not_established",
    "execution_answer_linkage": "producer_declared",
    "invocation_verified": False,
    "model_contact": "not_observed",
    "reviewer_identity": "not_authenticated",
    "human_action": "not_authenticated",
    "final_outcome": "unavailable_no_authoritative_rubric",
}
DecodedProjection = Callable[[object, Mapping[str, bytes]], dict[str, object]]
decoded_projection_impl = cast(DecodedProjection, cq.__dict__["_project_pinned_payload"])


def _exchange() -> dict[str, bytes]:
    return {name: (EXCHANGE / name).read_bytes() for name in cq.MEMBERS}


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_bytes())


def _bytes(value: object) -> bytes:
    return canonical_bytes(value) + b"\n"


def _seal(domain: str, value: object) -> str:
    return length_prefixed_digest((domain, canonical_bytes(value).decode("utf-8")))


def _seal_condition(condition: dict[str, Any]) -> None:
    condition["judgment_digest"] = _seal("goppi:synthetic-cq-judgment:v1", condition["judgment"])
    for event in condition["review_events"]:
        event["target_judgment_digest"] = condition["judgment_digest"]
        event["content_digest"] = _seal(
            "goppi:synthetic-cq-review-event:v1",
            {key: item for key, item in event.items() if key != "content_digest"},
        )
    condition["content_digest"] = _seal(
        "goppi:synthetic-cq-condition:v1",
        {key: item for key, item in condition.items() if key != "content_digest"},
    )


def _seal_receipt(value: dict[str, Any]) -> None:
    receipt = value["source"]["receipt"]
    receipt["content_digest"] = _seal(
        "goppi:saved-answer-set-receipt:v1",
        {key: item for key, item in receipt.items() if key != "content_digest"},
    )
    value["source"]["receipt_digest"] = receipt["content_digest"]


def _project_decoded(value: dict[str, Any]) -> dict[str, object]:
    return decoded_projection_impl(value, _exchange())


def test_exact_fixture_and_all_seven_member_pins() -> None:
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FIXTURE_SHA256
    assert set(MEMBER_PINS) == set(cq.MEMBERS)
    for name, (size, digest) in MEMBER_PINS.items():
        member = (EXCHANGE / name).read_bytes()
        assert len(member) == size
        assert hashlib.sha256(member).hexdigest() == digest
    assert cq.project_pinned_cq_provenance(raw, _exchange()) == EXPECTED


def test_public_boundary_rejects_changed_fixture_and_member_bytes() -> None:
    raw = FIXTURE.read_bytes()
    with pytest.raises(cq.CqProvenanceError, match="fixture_pin_mismatch"):
        cq.project_pinned_cq_provenance(raw + b" ", _exchange())
    exchange = _exchange()
    exchange.pop("trajectory/schema.json")
    with pytest.raises(cq.CqProvenanceError, match="member_set_invalid"):
        cq.project_pinned_cq_provenance(raw, exchange)
    with pytest.raises(cq.CqProvenanceError, match="byte_limit"):
        cq.project_pinned_cq_provenance(b"{" * (cq.MAX_FIXTURE_BYTES + 1), _exchange())
    oversized = _exchange()
    oversized["paired/execution.json"] = b"{" * (cq.MAX_MEMBER_BYTES + 1)
    with pytest.raises(cq.CqProvenanceError, match="byte_limit"):
        cq.project_pinned_cq_provenance(raw, oversized)


@pytest.mark.parametrize("name", tuple(MEMBER_PINS))
def test_each_original_member_byte_pin_rejects_tampering(name: str) -> None:
    exchange = _exchange()
    exchange[name] = b"X" + exchange[name][1:]
    with pytest.raises(cq.CqProvenanceError, match="member_pin_mismatch"):
        cq.project_pinned_cq_provenance(FIXTURE.read_bytes(), exchange)


def test_three_single_axes_mixed_and_unknown_keep_conservative_fields() -> None:
    grader = _fixture()
    before, after = grader["conditions"]
    after["criterion"] = copy.deepcopy(before["criterion"])
    after["declared_grader_model_ref"]["version"] = "model-v2"
    _seal_condition(after)
    assert _project_decoded(grader) == EXPECTED | {
        "changed_axes": ["grader_declaration"],
        "classification": "grader_declaration_only",
    }

    review = _fixture()
    before, after = review["conditions"]
    after["criterion"] = copy.deepcopy(before["criterion"])
    after["judgment"] = copy.deepcopy(before["judgment"])
    after["review_events"] = [
        {
            "event_id": "synthetic-review-event-1",
            "target_judgment_digest": after["judgment_digest"],
            "declared_decision": "accept",
            "actor_claim": "synthetic reviewer claim",
            "content_digest": "sha256:" + "0" * 64,
        }
    ]
    _seal_condition(after)
    assert _project_decoded(review) == EXPECTED | {
        "changed_axes": ["review_decision"],
        "classification": "review_only",
    }

    mixed = _fixture()
    mixed["conditions"][1]["declared_prompt_ref"]["version"] = "prompt-v2"
    _seal_condition(mixed["conditions"][1])
    assert _project_decoded(mixed) == EXPECTED | {
        "changed_axes": ["criterion", "grader_declaration"],
        "classification": "mixed",
    }

    unknown = _fixture()
    unknown["conditions"][0]["declared_grader_model_ref"] = None
    _seal_condition(unknown["conditions"][0])
    assert _project_decoded(unknown) == EXPECTED | {"changed_axes": [], "classification": "unknown"}


@pytest.mark.parametrize(
    ("path", "replacement", "reason"),
    [
        (("source", "row_ordinal"), 1, "selected_row_mismatch"),
        (("source", "row_ordinal"), False, "value_invalid"),
        (("source", "row_bytes"), 1, "selected_row_mismatch"),
        (("source", "row_sha256"), "sha256:" + "0" * 64, "selected_row_mismatch"),
        (("source", "row_key", "arm"), "candidate", "selected_row_mismatch"),
        (("source", "row_key", "case_id"), "other", "selected_row_mismatch"),
        (("source", "row_key", "cycle_run_id"), "sha256:" + "0" * 64, "selected_row_mismatch"),
        (("source", "row_key", "repeat_index"), False, "value_invalid"),
        (("source", "row_key", "repeat_run_id"), "sha256:" + "0" * 64, "selected_row_mismatch"),
        (("source", "row_key", "split"), "confirm", "selected_row_mismatch"),
        (("source", "row_key", "stage"), "held_in", "selected_row_mismatch"),
        (("source", "row_key", "stage_run_id"), "sha256:" + "0" * 64, "selected_row_mismatch"),
        (("source", "receipt_digest"), "sha256:" + "0" * 64, "receipt_digest_mismatch"),
        (("source", "answer_commitment"), "hmac-sha256:" + "0" * 64, "answer_commitment_mismatch"),
        (("source", "members", "paired/execution.json", "bytes"), 1, "member_pin_mismatch"),
    ],
)
def test_selected_row_and_receipt_claims_fail_closed(
    path: tuple[str, ...], replacement: object, reason: str
) -> None:
    value = _fixture()
    target = value
    for field in path[:-1]:
        target = target[field]
    target[path[-1]] = replacement
    with pytest.raises(cq.CqProvenanceError, match=reason):
        _project_decoded(value)


def test_resealed_receipt_case_and_source_crosslinks_fail() -> None:
    case = _fixture()
    case["source"]["receipt"]["cases"][0]["source_row_digest"] = "sha256:" + "0" * 64
    _seal_receipt(case)
    with pytest.raises(cq.CqProvenanceError, match="receipt_row_mismatch"):
        _project_decoded(case)

    source = _fixture()
    source["source"]["receipt"]["source"]["runner_identity_digest"] = "sha256:" + "0" * 64
    _seal_receipt(source)
    with pytest.raises(cq.CqProvenanceError, match="receipt_source_mismatch"):
        _project_decoded(source)


def test_condition_digest_review_target_event_conflict_and_zero_axis_fail() -> None:
    invented = _fixture()
    invented["conditions"][1]["judgment"] = {"state": "invented", "reason": None}
    _seal_condition(invented["conditions"][1])
    with pytest.raises(cq.CqProvenanceError, match="judgment_invalid"):
        _project_decoded(invented)

    stale = _fixture()
    event = {
        "event_id": "review-1",
        "target_judgment_digest": "sha256:" + "0" * 64,
        "declared_decision": "accept",
        "actor_claim": "reviewer",
        "content_digest": "sha256:" + "0" * 64,
    }
    stale["conditions"][1]["review_events"] = [event]
    _seal_condition(stale["conditions"][1])
    event["target_judgment_digest"] = "sha256:" + "0" * 64
    event["content_digest"] = _seal(
        "goppi:synthetic-cq-review-event:v1",
        {key: item for key, item in event.items() if key != "content_digest"},
    )
    stale["conditions"][1]["content_digest"] = _seal(
        "goppi:synthetic-cq-condition:v1",
        {key: item for key, item in stale["conditions"][1].items() if key != "content_digest"},
    )
    with pytest.raises(cq.CqProvenanceError, match="stale_review_target"):
        _project_decoded(stale)

    duplicate = _fixture()
    for condition, decision in zip(duplicate["conditions"], ("accept", "reject"), strict=True):
        condition["review_events"] = [
            {
                "event_id": "review-1",
                "target_judgment_digest": condition["judgment_digest"],
                "declared_decision": decision,
                "actor_claim": "reviewer",
                "content_digest": "sha256:" + "0" * 64,
            }
        ]
        _seal_condition(condition)
    with pytest.raises(cq.CqProvenanceError, match="review_event_id_conflict"):
        _project_decoded(duplicate)

    zero = _fixture()
    zero["conditions"][1] = copy.deepcopy(zero["conditions"][0])
    with pytest.raises(cq.CqProvenanceError, match="zero_axis"):
        _project_decoded(zero)


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("judgment_digest", "judgment_digest_mismatch"),
        ("content_digest", "content_digest_mismatch"),
    ],
)
def test_condition_digest_claims_reject_tampering(field: str, reason: str) -> None:
    value = _fixture()
    value["conditions"][1][field] = "sha256:" + "0" * 64
    with pytest.raises(cq.CqProvenanceError, match=reason):
        _project_decoded(value)


def test_review_event_digest_claim_rejects_tampering() -> None:
    value = _fixture()
    after = value["conditions"][1]
    after["review_events"] = [
        {
            "event_id": "review-1",
            "target_judgment_digest": after["judgment_digest"],
            "declared_decision": "accept",
            "actor_claim": "reviewer",
            "content_digest": "sha256:" + "0" * 64,
        }
    ]
    _seal_condition(after)
    after["review_events"][0]["content_digest"] = "sha256:" + "0" * 64
    with pytest.raises(cq.CqProvenanceError, match="content_digest_mismatch"):
        _project_decoded(value)


def test_coherent_answer_change_is_only_detected_by_external_fixture_pin() -> None:
    value = _fixture()
    commitment = "hmac-sha256:" + "a" * 64
    value["source"]["receipt"]["cases"][0]["answer_commitment"] = commitment
    value["source"]["answer_commitment"] = commitment
    _seal_receipt(value)
    assert _project_decoded(value) == EXPECTED
    with pytest.raises(cq.CqProvenanceError, match="fixture_pin_mismatch"):
        cq.project_pinned_cq_provenance(_bytes(value), _exchange())

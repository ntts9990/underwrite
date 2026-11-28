"""Pinned synthetic CQ fixture projection, not a general Goppi exchange reader.

The byte pins and selected-row/receipt joins establish local consistency only.
They do not authenticate the producer, saved answer, grader, or reviewer.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

from underwrite.instrument.evidence.goppi_identity import length_prefixed_digest

FIXTURE_SHA256 = "163372080ad8589ab2a0a56fbf7a7ed415d31c9e25321c0f61aba4e1373e9e58"
MEMBER_PINS: dict[str, tuple[int, str]] = {
    "paired/execution.json": (
        4595,
        "sha256:c84382fd7e9c9900f3074f50fee91a239969d4e1384c63a9bf3214f828979e54",
    ),
    "paired/manifest.json": (
        798,
        "sha256:d437983b3ced8fa3d63dfe35183ef8e6005506b65aa7f58b1081d064476e8074",
    ),
    "paired/rows.jsonl": (
        187384,
        "sha256:13c3e066e7ea45849f18118d0f81f0a49998d43adfbbfec7ac4b3541702ae554",
    ),
    "paired/schema.json": (
        12306,
        "sha256:0e76595668255eecea653090b66d9948626fa55a01c2f947c1b5463440fe1c81",
    ),
    "trajectory/manifest.json": (
        622,
        "sha256:350e037a4b56b0266e4712f1f73589c91cd228bb40bcbd77a984c1dded8a07c5",
    ),
    "trajectory/schema.json": (
        11253,
        "sha256:56501dee20f0b2afc28066c6d8a2b8af04dbe31d62b46bd26107377be66cd8dd",
    ),
    "trajectory/trajectory.json": (
        234824,
        "sha256:99d6d54ba2bbeb2309129d621cd87be52597ed5d08c5818f6394dc318f4aedbe",
    ),
}
MEMBERS = tuple(MEMBER_PINS)
SELECTED_ROW_ORDINAL = 0
SELECTED_ROW_BYTES = 1303
SELECTED_ROW_SHA256 = "sha256:430272e6fe97d44b95c6feda592aa937b0db6408716a7c64fd9f77a3583ce2e4"
SELECTED_ROW_KEY: dict[str, str | int] = {
    "arm": "baseline",
    "case_id": "synthetic-arithmetic-002",
    "cycle_run_id": "sha256:115ffcc26a45a8dab3aa8db3e1c45e5bd07cbdc78c79967327c3d96d6e2c1843",
    "repeat_index": 0,
    "repeat_run_id": "sha256:e1dadccb33ffed3cbc67c36834c1202b5495701d24f14a278bd142bbc3dba66f",
    "split": "explore",
    "stage": "same_batch",
    "stage_run_id": "sha256:115ffcc26a45a8dab3aa8db3e1c45e5bd07cbdc78c79967327c3d96d6e2c1843",
}
MAX_FIXTURE_BYTES = 64 * 1024
MAX_MEMBER_BYTES = 1024 * 1024
MAX_ROWS = 256
MAX_ROW_BYTES = 16 * 1024
MAX_TEXT_LENGTH = 256
CONDITION_COUNT = 2
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMITMENT = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z")
_ROW_FIELDS = (
    "arm assignment_digest case_id config_digest cycle_run_id evalset_digest evidence_digest "
    "planned primary_inference repeat_index repeat_proof repeat_run_id result schema "
    "split split_policy_digest stage stage_run_id trace_digest verifier"
)


class CqProvenanceError(ValueError):
    """Finite validation diagnostic without producer-controlled exception text."""

    def __init__(self, reason: str, location: str) -> None:
        self.reason = reason
        self.location = location
        super().__init__(f"{reason}: {location}")


def _check(ok: bool, reason: str, location: str) -> None:
    if not ok:
        raise CqProvenanceError(reason, location)


def _object(value: object, fields: str, location: str) -> dict[str, Any]:
    _check(type(value) is dict, "shape_invalid", location)
    result = cast(dict[str, Any], value)
    _check(set(result) == set(fields.split()), "shape_invalid", location)
    return result


def _list(value: object, maximum: int, location: str) -> list[Any]:
    _check(type(value) is list and len(value) <= maximum, "shape_invalid", location)
    return cast(list[Any], value)


def _text(value: object, location: str) -> str:
    _check(type(value) is str and 0 < len(value) <= MAX_TEXT_LENGTH, "value_invalid", location)
    return cast(str, value)


def _digest(value: object, location: str) -> str:
    result = _text(value, location)
    _check(_SHA.fullmatch(result) is not None, "digest_invalid", location)
    return result


def _commitment(value: object, location: str) -> str:
    result = _text(value, location)
    _check(_COMMITMENT.fullmatch(result) is not None, "commitment_invalid", location)
    return result


def _count(value: object, maximum: int, location: str) -> int:
    _check(type(value) is int and 0 <= value <= maximum, "value_invalid", location)
    return cast(int, value)


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        _check(key not in result, "duplicate_json_key", "/")
        result[key] = value
    return result


def _json_line(raw: bytes, limit: int, location: str) -> object:
    _check(len(raw) <= limit, "byte_limit", location)
    _check(raw.endswith(b"\n"), "canonical_bytes_invalid", location)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        _check(canonical_bytes(value) + b"\n" == raw, "canonical_bytes_invalid", location)
    except CqProvenanceError:
        raise
    except (UnicodeError, ValueError, RecursionError, OverflowError):
        raise CqProvenanceError("canonical_bytes_invalid", location) from None
    return value


def _semantic(domain: str, value: object) -> str:
    try:
        return length_prefixed_digest((domain, canonical_bytes(value).decode("utf-8")))
    except CanonicalizationError:
        raise CqProvenanceError("canonical_value_invalid", "/") from None


def _sealed(value: dict[str, Any], domain: str, location: str) -> None:
    claimed = _digest(value["content_digest"], f"{location}/content_digest")
    payload = {key: item for key, item in value.items() if key != "content_digest"}
    _check(claimed == _semantic(domain, payload), "content_digest_mismatch", location)


def _ref(value: object, location: str) -> None:
    if value is None:
        return
    ref = _object(value, "version digest", location)
    _text(ref["version"], f"{location}/version")
    _digest(ref["digest"], f"{location}/digest")


def _condition(value: object, location: str) -> dict[str, Any]:
    condition = _object(
        value,
        "criterion declared_grader_model_ref declared_prompt_ref judgment "
        "judgment_digest review_events content_digest",
        location,
    )
    for field in ("criterion", "declared_grader_model_ref", "declared_prompt_ref"):
        _ref(condition[field], f"{location}/{field}")
    judgment = _object(condition["judgment"], "state reason", f"{location}/judgment")
    reasons = {
        "binary_positive": "graded_binary",
        "binary_negative": "graded_binary",
        "indeterminate": "grader_abstained",
        "operational_failure": "grader_failure",
        "ungraded": "not_graded",
    }
    state = judgment["state"]
    _check(
        type(state) is str and state in reasons and judgment["reason"] == reasons[state],
        "judgment_invalid",
        location,
    )
    _check(
        _digest(condition["judgment_digest"], f"{location}/judgment_digest")
        == _semantic("goppi:synthetic-cq-judgment:v1", judgment),
        "judgment_digest_mismatch",
        location,
    )
    events = _list(condition["review_events"], 1, f"{location}/review_events")
    for index, raw in enumerate(events):
        where = f"{location}/review_events/{index}"
        event = _object(
            raw,
            "event_id target_judgment_digest declared_decision actor_claim content_digest",
            where,
        )
        _text(event["event_id"], f"{where}/event_id")
        _check(
            event["declared_decision"] in ("accept", "reject", "defer"), "decision_invalid", where
        )
        if event["actor_claim"] is not None:
            _text(event["actor_claim"], f"{where}/actor_claim")
        _digest(event["target_judgment_digest"], f"{where}/target_judgment_digest")
        _sealed(event, "goppi:synthetic-cq-review-event:v1", where)
        _check(
            event["target_judgment_digest"] == condition["judgment_digest"],
            "stale_review_target",
            where,
        )
    _sealed(condition, "goppi:synthetic-cq-condition:v1", location)
    return condition


def _receipt(value: object, location: str) -> dict[str, Any]:
    receipt = _object(
        value,
        "schema answer_set_id case_ids eligible_case_count case_inventory_digest "
        "source execution_answer_linkage local_key_identity_digest cases content_digest",
        location,
    )
    _check(receipt["schema"] == "goppi.saved_answer_set_receipt.v1", "schema_invalid", location)
    _text(receipt["answer_set_id"], f"{location}/answer_set_id")
    ids = _list(receipt["case_ids"], MAX_ROWS, f"{location}/case_ids")
    _check(
        bool(ids) and all(type(item) is str and item for item in ids), "case_ids_invalid", location
    )
    _check(len(set(ids)) == len(ids), "duplicate_case_id", location)
    _check(
        _count(receipt["eligible_case_count"], MAX_ROWS, location) == len(ids),
        "case_count_mismatch",
        location,
    )
    _check(
        _digest(receipt["case_inventory_digest"], location)
        == length_prefixed_digest(("goppi:saved-answer-case-inventory:v1", str(len(ids)), *ids)),
        "case_inventory_mismatch",
        location,
    )
    source = _object(
        receipt["source"],
        "paired_manifest_digest trajectory_manifest_digest "
        "execution_identity_digest runner_identity_digest",
        f"{location}/source",
    )
    for name in source:
        _digest(source[name], f"{location}/source/{name}")
    _digest(receipt["local_key_identity_digest"], location)
    _check(receipt["execution_answer_linkage"] == "producer_declared", "linkage_invalid", location)
    cases = _list(receipt["cases"], MAX_ROWS, f"{location}/cases")
    _check(len(cases) == len(ids), "case_count_mismatch", location)
    for index, item in enumerate(cases):
        case = _object(
            item, "case_id source_row_digest answer_commitment", f"{location}/cases/{index}"
        )
        _check(case["case_id"] == ids[index], "case_order_mismatch", location)
        if case["source_row_digest"] is not None:
            _digest(case["source_row_digest"], location)
        _commitment(case["answer_commitment"], location)
    _sealed(receipt, "goppi:saved-answer-set-receipt:v1", location)
    return receipt


def _pinned_members(exchange: Mapping[str, bytes]) -> None:
    _check(set(exchange) == set(MEMBERS), "member_set_invalid", "/exchange")
    for name, (size, sha) in MEMBER_PINS.items():
        raw = exchange[name]
        _check(
            type(raw) is bytes and len(raw) <= MAX_MEMBER_BYTES, "byte_limit", f"/exchange/{name}"
        )
        _check(
            len(raw) == size and digest_bytes(raw) == sha,
            "member_pin_mismatch",
            f"/exchange/{name}",
        )


def _row_lines(raw: bytes) -> list[bytes]:
    _check(raw.endswith(b"\n"), "row_framing_invalid", "/exchange/paired/rows.jsonl")
    lines = raw.splitlines(keepends=True)
    _check(0 < len(lines) <= MAX_ROWS, "row_count_limit", "/exchange/paired/rows.jsonl")
    _check(
        all(len(line) <= MAX_ROW_BYTES and line.endswith(b"\n") for line in lines),
        "row_byte_limit",
        "/exchange/paired/rows.jsonl",
    )
    return lines


def _selected_row(source: dict[str, Any], exchange: Mapping[str, bytes]) -> dict[str, Any]:
    lines = _row_lines(exchange["paired/rows.jsonl"])
    ordinal = _count(source["row_ordinal"], MAX_ROWS - 1, "/source/row_ordinal")
    _check(ordinal == SELECTED_ROW_ORDINAL, "selected_row_mismatch", "/source/row_ordinal")
    line = lines[ordinal]
    _check(
        _count(source["row_bytes"], MAX_ROW_BYTES, "/source/row_bytes")
        == len(line)
        == SELECTED_ROW_BYTES,
        "selected_row_mismatch",
        "/source/row_bytes",
    )
    _check(
        _digest(source["row_sha256"], "/source/row_sha256")
        == digest_bytes(line)
        == SELECTED_ROW_SHA256,
        "selected_row_mismatch",
        "/source/row_sha256",
    )
    row = _object(_json_line(line, MAX_ROW_BYTES, "/selected_row"), _ROW_FIELDS, "/selected_row")
    key = _object(
        source["row_key"],
        "arm case_id cycle_run_id repeat_index repeat_run_id split stage stage_run_id",
        "/source/row_key",
    )
    _count(key["repeat_index"], MAX_ROWS - 1, "/source/row_key/repeat_index")
    _count(row["repeat_index"], MAX_ROWS - 1, "/selected_row/repeat_index")
    _check(key == SELECTED_ROW_KEY, "selected_row_mismatch", "/source/row_key")
    _check(
        all(
            type(row[field]) is type(expected) and row[field] == expected
            for field, expected in key.items()
        ),
        "selected_row_mismatch",
        "/selected_row",
    )
    return row


def _manifests_and_receipt(
    source: dict[str, Any], row: dict[str, Any], exchange: Mapping[str, bytes]
) -> None:
    paired = _object(
        _json_line(exchange["paired/manifest.json"], MAX_MEMBER_BYTES, "/paired/manifest"),
        "content_digest files format_scope row_count schema source_plan_digest "
        "source_result_authentication_tag status",
        "/paired/manifest",
    )
    trajectory = _object(
        _json_line(exchange["trajectory/manifest.json"], MAX_MEMBER_BYTES, "/trajectory/manifest"),
        "content_digest files paired_manifest_digest paired_manifest_sha256 schema status",
        "/trajectory/manifest",
    )
    execution = _object(
        _json_line(exchange["paired/execution.json"], MAX_MEMBER_BYTES, "/paired/execution"),
        "authority claims counts format_scope schema source stages status",
        "/paired/execution",
    )
    _sealed(paired, "goppi:paired-run-exchange-manifest:v1", "/paired/manifest")
    _sealed(trajectory, "goppi:paired-trajectory-exchange-manifest:v1", "/trajectory/manifest")
    _check(
        paired["schema"] == "goppi.paired_run_exchange.manifest.v1"
        and paired["format_scope"] == "terminal_native_paired_run"
        and paired["status"] == "complete"
        and trajectory["schema"] == "goppi.paired_trajectory_exchange.manifest.v1"
        and trajectory["status"] == "complete"
        and execution["schema"] == "goppi.paired_run_exchange.execution.v1",
        "schema_invalid",
        "/exchange",
    )
    execution_source = _object(
        execution["source"],
        "assignment_digest baseline_snapshot_digest candidate_snapshot_digest cycle_run_id "
        "cycle_status evalset_digest execution_identity_digest plan_digest project_config_digest "
        "proposal_digest result_authentication_tag runner split_policy_digest verifier",
        "/paired/execution/source",
    )
    runner = _object(
        execution_source["runner"],
        "executable_digest profile profile_identity_digest",
        "/paired/execution/source/runner",
    )
    _check(
        _count(paired["row_count"], MAX_ROWS, "/paired/manifest/row_count")
        == len(_row_lines(exchange["paired/rows.jsonl"])),
        "row_count_mismatch",
        "/paired/manifest/row_count",
    )
    _check(
        _digest(paired["source_plan_digest"], "/paired/manifest/source_plan_digest")
        == _digest(execution_source["plan_digest"], "/paired/execution/source/plan_digest")
        and _digest(
            paired["source_result_authentication_tag"],
            "/paired/manifest/source_result_authentication_tag",
        )
        == _digest(
            execution_source["result_authentication_tag"],
            "/paired/execution/source/result_authentication_tag",
        ),
        "paired_source_mismatch",
        "/paired/manifest",
    )
    _check(
        trajectory["paired_manifest_digest"] == paired["content_digest"]
        and trajectory["paired_manifest_sha256"] == digest_bytes(exchange["paired/manifest.json"]),
        "trajectory_link_mismatch",
        "/trajectory/manifest",
    )
    receipt = _receipt(source["receipt"], "/source/receipt")
    _check(
        receipt["content_digest"] == _digest(source["receipt_digest"], "/source/receipt_digest"),
        "receipt_digest_mismatch",
        "/source/receipt_digest",
    )
    _check(
        _commitment(source["answer_commitment"], "/source/answer_commitment")
        == receipt["cases"][0]["answer_commitment"],
        "answer_commitment_mismatch",
        "/source/answer_commitment",
    )
    _check(
        receipt["case_ids"] == [row["case_id"]]
        and receipt["cases"][0]["source_row_digest"] == source["row_sha256"],
        "receipt_row_mismatch",
        "/source/receipt/cases",
    )
    receipt_source = cast(dict[str, Any], receipt["source"])
    _check(
        receipt_source["paired_manifest_digest"] == paired["content_digest"]
        and receipt_source["trajectory_manifest_digest"] == trajectory["content_digest"]
        and receipt_source["execution_identity_digest"]
        == execution_source["execution_identity_digest"]
        and receipt_source["runner_identity_digest"] == runner["profile_identity_digest"]
        and execution_source["cycle_run_id"] == row["cycle_run_id"],
        "receipt_source_mismatch",
        "/source/receipt/source",
    )


def _classify_conditions(value: object) -> dict[str, object]:
    conditions = _list(value, CONDITION_COUNT, "/conditions")
    _check(len(conditions) == CONDITION_COUNT, "condition_count_invalid", "/conditions")
    before, after = (
        _condition(item, f"/conditions/{index}") for index, item in enumerate(conditions)
    )
    prior, later = before["review_events"], after["review_events"]
    if prior and later and prior[0]["event_id"] == later[0]["event_id"]:
        _check(
            prior[0]["content_digest"] == later[0]["content_digest"],
            "review_event_id_conflict",
            "/conditions",
        )
    known = all(
        condition[field] is not None
        for condition in (before, after)
        for field in ("criterion", "declared_grader_model_ref", "declared_prompt_ref")
    )
    criterion = before["criterion"] != after["criterion"]
    grader = (before["declared_grader_model_ref"], before["declared_prompt_ref"]) != (
        after["declared_grader_model_ref"],
        after["declared_prompt_ref"],
    )
    review = [event["declared_decision"] for event in prior] != [
        event["declared_decision"] for event in later
    ]
    _check(
        criterion or grader or before["judgment_digest"] == after["judgment_digest"],
        "judgment_rewritten",
        "/conditions",
    )
    axes = (
        (["criterion"] if criterion else [])
        + (["grader_declaration"] if grader else [])
        + (["review_decision"] if review else [])
        if known
        else []
    )
    _check(not known or bool(axes), "zero_axis", "/conditions")
    classification = (
        "unknown"
        if not known
        else "mixed"
        if len(axes) > 1
        else {
            "criterion": "criterion_only",
            "grader_declaration": "grader_declaration_only",
            "review_decision": "review_only",
        }[axes[0]]
    )
    return {
        "changed_axes": axes,
        "classification": classification,
        "agent_improvement": "not_established",
        "execution_answer_linkage": "producer_declared",
        "invocation_verified": False,
        "model_contact": "not_observed",
        "reviewer_identity": "not_authenticated",
        "human_action": "not_authenticated",
        "final_outcome": "unavailable_no_authoritative_rubric",
    }


def _project_pinned_payload(fixture: object, exchange: Mapping[str, bytes]) -> dict[str, object]:
    """Internal test seam; caller must not treat decoded mutations as pinned input."""
    _pinned_members(exchange)
    root = _object(fixture, "schema source conditions", "/fixture")
    _check(
        root["schema"] == "goppi.synthetic_cq_provenance_fixture.v1", "schema_invalid", "/fixture"
    )
    source = _object(
        root["source"],
        "members row_ordinal row_sha256 row_bytes row_key receipt receipt_digest answer_commitment",
        "/source",
    )
    claims = _object(source["members"], " ".join(MEMBERS), "/source/members")
    for name, (size, sha) in MEMBER_PINS.items():
        claim = _object(claims[name], "bytes sha256", f"/source/members/{name}")
        _check(
            _count(claim["bytes"], MAX_MEMBER_BYTES, name) == size
            and _digest(claim["sha256"], name) == sha,
            "member_pin_mismatch",
            f"/source/members/{name}",
        )
    row = _selected_row(source, exchange)
    _manifests_and_receipt(source, row, exchange)
    return _classify_conditions(root["conditions"])


def project_pinned_cq_provenance(
    fixture_bytes: bytes, exchange: Mapping[str, bytes]
) -> dict[str, object]:
    """Project only the externally pinned synthetic fixture and seven original members."""
    _check(type(fixture_bytes) is bytes, "shape_invalid", "/fixture")
    _check(len(fixture_bytes) <= MAX_FIXTURE_BYTES, "byte_limit", "/fixture")
    _pinned_members(exchange)
    _check(
        digest_bytes(fixture_bytes) == "sha256:" + FIXTURE_SHA256,
        "fixture_pin_mismatch",
        "/fixture",
    )
    return _project_pinned_payload(
        _json_line(fixture_bytes, MAX_FIXTURE_BYTES, "/fixture"), exchange
    )

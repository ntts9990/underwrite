"""Card: trajectory-review | Paper: ADR-020 | Stage: producer-bridge.

Descriptive interpretation of a natively admitted paired trajectory exchange.
The caller must verify the same bytes with Goppi; this is not a native verifier.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from typing import Any, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes

RESULT_SCHEMA = "goppi_trajectory_review.v1"
REVIEW_POLICY = "goppi.descriptive-paired-trajectory.v1"
OUTCOME_REASONS = {
    "endpoint_failure": ("agent_timeout", "agent_budget", "agent_answer"),
    "missing": ("infra", "verifier_infra", "exhausted_retries", "not_started"),
}
CLOSED_STAGE_REASONS = ("partial_stage", "budget_stop", "hard_failure")
CLOSED_STAGE_CLASSIFICATIONS = ("indeterminate", "rejected")
OUTCOME_HARNESS_BINDINGS = (
    "v1_not_reverified",
    "v2_verified_against_patch_manifest",
    "not_applicable_no_evaluated_stages",
)
PROFILE = "native_pi_1_3_lifecycle.v1"
MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10_000
MAX_UNITS = 600
MAX_SPANS_PER_UNIT = 10_000
MEMBERS = (
    "paired/execution.json",
    "paired/manifest.json",
    "paired/rows.jsonl",
    "paired/schema.json",
    "trajectory/manifest.json",
    "trajectory/schema.json",
    "trajectory/trajectory.json",
)
AVAILABILITIES = (
    "retained",
    "not_retained",
    "unsupported",
    "interrupted",
    "malformed",
    "storage_failure",
)
UNAVAILABLE_REASONS = (
    "capture_limit_exceeded",
    "capture_policy_unrecorded",
    "recovered_bundle_not_retained",
    "capture_completion_missing",
    "profile_not_supported",
    "pi_lifecycle_interrupted",
    "pi_lifecycle_malformed",
    "receipt_derived_trace",
    "source_storage_write_failed",
    "no_evaluated_outcomes",
)
STAGES = ("same_batch", "held_in", "held_out")
PAIR_FIELDS = (
    "cycle_run_id",
    "stage_run_id",
    "stage",
    "split",
    "case_id",
    "repeat_run_id",
    "repeat_index",
)
CONTEXT_FIELDS = (
    "runner_model",
    "runner_base_identity_digest",
    "runner_profile",
    "verifier_runtime_digest",
    "verifier_profile_identity_digest",
    "primary_policy_digest",
    "budget_policy_digest",
    "plan_digest",
    "execution_identity_digest",
    "evalset_digest",
    "assignment_digest",
    "split_policy_digest",
    "config_digest",
    "baseline_snapshot_digest",
    "candidate_snapshot_digest",
    "declared_repeats",
)
ROW_CONTEXT_FIELDS = ("evalset_digest", "assignment_digest", "split_policy_digest", "config_digest")
TASK_EVIDENCE = {
    "obtain_supported_capture": (
        "goppi",
        "supported complete lifecycle capture for listed case arms",
    ),
    "inspect_tool_status_difference": (
        "calling_agent",
        "selected case tool-status evidence and task outcome review",
    ),
    "check_outcome_comparability": (
        "calling_agent",
        "matching verifier, value-kind and declared runtime/policy context",
    ),
    "obtain_verifier_process_linkage": (
        "goppi",
        "case-linked verifier-process observation or explicit unavailable evidence",
    ),
    "obtain_call_relationships": (
        "goppi",
        "explicit call parentage and retry relationships before retry or causal investigation",
    ),
}
INTERPRETATION_LIMITS = (
    "source_outcomes_are_producer_claims",
    "content_identity_is_not_independent_provenance",
    "source_authentication_and_private_projection_not_independently_verified",
    "tool_frequencies_do_not_align_individual_calls",
    "call_relationships_not_captured",
    "causality_and_retry_effects_not_established",
    "repeated_rows_are_not_independent_cases",
    "metadata_may_identify_sources_not_anonymized",
    "snapshot_checks_do_not_establish_original_directory_privacy",
)
_MEMBER_IDENTITY_PARTS = 2
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
Key = tuple[str | int, ...]


class TrajectoryError(ValueError):
    """Finite consumer diagnostics without reflecting raw producer payloads."""

    def __init__(self, code: str, reason: str, location: str) -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def _require(
    condition: bool, reason: str = "TRAJECTORY_VALUE_INVALID", location: str = "trajectory"
) -> None:
    if not condition:
        code = {
            "TRAJECTORY_LIMIT_EXCEEDED": "INPUT_LIMIT_EXCEEDED",
            "TRAJECTORY_UNSUPPORTED_VALUE": "UNSUPPORTED_FORMAT",
        }.get(reason, "INVALID_INPUT")
        raise TrajectoryError(code, reason, location)


def _object(value: object) -> dict[str, Any]:
    _require(type(value) is dict, "TRAJECTORY_SHAPE_INVALID")
    return cast(dict[str, Any], value)


def _array(value: object, maximum: int) -> list[Any]:
    _require(type(value) is list, "TRAJECTORY_SHAPE_INVALID")
    result = cast(list[Any], value)
    _require(len(result) <= maximum, "TRAJECTORY_LIMIT_EXCEEDED")
    return result


def _text(value: object, maximum: int = 4096) -> str:
    _require(type(value) is str and 0 < len(value) <= maximum)
    result = cast(str, value)
    _require(not any(unicodedata.category(c) in {"Cc", "Cs"} for c in result))
    return result


def _enum(value: object, choices: tuple[str, ...]) -> str:
    result = _text(value)
    _require(result in choices, "TRAJECTORY_UNSUPPORTED_VALUE")
    return result


def _integer(value: object, maximum: int = MAX_ROWS) -> int:
    _require(type(value) is int and 0 <= value <= maximum)
    return cast(int, value)


def _digest(value: object) -> str:
    result = _text(value, 71)
    _require(_DIGEST.fullmatch(result) is not None)
    return result


def _boolean(value: object) -> bool:
    _require(type(value) is bool)
    return cast(bool, value)


def _identity(prefix: str, value: object) -> str:
    try:
        encoded = canonical_bytes(value)
    except CanonicalizationError as exc:
        raise TrajectoryError("INVALID_INPUT", "TRAJECTORY_VALUE_INVALID", "identity") from exc
    return prefix + hashlib.sha256(encoded).hexdigest()


def _key(row: dict[str, Any]) -> Key:
    return (
        _digest(row.get("cycle_run_id")),
        _digest(row.get("stage_run_id")),
        _enum(row.get("stage"), STAGES),
        _enum(row.get("split"), ("explore", "confirm")),
        _text(row.get("case_id")),
        _digest(row.get("repeat_run_id")),
        _integer(row.get("repeat_index"), 99),
        _enum(row.get("arm"), ("baseline", "candidate")),
    )


def _verifier(value: object) -> dict[str, str]:
    obj = _object(value)
    return {
        "verifier_id": _text(obj.get("verifier_id"), 128),
        "verifier_digest": _digest(obj.get("verifier_digest")),
    }


def _outcome(value: object) -> dict[str, Any]:
    obj = _object(value)
    status = _enum(obj.get("status"), ("observed", "endpoint_failure", "missing"))
    result: dict[str, Any] = {"status": status}
    if status == "observed":
        kind = _enum(obj.get("value_kind"), ("binary", "scalar"))
        result["value_kind"] = kind
        for field in ("verifier_value", "scored_value"):
            val = obj.get(field)
            if kind == "binary":
                result[field] = _boolean(val)
            else:
                _require(type(val) in (int, float))
                try:
                    finite = math.isfinite(cast(float, val))
                except OverflowError:
                    finite = False
                _require(finite)
                result[field] = val
    else:
        result["reason"] = _enum(obj.get("reason"), OUTCOME_REASONS[status])
        _require("verifier_value" in obj and obj["verifier_value"] is None)
        _require(
            "scored_value" in obj
            and obj["scored_value"] is (False if status == "endpoint_failure" else None)
        )
        result.update(verifier_value=None, scored_value=obj["scored_value"])
    return result


def _context(value: object) -> dict[str, str | int | None]:
    obj = {} if value is None else _object(value)
    result: dict[str, str | int | None] = {}
    for field in CONTEXT_FIELDS:
        val = obj.get(field)
        if val is None:
            result[field] = None
        elif field == "declared_repeats":
            result[field] = _integer(val, 100)
        elif field in ("runner_model", "runner_profile"):
            result[field] = _text(val, 128)
        else:
            result[field] = _digest(val)
    return result


def _process(value: object, case_id: str) -> dict[str, Any]:
    if value is None:
        return {"state": "unlinked"}
    obj = _object(value)
    _require(_text(obj.get("case_id")) == case_id, "TRAJECTORY_BINDING_MISMATCH")
    state = _enum(obj.get("status"), ("observed", "not_observed"))
    result: dict[str, Any] = {"state": state}
    if state == "observed":
        result.update(
            attempt_id=_text(obj.get("attempt_id"), 256),
            receipt_identity_digest=_digest(obj.get("receipt_identity_digest")),
            expected_public_identity_digest=_digest(obj.get("expected_public_identity_digest")),
        )
    return result


def _spans(value: object, seen: set[str]) -> tuple[int, list[dict[str, Any]]]:
    spans = _array(value, MAX_SPANS_PER_UNIT)
    _require(len(seen) + len(spans) <= MAX_SPANS_PER_UNIT, "TRAJECTORY_LIMIT_EXCEEDED")
    llms = 0
    counts: Counter[tuple[str, str]] = Counter()
    for raw in spans:
        span = _object(raw)
        span_id = _text(span.get("span_id"))
        _require(span_id not in seen, "TRAJECTORY_DUPLICATE_IDENTITY")
        seen.add(span_id)
        kind = _enum(span.get("kind"), ("llm", "tool"))
        if kind == "llm":
            llms += 1
        else:
            counts[
                (_text(span.get("tool_name"), 128), _enum(span.get("status"), ("ok", "error")))
            ] += 1
    return llms, [
        {"tool_name": name, "status": status, "count": count}
        for (name, status), count in sorted(counts.items())
    ]


def _row_index(rows: tuple[dict[str, Any], ...]) -> dict[Key, dict[str, Any]]:
    _require(type(rows) is tuple, "TRAJECTORY_SHAPE_INVALID")
    _require(len(rows) <= MAX_ROWS, "TRAJECTORY_LIMIT_EXCEEDED")
    result: dict[Key, dict[str, Any]] = {}
    for index, raw in enumerate(rows):
        row = _object(raw)
        _require(
            row.get("schema") == "goppi.paired_run_exchange.row.v1", "TRAJECTORY_UNSUPPORTED_VALUE"
        )
        key = _key(row)
        _require(key not in result, "TRAJECTORY_DUPLICATE_IDENTITY")
        _require(row.get("planned") is True)
        result[key] = {
            "row_index": index,
            "evidence_digest": _digest(row.get("evidence_digest")),
            "trace_digest": _digest(row.get("trace_digest")),
            "verifier": _verifier(row.get("verifier")),
            "outcome": _outcome(row.get("result")),
            "primary_inference": _boolean(row.get("primary_inference")),
            "repeat_proof": _enum(
                row.get("repeat_proof"), ("v1_primary_only", "v2_attested_repeat")
            ),
            "row_context": {field: _digest(row.get(field)) for field in ROW_CONTEXT_FIELDS},
        }
    return result


def _join(
    rows: dict[Key, dict[str, Any]], trajectory: dict[str, Any]
) -> tuple[dict[Key, dict[str, Any]], dict[str, int]]:
    joined: dict[Key, dict[str, Any]] = {}
    coverage: dict[str, int] = dict.fromkeys(AVAILABILITIES, 0)
    unit_keys: set[Key] = set()
    for unit_index, raw in enumerate(_array(trajectory.get("units"), MAX_UNITS)):
        unit = _object(raw)
        unit_identity = _object(unit.get("unit"))
        unit_key = _key({**unit_identity, "case_id": "unit", "split": "explore"})
        _require(unit_key not in unit_keys, "TRAJECTORY_DUPLICATE_IDENTITY")
        unit_keys.add(unit_key)
        availability = _enum(unit.get("source_availability"), AVAILABILITIES)
        retained = availability == "retained"
        completeness = _enum(unit.get("capture_completeness"), ("complete", "incomplete"))
        _require(completeness == ("complete" if retained else "incomplete"))
        reason = unit.get("unavailable_reason")
        if retained:
            _require(reason is None and unit.get("capture_profile") == PROFILE)
        else:
            reason = _enum(reason, UNAVAILABLE_REASONS)
        context = _context(unit.get("context"))
        seen_spans: set[str] = set()
        cases = _array(unit.get("cases"), MAX_ROWS)
        _require(len(cases) + len(joined) <= MAX_ROWS, "TRAJECTORY_LIMIT_EXCEEDED")
        for case_index, case_raw in enumerate(cases):
            case = _object(case_raw)
            key = _key(_object(case.get("row_key")))
            _require(key not in joined, "TRAJECTORY_DUPLICATE_IDENTITY")
            _require(key in rows, "TRAJECTORY_COVERAGE_MISMATCH")
            for field in (*PAIR_FIELDS[:3], "repeat_run_id", "repeat_index", "arm"):
                _require(
                    case["row_key"][field] == unit_identity.get(field),
                    "TRAJECTORY_BINDING_MISMATCH",
                )
            row = rows[key]
            for field in ("evidence_digest", "trace_digest"):
                _require(_digest(case.get(field)) == row[field], "TRAJECTORY_BINDING_MISMATCH")
            _require(
                _digest(unit.get("bundle_digest")) == row["evidence_digest"],
                "TRAJECTORY_BINDING_MISMATCH",
            )
            _require(
                _verifier(case.get("verifier")) == row["verifier"], "TRAJECTORY_BINDING_MISMATCH"
            )
            llms, tools = _spans(case.get("spans"), seen_spans)
            _require(retained or (llms == 0 and not tools), "TRAJECTORY_BINDING_MISMATCH")
            joined[key] = {
                **row,
                "case_pointer": f"/units/{unit_index}/cases/{case_index}",
                "context_pointer": f"/units/{unit_index}/context",
                "source_availability": availability,
                "capture_completeness": completeness,
                "unavailable_reason": reason,
                "llm_span_count": llms if retained else None,
                "tool_status_counts": tools if retained else None,
                "context": context,
                "verifier_process": _process(case.get("verifier_observation"), str(key[4])),
            }
            coverage[availability] += 1
    _require(set(joined) == set(rows), "TRAJECTORY_COVERAGE_MISMATCH")
    declared = _object(trajectory.get("availability_counts"))
    for name, count in {**coverage, "total": len(rows)}.items():
        _require(_integer(declared.get(name)) == count, "TRAJECTORY_COVERAGE_MISMATCH")
    return joined, coverage


def _comparability(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    reasons: list[dict[str, str]] = []
    values = {"context." + f: (left["context"][f], right["context"][f]) for f in CONTEXT_FIELDS}
    values.update(
        {"row." + f: (left["row_context"][f], right["row_context"][f]) for f in ROW_CONTEXT_FIELDS}
    )
    values.update(
        {
            "verifier." + f: (left["verifier"][f], right["verifier"][f])
            for f in ("verifier_id", "verifier_digest")
        }
    )
    values.update({f: (left[f], right[f]) for f in ("primary_inference", "repeat_proof")})
    values["outcome.value_kind"] = (
        left["outcome"].get("value_kind"),
        right["outcome"].get("value_kind"),
    )
    for field, (a, b) in sorted(values.items()):
        if a is None or b is None:
            reasons.append({"field": field, "reason": "missing_context"})
        elif type(a) is not type(b) or a != b:
            reasons.append({"field": field, "reason": "different_context"})
    state = (
        "not_comparable"
        if any(r["reason"] == "different_context" for r in reasons)
        else ("unknown" if reasons else "comparable")
    )
    return {"state": state, "reasons": reasons}


def _difference(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    outcome_changed = left["outcome"] != right["outcome"]
    if left["tool_status_counts"] is None or right["tool_status_counts"] is None:
        return {
            "state": "unavailable",
            "outcome_changed": outcome_changed,
            "tool_status_deltas": None,
            "llm_span_delta": None,
        }

    def counts(side: dict[str, Any]) -> dict[tuple[str, str], int]:
        return {(v["tool_name"], v["status"]): v["count"] for v in side["tool_status_counts"]}

    a, b = counts(left), counts(right)
    return {
        "state": "observed",
        "outcome_changed": outcome_changed,
        "llm_span_delta": right["llm_span_count"] - left["llm_span_count"],
        "tool_status_deltas": [
            {
                "tool_name": name,
                "status": status,
                "candidate_minus_baseline": b.get((name, status), 0) - a.get((name, status), 0),
            }
            for name, status in sorted(a.keys() | b.keys())
            if a.get((name, status), 0) != b.get((name, status), 0)
        ],
    }


def _side(value: dict[str, Any]) -> dict[str, Any]:
    return {key: val for key, val in value.items() if key not in {"row_context", "context"}}


def _comparisons(joined: dict[Key, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    pairs = sorted({key[:-1] for key in joined})
    for key in pairs:
        _require(
            (*key, "baseline") in joined and (*key, "candidate") in joined,
            "TRAJECTORY_COVERAGE_MISMATCH",
        )
        left, right = joined[(*key, "baseline")], joined[(*key, "candidate")]
        coordinates = dict(zip(PAIR_FIELDS, key, strict=True))
        result.append(
            {
                "id": _identity("comparison:", coordinates),
                "key": coordinates,
                "baseline": _side(left),
                "candidate": _side(right),
                "comparability": _comparability(left, right),
                "difference": _difference(left, right),
            }
        )
    return result


def _tasks(comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scopes: dict[str, list[dict[str, Any]]] = {kind: [] for kind in TASK_EVIDENCE}
    for item in comparisons:
        arms = ("baseline", "candidate")
        gaps = {
            "obtain_supported_capture": [
                a for a in arms if item[a]["source_availability"] != "retained"
            ],
            "obtain_verifier_process_linkage": [
                a for a in arms if item[a]["verifier_process"]["state"] != "observed"
            ],
            "check_outcome_comparability": list(arms)
            if item["comparability"]["state"] != "comparable"
            else [],
            "inspect_tool_status_difference": list(arms)
            if item["difference"]["tool_status_deltas"]
            else [],
            "obtain_call_relationships": list(arms)
            if item["difference"]["tool_status_deltas"]
            else [],
        }
        for kind, affected in gaps.items():
            if affected:
                scopes[kind].append({"comparison_id": item["id"], "arms": affected})
    return [
        {
            "id": _identity("task:", {"kind": kind, "scope": scope}),
            "kind": kind,
            "scope": scope,
            "owner": TASK_EVIDENCE[kind][0],
            "required_evidence": TASK_EVIDENCE[kind][1],
            "execution_authorized": False,
        }
        for kind, scope in sorted(scopes.items())
        if scope
    ]


def _stages(execution: dict[str, Any], comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in _array(execution.get("stages"), len(STAGES)):
        stage = _object(raw)
        name = _enum(stage.get("stage"), STAGES)
        _require(name not in seen, "TRAJECTORY_DUPLICATE_IDENTITY")
        seen.add(name)
        run_id = _digest(stage.get("stage_run_id"))
        split = _enum(stage.get("split"), ("explore", "confirm"))
        items = [item for item in comparisons if item["key"]["stage"] == name]
        _require(
            all(
                item["key"]["stage_run_id"] == run_id and item["key"]["split"] == split
                for item in items
            ),
            "TRAJECTORY_BINDING_MISMATCH",
        )
        status = _enum(stage.get("execution_status"), ("evaluated", "closed"))
        exported = _integer(stage.get("exported_row_count"))
        _require(exported == 2 * len(items), "TRAJECTORY_COVERAGE_MISMATCH")
        _require(status != "closed" or not items, "TRAJECTORY_COVERAGE_MISMATCH")
        entry: dict[str, Any] = {
            "stage": name,
            "stage_run_id": run_id,
            "split": split,
            "execution_status": status,
            "planned_case_count": _integer(stage.get("planned_case_count")),
            "paired_rows": exported,
            "case_stage_repeat_pairs": len(items),
            "primary_repeat_pairs": sum(
                i["baseline"]["primary_inference"] and i["candidate"]["primary_inference"]
                for i in items
            ),
            "capture_counts": {
                a: sum(
                    i[arm]["source_availability"] == a
                    for i in items
                    for arm in ("baseline", "candidate")
                )
                for a in AVAILABILITIES
            },
        }
        if status == "closed":
            entry["reason_code"] = _enum(stage.get("reason_code"), CLOSED_STAGE_REASONS)
            entry["classification"] = _enum(
                stage.get("classification"), CLOSED_STAGE_CLASSIFICATIONS
            )
        else:
            primary = _integer(stage.get("primary_inference_repeat_index"), 99)
            _require(
                all(
                    i[arm]["primary_inference"] == (i["key"]["repeat_index"] == primary)
                    for i in items
                    for arm in ("baseline", "candidate")
                ),
                "TRAJECTORY_BINDING_MISMATCH",
            )
        result.append(entry)
    _require(
        all(item["key"]["stage"] in seen for item in comparisons), "TRAJECTORY_COVERAGE_MISMATCH"
    )
    return sorted(result, key=lambda item: STAGES.index(item["stage"]))


def analyze_goppi_trajectory(
    execution: dict[str, Any],
    rows: tuple[dict[str, Any], ...],
    trajectory: dict[str, Any],
    identities: Mapping[str, tuple[str, int]],
) -> dict[str, object]:
    """Interpret admitted same-byte documents; never run or replace native verification."""
    _require(
        _object(execution).get("schema") == "goppi.paired_run_exchange.execution.v1",
        "TRAJECTORY_UNSUPPORTED_VALUE",
    )
    _require(
        _object(trajectory).get("schema") == "goppi.paired_trajectory_exchange.document.v1",
        "TRAJECTORY_UNSUPPORTED_VALUE",
    )
    _require(
        trajectory.get("profile") == PROFILE
        and trajectory.get("call_relationships") == "not_captured",
        "TRAJECTORY_UNSUPPORTED_VALUE",
    )
    _require(set(identities) == set(MEMBERS), "TRAJECTORY_COVERAGE_MISMATCH")
    for identity in identities.values():
        _require(
            type(identity) is tuple and len(identity) == _MEMBER_IDENTITY_PARTS,
            "TRAJECTORY_SHAPE_INVALID",
        )
    members = [
        {
            "path": path,
            "sha256": _digest(identities[path][0]),
            "bytes": _integer(identities[path][1], 128 * 1024 * 1024),
        }
        for path in MEMBERS
    ]
    source = _object(execution.get("source"))
    source_context = {
        field: _digest(source.get(field))
        for field in ("cycle_run_id", "plan_digest", "execution_identity_digest")
    }
    _require(
        all(_object(trajectory.get("source")).get(k) == v for k, v in source_context.items()),
        "TRAJECTORY_BINDING_MISMATCH",
    )
    index = _row_index(rows)
    _require(
        all(key[0] == source_context["cycle_run_id"] for key in index),
        "TRAJECTORY_BINDING_MISMATCH",
    )
    joined, coverage = _join(index, trajectory)
    comparisons = _comparisons(joined)
    stages = _stages(execution, comparisons)
    closed = [
        _enum(value, STAGES) for value in _array(trajectory.get("closed_stages"), len(STAGES))
    ]
    _require(
        set(closed) == {s["stage"] for s in stages if s["execution_status"] == "closed"}
        and len(set(closed)) == len(closed),
        "TRAJECTORY_COVERAGE_MISMATCH",
    )
    binding = _enum(
        _object(execution.get("claims")).get("outcome_harness_binding"),
        OUTCOME_HARNESS_BINDINGS,
    )
    return {
        "schema": RESULT_SCHEMA,
        "state": "analyzed",
        "policy": REVIEW_POLICY,
        "input_members": members,
        "source_context": source_context,
        "trust_scope": {
            "native_verification": "not_performed",
            "integrity_scope": "not_checked",
            "authenticity": "not_checked",
            "source_authentication_independently_verified": False,
            "producer_projection_independently_verified": False,
            "outcome_harness_binding": binding,
        },
        "populations": {
            "paired_rows": len(rows),
            "case_stage_repeat_pairs": len(comparisons),
            "unique_source_case_ids": len({k[4] for k in index}),
            "stage_case_slots": len({(k[0], k[1], k[2], k[3], k[4]) for k in index}),
            "primary_repeat_pairs": sum(s["primary_repeat_pairs"] for s in stages),
        },
        "capture_counts": coverage,
        "stages": stages,
        "comparisons": comparisons,
        "next_verification_tasks": _tasks(comparisons),
        "interpretation_limits": list(INTERPRETATION_LIMITS),
        "authority": {
            "human_review": "required",
            "execution_authorized": False,
            "merge_authorized": False,
            "revert_authorized": False,
        },
    }

"""Content-minimized index of selected DeepEval 4.2.8 trace structure.

Only observed case/span status and within-trace parent references are described.
No metric, execution completeness, chronology or causal relation is inferred.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import cast

from underwrite.instrument.evidence.deepeval_accounting import (
    MAX_CASES,
    DeepevalAccountingError,
)

SOURCE_FORMAT = "deepeval.test-run"
RULE_PROFILE = "deepeval.trace-index.v1"
MAX_SPANS = 1024
MAX_ID_LENGTH = 256
GROUP_TYPES = MappingProxyType(
    {
        "baseSpans": "base",
        "agentSpans": "agent",
        "llmSpans": "llm",
        "retrieverSpans": "retriever",
        "toolSpans": "tool",
    }
)
SPAN_TYPES = tuple(GROUP_TYPES.values())
STATUSES = ("SUCCESS", "ERRORED")
TRACE_AVAILABILITY = ("absent", "null", "present")
GROUP_AVAILABILITY = ("absent", "null", "empty", "present")
PARENT_STATES = ("not_reported", "resolved", "unresolved")
SCOPE = MappingProxyType(
    {
        "unit": "observed_span",
        "case_roster": "supplied_test_cases",
        "selected_fields_only": True,
        "planned_steps": "unknown",
        "instrumentation_completeness": "unknown",
        "source_claims_verified": False,
    }
)
LIMITATIONS = (
    "SOURCE_CLAIMS_UNVERIFIED",
    "SELECTED_FIELDS_ONLY",
    "PLANNED_STEPS_UNKNOWN",
    "INSTRUMENTATION_COMPLETENESS_UNKNOWN",
    "METRICS_AND_TIMING_NOT_ANALYZED",
    "PARENT_LINKS_NOT_CAUSAL_DEPENDENCIES",
    "NO_RETRY_OR_CHRONOLOGY_INFERENCE",
    "SOURCE_HASH_AND_POINTERS_LINKABLE",
    "NO_STATISTICAL_OR_APPROVAL_AUTHORITY",
)
NEXT_ACTIONS = (
    "INSPECT_ERRORED_STEP_CONTEXT",
    "RESOLVE_UNBOUND_PARENT_REFERENCES",
    "INSPECT_CYCLIC_PARENT_LINKS",
    "CHECK_INSTRUMENTATION_COVERAGE_IF_NEEDED",
)


def _error(reason: str, location: str, code: str = "INVALID_INPUT") -> DeepevalAccountingError:
    return DeepevalAccountingError(code, reason, location)


def _object(value: object, location: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _error("DEEPEVAL_TRACE_OBJECT_REQUIRED", location)
    return cast(dict[str, object], value)


def _array(value: object, location: str, maximum: int) -> list[object]:
    if type(value) is not list:
        raise _error("DEEPEVAL_TRACE_ARRAY_REQUIRED", location)
    values = cast(list[object], value)
    if len(values) > maximum:
        raise _error("DEEPEVAL_TRACE_POPULATION_LIMIT_EXCEEDED", location, "INPUT_LIMIT_EXCEEDED")
    return values


def _opaque_id(value: object, location: str) -> str:
    if type(value) is not str or not value:
        raise _error("DEEPEVAL_TRACE_ID_REQUIRED", location)
    if len(value) > MAX_ID_LENGTH:
        raise _error("DEEPEVAL_TRACE_ID_LIMIT_EXCEEDED", location, "INPUT_LIMIT_EXCEEDED")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _error("DEEPEVAL_TRACE_ID_INVALID", location) from None
    return value


def _status(value: object, location: str) -> str:
    if type(value) is not str or value not in STATUSES:
        raise _error("DEEPEVAL_TRACE_STATUS_UNSUPPORTED", location, "UNSUPPORTED_PROFILE")
    return cast(str, value)


def _structural_keys(value: dict[str, object], location: str, *, trace: bool) -> None:
    for key in value:
        if type(key) is not str:
            raise _error("DEEPEVAL_TRACE_STRUCTURE_UNSUPPORTED", location, "UNSUPPORTED_PROFILE")
        if (key == "spans" or key.endswith("Spans")) and (not trace or key not in GROUP_TYPES):
            raise _error("DEEPEVAL_TRACE_STRUCTURE_UNSUPPORTED", location, "UNSUPPORTED_PROFILE")


def _source_success(case: dict[str, object], location: str) -> bool | None:
    if "success" not in case or case["success"] is None:
        return None
    if type(case["success"]) is not bool:
        raise _error("DEEPEVAL_TRACE_CASE_SUCCESS_INVALID", location + "/success")
    return case["success"]


def _trace_status(trace: dict[str, object], location: str) -> str | None:
    if "status" not in trace or trace["status"] is None:
        return None
    return _status(trace["status"], location + "/status")


def _group_rows(
    trace: dict[str, object], location: str, remaining: int
) -> tuple[dict[str, str], list[tuple[str, list[object]]], int]:
    states: dict[str, str] = {}
    selected: list[tuple[str, list[object]]] = []
    count = 0
    for group in GROUP_TYPES:
        if group not in trace:
            states[group] = "absent"
            continue
        raw = trace[group]
        if raw is None:
            states[group] = "null"
            continue
        rows = _array(raw, location + "/" + group, remaining - count)
        states[group] = "empty" if not rows else "present"
        selected.append((group, rows))
        count += len(rows)
    return states, selected, count


def _mark_cycles(rows: list[dict[str, object]], parents: list[int | None]) -> None:
    """Mark only cycle members in the bounded functional parent graph, iteratively."""
    state = [0] * len(rows)
    for start in range(len(rows)):
        if state[start]:
            continue
        path: list[int] = []
        positions: dict[int, int] = {}
        current: int | None = start
        while current is not None and state[current] == 0:
            state[current] = 1
            positions[current] = len(path)
            path.append(current)
            current = parents[current]
        if current is not None and current in positions:
            for member in path[positions[current] :]:
                rows[member]["cycle_member"] = True
        for member in path:
            state[member] = 2


def _spans(selected: list[tuple[str, list[object]]], location: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    indexes: dict[str, int] = {}
    parents: list[str | None] = []
    for group, group_rows in selected:
        for group_index, value in enumerate(group_rows):
            pointer = f"{location}/{group}/{group_index}"
            span = _object(value, pointer)
            _structural_keys(span, pointer, trace=False)
            identifier = _opaque_id(span.get("uuid"), pointer + "/uuid")
            if identifier in indexes:
                raise _error("DEEPEVAL_TRACE_DUPLICATE_SPAN_ID", pointer)
            indexes[identifier] = len(rows)
            declared_type = span.get("type")
            if type(declared_type) is not str or declared_type not in SPAN_TYPES:
                raise _error(
                    "DEEPEVAL_TRACE_SPAN_TYPE_UNSUPPORTED", pointer + "/type", "UNSUPPORTED_PROFILE"
                )
            parent = span.get("parentUuid")
            if parent is not None:
                parent = _opaque_id(parent, pointer + "/parentUuid")
            parents.append(parent)
            rows.append(
                {
                    "span_index": len(rows),
                    "pointer": pointer,
                    "group": group,
                    "group_index": group_index,
                    "declared_type": declared_type,
                    "source_status": _status(span.get("status"), pointer + "/status"),
                    "parent_state": "not_reported",
                    "parent_pointer": None,
                    "cycle_member": False,
                    "group_type_mismatch": declared_type != GROUP_TYPES[group],
                }
            )
    resolved: list[int | None] = []
    for index, parent in enumerate(parents):
        if parent is None:
            resolved.append(None)
        elif parent in indexes:
            target = indexes[parent]
            resolved.append(target)
            rows[index]["parent_state"] = "resolved"
            rows[index]["parent_pointer"] = rows[target]["pointer"]
        else:
            resolved.append(None)
            rows[index]["parent_state"] = "unresolved"
    _mark_cycles(rows, resolved)
    return rows


def _counts(cases: list[dict[str, object]]) -> dict[str, object]:
    case_counts = {"total": len(cases), "source_true": 0, "source_false": 0, "source_unknown": 0}
    trace_counts = {
        "present": 0,
        "absent": 0,
        "null": 0,
        "status_success": 0,
        "status_errored": 0,
        "status_unknown": 0,
    }
    span_counts: dict[str, object] = {
        "total": 0,
        "by_group": dict.fromkeys(GROUP_TYPES, 0),
        "by_declared_type": dict.fromkeys(SPAN_TYPES, 0),
        "status_success": 0,
        "status_errored": 0,
        "parent_resolved": 0,
        "parent_unresolved": 0,
        "parent_not_reported": 0,
        "cycle_members": 0,
        "group_type_mismatch": 0,
    }
    groups = cast(dict[str, int], span_counts["by_group"])
    types = cast(dict[str, int], span_counts["by_declared_type"])
    for case in cases:
        success = case["source_success"]
        case_counts[
            "source_unknown" if success is None else "source_true" if success else "source_false"
        ] += 1
        availability = cast(str, case["trace_availability"])
        trace_counts[availability] += 1
        status = case["trace_status"]
        trace_counts[
            "status_unknown"
            if status is None
            else "status_success"
            if status == "SUCCESS"
            else "status_errored"
        ] += 1
        for row in cast(list[dict[str, object]], case["spans"]):
            span_counts["total"] = cast(int, span_counts["total"]) + 1
            groups[cast(str, row["group"])] += 1
            types[cast(str, row["declared_type"])] += 1
            key = "status_success" if row["source_status"] == "SUCCESS" else "status_errored"
            span_counts[key] = cast(int, span_counts[key]) + 1
            parent_key = "parent_" + cast(str, row["parent_state"])
            span_counts[parent_key] = cast(int, span_counts[parent_key]) + 1
            span_counts["cycle_members"] = cast(int, span_counts["cycle_members"]) + int(
                cast(bool, row["cycle_member"])
            )
            span_counts["group_type_mismatch"] = cast(
                int, span_counts["group_type_mismatch"]
            ) + int(cast(bool, row["group_type_mismatch"]))
    return {"cases": case_counts, "traces": trace_counts, "spans": span_counts}


def index_deepeval_trace(payload: object) -> dict[str, object]:
    """Index selected native case/span structure without returning source content."""
    source = _object(payload, "/")
    rows = _array(source.get("testCases"), "/testCases", MAX_CASES)
    conversational = source.get("conversationalTestCases")
    if conversational is not None:
        conversational_rows = _array(conversational, "/conversationalTestCases", MAX_CASES)
        if conversational_rows:
            raise _error(
                "DEEPEVAL_TRACE_CONVERSATIONAL_UNSUPPORTED",
                "/conversationalTestCases",
                "UNSUPPORTED_PROFILE",
            )
    cases: list[dict[str, object]] = []
    total_spans = 0
    incomplete_structure = False
    for index, value in enumerate(rows):
        pointer = f"/testCases/{index}"
        case = _object(value, pointer)
        success = _source_success(case, pointer)
        if "trace" not in case:
            availability, status, groups, spans = (
                "absent",
                None,
                dict.fromkeys(GROUP_TYPES, "absent"),
                [],
            )
        elif case["trace"] is None:
            availability, status, groups, spans = (
                "null",
                None,
                dict.fromkeys(GROUP_TYPES, "absent"),
                [],
            )
        else:
            availability = "present"
            trace_pointer = pointer + "/trace"
            trace = _object(case["trace"], trace_pointer)
            _structural_keys(trace, trace_pointer, trace=True)
            _opaque_id(trace.get("uuid"), trace_pointer + "/uuid")
            status = _trace_status(trace, trace_pointer)
            groups, selected, count = _group_rows(trace, trace_pointer, MAX_SPANS - total_spans)
            total_spans += count
            spans = _spans(selected, trace_pointer)
        incomplete_structure |= availability != "present" or any(
            state in ("absent", "null") for state in groups.values()
        )
        cases.append(
            {
                "case_index": index,
                "pointer": pointer,
                "source_success": success,
                "trace_availability": availability,
                "trace_status": status,
                "groups": groups,
                "spans": spans,
            }
        )
    counts = _counts(cases)
    span_counts = cast(dict[str, object], counts["spans"])
    actions: list[str] = []
    if span_counts["status_errored"]:
        actions.append(NEXT_ACTIONS[0])
    if span_counts["parent_unresolved"]:
        actions.append(NEXT_ACTIONS[1])
    if span_counts["cycle_members"]:
        actions.append(NEXT_ACTIONS[2])
    if incomplete_structure:
        actions.append(NEXT_ACTIONS[3])
    return {
        "rule_profile": RULE_PROFILE,
        "scope": dict(SCOPE),
        "cases": cases,
        "counts": counts,
        "limitations": list(LIMITATIONS),
        "next_actions": actions,
    }

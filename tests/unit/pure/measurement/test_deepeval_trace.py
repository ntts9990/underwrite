"""Trace-index facts are observed source structure, not metric or causal verdicts."""

from __future__ import annotations

import json
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import canonical_bytes

from underwrite.instrument.evidence.deepeval_accounting import (
    MAX_CASES,
    DeepevalAccountingError,
)
from underwrite.instrument.evidence.deepeval_trace import (
    GROUP_TYPES,
    MAX_ID_LENGTH,
    MAX_SPANS,
    index_deepeval_trace,
)

FIXTURES = Path("fixtures/golden/external/deepeval-4.2.8/traces")
OUTPUT_MAX_BYTES = 1024 * 1024


def span(identifier: str = "span", **changes: object) -> dict[str, object]:
    return {"uuid": identifier, "type": "tool", "status": "SUCCESS", **changes}


def case(trace: object = None, **changes: object) -> dict[str, object]:
    return {"success": True, "trace": {"uuid": "trace"} if trace is None else trace, **changes}


def run(cases: Sequence[object], **changes: object) -> dict[str, Any]:
    return cast(dict[str, Any], index_deepeval_trace({"testCases": list(cases), **changes}))


def test_unmodified_native_cases_keep_errored_step_separate_from_success() -> None:
    for name, expected_errored in (("success", 0), ("recovery", 1)):
        source = json.loads((FIXTURES / f"{name}.json").read_bytes())
        before = deepcopy(source)
        result = cast(dict[str, Any], index_deepeval_trace(source))
        assert source == before
        counts = cast(dict[str, Any], result["counts"])
        assert counts["cases"]["source_true"] == 1
        assert counts["traces"]["status_success"] == 1
        assert counts["spans"]["status_errored"] == expected_errored
        only = cast(list[dict[str, Any]], result["cases"])[0]
        assert counts["spans"]["total"] == len(only["spans"])
        assert only["source_success"] is True and only["trace_status"] == "SUCCESS"
        assert only["groups"]["toolSpans"] == "present"
        tools = [row for row in only["spans"] if row["group"] == "toolSpans"]
        assert [row["parent_pointer"] for row in tools] == [
            "/testCases/0/trace/agentSpans/0",
            "/testCases/0/trace/agentSpans/0",
        ]
        if expected_errored:
            assert tools[0]["source_status"] == "ERRORED"
            assert "INSPECT_ERRORED_STEP_CONTEXT" in result["next_actions"]
        else:
            assert not result["next_actions"]
        output = json.dumps(result)
        for source_text in (
            "synthetic expected miss",
            "lookup_rule",
            "primary_lookup",
            "trace_fixed",
            "2+2?",
        ):
            assert source_text not in output


def test_absent_null_empty_and_present_states_preserve_unknowns() -> None:
    result = run(
        [
            {"success": None},
            {"success": False, "trace": None},
            case({"uuid": "t3"}),
            case(
                {
                    "uuid": "t4",
                    "status": None,
                    "baseSpans": None,
                    "agentSpans": [],
                    "toolSpans": [span()],
                }
            ),
        ]
    )
    rows = result["cases"]
    assert [row["trace_availability"] for row in rows] == ["absent", "null", "present", "present"]
    assert all(row["trace_status"] is None for row in rows)
    assert rows[0]["groups"] == dict.fromkeys(GROUP_TYPES, "absent")
    assert rows[3]["groups"] == {
        "baseSpans": "null",
        "agentSpans": "empty",
        "llmSpans": "absent",
        "retrieverSpans": "absent",
        "toolSpans": "present",
    }
    assert result["counts"]["cases"]["source_unknown"] == 1
    assert result["counts"]["traces"]["status_unknown"] == len(rows)
    assert result["counts"]["spans"]["total"] == 1
    assert "CHECK_INSTRUMENTATION_COVERAGE_IF_NEEDED" in result["next_actions"]


def test_group_type_mismatch_and_non_nfc_opaque_ids_are_retained_only_as_structure() -> None:
    identifier = "e\u0301-private-id"
    value = {
        "testCases": [
            case(
                {
                    "uuid": "trace",
                    "toolSpans": [
                        span(
                            identifier,
                            type="agent",
                            name="PRIVATE_NAME",
                            status="ERRORED",
                            error="PRIVATE_ERROR",
                        ),
                        span("second", parentUuid=identifier, name="PRIVATE_NAME"),
                    ],
                    "traceMetricsScores": {"tool": {"PRIVATE": {}}},
                },
                toolsCalled=[{"name": "PRIVATE_TOOL"}],
            )
        ]
    }
    result = cast(dict[str, Any], index_deepeval_trace(value))
    rows = cast(list[dict[str, Any]], result["cases"])[0]["spans"]
    assert rows[0]["group"] == "toolSpans" and rows[0]["declared_type"] == "agent"
    assert rows[0]["group_type_mismatch"] is True
    assert rows[1]["parent_pointer"] == rows[0]["pointer"]
    assert result["counts"]["spans"]["group_type_mismatch"] == 1
    assert result["counts"]["spans"]["total"] == len(rows)
    for private in (identifier, "PRIVATE_NAME", "PRIVATE_ERROR", "PRIVATE_TOOL"):
        assert private not in json.dumps(result)


def test_duplicates_within_trace_refuse_but_ids_reused_across_cases_do_not_join() -> None:
    duplicate = case({"uuid": "t", "toolSpans": [span("same"), span("same")]})
    with pytest.raises(DeepevalAccountingError) as exc:
        run([duplicate])
    assert (exc.value.code, exc.value.reason, exc.value.location) == (
        "INVALID_INPUT",
        "DEEPEVAL_TRACE_DUPLICATE_SPAN_ID",
        "/testCases/0/trace/toolSpans/1",
    )
    result = run(
        [
            case({"uuid": "t", "toolSpans": [span("same")]}),
            case({"uuid": "t", "toolSpans": [span("same", parentUuid="outside")]}),
        ]
    )
    assert result["counts"]["spans"]["total"] == len(result["cases"])
    assert result["cases"][1]["spans"][0]["parent_state"] == "unresolved"


def test_cycles_mark_only_members_and_preserve_unrelated_errored_row() -> None:
    rows = [
        span("self", parentUuid="self"),
        span("a", parentUuid="b"),
        span("b", parentUuid="a"),
        span("error", status="ERRORED", parentUuid="outside"),
        span("leaf", parentUuid="a"),
    ]
    result = run([case({"uuid": "trace", "toolSpans": rows})])
    output = result["cases"][0]["spans"]
    assert [row["cycle_member"] for row in output] == [True, True, True, False, False]
    assert output[3]["source_status"] == "ERRORED" and output[3]["parent_state"] == "unresolved"
    assert output[4]["parent_pointer"] == output[1]["pointer"]
    assert output[4]["cycle_member"] is False
    assert result["counts"]["spans"]["cycle_members"] == sum(row["cycle_member"] for row in output)
    assert result["counts"]["spans"]["parent_unresolved"] == 1
    assert result["next_actions"][:3] == [
        "INSPECT_ERRORED_STEP_CONTEXT",
        "RESOLVE_UNBOUND_PARENT_REFERENCES",
        "INSPECT_CYCLIC_PARENT_LINKS",
    ]


def test_long_parent_chain_is_iterative_and_global_budget_is_enforced() -> None:
    chain = [
        span(f"span-{index}", parentUuid=f"span-{index - 1}") if index else span("span-0")
        for index in range(MAX_SPANS)
    ]
    result = run([case({"uuid": "trace", "toolSpans": chain})])
    output = result["cases"][0]["spans"]
    assert len(output) == MAX_SPANS and not any(row["cycle_member"] for row in output)
    assert output[-1]["parent_pointer"] == output[-2]["pointer"]
    split = [
        case({"uuid": "a", "toolSpans": chain[:600]}),
        case({"uuid": "b", "toolSpans": chain[600:] + [span("extra")]}),
    ]
    with pytest.raises(DeepevalAccountingError) as exc:
        run(split)
    assert (exc.value.code, exc.value.location) == (
        "INPUT_LIMIT_EXCEEDED",
        "/testCases/1/trace/toolSpans",
    )


def test_maximum_case_span_body_fits_report_byte_budget() -> None:
    cases = [
        case(
            {
                "uuid": f"trace-{index}",
                "agentSpans": [span(f"a-{index}", type="agent")],
                "toolSpans": [span(f"b-{index}", parentUuid=f"a-{index}")],
            }
        )
        for index in range(MAX_CASES)
    ]
    result = run(cases)
    assert result["counts"]["spans"]["total"] == MAX_SPANS
    # The shell's actual maximal-report test owns the current envelope/newline budget.
    # This pure check covers only the index body returned by its calculation owner.
    assert len(canonical_bytes(result)) <= OUTPUT_MAX_BYTES


MALFORMED: list[tuple[object, str, str, str]] = [
    (
        {"testCases": [{}], "conversationalTestCases": [{}]},
        "UNSUPPORTED_PROFILE",
        "DEEPEVAL_TRACE_CONVERSATIONAL_UNSUPPORTED",
        "/conversationalTestCases",
    ),
    (
        {"testCases": [{"trace": {}}]},
        "INVALID_INPUT",
        "DEEPEVAL_TRACE_ID_REQUIRED",
        "/testCases/0/trace/uuid",
    ),
    (
        {"testCases": [case({"uuid": "t", "unknownSpans": []})]},
        "UNSUPPORTED_PROFILE",
        "DEEPEVAL_TRACE_STRUCTURE_UNSUPPORTED",
        "/testCases/0/trace",
    ),
    (
        {"testCases": [case({"uuid": "t", "toolSpans": [span(extraSpans=[])]})]},
        "UNSUPPORTED_PROFILE",
        "DEEPEVAL_TRACE_STRUCTURE_UNSUPPORTED",
        "/testCases/0/trace/toolSpans/0",
    ),
    (
        {"testCases": [case({"uuid": "t", "toolSpans": [span(status="PRIVATE_STATUS")]})]},
        "UNSUPPORTED_PROFILE",
        "DEEPEVAL_TRACE_STATUS_UNSUPPORTED",
        "/testCases/0/trace/toolSpans/0/status",
    ),
    (
        {"testCases": [case({"uuid": "t", "toolSpans": [span(type="PRIVATE_TYPE")]})]},
        "UNSUPPORTED_PROFILE",
        "DEEPEVAL_TRACE_SPAN_TYPE_UNSUPPORTED",
        "/testCases/0/trace/toolSpans/0/type",
    ),
    (
        {"testCases": [case({"uuid": "t", "toolSpans": [span("x" * (MAX_ID_LENGTH + 1))]})]},
        "INPUT_LIMIT_EXCEEDED",
        "DEEPEVAL_TRACE_ID_LIMIT_EXCEEDED",
        "/testCases/0/trace/toolSpans/0/uuid",
    ),
    (
        {"testCases": [case({"uuid": "t", "toolSpans": [span(parentUuid="")]})]},
        "INVALID_INPUT",
        "DEEPEVAL_TRACE_ID_REQUIRED",
        "/testCases/0/trace/toolSpans/0/parentUuid",
    ),
    (
        {"testCases": [case({"uuid": "t", "toolSpans": {}})]},
        "INVALID_INPUT",
        "DEEPEVAL_TRACE_ARRAY_REQUIRED",
        "/testCases/0/trace/toolSpans",
    ),
    (
        {"testCases": [case({"uuid": "t", "status": "PRIVATE_STATUS"})]},
        "UNSUPPORTED_PROFILE",
        "DEEPEVAL_TRACE_STATUS_UNSUPPORTED",
        "/testCases/0/trace/status",
    ),
    (
        {"testCases": [case({"uuid": "t"}, success=1)]},
        "INVALID_INPUT",
        "DEEPEVAL_TRACE_CASE_SUCCESS_INVALID",
        "/testCases/0/success",
    ),
]


@pytest.mark.parametrize("bad,code,reason,location", MALFORMED)
def test_malformed_selected_fields_refuse_with_fixed_private_free_diagnostic(
    bad: object, code: str, reason: str, location: str
) -> None:
    with pytest.raises(DeepevalAccountingError) as exc:
        index_deepeval_trace(bad)
    assert (exc.value.code, exc.value.reason, exc.value.location) == (code, reason, location)
    assert "PRIVATE" not in str(exc.value)

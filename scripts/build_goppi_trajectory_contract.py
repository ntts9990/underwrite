"""Generate the closed descriptive trajectory result from the pure policy vocabulary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _schema_compose import nullable, object_schema, reference, tagged

from underwrite.instrument.evidence.goppi_trajectory import (
    AVAILABILITIES,
    CLOSED_STAGE_CLASSIFICATIONS,
    CLOSED_STAGE_REASONS,
    INTERPRETATION_LIMITS,
    MAX_ROWS,
    MAX_SPANS_PER_UNIT,
    MEMBERS,
    OUTCOME_HARNESS_BINDINGS,
    OUTCOME_REASONS,
    RESULT_SCHEMA,
    REVIEW_POLICY,
    STAGES,
    TASK_EVIDENCE,
    UNAVAILABLE_REASONS,
)

JSON = dict[str, Any]
ROOT = Path(__file__).resolve().parents[1]


def array(items: JSON, maximum: int = MAX_ROWS) -> JSON:
    return {"type": "array", "items": items, "maxItems": maximum}


def const(value: object) -> JSON:
    return {"const": value}


def text(maximum: int = 4096) -> JSON:
    return {"type": "string", "minLength": 1, "maxLength": maximum}


def enum(values: tuple[str, ...]) -> JSON:
    return {"enum": list(values)}


def integer(maximum: int = MAX_ROWS) -> JSON:
    return {"type": "integer", "minimum": 0, "maximum": maximum}


def build() -> JSON:
    digest = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
    boolean = {"type": "boolean"}
    null = {"type": "null"}
    definitions: dict[str, JSON] = {}
    definitions["coverage"] = object_schema({a: integer() for a in AVAILABILITIES})
    definitions["verifier"] = object_schema({"verifier_id": text(128), "verifier_digest": digest})
    observed = object_schema(
        {
            "status": const("observed"),
            "value_kind": enum(("binary", "scalar")),
            "verifier_value": {},
            "scored_value": {},
        }
    )
    observed["allOf"] = [
        {
            "if": {"properties": {"value_kind": const(kind)}},
            "then": {"properties": {"verifier_value": shape, "scored_value": shape}},
        }
        for kind, shape in (("binary", boolean), ("scalar", {"type": "number"}))
    ]
    definitions["outcome"] = tagged(
        [
            observed,
            object_schema(
                {
                    "status": const("endpoint_failure"),
                    "reason": enum(OUTCOME_REASONS["endpoint_failure"]),
                    "verifier_value": null,
                    "scored_value": const(False),
                }
            ),
            object_schema(
                {
                    "status": const("missing"),
                    "reason": enum(OUTCOME_REASONS["missing"]),
                    "verifier_value": null,
                    "scored_value": null,
                }
            ),
        ],
        field="status",
    )
    definitions["process"] = tagged(
        [
            object_schema(
                {
                    "state": const("observed"),
                    "attempt_id": text(256),
                    "receipt_identity_digest": digest,
                    "expected_public_identity_digest": digest,
                }
            ),
            object_schema({"state": const("not_observed")}),
            object_schema({"state": const("unlinked")}),
        ]
    )
    definitions["tool_count"] = object_schema(
        {
            "tool_name": text(128),
            "status": enum(("ok", "error")),
            "count": integer(MAX_SPANS_PER_UNIT),
        }
    )
    side = object_schema(
        {
            "row_index": integer(),
            "evidence_digest": digest,
            "trace_digest": digest,
            "verifier": reference("verifier"),
            "outcome": reference("outcome"),
            "primary_inference": boolean,
            "repeat_proof": enum(("v1_primary_only", "v2_attested_repeat")),
            "case_pointer": {"type": "string", "pattern": "^/units/[0-9]+/cases/[0-9]+$"},
            "source_availability": enum(AVAILABILITIES),
            "capture_completeness": enum(("complete", "incomplete")),
            "unavailable_reason": nullable(enum(UNAVAILABLE_REASONS)),
            "llm_span_count": nullable(integer(MAX_SPANS_PER_UNIT)),
            "tool_status_counts": nullable(array(reference("tool_count"), MAX_SPANS_PER_UNIT)),
            "context_pointer": {"type": "string", "pattern": "^/units/[0-9]+/context$"},
            "verifier_process": reference("process"),
        }
    )
    side["allOf"] = [
        {
            "if": {"properties": {"source_availability": const("retained")}},
            "then": {
                "properties": {
                    "capture_completeness": const("complete"),
                    "unavailable_reason": null,
                    "llm_span_count": integer(MAX_SPANS_PER_UNIT),
                    "tool_status_counts": array(reference("tool_count"), MAX_SPANS_PER_UNIT),
                }
            },
            "else": {
                "properties": {
                    "capture_completeness": const("incomplete"),
                    "unavailable_reason": enum(UNAVAILABLE_REASONS),
                    "llm_span_count": null,
                    "tool_status_counts": null,
                }
            },
        }
    ]
    definitions["side"] = side
    definitions["key"] = object_schema(
        {
            "cycle_run_id": digest,
            "stage_run_id": digest,
            "stage": enum(STAGES),
            "split": enum(("explore", "confirm")),
            "case_id": text(),
            "repeat_run_id": digest,
            "repeat_index": integer(99),
        }
    )
    definitions["difference"] = tagged(
        [
            object_schema(
                {
                    "state": const("unavailable"),
                    "outcome_changed": boolean,
                    "tool_status_deltas": null,
                    "llm_span_delta": null,
                }
            ),
            object_schema(
                {
                    "state": const("observed"),
                    "outcome_changed": boolean,
                    "llm_span_delta": {
                        "type": "integer",
                        "minimum": -MAX_SPANS_PER_UNIT,
                        "maximum": MAX_SPANS_PER_UNIT,
                    },
                    "tool_status_deltas": array(
                        object_schema(
                            {
                                "tool_name": text(128),
                                "status": enum(("ok", "error")),
                                "candidate_minus_baseline": {
                                    "type": "integer",
                                    "minimum": -MAX_SPANS_PER_UNIT,
                                    "maximum": MAX_SPANS_PER_UNIT,
                                },
                            }
                        ),
                        2 * MAX_SPANS_PER_UNIT,
                    ),
                }
            ),
        ]
    )
    definitions["comparison"] = object_schema(
        {
            "id": {"type": "string", "pattern": "^comparison:[0-9a-f]{64}$"},
            "key": reference("key"),
            "baseline": reference("side"),
            "candidate": reference("side"),
            "comparability": object_schema(
                {
                    "state": enum(("comparable", "unknown", "not_comparable")),
                    "reasons": array(
                        object_schema(
                            {
                                "field": text(128),
                                "reason": enum(("missing_context", "different_context")),
                            }
                        ),
                        64,
                    ),
                }
            ),
            "difference": reference("difference"),
        }
    )
    stage_fields = {
        "stage": enum(STAGES),
        "stage_run_id": digest,
        "split": enum(("explore", "confirm")),
        "planned_case_count": integer(),
        "paired_rows": integer(),
        "case_stage_repeat_pairs": integer(),
        "primary_repeat_pairs": integer(),
        "capture_counts": reference("coverage"),
    }
    definitions["stage"] = tagged(
        [
            object_schema({**stage_fields, "execution_status": const("evaluated")}),
            object_schema(
                {
                    **stage_fields,
                    "execution_status": const("closed"),
                    "reason_code": enum(CLOSED_STAGE_REASONS),
                    "classification": enum(CLOSED_STAGE_CLASSIFICATIONS),
                }
            ),
        ],
        field="execution_status",
    )
    task_fields = {
        "id": {"type": "string", "pattern": "^task:[0-9a-f]{64}$"},
        "scope": array(
            object_schema(
                {
                    "comparison_id": {"type": "string", "pattern": "^comparison:[0-9a-f]{64}$"},
                    "arms": array(enum(("baseline", "candidate")), 2),
                }
            )
        ),
        "execution_authorized": const(False),
    }
    definitions["task"] = tagged(
        [
            object_schema(
                {
                    **task_fields,
                    "kind": const(kind),
                    "owner": const(owner),
                    "required_evidence": const(evidence),
                }
            )
            for kind, (owner, evidence) in TASK_EVIDENCE.items()
        ],
        field="kind",
    )
    result = object_schema(
        {
            "schema": const(RESULT_SCHEMA),
            "state": const("analyzed"),
            "policy": const(REVIEW_POLICY),
            "input_members": array(
                object_schema(
                    {"path": enum(MEMBERS), "sha256": digest, "bytes": integer(128 * 1024 * 1024)}
                ),
                len(MEMBERS),
            ),
            "source_context": object_schema(
                {k: digest for k in ("cycle_run_id", "plan_digest", "execution_identity_digest")}
            ),
            "trust_scope": tagged(
                [
                    object_schema(
                        {
                            "native_verification": const(state),
                            "integrity_scope": const(integrity),
                            "authenticity": const(authenticity),
                            "source_authentication_independently_verified": const(False),
                            "producer_projection_independently_verified": const(False),
                            "outcome_harness_binding": enum(OUTCOME_HARNESS_BINDINGS),
                        }
                    )
                    for state, integrity, authenticity in (
                        ("not_performed", "not_checked", "not_checked"),
                        (
                            "local_goppi_same_byte_snapshot",
                            "self_consistent_content_closure",
                            "content_digest_only",
                        ),
                    )
                ],
                field="native_verification",
            ),
            "populations": object_schema(
                {
                    k: integer()
                    for k in (
                        "paired_rows",
                        "case_stage_repeat_pairs",
                        "unique_source_case_ids",
                        "stage_case_slots",
                        "primary_repeat_pairs",
                    )
                }
            ),
            "capture_counts": reference("coverage"),
            "stages": array(reference("stage"), len(STAGES)),
            "comparisons": array(reference("comparison")),
            "next_verification_tasks": array(reference("task"), len(TASK_EVIDENCE)),
            "interpretation_limits": const(list(INTERPRETATION_LIMITS)),
            "authority": object_schema(
                {
                    "human_review": const("required"),
                    "execution_authorized": const(False),
                    "merge_authorized": const(False),
                    "revert_authorized": const(False),
                }
            ),
        }
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:underwrite:contract:{RESULT_SCHEMA}",
        **result,
        "$defs": definitions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = ROOT / "contracts" / f"{RESULT_SCHEMA}.schema.json"
    rendered = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if path.read_text() != rendered:
            raise SystemExit("STALE_GOPPI_TRAJECTORY_CONTRACT")
    else:
        path.write_text(rendered)


if __name__ == "__main__":
    main()

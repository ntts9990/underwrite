"""Generate the closed descriptive trajectory result from the pure policy vocabulary."""

from __future__ import annotations

import argparse
import itertools
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from _schema_compose import nullable, object_schema, reference, tagged

from underwrite.instrument.evidence.goppi_trajectory import (
    ABSENT_STAGE_LIMIT,
    AVAILABILITIES,
    CAPTURE_PROFILE_BY_RUNNER,
    CLOSED_STAGE_CLASSIFICATIONS,
    CLOSED_STAGE_REASONS,
    CURRENT_INTERPRETATION_LIMITS,
    CYCLE_LIMITS,
    CYCLE_STATUSES,
    CYCLE_TASK_EVIDENCE,
    DIGEST_ONLY_VERDICT_LIMIT,
    INTERPRETATION_LIMITS,
    MAX_ROWS,
    MAX_SPANS_PER_UNIT,
    MEMBERS,
    NOT_RUN_REASONS,
    OUTCOME_ACTION_TASK_EVIDENCE,
    OUTCOME_ACTIONS_POLICY,
    OUTCOME_ACTIONS_POLICY_V2,
    OUTCOME_ACTIONS_SCHEMA,
    OUTCOME_ACTIONS_SCHEMA_V3,
    OUTCOME_ACTIONS_SCHEMA_V4,
    OUTCOME_HARNESS_BINDINGS,
    OUTCOME_REASONS,
    RESULT_SCHEMA,
    RESULT_SCHEMA_V2,
    RESULT_SCHEMA_V3,
    RESULT_SCHEMA_V4,
    REVIEW_POLICY,
    REVIEW_POLICY_V2,
    STAGE_VERDICT_STATES,
    STAGES,
    STOP_REASON_LIMIT,
    TASK_EVIDENCE,
    UNAVAILABLE_REASONS,
    V2_COMPARABILITY_INTERVENTION_FIELD,
    V2_COMPARABILITY_INTERVENTION_STATES,
    V2_COMPARABILITY_INVARIANT_FIELDS,
)

JSON = dict[str, Any]
ROOT = Path(__file__).resolve().parents[1]
V2 = 2
V3 = 3
V4 = 4
SCHEMAS = {
    (False, 1): RESULT_SCHEMA,
    (True, 1): OUTCOME_ACTIONS_SCHEMA,
    # The direct-only v2 outcome-actions output is retired; v2 review remains readable.
    (False, V2): RESULT_SCHEMA_V2,
    (False, V3): RESULT_SCHEMA_V3,
    (True, V3): OUTCOME_ACTIONS_SCHEMA_V3,
    (False, V4): RESULT_SCHEMA_V4,
    (True, V4): OUTCOME_ACTIONS_SCHEMA_V4,
}
STOPPED_TASK = "obtain_stopped_cycle_stage_record"
VERDICT_TASK = "obtain_evaluated_stage_verdict"


def array(items: JSON, maximum: int = MAX_ROWS) -> JSON:
    return {"type": "array", "items": items, "maxItems": maximum}


def _comparability_schema(version: int) -> JSON:
    fields: JSON = {
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
    if version == V4:
        fields.update(
            {
                "intervention": object_schema(
                    {
                        "field": const(V2_COMPARABILITY_INTERVENTION_FIELD),
                        "state": enum(V2_COMPARABILITY_INTERVENTION_STATES),
                    }
                ),
                "held_constant": {
                    "type": "array",
                    "items": enum(V2_COMPARABILITY_INVARIANT_FIELDS),
                    "maxItems": len(V2_COMPARABILITY_INVARIANT_FIELDS),
                    "uniqueItems": True,
                },
            }
        )
    return object_schema(fields)


def const(value: object) -> JSON:
    return {"const": value}


def text(maximum: int = 4096) -> JSON:
    return {"type": "string", "minLength": 1, "maxLength": maximum}


def enum(values: tuple[str, ...]) -> JSON:
    return {"enum": list(values)}


def enum_values(values: Sequence[Sequence[str]]) -> JSON:
    """Exact ordered arrays; each admitted combination is listed explicitly."""
    return {"enum": [list(value) for value in values]}


def integer(maximum: int = MAX_ROWS) -> JSON:
    return {"type": "integer", "minimum": 0, "maximum": maximum}


def _all(*schemas: JSON) -> JSON:
    return {"allOf": list(schemas)}


def _stages_contain(name: str | None = None, status: str | None = None) -> JSON:
    item: JSON = {"required": [], "properties": {}}
    for field, value in (("stage", name), ("execution_status", status)):
        if value is not None:
            item["required"].append(field)
            item["properties"][field] = const(value)
    return {"required": ["stages"], "properties": {"stages": {"contains": item}}}


def _cycle(status: str | None = None, absent: str | None = None) -> JSON:
    fields: JSON = {}
    if status is not None:
        fields["status"] = const(status)
    if absent is not None:
        fields["stages_absent_from_export"] = {"contains": const(absent)}
    cycle = {"required": list(fields), "properties": fields}
    return {"required": ["cycle"], "properties": {"cycle": cycle}}


def _task(kind: str, stage: str | None = None) -> JSON:
    item: JSON = {"required": ["kind"], "properties": {"kind": const(kind)}}
    if stage is not None:
        item["required"].append("scope")
        item["properties"]["scope"] = {
            "contains": {"required": ["stage"], "properties": {"stage": const(stage)}}
        }
    return {"contains": item}


def _when(condition: JSON, present: JSON) -> JSON:
    """Require every listed array rule exactly when the condition holds."""
    return {
        "if": condition,
        "then": {"properties": present},
        "else": {"properties": {name: {"not": rule} for name, rule in present.items()}},
    }


def _cycle_bindings() -> list[JSON]:
    """Bind conditional limits, cycle tasks and the stages they name to the stage facts.

    Digest equality and runner row sums cannot be expressed here; the pure owner keeps them.
    """
    stopped = _cycle(status="stopped")
    evaluated = _stages_contain(status="evaluated")
    closed = _stages_contain(status="closed")
    stopped_with_absent = _all(
        stopped,
        {"properties": {"cycle": {"properties": {"stages_absent_from_export": {"minItems": 1}}}}},
    )
    bindings: list[JSON] = []
    for name in STAGES:
        bindings.append(
            {
                "if": _stages_contain(name),
                "then": {"not": _cycle(absent=name)},
                "else": _cycle(absent=name),
            }
        )
        bindings.append(
            _when(
                _all(stopped, _cycle(absent=name)),
                {"next_verification_tasks": _task(STOPPED_TASK, name)},
            )
        )
        bindings.append(
            _when(
                _stages_contain(name, "evaluated"),
                {"next_verification_tasks": _task(VERDICT_TASK, name)},
            )
        )
    bindings.extend(
        [
            _when(
                stopped_with_absent,
                {
                    "interpretation_limits": {"contains": const(ABSENT_STAGE_LIMIT)},
                    "next_verification_tasks": _task(STOPPED_TASK),
                },
            ),
            _when(
                _all(stopped, {"not": closed}),
                {"interpretation_limits": {"contains": const(STOP_REASON_LIMIT)}},
            ),
            _when(
                evaluated,
                {
                    "interpretation_limits": {"contains": const(DIGEST_ONLY_VERDICT_LIMIT)},
                    "next_verification_tasks": _task(VERDICT_TASK),
                },
            ),
        ]
    )
    return bindings


def _add_cycle_disclosures(result: JSON) -> JSON:
    properties = result["properties"]
    disclosed: JSON = {}
    for name, value in properties.items():
        disclosed[name] = value
        if name == "source_context":
            disclosed["cycle"] = object_schema(
                {
                    "status": enum(CYCLE_STATUSES),
                    "stages_absent_from_export": {
                        "type": "array",
                        "items": enum(STAGES),
                        "maxItems": len(STAGES),
                        "uniqueItems": True,
                    },
                }
            )
            disclosed["runner"] = object_schema(
                {
                    "profile": enum(tuple(CAPTURE_PROFILE_BY_RUNNER)),
                    "models": array(
                        object_schema(
                            {
                                "runner_model": text(128),
                                "paired_rows": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": MAX_ROWS,
                                },
                            }
                        )
                    ),
                    "model_unrecorded_rows": integer(),
                }
            )
    disclosed["interpretation_limits"] = enum_values(
        [
            [*CURRENT_INTERPRETATION_LIMITS, *itertools.compress(CYCLE_LIMITS, flags)]
            for flags in itertools.product((False, True), repeat=len(CYCLE_LIMITS))
        ]
    )
    return {**object_schema(disclosed), "allOf": _cycle_bindings()}


def _v4_stage(stage_fields: JSON, digest: JSON) -> JSON:
    v4_fields = {**stage_fields, "stage_run_id": nullable(digest)}
    zero_coverage = {availability: const(0) for availability in AVAILABILITIES}
    return tagged(
        [
            object_schema(
                {
                    **v4_fields,
                    "execution_status": const("evaluated"),
                    "stage_run_id": digest,
                    "classification": enum(
                        ("screened", "confirmed", "indeterminate", "rejected")
                    ),
                    "reason_code": const(None),
                    "not_run_reason": const(None),
                    "verdict_state": const(STAGE_VERDICT_STATES["evaluated"]),
                    "decision_digest": digest,
                }
            ),
            object_schema(
                {
                    **v4_fields,
                    "execution_status": const("closed"),
                    "stage_run_id": digest,
                    "reason_code": enum(CLOSED_STAGE_REASONS),
                    "classification": enum(CLOSED_STAGE_CLASSIFICATIONS),
                    "not_run_reason": const(None),
                    "verdict_state": const(STAGE_VERDICT_STATES["closed"]),
                    "paired_rows": const(0),
                    "case_stage_repeat_pairs": const(0),
                    "primary_repeat_pairs": const(0),
                    "capture_counts": object_schema(zero_coverage),
                }
            ),
            object_schema(
                {
                    **v4_fields,
                    "execution_status": const("not_run"),
                    "stage_run_id": const(None),
                    "classification": const("not_evaluated"),
                    "reason_code": const(None),
                    "not_run_reason": enum(NOT_RUN_REASONS),
                    "verdict_state": const(STAGE_VERDICT_STATES["not_run"]),
                    "paired_rows": const(0),
                    "case_stage_repeat_pairs": const(0),
                    "primary_repeat_pairs": const(0),
                    "capture_counts": object_schema(zero_coverage),
                }
            ),
        ],
        field="execution_status",
    )


def _stage_contains(name: str, status: str | None = None) -> JSON:
    properties: JSON = {"stage": const(name)}
    required = ["stage"]
    if status is not None:
        properties["execution_status"] = const(status)
        required.append("execution_status")
    return {
        "contains": {"required": required, "properties": properties},
        "minContains": 1,
        "maxContains": 1,
    }


def _add_v4_producer_declarations(definitions: dict[str, JSON], digest: JSON) -> None:
    definitions["build_record"] = {
        "oneOf": [
            object_schema(
                {
                    "state": const("self_reported"),
                    "record_state": const("recorded"),
                    "package_name": const("goppi"),
                    "package_version": text(128),
                    "identity_assurance": const("self_reported"),
                    "dir_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "unavailable_reason": const(None),
                }
            ),
            object_schema(
                {
                    "state": const("not_measured"),
                    "record_state": const("recorded"),
                    "package_name": const("goppi"),
                    "package_version": text(128),
                    "identity_assurance": const("self_reported"),
                    "dir_hash": const(None),
                    "unavailable_reason": enum(
                        ("source_checkout", "package_unverifiable", "package_changed_during_run")
                    ),
                }
            ),
            object_schema(
                {
                    "state": const("not_measured"),
                    "record_state": const("not_recorded"),
                    "package_name": const(None),
                    "package_version": const(None),
                    "identity_assurance": const(None),
                    "dir_hash": const(None),
                    "unavailable_reason": const(None),
                }
            ),
        ],
    }
    definitions["stage_build"] = tagged(
        [
            object_schema(
                {
                    "stage": enum(STAGES),
                    "execution_status": const("evaluated"),
                    "record": reference("build_record"),
                }
            ),
            object_schema(
                {
                    "stage": enum(STAGES),
                    "execution_status": const("closed"),
                    "record": reference("build_record"),
                }
            ),
            object_schema(
                {
                    "stage": enum(STAGES),
                    "execution_status": const("not_run"),
                    "record": const(None),
                }
            ),
        ],
        field="execution_status",
    )
    declarations = object_schema(
        {
            "builds": object_schema(
                {
                    "identity_assurance": const("self_reported"),
                    "independently_verified": const(False),
                    "cross_stage_comparability": const("not_inferred_from_digest_equality"),
                    "export": reference("build_record"),
                    "stages": array(reference("stage_build"), len(STAGES)),
                }
            ),
            "runner": object_schema(
                {
                    "identity_assurance": const("declared"),
                    "independently_verified": const(False),
                    "profile": enum(tuple(CAPTURE_PROFILE_BY_RUNNER)),
                    "provider": nullable(text(128)),
                    "model": nullable(text(128)),
                    "thinking_level": nullable(text(128)),
                    "thinking_level_source": nullable(enum(("plan", "runner_default"))),
                }
            ),
            "split": object_schema(
                {
                    "identity_assurance": const("self_reported"),
                    "commitment_recomputed": const(False),
                    "salt_origin": enum(
                        ("csprng_generated", "synthetic_fixture_fixed", "human_supplied")
                    ),
                    "limitations": array(enum(("split_salt_guessable",)), 1),
                    "assignment_binding_profile": const("salted_length_prefixed_sha256_v1"),
                    "assignment_digest": digest,
                    "split_policy_digest": digest,
                }
            ),
        }
    )
    builds = declarations["properties"]["builds"]
    build_stages = builds["properties"]["stages"]
    build_stages["minItems"] = len(STAGES)
    builds["allOf"] = [
        {"properties": {"stages": _stage_contains(stage)}} for stage in STAGES
    ]
    runner = declarations["properties"]["runner"]
    runner["allOf"] = [
        {
            "if": {
                "properties": {
                    "profile": const("deterministic_node_conformance"),
                }
            },
            "then": {
                "properties": {
                    field: const(None)
                    for field in (
                        "provider",
                        "model",
                        "thinking_level",
                        "thinking_level_source",
                    )
                }
            },
        },
        {
            "if": {
                "properties": {
                    "profile": enum(
                        tuple(
                            profile
                            for profile in CAPTURE_PROFILE_BY_RUNNER
                            if profile != "deterministic_node_conformance"
                        )
                    ),
                }
            },
            "then": {
                "properties": {
                    "provider": text(128),
                    "model": text(128),
                    "thinking_level": text(128),
                    "thinking_level_source": enum(("plan", "runner_default")),
                }
            },
        },
        {
            "if": {
                "properties": {
                    "thinking_level_source": const("runner_default"),
                }
            },
            "then": {"properties": {"thinking_level": const("max")}},
        },
    ]
    split = declarations["properties"]["split"]
    split["allOf"] = [
        {
            "if": {
                "properties": {"salt_origin": const("synthetic_fixture_fixed")}
            },
            "then": {
                "properties": {
                    "limitations": const(["split_salt_guessable"])
                }
            },
            "else": {"properties": {"limitations": const([])}},
        }
    ]
    definitions["producer_declarations"] = declarations


def build(*, outcome_actions: bool = False, version: int = 1) -> JSON:
    if (outcome_actions, version) not in SCHEMAS:
        raise ValueError("UNSUPPORTED_GOPPI_TRAJECTORY_CONTRACT_VERSION")
    schema = SCHEMAS[(outcome_actions, version)]
    if version in (V3, V4):
        policy = OUTCOME_ACTIONS_POLICY_V2 if outcome_actions else REVIEW_POLICY_V2
    else:
        policy = OUTCOME_ACTIONS_POLICY if outcome_actions else REVIEW_POLICY
    task_evidence = (
        {**TASK_EVIDENCE, **OUTCOME_ACTION_TASK_EVIDENCE} if outcome_actions else TASK_EVIDENCE
    )
    if version in (V3, V4):
        task_evidence = {**task_evidence, **CYCLE_TASK_EVIDENCE}
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
            "comparability": _comparability_schema(version),
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
    evaluated_verdict: JSON = {}
    closed_verdict: JSON = {}
    if version == V3:
        evaluated_verdict = {
            "verdict_state": const(STAGE_VERDICT_STATES["evaluated"]),
            "decision_digest": digest,
        }
        closed_verdict = {"verdict_state": const(STAGE_VERDICT_STATES["closed"])}
    definitions["stage"] = tagged(
        [
            object_schema(
                {**stage_fields, "execution_status": const("evaluated"), **evaluated_verdict}
            ),
            object_schema(
                {
                    **stage_fields,
                    "execution_status": const("closed"),
                    "reason_code": enum(CLOSED_STAGE_REASONS),
                    "classification": enum(CLOSED_STAGE_CLASSIFICATIONS),
                    **closed_verdict,
                }
            ),
        ],
        field="execution_status",
    )
    if version == V4:
        definitions["stage"] = _v4_stage(stage_fields, digest)
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
    cycle_scopes = {
        STOPPED_TASK: array(
            object_schema({"cycle_run_id": digest, "stage": enum(STAGES)}), len(STAGES)
        ),
        VERDICT_TASK: array(
            object_schema({"stage": enum(STAGES), "decision_digest": digest}), len(STAGES)
        ),
    }
    definitions["task"] = tagged(
        [
            object_schema(
                {
                    **task_fields,
                    **({"scope": cycle_scopes[kind]} if kind in cycle_scopes else {}),
                    "kind": const(kind),
                    "owner": const(owner),
                    "required_evidence": const(evidence),
                }
            )
            for kind, (owner, evidence) in task_evidence.items()
        ],
        field="kind",
    )
    result = object_schema(
        {
            "schema": const(schema),
            "state": const("analyzed"),
            "policy": const(policy),
            "input_members": array(
                object_schema(
                    {"path": enum(MEMBERS), "sha256": digest, "bytes": integer(128 * 1024 * 1024)}
                ),
                len(MEMBERS),
            ),
            "source_context": object_schema(
                {k: digest for k in ("cycle_run_id", "plan_digest", "execution_identity_digest")}
            ),
            "trust_scope": (
                tagged(
                    [
                        object_schema(
                            {
                                "verifier_report_state": const("not_performed"),
                                "verifier_source": const(None),
                                "verifier_identity_verified": const(False),
                                "response_snapshot_binding": const("not_checked"),
                                "snapshot_postcheck": const("not_checked"),
                                "content_closure": const("not_checked"),
                                "authenticity": const("not_checked"),
                                "source_authentication_independently_verified": const(False),
                                "producer_projection_independently_verified": const(False),
                                "outcome_harness_binding": enum(OUTCOME_HARNESS_BINDINGS),
                            }
                        ),
                        object_schema(
                            {
                                "verifier_report_state": const("report_accepted"),
                                "verifier_source": enum(
                                    (
                                        "caller_supplied_python_port",
                                        "operator_configured_executable",
                                    )
                                ),
                                "verifier_identity_verified": const(False),
                                "response_snapshot_binding": const("selected_fields_matched"),
                                "snapshot_postcheck": const("unchanged"),
                                "content_closure": const("verifier_reported_valid"),
                                "authenticity": const(
                                    "verifier_reported_content_digest_only"
                                ),
                                "source_authentication_independently_verified": const(False),
                                "producer_projection_independently_verified": const(False),
                                "outcome_harness_binding": enum(OUTCOME_HARNESS_BINDINGS),
                            }
                        ),
                    ],
                    field="verifier_report_state",
                )
                if version >= V2
                else tagged(
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
                )
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
            "next_verification_tasks": array(reference("task"), len(task_evidence)),
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
    if version in (V3, V4):
        result = _add_cycle_disclosures(result)
    if version == V4:
        _add_v4_producer_declarations(definitions, digest)
        result["properties"]["producer_declarations"] = reference("producer_declarations")
        result["required"].append("producer_declarations")
        result_stages = result["properties"]["stages"]
        result_stages["minItems"] = len(STAGES)
        result["allOf"] = [
            *result.get("allOf", []),
            *(
                {"properties": {"stages": _stage_contains(stage)}} for stage in STAGES
            ),
            *(
                {
                    "if": {"properties": {"stages": _stage_contains(stage, status)}},
                    "then": {
                        "properties": {
                            "producer_declarations": {
                                "properties": {
                                    "builds": {
                                        "properties": {
                                            "stages": _stage_contains(stage, status)
                                        }
                                    }
                                }
                            }
                        }
                    },
                }
                for stage in STAGES
                for status in ("evaluated", "closed", "not_run")
            ),
        ]
        result["allOf"] = [
            *result.get("allOf", []),
            *[
                {
                    "if": {
                        "required": ["producer_declarations", "runner"],
                        "properties": {
                            "producer_declarations": {
                                "properties": {
                                    "runner": {"properties": {"profile": const(profile)}}
                                }
                            }
                        },
                    },
                    "then": {
                        "properties": {"runner": {"properties": {"profile": const(profile)}}}
                    },
                }
                for profile in CAPTURE_PROFILE_BY_RUNNER
            ],
        ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:underwrite:contract:{schema}",
        **result,
        "$defs": definitions,
    }


def build_verifier_response_v3() -> JSON:
    """Bind the accepted response envelope to either exact exchange family at runtime."""
    path = ROOT / "contracts/goppi_trajectory_verifier_response.v2.schema.json"
    response = json.loads(path.read_bytes())
    response["$id"] = "urn:underwrite:contract:goppi_trajectory_verifier_response.v3"
    response["title"] = (
        "Trusted consumed fields of the offline Goppi combined verifier response, "
        "paired execution v1 or v2"
    )
    properties = response["properties"]["data"]["properties"]
    properties["format"] = enum(("v1", "v2"))
    properties["trajectory"]["properties"]["format"] = enum(("v1", "v2"))
    return response


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for (outcome_actions, version), schema in SCHEMAS.items():
        path = ROOT / "contracts" / f"{schema}.schema.json"
        rendered = (
            json.dumps(
                build(outcome_actions=outcome_actions, version=version),
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
        if args.check:
            if not path.exists() or path.read_text() != rendered:
                raise SystemExit(f"STALE_GOPPI_TRAJECTORY_CONTRACT: {schema}")
        else:
            path.write_text(rendered)
    response_path = ROOT / "contracts/goppi_trajectory_verifier_response.v3.schema.json"
    response_rendered = (
        json.dumps(build_verifier_response_v3(), indent=2, ensure_ascii=False) + "\n"
    )
    if args.check:
        if not response_path.exists() or response_path.read_text() != response_rendered:
            raise SystemExit(
                "STALE_GOPPI_TRAJECTORY_CONTRACT: goppi_trajectory_verifier_response.v3"
            )
    else:
        response_path.write_text(response_rendered)


if __name__ == "__main__":
    main()

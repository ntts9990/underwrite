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
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, NoReturn, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes

RESULT_SCHEMA = "goppi_trajectory_review.v1"
RESULT_SCHEMA_V2 = "goppi_trajectory_review.v2"
RESULT_SCHEMA_V3 = "goppi_trajectory_review.v3"
RESULT_SCHEMA_V4 = "goppi_trajectory_review.v4"
EXECUTION_FORMAT_V1 = 1
EXECUTION_FORMAT_V2 = 2
REVIEW_POLICY = "goppi.descriptive-paired-trajectory.v1"
REVIEW_POLICY_V2 = "goppi.descriptive-paired-trajectory.v2"
OUTCOME_ACTIONS_SCHEMA = "goppi_trajectory_outcome_actions.v1"
OUTCOME_ACTIONS_SCHEMA_V2 = "goppi_trajectory_outcome_actions.v2"
OUTCOME_ACTIONS_SCHEMA_V3 = "goppi_trajectory_outcome_actions.v3"
OUTCOME_ACTIONS_SCHEMA_V4 = "goppi_trajectory_outcome_actions.v4"
OUTCOME_ACTIONS_POLICY = "goppi.descriptive-paired-trajectory-outcome-actions.v1"
OUTCOME_ACTIONS_POLICY_V2 = "goppi.descriptive-paired-trajectory-outcome-actions.v2"
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
PI_1_3_PROFILE = "native_pi_1_3_lifecycle.v1"
PI_1_5_PROFILE = "native_pi_1_5_lifecycle.v1"
CAPTURE_PROFILE_BY_RUNNER = {
    "deterministic_node_conformance": PI_1_3_PROFILE,
    "pi_0_84_4": PI_1_3_PROFILE,
    "pi_1_3_0": PI_1_3_PROFILE,
    "pi_1_5_0": PI_1_5_PROFILE,
}
SUPPORTED_CAPTURE_PROFILES = (PI_1_3_PROFILE, PI_1_5_PROFILE)
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
_OUTCOME_ACTION_TASK_KIND = "inspect_outcome_change_without_tool_status_change"
OUTCOME_ACTION_TASK_EVIDENCE = {
    _OUTCOME_ACTION_TASK_KIND: (
        "calling_agent",
        "selected baseline/candidate source outcome claims and retained comparison context review",
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
_DIR_HASH = re.compile(r"[0-9a-f]{64}\Z")
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
Key = tuple[str | int, ...]

_V2_EXECUTION_KEYS = frozenset(
    {
        "schema",
        "format_scope",
        "status",
        "source",
        "stages",
        "counts",
        "claims",
        "authority",
        "export_build",
    }
)
_V2_SOURCE_KEYS = frozenset(
    {
        "assignment_binding",
        "assignment_digest",
        "baseline_snapshot_digest",
        "candidate_snapshot_digest",
        "cycle_run_id",
        "cycle_status",
        "evalset_digest",
        "execution_identity_digest",
        "plan_digest",
        "project_config_digest",
        "proposal_digest",
        "result_authentication_tag",
        "runner",
        "runner_declaration",
        "split_policy_digest",
        "split_salt",
        "verifier",
    }
)
_V2_ROW_KEYS = frozenset(
    {
        "schema",
        "cycle_run_id",
        "stage_run_id",
        "repeat_run_id",
        "stage",
        "split",
        "case_id",
        "arm",
        "repeat_index",
        "repeat_proof",
        "primary_inference",
        "planned",
        "evidence_digest",
        "trace_digest",
        "evalset_digest",
        "split_policy_digest",
        "assignment_digest",
        "config_digest",
        "verifier",
        "result",
    }
)
_V2_TRAJECTORY_KEYS = frozenset(
    {
        "schema",
        "assignment_binding",
        "availability_counts",
        "call_relationships",
        "closed_stages",
        "paired_manifest_digest",
        "paired_manifest_sha256",
        "producer_claims",
        "profile",
        "redaction",
        "source",
        "units",
    }
)
_V2_TRAJECTORY_UNIT_KEYS = frozenset(
    {
        "schema",
        "unit",
        "roster_count",
        "roster_digest",
        "bundle_digest",
        "source_availability",
        "capture_completeness",
        "unavailable_reason",
        "capture_profile",
        "context",
        "cases",
    }
)
_V2_TRAJECTORY_CASE_KEYS = frozenset(
    {"row_key", "evidence_digest", "trace_digest", "verifier", "spans", "verifier_observation"}
)
_V2_CONTEXT_FIELDS = (
    *CONTEXT_FIELDS,
    "approved_draft_digest",
    "harness_identity_digest",
    "roster_count",
    "roster_digest",
)
_V2_TRAJECTORY_SOURCE_KEYS = frozenset({"cycle_run_id", "plan_digest", "execution_identity_digest"})
_V2_ROW_KEY_FIELDS = frozenset(
    {
        "cycle_run_id",
        "stage_run_id",
        "repeat_run_id",
        "stage",
        "split",
        "case_id",
        "arm",
        "repeat_index",
    }
)
_V2_UNIT_IDENTITY_FIELDS = frozenset(
    {
        "cycle_run_id",
        "stage_run_id",
        "repeat_run_id",
        "stage_input_digest",
        "stage",
        "arm",
        "repeat_index",
    }
)
_V2_EVALUATED_STAGE_KEYS = frozenset(
    {
        "stage",
        "split",
        "stage_run_id",
        "execution_status",
        "stage_input_digest",
        "stage_artifact_digest",
        "execution_attestation_digest",
        "measurement_receipt_digest",
        "decision_digest",
        "planned_case_count",
        "authenticated_repeat_count",
        "primary_inference_repeat_index",
        "exported_row_count",
        "planned",
        "not_run_reason",
        "classification",
        "stage_build",
    }
)
_V2_CLOSED_STAGE_KEYS = frozenset(
    {
        "stage",
        "split",
        "stage_run_id",
        "execution_status",
        "stage_input_digest",
        "stage_artifact_digest",
        "classification",
        "stop_receipt_digest",
        "stop_reason",
        "reason_code",
        "planned_case_count",
        "authenticated_repeat_count",
        "exported_row_count",
        "planned",
        "not_run_reason",
        "decision_digest",
        "stage_build",
    }
)
_V2_NOT_RUN_STAGE_KEYS = frozenset(
    {
        "stage",
        "split",
        "execution_status",
        "planned",
        "not_run_reason",
        "classification",
        "decision_digest",
        "planned_case_count",
        "authenticated_repeat_count",
        "exported_row_count",
        "stage_build",
    }
)
NOT_RUN_REASONS = (
    "earlier_stage_rejected",
    "earlier_stage_indeterminate",
    "earlier_stage_partial_stop",
    "earlier_stage_budget_stop",
    "earlier_stage_hard_failure",
)


DIAGNOSTIC_CODES = {
    "TRAJECTORY_BINDING_MISMATCH": "INVALID_INPUT",
    "TRAJECTORY_COVERAGE_MISMATCH": "INVALID_INPUT",
    "TRAJECTORY_DUPLICATE_IDENTITY": "INVALID_INPUT",
    "TRAJECTORY_LIMIT_EXCEEDED": "INPUT_LIMIT_EXCEEDED",
    "TRAJECTORY_SHAPE_INVALID": "INVALID_INPUT",
    "TRAJECTORY_UNSUPPORTED_VALUE": "UNSUPPORTED_FORMAT",
    "TRAJECTORY_VALUE_INVALID": "INVALID_INPUT",
}


class TrajectoryError(ValueError):
    """Finite consumer diagnostics without reflecting raw producer payloads."""

    def __init__(self, code: str, reason: str, location: str) -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def _unsupported_format(location: str) -> NoReturn:
    raise TrajectoryError("UNSUPPORTED_FORMAT", "TRAJECTORY_UNSUPPORTED_VALUE", location)


@dataclass(frozen=True)
class _ExecutionBindings:
    format_version: int
    assignment_digest: str
    split_policy_digest: str
    runner_model: str | None
    stage_input_digests: Mapping[str, str]


@dataclass(frozen=True)
class _JoinConfig:
    rows: dict[Key, dict[str, Any]]
    capture_profile: str
    runner_profile: str
    bindings: _ExecutionBindings | None

    @property
    def format_version(self) -> int:
        return self.bindings.format_version if self.bindings is not None else EXECUTION_FORMAT_V1


@dataclass(frozen=True)
class _TrajectoryUnit:
    index: int
    key: Key
    identity: dict[str, Any]
    availability: str
    retained: bool
    completeness: str
    unavailable_reason: str | None
    bundle_digest: object
    context: dict[str, str | int | None]
    cases: list[Any]


def _require(
    condition: bool, reason: str = "TRAJECTORY_VALUE_INVALID", location: str = "trajectory"
) -> None:
    if not condition:
        raise TrajectoryError(DIAGNOSTIC_CODES[reason], reason, location)


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


def _exact_keys(value: dict[str, Any], expected: frozenset[str]) -> None:
    _require(value.keys() == expected, "TRAJECTORY_SHAPE_INVALID")


def _build_record(value: object, *, allow_unrecorded: bool = False) -> dict[str, Any]:
    record = _object(value)
    recorded_keys = frozenset(
        {
            "package_name",
            "package_version",
            "build_digest",
            "build_digest_unavailable_reason",
            "record_state",
            "identity_assurance",
        }
    )
    if record.get("record_state") == "not_recorded":
        _require(allow_unrecorded, "TRAJECTORY_VALUE_INVALID")
        _exact_keys(record, recorded_keys)
        _require(
            all(record.get(field) is None for field in recorded_keys - {"record_state"}),
            "TRAJECTORY_VALUE_INVALID",
        )
        return {
            "state": "not_measured",
            "record_state": "not_recorded",
            "package_name": None,
            "package_version": None,
            "identity_assurance": None,
            "dir_hash": None,
            "unavailable_reason": None,
        }
    _exact_keys(record, recorded_keys)
    _require(record.get("record_state") == "recorded", "TRAJECTORY_VALUE_INVALID")
    _require(record.get("package_name") == "goppi", "TRAJECTORY_BINDING_MISMATCH")
    package_version = _text(record.get("package_version"), 128)
    _require(_SEMVER.fullmatch(package_version) is not None)
    _require(record.get("identity_assurance") == "self_reported", "TRAJECTORY_VALUE_INVALID")
    digest_value = record.get("build_digest")
    unavailable = record.get("build_digest_unavailable_reason")
    if digest_value is None:
        _require(
            unavailable
            in ("source_checkout", "package_unverifiable", "package_changed_during_run"),
            "TRAJECTORY_VALUE_INVALID",
        )
        dir_hash = None
        state = "not_measured"
    else:
        digest_object = _object(digest_value)
        _exact_keys(digest_object, frozenset({"dirHash"}))
        dir_hash = _text(digest_object.get("dirHash"), 64)
        _require(_DIR_HASH.fullmatch(dir_hash) is not None, "TRAJECTORY_VALUE_INVALID")
        _require(unavailable is None, "TRAJECTORY_VALUE_INVALID")
        state = "self_reported"
    return {
        "state": state,
        "record_state": "recorded",
        "package_name": "goppi",
        "package_version": package_version,
        "identity_assurance": "self_reported",
        "dir_hash": dir_hash,
        "unavailable_reason": unavailable,
    }


def _runner_declaration(value: object, runner_profile: str) -> dict[str, Any]:
    declaration = _object(value)
    _exact_keys(
        declaration,
        frozenset(
            {
                "provider",
                "model",
                "thinking_level",
                "thinking_level_source",
                "profile",
                "identity_assurance",
            }
        ),
    )
    _require(declaration.get("identity_assurance") == "declared", "TRAJECTORY_VALUE_INVALID")
    profile = _enum(declaration.get("profile"), tuple(CAPTURE_PROFILE_BY_RUNNER))
    _require(profile == runner_profile, "TRAJECTORY_BINDING_MISMATCH")
    if profile == "deterministic_node_conformance":
        for field in ("provider", "model", "thinking_level", "thinking_level_source"):
            _require(declaration.get(field) is None, "TRAJECTORY_VALUE_INVALID")
        return {
            "identity_assurance": "declared",
            "independently_verified": False,
            "profile": profile,
            "provider": None,
            "model": None,
            "thinking_level": None,
            "thinking_level_source": None,
        }
    provider = _text(declaration.get("provider"), 128)
    model = _text(declaration.get("model"), 128)
    thinking_level = _text(declaration.get("thinking_level"), 128)
    thinking_source = _enum(declaration.get("thinking_level_source"), ("plan", "runner_default"))
    _require(
        thinking_source != "runner_default" or thinking_level == "max",
        "TRAJECTORY_BINDING_MISMATCH",
    )
    return {
        "identity_assurance": "declared",
        "independently_verified": False,
        "profile": profile,
        "provider": provider,
        "model": model,
        "thinking_level": thinking_level,
        "thinking_level_source": thinking_source,
    }


def _split_declaration(value: object) -> dict[str, Any]:
    salt = _object(value)
    _exact_keys(salt, frozenset({"origin", "limitations"}))
    origin = _enum(
        salt.get("origin"), ("csprng_generated", "synthetic_fixture_fixed", "human_supplied")
    )
    limitations = [
        _enum(item, ("split_salt_guessable",)) for item in _array(salt.get("limitations"), 1)
    ]
    _require(len(set(limitations)) == len(limitations), "TRAJECTORY_DUPLICATE_IDENTITY")
    _require(
        (origin == "synthetic_fixture_fixed") == ("split_salt_guessable" in limitations),
        "TRAJECTORY_BINDING_MISMATCH",
    )
    return {
        "identity_assurance": "self_reported",
        "commitment_recomputed": False,
        "salt_origin": origin,
        "limitations": limitations,
    }


def _v2_source(source: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    _exact_keys(source, _V2_SOURCE_KEYS)
    runner = _object(source.get("runner"))
    _exact_keys(runner, frozenset({"profile", "profile_identity_digest", "executable_digest"}))
    runner_profile = _enum(
        source.get("runner", {}).get("profile"), tuple(CAPTURE_PROFILE_BY_RUNNER)
    )
    _digest(runner.get("profile_identity_digest"))
    _digest(runner.get("executable_digest"))
    verifier = _object(source.get("verifier"))
    _exact_keys(verifier, frozenset({"profile_identity_digest", "executable_digest", "protocol"}))
    _digest(verifier.get("profile_identity_digest"))
    _digest(verifier.get("executable_digest"))
    _require(verifier.get("protocol") == "json_stdio_v2", "TRAJECTORY_UNSUPPORTED_VALUE")
    for field in (
        "assignment_digest",
        "baseline_snapshot_digest",
        "candidate_snapshot_digest",
        "cycle_run_id",
        "evalset_digest",
        "execution_identity_digest",
        "plan_digest",
        "project_config_digest",
        "proposal_digest",
        "result_authentication_tag",
        "split_policy_digest",
    ):
        _digest(source.get(field))
    _require(source.get("cycle_status") in ("completed", "stopped"), "TRAJECTORY_UNSUPPORTED_VALUE")
    assignment_binding = _object(source.get("assignment_binding"))
    _exact_keys(assignment_binding, frozenset({"commitment_profile"}))
    _require(
        assignment_binding.get("commitment_profile") == "salted_length_prefixed_sha256_v1",
        "TRAJECTORY_UNSUPPORTED_VALUE",
    )
    declaration = _runner_declaration(source.get("runner_declaration"), runner_profile)
    split = _split_declaration(source.get("split_salt"))
    split.update(
        assignment_binding_profile="salted_length_prefixed_sha256_v1",
        assignment_digest=_digest(source.get("assignment_digest")),
        split_policy_digest=_digest(source.get("split_policy_digest")),
    )
    return runner_profile, declaration, split


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


def _verifier(value: object, *, strict: bool = False) -> dict[str, str]:
    obj = _object(value)
    if strict:
        _exact_keys(obj, frozenset({"verifier_id", "verifier_digest"}))
    return {
        "verifier_id": _text(obj.get("verifier_id"), 128),
        "verifier_digest": _digest(obj.get("verifier_digest")),
    }


def _outcome(value: object, *, strict: bool = False) -> dict[str, Any]:
    obj = _object(value)
    status = _enum(obj.get("status"), ("observed", "endpoint_failure", "missing"))
    result: dict[str, Any] = {"status": status}
    if status == "observed":
        if strict:
            _exact_keys(obj, frozenset({"status", "value_kind", "verifier_value", "scored_value"}))
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
        if strict:
            _exact_keys(obj, frozenset({"status", "reason", "verifier_value", "scored_value"}))
        result["reason"] = _enum(obj.get("reason"), OUTCOME_REASONS[status])
        _require("verifier_value" in obj and obj["verifier_value"] is None)
        _require(
            "scored_value" in obj
            and obj["scored_value"] is (False if status == "endpoint_failure" else None)
        )
        result.update(verifier_value=None, scored_value=obj["scored_value"])
    return result


def _context(
    value: object, *, format_version: int = EXECUTION_FORMAT_V1
) -> dict[str, str | int | None]:
    obj = {} if value is None else _object(value)
    result: dict[str, str | int | None] = {}
    fields = _V2_CONTEXT_FIELDS if format_version == EXECUTION_FORMAT_V2 else CONTEXT_FIELDS
    if format_version == EXECUTION_FORMAT_V2 and value is not None:
        _exact_keys(obj, frozenset(fields))
    for field in fields:
        val = obj.get(field)
        if val is None:
            result[field] = None
        elif field in ("declared_repeats", "roster_count"):
            result[field] = _integer(val, 10_000 if field == "roster_count" else 100)
        elif field in ("runner_model", "runner_profile"):
            result[field] = _text(val, 128)
        else:
            result[field] = _digest(val)
    return result


def _process(value: object, case_id: str, *, strict: bool = False) -> dict[str, Any]:
    if value is None:
        return {"state": "unlinked"}
    obj = _object(value)
    _require(_text(obj.get("case_id")) == case_id, "TRAJECTORY_BINDING_MISMATCH")
    state = _enum(obj.get("status"), ("observed", "not_observed"))
    result: dict[str, Any] = {"state": state}
    if state == "observed":
        if strict:
            _exact_keys(
                obj,
                frozenset(
                    {
                        "case_id",
                        "status",
                        "attempt_id",
                        "receipt_identity_digest",
                        "expected_public_identity_digest",
                    }
                ),
            )
        result.update(
            attempt_id=_text(obj.get("attempt_id"), 256),
            receipt_identity_digest=_digest(obj.get("receipt_identity_digest")),
            expected_public_identity_digest=_digest(obj.get("expected_public_identity_digest")),
        )
    elif strict:
        _exact_keys(obj, frozenset({"case_id", "status"}))
    return result


def _spans(
    value: object, seen: set[str], *, strict: bool = False
) -> tuple[int, list[dict[str, Any]]]:
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
            if strict:
                _exact_keys(span, frozenset({"kind", "span_id"}))
            llms += 1
        else:
            if strict:
                _exact_keys(span, frozenset({"kind", "span_id", "tool_name", "status"}))
            counts[
                (_text(span.get("tool_name"), 128), _enum(span.get("status"), ("ok", "error")))
            ] += 1
    return llms, [
        {"tool_name": name, "status": status, "count": count}
        for (name, status), count in sorted(counts.items())
    ]


def _row_index(
    rows: tuple[dict[str, Any], ...], *, format_version: int = EXECUTION_FORMAT_V1
) -> dict[Key, dict[str, Any]]:
    _require(type(rows) is tuple, "TRAJECTORY_SHAPE_INVALID")
    _require(len(rows) <= MAX_ROWS, "TRAJECTORY_LIMIT_EXCEEDED")
    result: dict[Key, dict[str, Any]] = {}
    for index, raw in enumerate(rows):
        row = _object(raw)
        if format_version == EXECUTION_FORMAT_V2:
            _exact_keys(row, _V2_ROW_KEYS)
        _require(
            row.get("schema") == f"goppi.paired_run_exchange.row.v{format_version}",
            "TRAJECTORY_UNSUPPORTED_VALUE",
        )
        key = _key(row)
        _require(key not in result, "TRAJECTORY_DUPLICATE_IDENTITY")
        _require(row.get("planned") is True)
        result[key] = {
            "row_index": index,
            "evidence_digest": _digest(row.get("evidence_digest")),
            "trace_digest": _digest(row.get("trace_digest")),
            "verifier": _verifier(
                row.get("verifier"), strict=format_version == EXECUTION_FORMAT_V2
            ),
            "outcome": _outcome(row.get("result"), strict=format_version == EXECUTION_FORMAT_V2),
            "primary_inference": _boolean(row.get("primary_inference")),
            "repeat_proof": _enum(
                row.get("repeat_proof"), ("v1_primary_only", "v2_attested_repeat")
            ),
            "row_context": {field: _digest(row.get(field)) for field in ROW_CONTEXT_FIELDS},
        }
    return result


def _parse_unit(index: int, raw: object, config: _JoinConfig, joined_count: int) -> _TrajectoryUnit:
    unit = _object(raw)
    bindings = config.bindings
    if bindings is not None:
        _exact_keys(unit, _V2_TRAJECTORY_UNIT_KEYS)
        _require(unit.get("schema") == "goppi.paired_trajectory_resolved_unit.v1")
        roster_count = _integer(unit.get("roster_count"), MAX_ROWS)
        roster_digest = _digest(unit.get("roster_digest"))
    else:
        roster_count, roster_digest = None, None
    identity = _object(unit.get("unit"))
    if bindings is not None:
        _exact_keys(identity, _V2_UNIT_IDENTITY_FIELDS)
        stage = _enum(identity.get("stage"), STAGES)
        _require(
            _digest(identity.get("stage_input_digest")) == bindings.stage_input_digests.get(stage),
            "TRAJECTORY_BINDING_MISMATCH",
        )
    key = _key({**identity, "case_id": "unit", "split": "explore"})
    availability = _enum(unit.get("source_availability"), AVAILABILITIES)
    retained = availability == "retained"
    completeness = _enum(unit.get("capture_completeness"), ("complete", "incomplete"))
    _require(completeness == ("complete" if retained else "incomplete"))
    reason = unit.get("unavailable_reason")
    raw_context = unit.get("context")
    if raw_context is None:
        _require(unit.get("capture_profile") is None, "TRAJECTORY_BINDING_MISMATCH")
    else:
        _require(
            unit.get("capture_profile") == config.capture_profile,
            "TRAJECTORY_BINDING_MISMATCH",
        )
    if retained:
        _require(
            reason is None
            and raw_context is not None
            and config.runner_profile in ("pi_1_3_0", "pi_1_5_0"),
            "TRAJECTORY_BINDING_MISMATCH",
        )
    else:
        reason = _enum(reason, UNAVAILABLE_REASONS)
    context = _context(raw_context, format_version=config.format_version)
    if raw_context is not None:
        _require(
            context["runner_profile"] == config.runner_profile,
            "TRAJECTORY_BINDING_MISMATCH",
        )
        if bindings is not None:
            _require(
                context["runner_model"] == bindings.runner_model,
                "TRAJECTORY_BINDING_MISMATCH",
            )
            _require(
                context["assignment_digest"] == bindings.assignment_digest,
                "TRAJECTORY_BINDING_MISMATCH",
            )
            _require(
                context["split_policy_digest"] == bindings.split_policy_digest,
                "TRAJECTORY_BINDING_MISMATCH",
            )
            _require(context["roster_count"] == roster_count, "TRAJECTORY_BINDING_MISMATCH")
            _require(context["roster_digest"] == roster_digest, "TRAJECTORY_BINDING_MISMATCH")
    cases = _array(unit.get("cases"), MAX_ROWS)
    _require(len(cases) + joined_count <= MAX_ROWS, "TRAJECTORY_LIMIT_EXCEEDED")
    return _TrajectoryUnit(
        index=index,
        key=key,
        identity=identity,
        availability=availability,
        retained=retained,
        completeness=completeness,
        unavailable_reason=cast(str | None, reason),
        bundle_digest=unit.get("bundle_digest"),
        context=context,
        cases=cases,
    )


def _join_case(
    case_index: int,
    raw: object,
    unit: _TrajectoryUnit,
    config: _JoinConfig,
    seen_spans: set[str],
) -> tuple[Key, dict[str, Any]]:
    strict = config.bindings is not None
    case = _object(raw)
    if strict:
        _exact_keys(case, _V2_TRAJECTORY_CASE_KEYS)
    row_key = _object(case.get("row_key"))
    if strict:
        _exact_keys(row_key, _V2_ROW_KEY_FIELDS)
    key = _key(row_key)
    _require(key in config.rows, "TRAJECTORY_COVERAGE_MISMATCH")
    for field in (*PAIR_FIELDS[:3], "repeat_run_id", "repeat_index", "arm"):
        _require(row_key[field] == unit.identity.get(field), "TRAJECTORY_BINDING_MISMATCH")
    row = config.rows[key]
    for field in ("evidence_digest", "trace_digest"):
        _require(_digest(case.get(field)) == row[field], "TRAJECTORY_BINDING_MISMATCH")
    _require(
        _digest(unit.bundle_digest) == row["evidence_digest"],
        "TRAJECTORY_BINDING_MISMATCH",
    )
    _require(
        _verifier(case.get("verifier"), strict=strict) == row["verifier"],
        "TRAJECTORY_BINDING_MISMATCH",
    )
    llms, tools = _spans(case.get("spans"), seen_spans, strict=strict)
    _require(unit.retained or (llms == 0 and not tools), "TRAJECTORY_BINDING_MISMATCH")
    return key, {
        **row,
        "case_pointer": f"/units/{unit.index}/cases/{case_index}",
        "context_pointer": f"/units/{unit.index}/context",
        "source_availability": unit.availability,
        "capture_completeness": unit.completeness,
        "unavailable_reason": unit.unavailable_reason,
        "llm_span_count": llms if unit.retained else None,
        "tool_status_counts": tools if unit.retained else None,
        "context": unit.context,
        "verifier_process": _process(case.get("verifier_observation"), str(key[4]), strict=strict),
    }


def _join(
    rows: dict[Key, dict[str, Any]],
    trajectory: dict[str, Any],
    capture_profile: str,
    runner_profile: str,
    bindings: _ExecutionBindings | None = None,
) -> tuple[dict[Key, dict[str, Any]], dict[str, int]]:
    config = _JoinConfig(rows, capture_profile, runner_profile, bindings)
    joined: dict[Key, dict[str, Any]] = {}
    coverage: dict[str, int] = dict.fromkeys(AVAILABILITIES, 0)
    unit_keys: set[Key] = set()
    for index, raw in enumerate(_array(trajectory.get("units"), MAX_UNITS)):
        unit = _parse_unit(index, raw, config, len(joined))
        _require(unit.key not in unit_keys, "TRAJECTORY_DUPLICATE_IDENTITY")
        unit_keys.add(unit.key)
        seen_spans: set[str] = set()
        for case_index, case in enumerate(unit.cases):
            key, value = _join_case(case_index, case, unit, config, seen_spans)
            _require(key not in joined, "TRAJECTORY_DUPLICATE_IDENTITY")
            joined[key] = value
            coverage[unit.availability] += 1
    _require(set(joined) == set(rows), "TRAJECTORY_COVERAGE_MISMATCH")
    declared = _object(trajectory.get("availability_counts"))
    for name, count in {**coverage, "total": len(rows)}.items():
        _require(_integer(declared.get(name)) == count, "TRAJECTORY_COVERAGE_MISMATCH")
    return joined, coverage


def _comparability(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    reasons: list[dict[str, str]] = []
    values = {"context." + f: (left["context"][f], right["context"][f]) for f in left["context"]}
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
            if item["comparability"]["state"] == "comparable"
            and item["difference"]["tool_status_deltas"]
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


def _validate_v2_execution(
    execution: dict[str, Any], rows: tuple[dict[str, Any], ...]
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    _exact_keys(execution, _V2_EXECUTION_KEYS)
    _require(execution.get("schema") == "goppi.paired_run_exchange.execution.v2")
    _require(execution.get("format_scope") == "terminal_native_paired_run")
    _require(execution.get("status") == "complete")
    source = _object(execution.get("source"))
    runner_profile, runner_declaration, split_declaration = _v2_source(source)

    claims = _object(execution.get("claims"))
    claim_keys = frozenset(
        {
            "approval_created_by_exporter",
            "independently_regraded",
            "local_result_authentication_checked",
            "model_contacted_by_exporter",
            "network_contacted_by_exporter",
            "outcome_harness_binding",
            "source_project_modified_by_exporter",
            "subordinate_content_and_crosslinks_checked",
        }
    )
    _exact_keys(claims, claim_keys)
    expected_claims = {
        "approval_created_by_exporter": False,
        "independently_regraded": False,
        "local_result_authentication_checked": True,
        "model_contacted_by_exporter": False,
        "network_contacted_by_exporter": False,
        "source_project_modified_by_exporter": False,
        "subordinate_content_and_crosslinks_checked": True,
    }
    for field, expected in expected_claims.items():
        _require(_boolean(claims.get(field)) is expected, "TRAJECTORY_BINDING_MISMATCH")
    _enum(claims.get("outcome_harness_binding"), OUTCOME_HARNESS_BINDINGS)
    authority = _object(execution.get("authority"))
    _exact_keys(authority, frozenset({"human_review", "merge_authorized", "revert_authorized"}))
    _require(authority.get("human_review") == "required")
    _require(
        authority.get("merge_authorized") is False and authority.get("revert_authorized") is False
    )

    counts = _object(execution.get("counts"))
    _exact_keys(
        counts,
        frozenset(
            {
                "authenticated_repeat_count_total",
                "closed_stage_count",
                "evaluated_stage_count",
                "not_run_stage_count",
                "planned_stage_case_count_total",
                "row_count",
                "stage_artifact_count",
            }
        ),
    )
    parsed_counts = {key: _integer(value, MAX_ROWS) for key, value in counts.items()}
    _require(parsed_counts["row_count"] == len(rows), "TRAJECTORY_COVERAGE_MISMATCH")

    stages = _array(execution.get("stages"), len(STAGES))
    statuses = [
        _enum(_object(stage).get("execution_status"), ("evaluated", "closed", "not_run"))
        for stage in stages
    ]
    stage_names = [_enum(_object(stage).get("stage"), STAGES) for stage in stages]
    _require(len(set(stage_names)) == len(stage_names))
    _require(set(stage_names) == set(STAGES), "TRAJECTORY_COVERAGE_MISMATCH")
    expected_counts = {
        "evaluated_stage_count": statuses.count("evaluated"),
        "closed_stage_count": statuses.count("closed"),
        "not_run_stage_count": statuses.count("not_run"),
        "stage_artifact_count": statuses.count("evaluated") + statuses.count("closed"),
        "planned_stage_case_count_total": sum(
            _integer(_object(stage).get("planned_case_count")) for stage in stages
        ),
        "authenticated_repeat_count_total": sum(
            _integer(_object(stage).get("authenticated_repeat_count"), 100)
            for stage, status in zip(stages, statuses, strict=True)
            if status != "not_run"
        ),
    }
    _require(
        all(parsed_counts[field] == expected for field, expected in expected_counts.items()),
        "TRAJECTORY_COVERAGE_MISMATCH",
    )

    export_build = _build_record(execution.get("export_build"))
    stage_builds: list[dict[str, Any]] = []
    for stage, status in zip(stages, statuses, strict=True):
        stage_obj = _object(stage)
        build = stage_obj.get("stage_build")
        if status == "not_run":
            _require(build is None, "TRAJECTORY_BINDING_MISMATCH")
            parsed_build = None
        else:
            parsed_build = _build_record(build, allow_unrecorded=True)
        stage_builds.append(
            {
                "stage": _enum(stage_obj.get("stage"), STAGES),
                "execution_status": status,
                "record": parsed_build,
            }
        )
    build_declarations = {
        "identity_assurance": "self_reported",
        "independently_verified": False,
        "cross_stage_comparability": "not_inferred_from_digest_equality",
        "export": export_build,
        "stages": stage_builds,
    }
    return (
        runner_profile,
        runner_declaration,
        split_declaration,
        {
            "builds": build_declarations,
            "runner": runner_declaration,
            "split": split_declaration,
        },
    )


def _validate_v2_trajectory(trajectory: dict[str, Any], source: dict[str, Any]) -> None:
    _exact_keys(trajectory, _V2_TRAJECTORY_KEYS)
    _require(trajectory.get("schema") == "goppi.paired_trajectory_exchange.document.v2")
    _require(trajectory.get("call_relationships") == "not_captured")
    _digest(trajectory.get("paired_manifest_digest"))
    _digest(trajectory.get("paired_manifest_sha256"))
    _require(trajectory.get("redaction") == "allowlisted_metadata_only")
    _enum(trajectory.get("profile"), SUPPORTED_CAPTURE_PROFILES)
    assignment_binding = _object(trajectory.get("assignment_binding"))
    _exact_keys(assignment_binding, frozenset({"commitment_profile"}))
    _require(
        assignment_binding == source.get("assignment_binding"),
        "TRAJECTORY_BINDING_MISMATCH",
    )
    trajectory_source = _object(trajectory.get("source"))
    _exact_keys(trajectory_source, _V2_TRAJECTORY_SOURCE_KEYS)
    for field in _V2_TRAJECTORY_SOURCE_KEYS:
        _require(
            _digest(trajectory_source.get(field)) == _digest(source.get(field)),
            "TRAJECTORY_BINDING_MISMATCH",
        )
    claims = _object(trajectory.get("producer_claims"))
    _exact_keys(
        claims,
        frozenset(
            {
                "independent_provenance_established",
                "projection_binding_checked",
                "source_authentication_checked",
            }
        ),
    )
    _require(claims.get("independent_provenance_established") is False)
    _require(
        _boolean(claims.get("projection_binding_checked")) is True,
        "TRAJECTORY_BINDING_MISMATCH",
    )
    _require(
        _boolean(claims.get("source_authentication_checked")) is True,
        "TRAJECTORY_BINDING_MISMATCH",
    )
    counts = _object(trajectory.get("availability_counts"))
    _exact_keys(counts, frozenset((*AVAILABILITIES, "total")))
    for count in counts.values():
        _integer(count, MAX_ROWS)


def _stages(
    execution: dict[str, Any],
    comparisons: list[dict[str, Any]],
    *,
    format_version: int = EXECUTION_FORMAT_V1,
) -> list[dict[str, Any]]:
    if format_version == EXECUTION_FORMAT_V1:
        return _stages_v1(execution, comparisons)
    return _stages_v2(execution, comparisons)


def _stages_v1(
    execution: dict[str, Any], comparisons: list[dict[str, Any]]
) -> list[dict[str, Any]]:
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
                availability: sum(
                    item[arm]["source_availability"] == availability
                    for item in items
                    for arm in ("baseline", "candidate")
                )
                for availability in AVAILABILITIES
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
                    item[arm]["primary_inference"] == (item["key"]["repeat_index"] == primary)
                    for item in items
                    for arm in ("baseline", "candidate")
                ),
                "TRAJECTORY_BINDING_MISMATCH",
            )
        result.append(entry)
    _require(
        all(item["key"]["stage"] in seen for item in comparisons), "TRAJECTORY_COVERAGE_MISMATCH"
    )
    return sorted(result, key=lambda item: STAGES.index(item["stage"]))


def _v2_stage_details(
    stage: dict[str, Any],
    status: str,
    authenticated_repeats: int,
    planned_case_count: int,
    exported: int,
) -> dict[str, Any]:
    if status == "not_run":
        _require(stage.get("stage_build") is None, "TRAJECTORY_BINDING_MISMATCH")
        _require(exported == 0 and authenticated_repeats == 0, "TRAJECTORY_COVERAGE_MISMATCH")
        _require(stage.get("not_run_reason") in NOT_RUN_REASONS, "TRAJECTORY_UNSUPPORTED_VALUE")
        _require(stage.get("classification") == "not_evaluated", "TRAJECTORY_BINDING_MISMATCH")
        _require(stage.get("decision_digest") is None, "TRAJECTORY_BINDING_MISMATCH")
        return {
            "classification": "not_evaluated",
            "reason_code": None,
            "not_run_reason": stage["not_run_reason"],
        }

    _require(stage.get("not_run_reason") is None, "TRAJECTORY_BINDING_MISMATCH")
    _build_record(stage.get("stage_build"), allow_unrecorded=True)
    for field in ("stage_input_digest", "stage_artifact_digest"):
        _digest(stage.get(field))
    if status == "evaluated":
        _digest(stage.get("execution_attestation_digest"))
        _digest(stage.get("measurement_receipt_digest"))
    classification = _enum(
        stage.get("classification"),
        ("screened", "confirmed", "indeterminate", "rejected")
        if status == "evaluated"
        else CLOSED_STAGE_CLASSIFICATIONS,
    )
    result = {
        "classification": classification,
        "reason_code": None,
        "not_run_reason": None,
    }
    if status == "closed":
        _digest(stage.get("stop_receipt_digest"))
        decision_digest = stage.get("decision_digest")
        if decision_digest is not None:
            _digest(decision_digest)
        _require(stage.get("stop_reason") in ("partial", "budget", "hard_failure"))
        result["reason_code"] = _enum(stage.get("reason_code"), CLOSED_STAGE_REASONS)
        _require(authenticated_repeats == 0, "TRAJECTORY_COVERAGE_MISMATCH")
        _require(stage.get("decision_digest") is None, "TRAJECTORY_BINDING_MISMATCH")
    _require(authenticated_repeats <= planned_case_count, "TRAJECTORY_COVERAGE_MISMATCH")
    return result


def _stages_v2(
    execution: dict[str, Any], comparisons: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in _array(execution.get("stages"), len(STAGES)):
        stage = _object(raw)
        name = _enum(stage.get("stage"), STAGES)
        _require(name not in seen, "TRAJECTORY_DUPLICATE_IDENTITY")
        seen.add(name)
        status = _enum(stage.get("execution_status"), ("evaluated", "closed", "not_run"))
        required_keys = {
            "evaluated": _V2_EVALUATED_STAGE_KEYS,
            "closed": _V2_CLOSED_STAGE_KEYS,
            "not_run": _V2_NOT_RUN_STAGE_KEYS,
        }[status]
        _exact_keys(stage, required_keys)
        _require(stage.get("planned") is True)
        authenticated_repeats = _integer(stage.get("authenticated_repeat_count"), 100)
        run_id = None if status == "not_run" else _digest(stage.get("stage_run_id"))
        split = _enum(stage.get("split"), ("explore", "confirm"))
        items = [item for item in comparisons if item["key"]["stage"] == name]
        if run_id is not None:
            _require(
                all(
                    item["key"]["stage_run_id"] == run_id and item["key"]["split"] == split
                    for item in items
                ),
                "TRAJECTORY_BINDING_MISMATCH",
            )
        exported = _integer(stage.get("exported_row_count"))
        _require(exported == 2 * len(items), "TRAJECTORY_COVERAGE_MISMATCH")
        _require(status not in ("closed", "not_run") or not items, "TRAJECTORY_COVERAGE_MISMATCH")
        planned_case_count = _integer(stage.get("planned_case_count"))
        entry: dict[str, Any] = {
            "stage": name,
            "stage_run_id": run_id,
            "split": split,
            "execution_status": status,
            "planned_case_count": planned_case_count,
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
        entry.update(
            _v2_stage_details(
                stage,
                status,
                authenticated_repeats,
                planned_case_count,
                exported,
            )
        )
        if status == "evaluated":
            primary = _integer(stage.get("primary_inference_repeat_index"), 99)
            repeat_indexes = {item["key"]["repeat_index"] for item in items}
            _require(
                authenticated_repeats == len(repeat_indexes),
                "TRAJECTORY_COVERAGE_MISMATCH",
            )
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


def _analysis_v2(
    execution: dict[str, Any],
    rows: tuple[dict[str, Any], ...],
    trajectory: dict[str, Any],
    identities: Mapping[str, tuple[str, int]],
) -> tuple[dict[str, Any], dict[Key, dict[str, Any]], str]:
    """Interpret admitted same-byte documents; never run or replace native verification."""
    execution = _object(execution)
    trajectory = _object(trajectory)
    if execution.get("schema") != "goppi.paired_run_exchange.execution.v2":
        _unsupported_format("trajectory")
    format_version = EXECUTION_FORMAT_V2
    runner_profile, runner_declaration, split_declaration, producer_declarations = (
        _validate_v2_execution(execution, rows)
    )
    _validate_v2_trajectory(trajectory, _object(execution.get("source")))
    capture_profile = _enum(trajectory.get("profile"), SUPPORTED_CAPTURE_PROFILES)
    _require(
        capture_profile == CAPTURE_PROFILE_BY_RUNNER[runner_profile],
        "TRAJECTORY_BINDING_MISMATCH",
    )
    _require(trajectory.get("call_relationships") == "not_captured", "TRAJECTORY_UNSUPPORTED_VALUE")
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
    index = _row_index(rows, format_version=format_version)
    _require(
        all(key[0] == source_context["cycle_run_id"] for key in index),
        "TRAJECTORY_BINDING_MISMATCH",
    )
    if format_version == EXECUTION_FORMAT_V2:
        expected_row_context = {
            "evalset_digest": _digest(source.get("evalset_digest")),
            "assignment_digest": _digest(source.get("assignment_digest")),
            "split_policy_digest": _digest(source.get("split_policy_digest")),
            "config_digest": _digest(source.get("project_config_digest")),
        }
        _require(
            all(row["row_context"] == expected_row_context for row in index.values()),
            "TRAJECTORY_BINDING_MISMATCH",
        )
    bindings = (
        _ExecutionBindings(
            format_version=EXECUTION_FORMAT_V2,
            assignment_digest=_digest(source.get("assignment_digest")),
            split_policy_digest=_digest(source.get("split_policy_digest")),
            runner_model=runner_declaration["model"],
            stage_input_digests={
                _enum(_object(stage).get("stage"), STAGES): _digest(
                    _object(stage).get("stage_input_digest")
                )
                for stage in _array(execution.get("stages"), len(STAGES))
                if _object(stage).get("execution_status") in ("evaluated", "closed")
            },
        )
        if format_version == EXECUTION_FORMAT_V2
        else None
    )
    joined, coverage = _join(index, trajectory, capture_profile, runner_profile, bindings)
    comparisons = _comparisons(joined)
    stages = _stages(execution, comparisons, format_version=format_version)
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
    trust_scope: dict[str, Any] = {
        **VERIFIER_REPORT_NOT_PERFORMED,
        "source_authentication_independently_verified": False,
        "producer_projection_independently_verified": False,
        "outcome_harness_binding": binding,
    }
    report: dict[str, Any] = {
        "schema": RESULT_SCHEMA_V4,
        "state": "analyzed",
        "policy": REVIEW_POLICY_V2,
        "input_members": members,
        "source_context": source_context,
        "trust_scope": trust_scope,
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
    if format_version == EXECUTION_FORMAT_V2:
        assert producer_declarations is not None
        assert split_declaration is not None
        split_declaration.update(
            assignment_digest=_digest(source.get("assignment_digest")),
            split_policy_digest=_digest(source.get("split_policy_digest")),
        )
        producer_declarations["split"] = split_declaration
        report["producer_declarations"] = producer_declarations
    return report, joined, runner_profile


def analyze_goppi_trajectory_actions_v2(
    execution: dict[str, Any],
    rows: tuple[dict[str, Any], ...],
    trajectory: dict[str, Any],
    identities: Mapping[str, tuple[str, int]],
) -> dict[str, object]:
    """Add v2 outcome-review tasks while preserving the v4 report family."""
    return _review_v2(execution, rows, trajectory, identities, outcome_actions=True)


def analyze_goppi_trajectory_actions(
    execution: dict[str, Any],
    rows: tuple[dict[str, Any], ...],
    trajectory: dict[str, Any],
    identities: Mapping[str, tuple[str, int]],
) -> dict[str, object]:
    """Add outcome-review tasks while keeping each execution family in its own result."""
    if _object(execution).get("schema") == "goppi.paired_run_exchange.execution.v2":
        return analyze_goppi_trajectory_actions_v2(execution, rows, trajectory, identities)
    report = analyze_goppi_trajectory(execution, rows, trajectory, identities)
    tasks = [
        *cast(list[dict[str, Any]], report["next_verification_tasks"]),
        *_outcome_action_tasks(cast(list[dict[str, Any]], report["comparisons"])),
    ]
    return {
        **report,
        "schema": OUTCOME_ACTIONS_SCHEMA,
        "policy": OUTCOME_ACTIONS_POLICY,
        "next_verification_tasks": sorted(tasks, key=lambda task: task["kind"]),
    }


def _outcome_action_tasks(comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kind = _OUTCOME_ACTION_TASK_KIND
    scope = [
        {"comparison_id": item["id"], "arms": ["baseline", "candidate"]}
        for item in comparisons
        if item["comparability"]["state"] == "comparable"
        and item["difference"]["state"] == "observed"
        and item["difference"]["outcome_changed"] is True
        and item["difference"]["tool_status_deltas"] == []
    ]
    return [_task(kind, scope, OUTCOME_ACTION_TASK_EVIDENCE[kind])] if scope else []


def _review_v2(
    execution: dict[str, Any],
    rows: tuple[dict[str, Any], ...],
    trajectory: dict[str, Any],
    identities: Mapping[str, tuple[str, int]],
    *,
    outcome_actions: bool,
) -> dict[str, object]:
    report, joined, runner_profile = _analysis_v2(execution, rows, trajectory, identities)
    source = _object(execution.get("source"))
    status = _enum(source.get("cycle_status"), CYCLE_STATUSES)
    stages, digests = _verdict_stages(execution, cast(list[dict[str, Any]], report["stages"]))
    recorded = {stage["stage"] for stage in stages}
    absent = [name for name in STAGES if name not in recorded]
    stopped = status == "stopped"
    closed = any(stage["execution_status"] == "closed" for stage in stages)
    tasks = list(cast(list[dict[str, Any]], report["next_verification_tasks"]))
    if outcome_actions:
        tasks.extend(_outcome_action_tasks(cast(list[dict[str, Any]], report["comparisons"])))
    if stopped and absent:
        kind = "obtain_stopped_cycle_stage_record"
        cycle_run_id = cast(dict[str, str], report["source_context"])["cycle_run_id"]
        scope = [{"cycle_run_id": cycle_run_id, "stage": name} for name in absent]
        tasks.append(_task(kind, scope, CYCLE_TASK_EVIDENCE[kind]))
    if digests:
        kind = "obtain_evaluated_stage_verdict"
        tasks.append(_task(kind, list(digests), CYCLE_TASK_EVIDENCE[kind]))
    applies = (stopped and bool(absent), stopped and not closed, bool(digests))
    limits = [
        *CURRENT_INTERPRETATION_LIMITS,
        *(limit for limit, applied in zip(CYCLE_LIMITS, applies, strict=True) if applied),
    ]
    binding = cast(dict[str, Any], report["trust_scope"])["outcome_harness_binding"]
    return {
        **report,
        "schema": OUTCOME_ACTIONS_SCHEMA_V4 if outcome_actions else RESULT_SCHEMA_V4,
        "policy": OUTCOME_ACTIONS_POLICY_V2 if outcome_actions else REVIEW_POLICY_V2,
        "cycle": {"status": status, "stages_absent_from_export": absent},
        "runner": _runner(runner_profile, joined),
        "trust_scope": {
            **VERIFIER_REPORT_NOT_PERFORMED,
            "source_authentication_independently_verified": False,
            "producer_projection_independently_verified": False,
            "outcome_harness_binding": binding,
        },
        "stages": stages,
        "next_verification_tasks": sorted(tasks, key=lambda task: task["kind"]),
        "interpretation_limits": limits,
    }


def analyze_goppi_trajectory_v2(
    execution: dict[str, Any],
    rows: tuple[dict[str, Any], ...],
    trajectory: dict[str, Any],
    identities: Mapping[str, tuple[str, int]],
) -> dict[str, object]:
    """Analyze only the execution.v2, row.v2 and trajectory.v2 family."""
    return _review_v2(execution, rows, trajectory, identities, outcome_actions=False)


# Latest main cycle interpretation disclosures, shared by the v1 and v2 review projections.
CYCLE_STATUSES = ("completed", "stopped")
STAGE_VERDICT_STATES = {
    "evaluated": "digest_only",
    "closed": "producer_reported",
    "not_run": "not_measured",
}
CYCLE_TASK_EVIDENCE = {
    "obtain_evaluated_stage_verdict": (
        "goppi",
        "producer stage verdict value bound to each listed decision digest",
    ),
    "obtain_stopped_cycle_stage_record": (
        "goppi",
        "producer plan and execution status for listed stages absent from the stopped cycle",
    ),
}
CURRENT_INTERPRETATION_LIMITS = (
    *INTERPRETATION_LIMITS,
    "cycle_status_stage_verdicts_and_runner_are_producer_claims",
)
ABSENT_STAGE_LIMIT = "stopped_cycle_absent_stages_may_be_unrecorded"
STOP_REASON_LIMIT = "stopped_cycle_stop_reason_not_exported"
DIGEST_ONLY_VERDICT_LIMIT = "evaluated_stage_verdicts_are_digest_only"
CYCLE_LIMITS = (ABSENT_STAGE_LIMIT, STOP_REASON_LIMIT, DIGEST_ONLY_VERDICT_LIMIT)
VERIFIER_REPORT_NOT_PERFORMED: Mapping[str, object] = MappingProxyType(
    {
        "verifier_report_state": "not_performed",
        "verifier_source": None,
        "verifier_identity_verified": False,
        "response_snapshot_binding": "not_checked",
        "snapshot_postcheck": "not_checked",
        "content_closure": "not_checked",
        "authenticity": "not_checked",
    }
)

def _task(kind: str, scope: list[dict[str, Any]], evidence: tuple[str, str]) -> dict[str, Any]:
    owner, required_evidence = evidence
    return {
        "id": _identity("task:", {"kind": kind, "scope": scope}),
        "kind": kind,
        "scope": scope,
        "owner": owner,
        "required_evidence": required_evidence,
        "execution_authorized": False,
    }

def _analysis_v1(
    execution: dict[str, Any],
    rows: tuple[dict[str, Any], ...],
    trajectory: dict[str, Any],
    identities: Mapping[str, tuple[str, int]],
) -> tuple[dict[str, object], dict[Key, dict[str, Any]], str]:
    _require(
        _object(execution).get("schema") == "goppi.paired_run_exchange.execution.v1",
        "TRAJECTORY_UNSUPPORTED_VALUE",
    )
    _require(
        _object(trajectory).get("schema") == "goppi.paired_trajectory_exchange.document.v1",
        "TRAJECTORY_UNSUPPORTED_VALUE",
    )
    runner_profile = _enum(
        _object(_object(execution.get("source")).get("runner")).get("profile"),
        tuple(CAPTURE_PROFILE_BY_RUNNER),
    )
    capture_profile = _enum(trajectory.get("profile"), SUPPORTED_CAPTURE_PROFILES)
    _require(
        capture_profile == CAPTURE_PROFILE_BY_RUNNER[runner_profile],
        "TRAJECTORY_BINDING_MISMATCH",
    )
    _require(trajectory.get("call_relationships") == "not_captured", "TRAJECTORY_UNSUPPORTED_VALUE")
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
    joined, coverage = _join(index, trajectory, capture_profile, runner_profile)
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
    report: dict[str, object] = {
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
    return report, joined, runner_profile

def _verdict_stages(
    execution: dict[str, Any], stages: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Mark each recorded stage's producer verdict; evaluated stages export only a digest."""
    recorded = {
        _object(raw).get("stage"): _object(raw)
        for raw in _array(execution.get("stages"), len(STAGES))
    }
    result: list[dict[str, Any]] = []
    digests: list[dict[str, str]] = []
    for stage in stages:
        entry = {**stage, "verdict_state": STAGE_VERDICT_STATES[stage["execution_status"]]}
        if stage["execution_status"] == "evaluated":
            digest = _digest(recorded[stage["stage"]].get("decision_digest"))
            entry["decision_digest"] = digest
            digests.append({"stage": stage["stage"], "decision_digest": digest})
        result.append(entry)
    return result, digests

def _runner(profile: str, joined: dict[Key, dict[str, Any]]) -> dict[str, object]:
    declared = [item["context"]["runner_model"] for item in joined.values()]
    models = Counter(cast(str, model) for model in declared if model is not None)
    return {
        "profile": profile,
        "models": [
            {"runner_model": model, "paired_rows": count} for model, count in sorted(models.items())
        ],
        "model_unrecorded_rows": declared.count(None),
    }

def review_goppi_trajectory(
    execution: dict[str, Any],
    rows: tuple[dict[str, Any], ...],
    trajectory: dict[str, Any],
    identities: Mapping[str, tuple[str, int]],
    *,
    outcome_actions: bool = False,
) -> dict[str, object]:
    """Current review: the same comparisons plus producer cycle, verdict and runner claims.

    No verifier report is accepted here; an adapter that accepts one replaces the trust
    state. A stopped cycle whose export lacks a stage cannot show whether it was planned,
    and without a closed stage the export records no reason for the stop.
    """
    if _object(execution).get("schema") == "goppi.paired_run_exchange.execution.v2":
        return (
            analyze_goppi_trajectory_actions_v2(execution, rows, trajectory, identities)
            if outcome_actions
            else analyze_goppi_trajectory_v2(execution, rows, trajectory, identities)
        )
    report, joined, runner_profile = _analysis_v1(execution, rows, trajectory, identities)
    status = _enum(_object(execution.get("source")).get("cycle_status"), CYCLE_STATUSES)
    stages, digests = _verdict_stages(execution, cast(list[dict[str, Any]], report["stages"]))
    recorded = {stage["stage"] for stage in stages}
    absent = [name for name in STAGES if name not in recorded]
    stopped = status == "stopped"
    closed = any(stage["execution_status"] == "closed" for stage in stages)
    tasks = list(cast(list[dict[str, Any]], report["next_verification_tasks"]))
    if outcome_actions:
        tasks.extend(_outcome_action_tasks(cast(list[dict[str, Any]], report["comparisons"])))
    if stopped and absent:
        kind = "obtain_stopped_cycle_stage_record"
        cycle_run_id = cast(dict[str, str], report["source_context"])["cycle_run_id"]
        scope = [{"cycle_run_id": cycle_run_id, "stage": name} for name in absent]
        tasks.append(_task(kind, scope, CYCLE_TASK_EVIDENCE[kind]))
    if digests:
        kind = "obtain_evaluated_stage_verdict"
        tasks.append(_task(kind, list(digests), CYCLE_TASK_EVIDENCE[kind]))
    applies = (stopped and bool(absent), stopped and not closed, bool(digests))
    limits = [
        *CURRENT_INTERPRETATION_LIMITS,
        *(limit for limit, applied in zip(CYCLE_LIMITS, applies, strict=True) if applied),
    ]
    binding = cast(dict[str, Any], report["trust_scope"])["outcome_harness_binding"]
    return {
        **report,
        "schema": OUTCOME_ACTIONS_SCHEMA_V3 if outcome_actions else RESULT_SCHEMA_V3,
        "policy": OUTCOME_ACTIONS_POLICY_V2 if outcome_actions else REVIEW_POLICY_V2,
        "cycle": {"status": status, "stages_absent_from_export": absent},
        "runner": _runner(runner_profile, joined),
        "trust_scope": {
            **VERIFIER_REPORT_NOT_PERFORMED,
            "source_authentication_independently_verified": False,
            "producer_projection_independently_verified": False,
            "outcome_harness_binding": binding,
        },
        "stages": stages,
        "next_verification_tasks": sorted(tasks, key=lambda task: task["kind"]),
        "interpretation_limits": limits,
    }

def analyze_goppi_trajectory(
    execution: dict[str, Any],
    rows: tuple[dict[str, Any], ...],
    trajectory: dict[str, Any],
    identities: Mapping[str, tuple[str, int]],
) -> dict[str, object]:
    """Interpret one explicitly versioned Goppi execution family."""
    schema = _object(execution).get("schema")
    if schema == "goppi.paired_run_exchange.execution.v1":
        return _analysis_v1(execution, rows, trajectory, identities)[0]
    if schema == "goppi.paired_run_exchange.execution.v2":
        return analyze_goppi_trajectory_v2(execution, rows, trajectory, identities)
    _unsupported_format("trajectory")

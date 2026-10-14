"""Card: baseline-event | Paper: ADR-020 | Stage: producer-bridge.

Pure exact baseline exchange v2 projection; producer labels remain source claims.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes

from underwrite.instrument.evidence.common_result import (
    ResultInvariantError,
    check_result_invariants,
)
from underwrite.instrument.evidence.goppi_identity import length_prefixed_digest
from underwrite.measurement_contract.semantics import (
    ContractError,
    MeasurementSemantics,
    parse_semantics,
    project_semantics,
    semantic_digest,
)

FILES = ("manifest.json", "export.json", "rows.jsonl", "schema.json", "scope.json")
MAX_CASES = 200
FORMAT_SCOPE = "single_product_baseline_run_with_approved_scope"
MANIFEST_SCHEMA = "goppi.baseline_case_exchange.manifest.v2"
EXPORT_SCHEMA = "goppi.baseline_case_exchange.export.v2"
SCOPE_SCHEMA = "goppi.baseline_case_exchange.scope.v1"
ROW_SCHEMA = "goppi.baseline_case_exchange.row.v2"
INSTRUMENT = "goppi.baseline-primary-status.v2"
METRIC = "producer-primary-observed-true"
PROTOCOL = "goppi.baseline_case_exchange.v2"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_ISO_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\Z")
_RUNNERS = {
    "deterministic_node_conformance": "deterministic_conformance",
    "pi_0_84_4": "existing_pi",
    "claude_code_2_1_283_preview": "preview_protocol",
}
_CLAIMS = (
    "model_contacted_by_exporter network_contacted_by_exporter "
    "source_project_modified_by_exporter paired_effect_claimed human_approval_claimed "
    "conditions_comparability_claimed producer_scores_independently_regraded "
    "native_receipt_authentication_checked_by_exporter provider_authentication_verified "
    "provider_billing_verified"
)


@dataclass(frozen=True)
class BaselineDocuments:
    manifest: dict[str, Any]
    export: dict[str, Any]
    scope: dict[str, Any]
    rows: tuple[dict[str, Any], ...]
    identities: Mapping[str, tuple[str, int]]


@dataclass(frozen=True)
class _ValidatedExchange:
    scope: dict[str, Any]
    ids: list[str]
    verifier: str
    source: dict[str, Any]
    statuses: list[str]
    counts: dict[str, int]


class BaselineProjectionError(ValueError):
    """Finite diagnostic fields, never producer-controlled exception text."""

    def __init__(self, code: str, reason: str, location: str) -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def _require(condition: bool, reason: str, location: str) -> None:
    if not condition:
        raise BaselineProjectionError("INVALID_INPUT", reason, location)


def _closed(value: object, fields: str, location: str) -> dict[str, Any]:
    _require(type(value) is dict, "BASELINE_SHAPE_INVALID", location)
    result = cast(dict[str, Any], value)
    _require(set(result) == set(fields.split()), "BASELINE_SHAPE_INVALID", location)
    return result


def _text(value: object, location: str, maximum: int = 128) -> str:
    _require(type(value) is str, "BASELINE_VALUE_INVALID", location)
    assert isinstance(value, str)
    _require(
        bool(value.strip())
        and len(value) <= maximum
        and unicodedata.normalize("NFC", value) == value
        and all(not unicodedata.category(c).startswith("C") for c in value),
        "BASELINE_VALUE_INVALID",
        location,
    )
    return value


def _digest(value: object, location: str) -> str:
    result = _text(value, location)
    _require(_DIGEST.fullmatch(result) is not None, "BASELINE_DIGEST_INVALID", location)
    return result


def _integer(value: object, location: str, maximum: int = 2**53 - 1) -> int:
    _require(type(value) is int and 0 <= value <= maximum, "BASELINE_VALUE_INVALID", location)
    assert isinstance(value, int)
    return value


def _nonnegative_number(value: object, location: str) -> None:
    _require(type(value) in (int, float), "BASELINE_VALUE_INVALID", location)
    number = cast(int | float, value)
    try:
        valid = isfinite(number) and number >= 0
    except OverflowError:
        valid = False
    _require(valid, "BASELINE_VALUE_INVALID", location)


def _utc_datetime(value: object, location: str) -> None:
    timestamp = _text(value, location, 64)
    _require(_ISO_UTC.fullmatch(timestamp) is not None, "BASELINE_VALUE_INVALID", location)
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        raise BaselineProjectionError("INVALID_INPUT", "BASELINE_VALUE_INVALID", location) from None


def _literal(value: object, expected: object, location: str) -> None:
    _require(
        type(value) is type(expected) and value == expected, "BASELINE_PROFILE_INVALID", location
    )


def _tagged(value: dict[str, Any], tag: str, location: str) -> None:
    claimed = _digest(value["content_digest"], location)
    payload = {k: v for k, v in value.items() if k != "content_digest"}
    try:
        actual = length_prefixed_digest((tag, canonical_bytes(payload).decode("utf-8")))
    except CanonicalizationError:
        raise BaselineProjectionError("INVALID_INPUT", "BASELINE_VALUE_INVALID", location) from None
    _require(claimed == actual, "BASELINE_CONTENT_DIGEST_MISMATCH", location)


def _manifest(documents: BaselineDocuments) -> None:
    location = "/manifest"
    manifest = _closed(
        documents.manifest,
        "schema format_scope status manifest_written_last immutable_fresh_output "
        "source_result_digest scope_content_digest files content_digest",
        location,
    )
    for key, value in (
        ("schema", MANIFEST_SCHEMA),
        ("format_scope", FORMAT_SCOPE),
        ("status", "complete"),
        ("manifest_written_last", True),
        ("immutable_fresh_output", True),
    ):
        _literal(manifest[key], value, location)
    _require(
        isinstance(cast(object, documents.identities), Mapping)
        and set(documents.identities) == set(FILES),
        "BASELINE_FILE_SET_INVALID",
        location,
    )
    for name in FILES:
        identity = documents.identities[name]
        _require(
            type(identity) is tuple and len(identity) == len(("sha256", "bytes")),
            "BASELINE_IDENTITY_INVALID",
            location,
        )
        _digest(identity[0], location)
        _integer(identity[1], location)
    entries = manifest["files"]
    _require(
        type(entries) is list and len(entries) == len(FILES) - 1,
        "BASELINE_FILE_SET_INVALID",
        location,
    )
    for name, item in zip(FILES[1:], entries, strict=True):
        entry = _closed(item, "path sha256 bytes", location)
        _literal(entry["path"], name, location)
        _digest(entry["sha256"], location)
        _integer(entry["bytes"], location)
        _require(
            (entry["sha256"], entry["bytes"]) == documents.identities[name],
            "BASELINE_FILE_IDENTITY_MISMATCH",
            location,
        )
    _digest(manifest["source_result_digest"], location)
    _digest(manifest["scope_content_digest"], location)
    _tagged(manifest, "goppi:baseline-case-exchange-manifest:v2", location)


def _scope(value: object) -> tuple[dict[str, Any], list[str], str]:
    location = "/scope"
    scope = _closed(value, "schema approved selection cases exclusions content_digest", location)
    _literal(scope["schema"], SCOPE_SCHEMA, location)
    approved = _closed(
        scope["approved"],
        "draft_digest evalset_digest approval_receipt_digest "
        "split_policy_digest assignment_digest classification",
        location,
    )
    for key in approved:
        if key != "classification":
            _digest(approved[key], location)
    _require(
        approved["classification"] in ("synthetic_fixture", "curated_development"),
        "BASELINE_PROFILE_INVALID",
        location,
    )
    selection = _closed(scope["selection"], "split case_ids", location)
    _literal(selection["split"], "explore", location)
    ids = selection["case_ids"]
    _require(
        type(ids) is list and 0 < len(ids) <= MAX_CASES, "BASELINE_POPULATION_INVALID", location
    )
    ids = [_text(item, location) for item in ids]
    _require(len(set(ids)) == len(ids), "BASELINE_POPULATION_INVALID", location)
    cases = scope["cases"]
    _require(
        type(cases) is list and len(cases) == len(ids), "BASELINE_POPULATION_INVALID", location
    )
    verifiers: set[str] = set()
    for case_id, item in zip(ids, cases, strict=True):
        case = _closed(
            item,
            "case_id family source_kind case_content_digest verifier_id "
            "verifier_digest verifier_outcome",
            location,
        )
        _literal(case["case_id"], case_id, location)
        _text(case["family"], location)
        _require(
            case["source_kind"] in ("git_pr", "manual", "synthetic"),
            "BASELINE_PROFILE_INVALID",
            location,
        )
        _digest(case["case_content_digest"], location)
        _literal(case["verifier_outcome"], "binary", location)
        verifiers.add(_text(case["verifier_id"], location))
        _digest(case["verifier_digest"], location)
    _require(len(verifiers) == 1, "BASELINE_MIXED_VERIFIERS", location)
    exclusions = _closed(
        scope["exclusions"],
        "benchmark_claims private_truth_included "
        "confirm_case_metadata_included instructions_included "
        "source_references_included reviewer_details_included",
        location,
    )
    for key in exclusions:
        _literal(exclusions[key], "prohibited" if key == "benchmark_claims" else False, location)
    _tagged(scope, "goppi:baseline-case-exchange-scope:v1", location)
    return scope, ids, next(iter(verifiers))


def _runner(value: object, location: str) -> dict[str, Any]:
    runner = _closed(
        value,
        "profile support_scope provider_authentication_verified provider_billing_verified",
        location,
    )
    profile = _text(runner["profile"], location)
    _require(
        profile in _RUNNERS and runner["support_scope"] == _RUNNERS[profile],
        "BASELINE_PROFILE_INVALID",
        location,
    )
    _literal(runner["provider_authentication_verified"], False, location)
    _literal(runner["provider_billing_verified"], False, location)
    return runner


def _aggregate_cost(value: object) -> None:
    location = "/export/aggregate_cost"
    cost = _closed(
        value,
        "status value unit source lower_bound provider_authentication_verified "
        "provider_billing_verified provider_billing_cap missing_values_are_zero",
        location,
    )
    _require(
        cost["status"] in ("known_zero", "reported_estimate", "unknown"),
        "BASELINE_VALUE_INVALID",
        location,
    )
    if cost["value"] is not None:
        _nonnegative_number(cost["value"], location)
    _literal(cost["unit"], "USD", location)
    _require(
        cost["source"]
        in (
            "deterministic_runner_no_model_cost",
            "source_result.pi_reported_cost_usd",
            "source_result.runner_reported_cost_usd",
        ),
        "BASELINE_VALUE_INVALID",
        location,
    )
    _require(type(cost["lower_bound"]) is bool, "BASELINE_VALUE_INVALID", location)
    for key in (
        "provider_authentication_verified",
        "provider_billing_verified",
        "provider_billing_cap",
        "missing_values_are_zero",
    ):
        _literal(cost[key], False, location)


def _usage(value: object) -> None:
    location = "/rows/usage"
    usage = _closed(
        value,
        "status input_tokens output_tokens total_tokens cache_read_tokens "
        "cache_write_tokens reported_cost cost_unit source lower_bound "
        "provider_authentication_verified provider_billing_verified missing_values_are_zero",
        location,
    )
    status = usage["status"]
    _require(
        status in ("observed", "partial", "unknown", "not_stored_per_case"),
        "BASELINE_VALUE_INVALID",
        location,
    )
    for key in (
        "input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "cache_write_tokens"
    ):
        if usage[key] is not None:
            _integer(usage[key], location)
    if usage["reported_cost"] is not None:
        _nonnegative_number(usage["reported_cost"], location)
    _literal(usage["cost_unit"], "USD", location)
    _require(
        usage["source"]
        in (
            "pi_reported_runner_usage",
            "claude_cli_reported",
            "runner_unknown_cost",
            "product_result_stores_aggregate_only",
        ),
        "BASELINE_VALUE_INVALID",
        location,
    )
    _require(type(usage["lower_bound"]) is bool, "BASELINE_VALUE_INVALID", location)
    for key in (
        "provider_authentication_verified", "provider_billing_verified", "missing_values_are_zero"
    ):
        _literal(usage[key], False, location)
    measured = status in ("observed", "partial")
    core = ("input_tokens", "output_tokens", "total_tokens", "reported_cost")
    _require(
        all((usage[key] is not None) == measured for key in core)
        and not (status == "observed" and usage["lower_bound"]),
        "BASELINE_USAGE_STATE_MISMATCH",
        location,
    )


def _export(value: object) -> tuple[dict[str, Any], dict[str, Any]]:
    location = "/export"
    exported = _closed(
        value,
        "schema format_scope status generated_at include_answers source "
        "counts aggregate_cost claims",
        location,
    )
    for key, expected in (
        ("schema", EXPORT_SCHEMA),
        ("format_scope", FORMAT_SCOPE),
        ("status", "complete"),
    ):
        _literal(exported[key], expected, location)
    _utc_datetime(exported["generated_at"], "/export/generated_at")
    _require(type(exported["include_answers"]) is bool, "BASELINE_VALUE_INVALID", location)
    _aggregate_cost(exported["aggregate_cost"])
    source = _closed(
        exported["source"],
        "alias run_id result_digest result_schema preparation_digest "
        "evalset_digest scope_content_digest runner snapshot_material "
        "local_authenticated_source_checked authenticity_scope",
        location,
    )
    alias = _text(source["alias"], "/export/source/alias")
    _require(_ALIAS.fullmatch(alias) is not None, "BASELINE_VALUE_INVALID", "/export/source/alias")
    for key in (
        "run_id",
        "result_digest",
        "preparation_digest",
        "evalset_digest",
        "scope_content_digest",
    ):
        _digest(source[key], location)
    _require(
        source["result_schema"]
        in ("goppi.baseline_discovery_result.v1", "goppi.baseline_discovery_result.v2"),
        "BASELINE_PROFILE_INVALID",
        location,
    )
    _runner(source["runner"], location)
    _literal(source["local_authenticated_source_checked"], True, location)
    _literal(source["authenticity_scope"], "same_local_integrity_key_operator", location)
    material = _closed(
        source["snapshot_material"],
        "bind_schema source_digest graph_digest "
        "materialization_binding_digest data_identity_digest",
        location,
    )
    is_v2 = source["result_schema"].endswith(".v2")
    _literal(
        material["bind_schema"],
        "goppi.native_harness_snapshot_bind.v2"
        if is_v2
        else "goppi.native_harness_snapshot_bind.v1",
        location,
    )
    _digest(material["source_digest"], location)
    for key in ("graph_digest", "materialization_binding_digest", "data_identity_digest"):
        if is_v2:
            _digest(material[key], location)
        else:
            _literal(material[key], None, location)
    claims = _closed(exported["claims"], _CLAIMS, location)
    for claim in claims.values():
        _literal(claim, False, location)
    counts = _closed(exported["counts"], "planned observed_true observed_false missing", location)
    for count in counts.values():
        _integer(count, location, MAX_CASES)
    return exported, source


def _row(value: object, source: dict[str, Any], case_id: str, include_answers: bool) -> str:
    location = "/rows"
    row = _closed(
        value,
        "schema source_result_digest source_result_schema run_id case_id split "
        "arm repeat_index runner observation_id observation_manifest_digest outcome "
        "attempt usage answer",
        location,
    )
    for key, expected in (
        ("schema", ROW_SCHEMA),
        ("case_id", case_id),
        ("split", "explore"),
        ("arm", "baseline"),
        ("repeat_index", 0),
    ):
        _literal(row[key], expected, location)
    for key, source_key in (
        ("source_result_digest", "result_digest"),
        ("source_result_schema", "result_schema"),
        ("run_id", "run_id"),
    ):
        _require(row[key] == source[source_key], "BASELINE_SOURCE_JOIN_MISMATCH", location)
    _require(
        _runner(row["runner"], location) == source["runner"],
        "BASELINE_SOURCE_JOIN_MISMATCH",
        location,
    )
    for key in ("observation_id", "observation_manifest_digest"):
        if row[key] is not None:
            _digest(row[key], location)
    _require(
        (row["observation_id"] is None) == (row["observation_manifest_digest"] is None),
        "BASELINE_OBSERVATION_INVALID",
        location,
    )
    outcome = _closed(
        row["outcome"], "primary_status reason producer_recorded independently_regraded", location
    )
    status = _text(outcome["primary_status"], location)
    _require(
        status in ("observed_true", "observed_false", "missing"),
        "BASELINE_OUTCOME_INVALID",
        location,
    )
    _text(outcome["reason"], location, 256)
    _literal(outcome["producer_recorded"], True, location)
    _literal(outcome["independently_regraded"], False, location)
    _usage(row["usage"])
    attempt = _closed(
        row["attempt"],
        "status attempt_id request_digest receipt_digest termination_reason "
        "duration_ms local_receipt_content_digest_checked",
        location,
    )
    _require(attempt["status"] in ("available", "missing"), "BASELINE_ATTEMPT_INVALID", location)
    available = attempt["status"] == "available"
    _literal(attempt["local_receipt_content_digest_checked"], available, location)
    for key in (
        "attempt_id",
        "request_digest",
        "receipt_digest",
        "termination_reason",
        "duration_ms",
    ):
        if not available:
            _literal(attempt[key], None, location)
        elif key.endswith("digest"):
            _digest(attempt[key], location)
        elif key != "duration_ms":
            _text(attempt[key], location)
    if available and attempt["duration_ms"] is not None:
        _nonnegative_number(attempt["duration_ms"], "/rows/attempt/duration_ms")
    answer = row["answer"]
    _require(type(answer) is dict, "BASELINE_SHAPE_INVALID", location)
    answer = _closed(
        answer,
        "included availability value" if "value" in answer else "included availability",
        location,
    )
    _require(type(answer["included"]) is bool, "BASELINE_ANSWER_INVALID", location)
    _require(
        answer["availability"]
        in ("included_private", "omitted_by_default", "not_retained", "missing"),
        "BASELINE_ANSWER_INVALID",
        location,
    )
    _require(
        answer["included"] == (answer["availability"] == "included_private")
        and ("value" in answer) == answer["included"]
        and (include_answers or not answer["included"]),
        "BASELINE_ANSWER_INVALID",
        location,
    )
    return status


def _semantics(scope: dict[str, Any], verifier: str, direction: str) -> dict[str, Any]:
    declaration = {
        "schema": "measurement_semantics.v1",
        "profile": "binary-event-directional.v1",
        "construct": {
            "id": "goppi-producer-primary-outcome",
            "definition": "Producer primary outcome accounting; observed_false includes "
            "binary verifier false "
            "and producer-attributed endpoint failures. Missing is not failure.",
        },
        "event": {
            "id": METRIC,
            "positive_label_definition": "Producer primary_status is observed_true, "
            "representing reported binary verifier true. "
            "Labels are producer claims, not independently established correctness.",
        },
        "unit": "proportion",
        "sampling_unit": "case",
        "desirable_direction": direction,
        "outcome": {
            "instrument": INSTRUMENT,
            "metric_id": METRIC,
            "label_source": {
                "id": "goppi-baseline-exporter",
                "version": PROTOCOL,
                "reference": "Exchange protocol version, not goppi software version.",
            },
            "rubric": {
                "id": "goppi-primary-status-accounting",
                "version": PROTOCOL,
                "reference": f"Composite declared verifier {verifier}; "
                f"scope {scope['content_digest']} "
                "binds ordered case-specific verifier digest assignments, not identical logic. "
                "Observed_false also includes producer-attributed endpoint failures.",
            },
        },
        "prediction": {"status": "unavailable"},
        "population": {
            "frame_id": scope["content_digest"],
            "description": "Declared ordered explore cases from evalset "
            f"{scope['approved']['evalset_digest']}; "
            "one baseline repeat-zero slot per case; order prespecification is not established.",
        },
    }
    return project_semantics(parse_semantics(declaration))


def _validate_exchange(documents: BaselineDocuments) -> _ValidatedExchange:
    """Validate the exact five-file exchange independently of measurement direction."""
    _require(type(documents) is BaselineDocuments, "BASELINE_DOCUMENTS_REQUIRED", "/")
    _manifest(documents)
    scope, ids, verifier = _scope(documents.scope)
    exported, source = _export(documents.export)
    _require(
        documents.manifest["source_result_digest"] == source["result_digest"]
        and documents.manifest["scope_content_digest"] == scope["content_digest"]
        and source["scope_content_digest"] == scope["content_digest"]
        and source["evalset_digest"] == scope["approved"]["evalset_digest"],
        "BASELINE_SOURCE_JOIN_MISMATCH",
        "/source",
    )
    _require(
        type(documents.rows) is tuple and len(documents.rows) == len(ids),
        "BASELINE_POPULATION_INVALID",
        "/rows",
    )
    statuses = [
        _row(row, source, case_id, exported["include_answers"])
        for row, case_id in zip(documents.rows, ids, strict=True)
    ]
    counts = {
        "planned": len(ids),
        **{
            status: statuses.count(status)
            for status in ("observed_true", "observed_false", "missing")
        },
    }
    _require(counts == exported["counts"], "BASELINE_COUNTS_MISMATCH", "/export/counts")
    return _ValidatedExchange(scope, ids, verifier, source, statuses, counts)


def project_baseline_result(documents: BaselineDocuments) -> dict[str, object]:
    """Project validated producer statuses as descriptive common evidence."""
    validated = _validate_exchange(documents)
    items: list[dict[str, object]] = []
    for index, status in enumerate(validated.statuses):
        missing = status == "missing"
        items.append(
            {
                "coordinate": {"kind": "case", "index": index},
                "metric": {"semantic_binding": "unbound", "source_metric_index": 0},
                "applicability": "applicable",
                "availability": "missing" if missing else "observed",
                "value": None if missing else status == "observed_true",
                "reason": "SOURCE_MISSING" if missing else None,
                "next_action": "inspect_source" if missing else "none",
                "origin": "source_claim",
                "method": None,
                "policy": None,
                "permitted_effect": "descriptive",
            }
        )
    result: dict[str, object] = {
        "schema": "evidence_result.v1",
        "source": {
            "format": "goppi.baseline_case_exchange",
            "format_version": "v2",
            "identity": {
                "kind": "bound_bundle",
                "manifest_raw_hash": documents.identities["manifest.json"][0],
                "members": {name: documents.identities[name][0] for name in FILES},
            },
        },
        "processing": {"state": "analyzed", "reason": None, "next_action": "none"},
        "population": {
            "unit": "case",
            "observed_count": len(documents.rows),
            "planned_count": len(validated.ids),
        },
        "items": items,
        "verification": [],
    }
    try:
        check_result_invariants(result)
    except ResultInvariantError:
        raise BaselineProjectionError(
            "INTERNAL_ERROR", "BASELINE_RESULT_INVARIANT_FAILED", "/result"
        ) from None
    return result


def project_baseline_bundle(documents: BaselineDocuments, *, direction: str) -> dict[str, object]:
    """Validate consumed producer contracts and project a native event bundle without I/O."""
    _require(type(documents) is BaselineDocuments, "BASELINE_DOCUMENTS_REQUIRED", "/")
    _require(direction in ("higher", "lower"), "BASELINE_DIRECTION_INVALID", "/direction")
    validated = _validate_exchange(documents)
    scope, ids, verifier = validated.scope, validated.ids, validated.verifier
    source, statuses, counts = validated.source, validated.statuses, validated.counts
    selector = {"instrument": INSTRUMENT, "metric_id": METRIC}
    rows = [
        {
            "case_id": case_id,
            "sequence": index,
            "repeat": 0,
            "split": "explore",
            "outcome": None if status == "missing" else int(status == "observed_true"),
            "score": None,
        }
        for index, (case_id, status) in enumerate(zip(ids, statuses, strict=True))
    ]
    availability = [
        {
            **selector,
            "case_id": case_id,
            "repeat": 0,
            "split": "explore",
            "availability": "missing" if status == "missing" else "observed",
        }
        for case_id, status in zip(ids, statuses, strict=True)
    ]
    semantics = _semantics(scope, verifier, direction)
    return {
        "schema_version": "underwrite.evidence-bundle.v1",
        "manifest": {"run_id": source["run_id"]},
        "measurement_semantics": semantics,
        "semantic_digest": semantic_digest(parse_semantics(semantics)),
        "sources": [
            {
                "format": "goppi.baseline_case_exchange",
                "format_version": "v2",
                "run_id": source["run_id"],
                "result_digest": source["result_digest"],
                "manifest_content_digest": documents.manifest["content_digest"],
                "evalset_digest": source["evalset_digest"],
                "scope_digest": scope["content_digest"],
                "expected_case_ids": ids,
                "counts": counts,
                "files": [
                    {
                        "name": name,
                        "sha256": documents.identities[name][0],
                        "bytes": documents.identities[name][1],
                    }
                    for name in FILES
                ],
                "limitations": [
                    "source_claims_not_independently_verified",
                    "byte_identity_not_independent_provenance",
                    "source_order_not_established_as_prespecified",
                    "statistical_assumptions_not_supplied",
                    "predictions_not_supplied",
                ],
            }
        ],
        "readings": [{**selector, "rows": rows}],
        "availability": availability,
    }


def baseline_policy_inputs(observation: object) -> tuple[MeasurementSemantics, tuple[str, ...]]:
    """Extract a baseline declaration, without authenticating it or validating rows."""
    location = "/observation"
    _require(type(observation) is dict, "BASELINE_SHAPE_INVALID", location)
    obs = cast(dict[str, Any], observation)
    _literal(
        obs.get("source"),
        {"format": "underwrite.evidence-bundle", "format_version": "v1"},
        location,
    )
    _literal(obs.get("kind"), "eval_run", location)
    payload = obs.get("payload")
    _require(type(payload) is dict, "BASELINE_SHAPE_INVALID", location)
    payload = cast(dict[str, Any], payload)
    _literal(payload.get("schema_version"), "underwrite.evidence-bundle.v1", location)
    sources = payload.get("sources")
    _require(type(sources) is list and len(sources) == 1, "BASELINE_SHAPE_INVALID", location)
    source = cast(list[object], sources)[0]
    _require(type(source) is dict, "BASELINE_SHAPE_INVALID", location)
    source = cast(dict[str, Any], source)
    _literal(source.get("format"), "goppi.baseline_case_exchange", location)
    _literal(source.get("format_version"), "v2", location)
    roster_location = "/observation/payload/sources/0/expected_case_ids"
    _require("expected_case_ids" in source, "BASELINE_EXPECTED_CASES_REQUIRED", roster_location)
    ids = source["expected_case_ids"]
    _require(
        type(ids) is list and 0 < len(ids) <= MAX_CASES,
        "BASELINE_EXPECTED_CASES_INVALID",
        roster_location,
    )
    try:
        roster = tuple(_text(item, roster_location) for item in cast(list[object], ids))
    except BaselineProjectionError:
        raise BaselineProjectionError(
            "INVALID_INPUT", "BASELINE_EXPECTED_CASES_INVALID", roster_location
        ) from None
    _require(len(set(roster)) == len(roster), "BASELINE_EXPECTED_CASES_INVALID", roster_location)
    counts = source.get("counts")
    _require(type(counts) is dict, "BASELINE_SHAPE_INVALID", location)
    planned = _integer(cast(dict[str, Any], counts).get("planned"), location, MAX_CASES)
    _require(planned == len(roster), "BASELINE_COUNTS_MISMATCH", location)
    scope_digest = _digest(source.get("scope_digest"), location)
    try:
        semantics = parse_semantics(payload.get("measurement_semantics"))
    except ContractError:
        raise BaselineProjectionError("INVALID_INPUT", "BASELINE_VALUE_INVALID", location) from None
    declaration = project_semantics(semantics)
    _literal(declaration["population"]["frame_id"], scope_digest, location)
    _literal(declaration["outcome"]["instrument"], INSTRUMENT, location)
    _literal(declaration["outcome"]["metric_id"], METRIC, location)
    _literal(declaration["prediction"], {"status": "unavailable"}, location)
    return semantics, roster

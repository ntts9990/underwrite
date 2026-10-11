"""Bounded paired evidence from explicitly declared binary source observations.

The input is a source claim. This module checks its structure and accounting, then
uses only primary repeat-zero pairs for the existing paired evidence calculation.
It does not verify execution, assumptions, or authorize a decision.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

from underwrite.instrument.evidence._resampling import ResamplingPolicy
from underwrite.instrument.evidence.paired import (
    EXACT_MAX_CASES,
    CaseScore,
    PairedError,
    measure_paired,
)

INPUT_MAX_BYTES = 2 * 1024 * 1024
OUTPUT_MAX_BYTES = 1024 * 1024
MAX_PRIMARY_CASES = 128
MAX_SLOTS = 4096
MAX_REPEAT = 1024
MIN_PERMUTATION_ITERATIONS = 10_000
MAX_PERMUTATION_ITERATIONS = 20_000
MIN_BOOTSTRAP_ITERATIONS = 1_000
MAX_BOOTSTRAP_ITERATIONS = 10_000
MAX_WORK = 2_000_000
MAX_MISSING_EXAMPLES = 16
MAX_METADATA_ENTRIES = 16
MIN_MEASURED_CASES = 2
SAFE_INTEGER = 2**53 - 1
REQUIRED_CONTEXT_KEYS = frozenset({"baseline_context_id", "candidate_context_id", "evaluator_id"})

Arm = Literal["baseline", "candidate"]
MissingReason = Literal["infra", "verifier", "not_started", "unknown"]
SlotKey = tuple[str, Arm, int]
ARMS: tuple[Arm, Arm] = ("baseline", "candidate")
MISSING_REASONS: tuple[MissingReason, ...] = ("infra", "verifier", "not_started", "unknown")
LIMITATIONS = (
    "SOURCE_CLAIMS_UNVERIFIED",
    "OUTCOME_DEFINITION_UNVERIFIED",
    "ASSUMPTIONS_DECLARED_NOT_VERIFIED",
    "NO_APPROVAL_OR_DEPLOYMENT_AUTHORITY",
)
UNMEASURED_LIMITATIONS = (
    "SAMPLING_REPRESENTATIVENESS_NOT_ESTABLISHED",
    "OPTIONAL_STOPPING_NOT_ACCOUNTED",
)


class PairedBinaryError(ValueError):
    """A bounded code without user-supplied text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _Record:
    key: SlotKey
    outcome: int | None
    missing_reason: MissingReason | None
    source_reason: str | None


@dataclass(frozen=True)
class _Parsed:
    source: dict[str, str]
    outcome_definition: dict[str, str]
    metadata: dict[str, str]
    primary: set[str] | None
    planned: set[SlotKey] | None
    records: dict[SlotKey, _Record]
    settings: dict[str, object]
    assumptions: dict[str, bool]
    input_digest: str
    policy_digest: str


def _invalid(code: str = "INVALID_INPUT") -> PairedBinaryError:
    return PairedBinaryError(code)


def _object(value: object, required: set[str], allowed: set[str]) -> dict[str, object]:
    if type(value) is not dict:
        raise _invalid()
    result = cast(dict[str, object], value)
    if (
        any(type(key) is not str for key in result)
        or not required <= result.keys()
        or not result.keys() <= allowed
    ):
        raise _invalid()
    return result


def _text(value: object, *, maximum: int = 128) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise _invalid()
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise _invalid() from exc
    if not unicodedata.is_normalized("NFC", value):
        raise _invalid()
    return value


def _integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _invalid()
    return value


def _bounded_list(value: object, maximum: int) -> list[object]:
    if type(value) is not list:
        raise _invalid()
    items = cast(list[object], value)
    if len(items) > maximum:
        raise _invalid()
    return items


def _identity(value: object) -> dict[str, str]:
    source = _object(value, {"producer", "version", "run_id"}, {"producer", "version", "run_id"})
    return {key: _text(source[key]) for key in ("producer", "version", "run_id")}


def _outcome_definition(value: object) -> dict[str, str]:
    definition = _object(value, {"name", "positive_means"}, {"name", "positive_means"})
    return {
        "name": _text(definition["name"]),
        "positive_means": _text(definition["positive_means"], maximum=256),
    }


def _metadata(value: object) -> dict[str, str]:
    if type(value) is not dict:
        raise _invalid()
    metadata = cast(dict[object, object], value)
    if len(metadata) > MAX_METADATA_ENTRIES:
        raise _invalid()
    if not REQUIRED_CONTEXT_KEYS <= metadata.keys():
        raise _invalid()
    result: dict[str, str] = {}
    for key, item in metadata.items():
        result[_text(key, maximum=64)] = _text(item, maximum=256)
    return result


def _key(value: object) -> SlotKey:
    slot = _object(
        value,
        {"case_id", "arm", "repeat"},
        {"case_id", "arm", "repeat", "outcome", "missing_reason", "source_reason"},
    )
    case_id = _text(slot["case_id"])
    arm = slot["arm"]
    if arm not in ("baseline", "candidate") or type(arm) is not str:
        raise _invalid()
    repeat = _integer(slot["repeat"], 0, MAX_REPEAT)
    return case_id, cast(Arm, arm), repeat


def _planned(value: object) -> set[SlotKey] | None:
    if value is None:
        return None
    slots: set[SlotKey] = set()
    for item in _bounded_list(value, MAX_SLOTS):
        slot = _object(item, {"case_id", "arm", "repeat"}, {"case_id", "arm", "repeat"})
        key = _key(slot)
        if key in slots:
            raise _invalid("DUPLICATE_SLOT")
        slots.add(key)
    return slots


def _records(value: object) -> dict[SlotKey, _Record]:
    records: dict[SlotKey, _Record] = {}
    for item in _bounded_list(value, MAX_SLOTS):
        row = _object(
            item,
            {"case_id", "arm", "repeat"},
            {"case_id", "arm", "repeat", "outcome", "missing_reason", "source_reason"},
        )
        key = _key(row)
        if key in records:
            raise _invalid("DUPLICATE_SLOT")
        has_outcome = "outcome" in row
        has_missing = "missing_reason" in row
        if has_outcome == has_missing:
            raise _invalid()
        source_reason = _text(row["source_reason"], maximum=128) if "source_reason" in row else None
        if has_outcome:
            outcome = _integer(row["outcome"], 0, 1)
            records[key] = _Record(key, outcome, None, source_reason)
        else:
            reason = row["missing_reason"]
            if type(reason) is not str or reason not in MISSING_REASONS:
                raise _invalid()
            records[key] = _Record(key, None, reason, source_reason)
    return records


def _primary_ids(value: object) -> set[str] | None:
    if value is None:
        return None
    items = _bounded_list(value, MAX_PRIMARY_CASES)
    result: set[str] = set()
    for item in items:
        case_id = _text(item)
        if case_id in result:
            raise _invalid("DUPLICATE_CASE_ID")
        result.add(case_id)
    return result


def _assumptions(value: object) -> dict[str, bool]:
    keys = {"independent_units", "sign_flip_invariance", "fixed_sample"}
    data = _object(value, keys, keys)
    if any(type(data[key]) is not bool for key in keys):
        raise _invalid()
    return {key: cast(bool, data[key]) for key in sorted(keys)}


def _policy(value: object) -> tuple[dict[str, object], dict[str, bool]]:
    keys = {
        "schema",
        "seed",
        "permutation_iterations",
        "bootstrap_iterations",
        "confidence",
        "max_work",
        "assumptions",
    }
    data = _object(value, keys, keys)
    if data["schema"] != "paired_binary_policy.v1":
        raise _invalid("UNSUPPORTED_PROFILE")
    seed = _integer(data["seed"], 0, SAFE_INTEGER)
    permutation_iterations = _integer(
        data["permutation_iterations"], MIN_PERMUTATION_ITERATIONS, MAX_PERMUTATION_ITERATIONS
    )
    bootstrap_iterations = _integer(
        data["bootstrap_iterations"], MIN_BOOTSTRAP_ITERATIONS, MAX_BOOTSTRAP_ITERATIONS
    )
    confidence = data["confidence"]
    if type(confidence) is not float or not 0.0 < confidence < 1.0:
        raise _invalid()
    max_work = _integer(data["max_work"], 1, MAX_WORK)
    assumptions = _assumptions(data["assumptions"])
    return {
        "seed": seed,
        "permutation_iterations": permutation_iterations,
        "bootstrap_iterations": bootstrap_iterations,
        "confidence": confidence,
        "max_work": max_work,
    }, assumptions


def _bucket(
    planned: set[SlotKey] | None, records: dict[SlotKey, _Record], keys: set[SlotKey]
) -> dict[str, object]:
    selected = [record for key, record in records.items() if key in keys]
    reasons = {reason: 0 for reason in MISSING_REASONS}
    for record in selected:
        if record.missing_reason is not None:
            reasons[record.missing_reason] += 1
    return {
        "planned": None if planned is None else len(planned & keys),
        "reported": len(selected),
        "observed_one": sum(record.outcome == 1 for record in selected),
        "observed_zero": sum(record.outcome == 0 for record in selected),
        "missing_by_reason": reasons,
        "unreported_planned": None if planned is None else len((planned & keys) - records.keys()),
    }


def _missing_examples(records: dict[SlotKey, _Record]) -> tuple[list[dict[str, object]], int]:
    missing = sorted(
        (record for record in records.values() if record.missing_reason is not None),
        key=lambda record: record.key,
    )
    examples: list[dict[str, object]] = [
        {
            "case_id": record.key[0],
            "arm": record.key[1],
            "repeat": record.key[2],
            "missing_reason": record.missing_reason,
            "source_reason": record.source_reason,
            "source_claim_unverified": True,
        }
        for record in missing[:MAX_MISSING_EXAMPLES]
    ]
    return examples, max(0, len(missing) - len(examples))


def _observed_reason_examples(
    records: dict[SlotKey, _Record],
) -> tuple[list[dict[str, object]], int]:
    observed = sorted(
        (
            record
            for record in records.values()
            if record.outcome is not None and record.source_reason is not None
        ),
        key=lambda record: record.key,
    )
    examples: list[dict[str, object]] = [
        {
            "case_id": record.key[0],
            "arm": record.key[1],
            "repeat": record.key[2],
            "outcome": record.outcome,
            "source_reason": record.source_reason,
            "source_claim_unverified": True,
        }
        for record in observed[:MAX_MISSING_EXAMPLES]
    ]
    return examples, len(observed) - len(examples)


def _parse(input_doc: object, policy_doc: object) -> _Parsed:
    data = _object(
        input_doc,
        {"schema", "source", "outcome_definition", "metadata", "slot_records"},
        {
            "schema",
            "source",
            "outcome_definition",
            "metadata",
            "primary_case_ids",
            "planned_slots",
            "slot_records",
        },
    )
    if data["schema"] != "paired_binary_input.v1":
        raise _invalid("UNSUPPORTED_PROFILE")
    source = _identity(data["source"])
    outcome_definition = _outcome_definition(data["outcome_definition"])
    metadata = _metadata(data["metadata"])
    primary = _primary_ids(data.get("primary_case_ids"))
    planned = _planned(data.get("planned_slots"))
    records = _records(data["slot_records"])
    if len(records) + (len(planned) if planned is not None else 0) > MAX_SLOTS:
        raise _invalid("INPUT_LIMIT_EXCEEDED")
    if planned is not None and any(key not in planned for key in records):
        raise _invalid("UNPLANNED_SLOT")
    if primary is not None and any(key[0] not in primary for key in (*records, *(planned or ()))):
        raise _invalid("UNKNOWN_CASE_ID")
    settings, assumptions = _policy(policy_doc)
    try:
        input_bytes = canonical_bytes(data)
        policy_bytes = canonical_bytes(policy_doc)
    except (CanonicalizationError, RecursionError) as exc:
        raise _invalid() from exc
    if len(input_bytes) > INPUT_MAX_BYTES:
        raise _invalid("INPUT_LIMIT_EXCEEDED")
    if len(policy_bytes) > 64 * 1024:
        raise _invalid("INPUT_LIMIT_EXCEEDED")
    return _Parsed(
        source,
        outcome_definition,
        metadata,
        primary,
        planned,
        records,
        settings,
        assumptions,
        digest_bytes(input_bytes),
        digest_bytes(policy_bytes),
    )


def _incomplete_primary_cases(
    primary: set[str] | None, planned: set[SlotKey] | None, records: dict[SlotKey, _Record]
) -> list[str]:
    if primary is None or planned is None:
        return []
    return [
        case_id
        for case_id in sorted(primary)
        if any(
            (case_id, arm, 0) not in planned
            or (case_id, arm, 0) not in records
            or records[(case_id, arm, 0)].outcome is None
            for arm in ARMS
        )
    ]


def _not_measured_reason(parsed: _Parsed, incomplete_cases: list[str]) -> str | None:
    if parsed.primary is None:
        return "PRIMARY_CASE_INVENTORY_MISSING"
    if parsed.planned is None:
        return "PLANNED_SLOT_INVENTORY_MISSING"
    if incomplete_cases:
        return "PRIMARY_COHORT_INCOMPLETE"
    if len(parsed.primary) < MIN_MEASURED_CASES:
        return "INSUFFICIENT_PRIMARY_CASES"
    if not all(parsed.assumptions.values()):
        return "ASSUMPTIONS_NOT_DECLARED"
    return None


def _empty_estimate() -> dict[str, object]:
    return {
        "scope": "primary_repeat_zero",
        "n": None,
        "effect": None,
        "effect_method": None,
        "direction": None,
        "interval": None,
        "bootstrap_method": None,
        "p_value": None,
        "permutation_method": None,
        "permutation_completed": 0,
        "bootstrap_completed": 0,
        "reserved_work": None,
    }


def _measure(parsed: _Parsed) -> tuple[dict[str, object], tuple[str, ...]]:
    primary = parsed.primary
    if primary is None:
        raise _invalid()
    settings = parsed.settings
    n = len(primary)
    transforms = 2**n if n <= EXACT_MAX_CASES else cast(int, settings["permutation_iterations"])
    reserved = n * (transforms + cast(int, settings["bootstrap_iterations"]))
    if reserved > cast(int, settings["max_work"]):
        raise _invalid("COMPUTATION_LIMIT_EXCEEDED")
    baseline = [
        CaseScore(case_id, cast(int, parsed.records[(case_id, "baseline", 0)].outcome))
        for case_id in sorted(primary)
    ]
    candidate = [
        CaseScore(case_id, cast(int, parsed.records[(case_id, "candidate", 0)].outcome))
        for case_id in sorted(primary)
    ]
    try:
        evidence = measure_paired(
            baseline,
            candidate,
            resampling=ResamplingPolicy(
                cast(int, settings["seed"]),
                cast(int, settings["bootstrap_iterations"]),
                cast(float, settings["confidence"]),
            ),
            permutation_iterations=cast(int, settings["permutation_iterations"]),
            max_work=cast(int, settings["max_work"]),
        )
    except PairedError as exc:
        if exc.code == "COMPUTATION_LIMIT":
            raise _invalid("COMPUTATION_LIMIT_EXCEEDED") from exc
        raise _invalid() from exc
    return {
        "scope": "primary_repeat_zero",
        "n": evidence.n,
        "effect": evidence.effect,
        "effect_method": evidence.effect_method,
        "direction": evidence.direction,
        "interval": list(evidence.interval) if evidence.interval is not None else None,
        "bootstrap_method": evidence.bootstrap_method,
        "p_value": evidence.permutation.p_value,
        "permutation_method": evidence.permutation.method,
        "permutation_completed": evidence.permutation.completed_iterations,
        "bootstrap_completed": evidence.bootstrap_completed,
        "reserved_work": evidence.reserved_work,
    }, evidence.limitations


def _accounting(parsed: _Parsed, incomplete_cases: list[str]) -> dict[str, object]:
    primary_keys: set[SlotKey] = (
        {(case_id, arm, 0) for case_id in parsed.primary for arm in ARMS}
        if parsed.primary is not None
        else set()
    )
    all_keys = set(parsed.records) | (parsed.planned or set())
    repeat_keys = {key for key in all_keys if key[2] > 0}
    unscoped_keys = all_keys - primary_keys - repeat_keys
    examples, omitted_examples = _missing_examples(parsed.records)
    observed_examples, omitted_observed = _observed_reason_examples(parsed.records)
    accounting: dict[str, object] = {
        "primary_case_count": None if parsed.primary is None else len(parsed.primary),
        "planned_slot_count": None if parsed.planned is None else len(parsed.planned),
        "slot_record_count": len(parsed.records),
        "primary_repeat_zero": _bucket(parsed.planned, parsed.records, primary_keys),
        "additional_repeats": _bucket(parsed.planned, parsed.records, repeat_keys),
        "unscoped_repeat_zero": _bucket(parsed.planned, parsed.records, unscoped_keys),
        "incomplete_primary_case_ids": incomplete_cases,
        "missing_examples": examples,
        "missing_examples_omitted": omitted_examples,
    }
    if observed_examples:
        accounting["observed_reason_examples"] = observed_examples
        accounting["observed_reason_examples_omitted"] = omitted_observed
    return accounting


def evaluate_paired_binary(input_doc: object, policy_doc: object) -> dict[str, object]:
    """Return a bounded descriptive report; only complete primary pairs are estimated."""
    parsed = _parse(input_doc, policy_doc)
    incomplete_cases = _incomplete_primary_cases(parsed.primary, parsed.planned, parsed.records)
    reason = _not_measured_reason(parsed, incomplete_cases)
    estimate, engine_limitations = (
        (_empty_estimate(), ()) if reason is not None else _measure(parsed)
    )
    accounting = _accounting(parsed, incomplete_cases)
    limitations = set((*LIMITATIONS, *engine_limitations))
    if reason is not None:
        limitations.update(UNMEASURED_LIMITATIONS)
    additional = cast(dict[str, object], accounting["additional_repeats"])
    if additional["planned"] or additional["reported"]:
        limitations.add("ADDITIONAL_REPEATS_NOT_INCLUDED_IN_ESTIMATE")
    if accounting["missing_examples_omitted"] or accounting.get("observed_reason_examples_omitted"):
        limitations.add("SOURCE_REASON_EXAMPLES_TRUNCATED")
    return {
        "schema": "paired_binary_evidence.v1",
        "status": "measured" if reason is None else "not_measured",
        "reason": reason,
        "input_digest": parsed.input_digest,
        "policy_digest": parsed.policy_digest,
        "source_claims": {
            "source": parsed.source,
            "outcome_definition": parsed.outcome_definition,
            "metadata": parsed.metadata,
            "verified": False,
        },
        "assumptions": {"declared": parsed.assumptions, "verified": False},
        "settings": parsed.settings,
        "accounting": accounting,
        "estimate": estimate,
        "limitations": sorted(limitations),
    }

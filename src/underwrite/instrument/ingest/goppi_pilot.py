"""Exact bounded normalizer for one private goppi grounded-pilot exchange profile."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import unicodedata
from pathlib import Path
from typing import Final, cast

from underwrite.instrument.ingest.transport import (
    DecodeError,
    TransportError,
    decode_json,
    read_descriptor,
)

MANIFEST_MAX_BYTES: Final = 1024 * 1024
EXECUTION_MAX_BYTES: Final = 1024 * 1024
SCHEMA_MAX_BYTES: Final = 1024 * 1024
ROWS_MAX_BYTES: Final = 4 * 1024 * 1024
ROW_MAX_BYTES: Final = 2 * 1024 * 1024
MAX_ROWS: Final = 128
MAX_GROUPS: Final = 32
MAX_SOURCE_REFS: Final = 128
MAX_ARTIFACT_REFS: Final = 16
MAX_DEPTH: Final = 32

_FORMAT_SCOPE = "goppi_repo_grounded_pilot_exchange_not_public_product_contract"
_MANIFEST_SCHEMA = "goppi.grounded_pilot_exchange_manifest.v1"
_EXECUTION_SCHEMA = "goppi.grounded_pilot_exchange_execution.v1"
_ROW_SCHEMA = "goppi.grounded_pilot_exchange_row.v1"
_FILES = ("execution.json", "rows.jsonl", "schema.json")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_PILOT = re.compile(r"pilot-[0-9]{3}")
_SAFE_LEAF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z")
_CLASSIFICATIONS = frozenset(
    {"observed_true", "observed_false", "endpoint_failure", "infra_missing", "unknown"}
)
_TERMINATIONS = frozenset(
    {
        "completed",
        "timeout",
        "aborted",
        "output_limit",
        "stdin_error",
        "start_failed",
        "signaled",
        "outcome_unknown_after_start",
    }
)


class GoppiPilotError(ValueError):
    """A finite adapter diagnostic without producer-controlled text."""

    def __init__(self, code: str, reason: str, location: str = "") -> None:
        self.code = code
        self.reason = reason
        self.location = location
        super().__init__(reason)


def _fail(code: str, reason: str, location: str = "") -> GoppiPilotError:
    return GoppiPilotError(code, reason, location)


def _object(value: object, keys: set[str], location: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_EXCHANGE", location)
    raw = cast(dict[object, object], value)
    if any(type(key) is not str for key in raw) or set(raw) != keys:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_EXCHANGE", location)
    return cast(dict[str, object], raw)


def _list(value: object, maximum: int, location: str, *, minimum: int = 0) -> list[object]:
    if type(value) is not list:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_EXCHANGE", location)
    result = cast(list[object], value)
    if not minimum <= len(result) <= maximum:
        raise _fail("INPUT_LIMIT_EXCEEDED", "GOPPI_EXCHANGE_LIMIT_EXCEEDED", location)
    return result


def _text(value: object, maximum: int, location: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_EXCHANGE", location)
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_EXCHANGE", location) from exc
    if not unicodedata.is_normalized("NFC", value):
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_EXCHANGE", location)
    return value


def _required_text(value: object, maximum: int, location: str) -> str:
    return cast(str, _text(value, maximum, location))


def _integer(value: object, maximum: int, location: str, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_EXCHANGE", location)
    return value


def _bounded_integer(value: object, maximum: int, location: str, *, minimum: int = 0) -> int:
    if type(value) is int and value > maximum:
        raise _fail("INPUT_LIMIT_EXCEEDED", "GOPPI_EXCHANGE_LIMIT_EXCEEDED", location)
    return _integer(value, maximum, location, minimum=minimum)


def _number(value: object, location: str, *, nullable: bool = False) -> int | float | None:
    if nullable and value is None:
        return None
    if type(value) not in (int, float):
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_EXCHANGE", location)
    number = cast(int | float, value)
    if number < 0:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_EXCHANGE", location)
    return number


def _digest(value: object, location: str, *, nullable: bool = False) -> str | None:
    text = _text(value, 71, location, nullable=nullable)
    if text is not None and _DIGEST.fullmatch(text) is None:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_EXCHANGE", location)
    return text


def _required_digest(value: object, location: str) -> str:
    return cast(str, _digest(value, location))


def _literal(value: object, expected: object, location: str) -> None:
    if type(value) is not type(expected) or value != expected:
        raise _fail("UNSUPPORTED_PROFILE", "UNSUPPORTED_GOPPI_EXCHANGE_PROFILE", location)


def _raw_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _open_directory(path: Path) -> int:
    flags = [
        getattr(os, name, None) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK", "O_NOCTTY")
    ]
    if any(flag is None for flag in flags):
        raise _fail("UNSUPPORTED_PROFILE", "GOPPI_EXCHANGE_PLATFORM_UNSUPPORTED")
    try:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise _fail("SOURCE_READ_FAILED", "GOPPI_EXCHANGE_DIRECTORY_REQUIRED")
        directory, nofollow, nonblock, noctty = cast(list[int], flags)
        return os.open(path, os.O_RDONLY | directory | nofollow | nonblock | noctty)
    except GoppiPilotError:
        raise
    except OSError:
        raise _fail("SOURCE_READ_FAILED", "GOPPI_EXCHANGE_READ_FAILED") from None


def _read_file(directory: int, name: str, maximum: int, location: str) -> bytes:
    flags = [getattr(os, flag, None) for flag in ("O_NOFOLLOW", "O_NONBLOCK", "O_NOCTTY")]
    if any(flag is None for flag in flags):
        raise _fail("UNSUPPORTED_PROFILE", "GOPPI_EXCHANGE_PLATFORM_UNSUPPORTED")
    nofollow, nonblock, noctty = cast(list[int], flags)
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | nofollow | nonblock | noctty,
            dir_fd=directory,
        )
        try:
            data = read_descriptor(descriptor, maximum)
        finally:
            os.close(descriptor)
    except (OSError, TransportError):
        raise _fail("SOURCE_READ_FAILED", "GOPPI_EXCHANGE_READ_FAILED", location) from None
    if len(data) > maximum:
        raise _fail("INPUT_LIMIT_EXCEEDED", "GOPPI_EXCHANGE_LIMIT_EXCEEDED", location)
    return data


def _decode(data: bytes, location: str) -> object:
    try:
        return decode_json(data, MAX_DEPTH)
    except DecodeError:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_EXCHANGE_JSON", location) from None


def _manifest(
    value: object,
) -> tuple[dict[str, dict[str, object]], dict[str, str], str]:
    location = "/goppi-grounded-pilot-exchange/manifest.json"
    keys = {
        "schema",
        "format_scope",
        "status",
        "generated_at",
        "private_output",
        "manifest_written_last",
        "files",
        "source_inputs",
    }
    data = _object(value, keys, location)
    _literal(data["schema"], _MANIFEST_SCHEMA, location)
    _literal(data["format_scope"], _FORMAT_SCOPE, location)
    for key, expected in (
        ("status", "complete"),
        ("private_output", True),
        ("manifest_written_last", True),
    ):
        _literal(data[key], expected, location)
    generated = _required_text(data["generated_at"], 64, location)
    if _UTC.fullmatch(generated) is None:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_EXCHANGE", location)
    claims: dict[str, dict[str, object]] = {}
    for item in _list(data["files"], 3, location, minimum=3):
        entry = _object(item, {"path", "bytes", "sha256"}, location)
        name = _required_text(entry["path"], 64, location)
        if name not in _FILES or name in claims:
            raise _fail("INVALID_INPUT", "INVALID_GOPPI_FILE_SET", location)
        claims[name] = {
            "bytes": _integer(entry["bytes"], ROWS_MAX_BYTES, location),
            "sha256": _required_digest(entry["sha256"], location),
        }
    if set(claims) != set(_FILES):
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_FILE_SET", location)
    sources: dict[str, str] = {}
    for item in _list(data["source_inputs"], MAX_SOURCE_REFS, location, minimum=1):
        entry = _object(
            item,
            {"source_ref", "manifest_sha256", "project_root_sha256", "artifact_refs"},
            location,
        )
        source_ref = _required_text(entry["source_ref"], 9, location)
        if _PILOT.fullmatch(source_ref) is None or source_ref in sources:
            raise _fail("INVALID_INPUT", "INVALID_GOPPI_SOURCE_REFS", location)
        sources[source_ref] = _required_digest(entry["manifest_sha256"], location)
        _required_digest(entry["project_root_sha256"], location)
        artifacts: set[str] = set()
        for raw_ref in _list(entry["artifact_refs"], MAX_ARTIFACT_REFS, location):
            ref = _required_text(raw_ref, 138, location)
            prefix, separator, leaf = ref.partition("/")
            if (
                separator != "/"
                or prefix != source_ref
                or _SAFE_LEAF.fullmatch(leaf) is None
                or ref in artifacts
            ):
                raise _fail("INVALID_INPUT", "INVALID_GOPPI_SOURCE_REFS", location)
            artifacts.add(ref)
    return claims, sources, generated


def _group(value: object, location: str) -> dict[str, object]:
    keys = {
        "group_id",
        "evalset_digest",
        "harness_version",
        "grouping_axes",
        "row_count",
        "profile_identity_digests",
        "snapshot_digests",
        "distinct_profile_identity_count",
        "distinct_snapshot_count",
        "conditions_compatibility_established",
        "pooled_accuracy_or_effect_claimed",
        "automatic_comparability_claimed",
        "cross_version_pairing_performed",
    }
    data = _object(value, keys, location)
    axes = _list(data["grouping_axes"], 2, location, minimum=2)
    if axes != ["evalset_digest", "harness_version"]:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_GROUP", location)
    profiles = [
        _required_digest(item, location)
        for item in _list(data["profile_identity_digests"], MAX_ROWS, location, minimum=1)
    ]
    snapshots = [
        _required_digest(item, location)
        for item in _list(data["snapshot_digests"], MAX_ROWS, location, minimum=1)
    ]
    if len(profiles) != len(set(profiles)) or len(snapshots) != len(set(snapshots)):
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_GROUP", location)
    for key in (
        "conditions_compatibility_established",
        "pooled_accuracy_or_effect_claimed",
        "automatic_comparability_claimed",
        "cross_version_pairing_performed",
    ):
        _literal(data[key], False, location)
    if _integer(data["distinct_profile_identity_count"], MAX_ROWS, location, minimum=1) != len(
        profiles
    ):
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_GROUP", location)
    if _integer(data["distinct_snapshot_count"], MAX_ROWS, location, minimum=1) != len(snapshots):
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_GROUP", location)
    return {
        "group_id": _required_digest(data["group_id"], location),
        "evalset_digest": _required_digest(data["evalset_digest"], location),
        "harness_version": _required_digest(data["harness_version"], location),
        "row_count": _bounded_integer(data["row_count"], MAX_ROWS, location, minimum=1),
        "profiles": profiles,
        "snapshots": snapshots,
        "conditions_compatibility_established": False,
        "pooled_accuracy_or_effect_claimed": False,
        "automatic_comparability_claimed": False,
        "cross_version_pairing_performed": False,
    }


def _execution(
    value: object,
) -> tuple[dict[str, dict[str, object]], bool, dict[str, object]]:
    location = "/goppi-grounded-pilot-exchange/execution.json"
    keys = {
        "schema",
        "format_scope",
        "status",
        "generated_at",
        "include_answers",
        "rows",
        "groups",
        "accounting",
        "claims",
        "exclusions",
    }
    data = _object(value, keys, location)
    _literal(data["schema"], _EXECUTION_SCHEMA, location)
    _literal(data["format_scope"], _FORMAT_SCOPE, location)
    _literal(data["status"], "complete", location)
    generated = _required_text(data["generated_at"], 64, location)
    if _UTC.fullmatch(generated) is None or type(data["include_answers"]) is not bool:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_EXECUTION", location)
    include_answers = data["include_answers"]
    rows = _bounded_integer(data["rows"], MAX_ROWS, location, minimum=1)
    groups: dict[str, dict[str, object]] = {}
    for index, item in enumerate(_list(data["groups"], MAX_GROUPS, location, minimum=1)):
        group = _group(item, f"{location}/groups/{index}")
        identifier = cast(str, group["group_id"])
        if identifier in groups:
            raise _fail("INVALID_INPUT", "INVALID_GOPPI_GROUP", location)
        groups[identifier] = group
    accounting_keys = {
        "unique_attempt_ids",
        "durable_attempts",
        "authenticated_native_receipts",
        "rows_without_native_receipt",
        "observed_duration_count",
        "total_observed_duration_ms",
        "duration_scope",
        "observed_cost_count",
        "total_pi_reported_cost_usd",
        "missing_values_are_zero",
    }
    accounting = _object(data["accounting"], accounting_keys, location)
    counts = {
        key: _integer(accounting[key], MAX_ROWS, location)
        for key in (
            "unique_attempt_ids",
            "durable_attempts",
            "authenticated_native_receipts",
            "rows_without_native_receipt",
            "observed_duration_count",
            "observed_cost_count",
        )
    }
    total_duration = _number(accounting["total_observed_duration_ms"], location, nullable=True)
    total_cost = _number(accounting["total_pi_reported_cost_usd"], location, nullable=True)
    _literal(
        accounting["duration_scope"], "sum_of_observed_attempt_durations_not_wall_clock", location
    )
    _literal(accounting["missing_values_are_zero"], False, location)
    claim_values = {
        "executed_native_receipts_authenticated": True,
        "producer_scores_authenticated_by_native_receipts": False,
        "producer_scores_independently_regraded": False,
        "model_contacted_by_exporter": False,
        "network_contacted_by_exporter": False,
        "model_credentials_loaded_by_exporter": False,
        "source_projects_modified_by_exporter": False,
        "groups_are_automatically_comparable": False,
        "pooled_accuracy_or_effect_claimed": False,
    }
    claims = _object(data["claims"], set(claim_values), location)
    for key, expected in claim_values.items():
        _literal(claims[key], expected, location)
    exclusions = [
        _required_text(item, 128, location)
        for item in _list(data["exclusions"], 32, location, minimum=1)
    ]
    if len(exclusions) != len(set(exclusions)):
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_EXECUTION", location)
    if not include_answers and "final_assistant_answer" not in exclusions:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_EXECUTION", location)
    return (
        groups,
        include_answers,
        {
            **counts,
            "rows": rows,
            "generated_at": generated,
            "total_observed_duration_ms": total_duration,
            "total_pi_reported_cost_usd": total_cost,
        },
    )


def _nullable_integer(value: object, location: str) -> int | None:
    return None if value is None else _integer(value, 2**53 - 1, location)


def _answer(value: object, include_answers: bool, location: str) -> str | None:
    if value is None:
        return None
    if not include_answers:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_ANSWER", location)
    answer = _object(value, {"final_assistant_answer", "sha256", "source"}, location)
    text = answer["final_assistant_answer"]
    if type(text) is not str or len(text) > 1024 * 1024:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_ANSWER", location)
    try:
        raw = text.encode("utf-8")
    except UnicodeError as exc:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_ANSWER", location) from exc
    digest = _required_digest(answer["sha256"], location)
    _literal(answer["source"], "authenticated_stdout_terminal_answer", location)
    if _raw_digest(raw) != digest:
        raise _fail("INVALID_INPUT", "GOPPI_ANSWER_DIGEST_MISMATCH", location)
    return digest


def _harness(value: object, location: str) -> dict[str, str]:
    harness = _object(
        value,
        {"harness_version", "snapshot_digest", "profile_identity_digest"},
        location,
    )
    return {
        key: _required_digest(harness[key], location)
        for key in ("harness_version", "snapshot_digest", "profile_identity_digest")
    }


def _case(value: object, location: str) -> dict[str, str]:
    case = _object(value, {"case_id", "family", "content_digest", "split"}, location)
    case_id = _required_text(case["case_id"], 128, location)
    if _SAFE_LEAF.fullmatch(case_id) is None:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_ROW", location)
    _literal(case["split"], "explore", location)
    return {
        "case_id": case_id,
        "family": _required_text(case["family"], 128, location),
        "content_digest": _required_digest(case["content_digest"], location),
        "split": "explore",
    }


def _attempt(value: object, location: str) -> dict[str, object]:
    attempt_keys = {
        "attempt_id",
        "request_digest",
        "receipt_digest",
        "termination_reason",
        "cleanup",
        "authenticated_local_receipt",
        "duration_ms",
    }
    attempt = _object(value, attempt_keys, location)
    attempt_id = _required_text(attempt["attempt_id"], 128, location)
    if _SAFE_LEAF.fullmatch(attempt_id) is None:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_ROW", location)
    termination = _text(attempt["termination_reason"], 64, location, nullable=True)
    if termination is not None and termination not in _TERMINATIONS:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_ROW", location)
    authenticated = attempt["authenticated_local_receipt"]
    if attempt["cleanup"] not in (None, "complete") or type(authenticated) is not bool:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_ROW", location)
    duration = _number(attempt["duration_ms"], location, nullable=True)
    return {
        "attempt_id": attempt_id,
        "request_digest": _digest(attempt["request_digest"], location, nullable=True),
        "receipt_digest": _digest(attempt["receipt_digest"], location, nullable=True),
        "authenticated": authenticated,
        "duration": duration,
    }


def _outcome(value: object, location: str) -> tuple[str, str | None]:
    outcome = _object(
        value,
        {"classification", "reason", "producer_recorded", "independently_regraded"},
        location,
    )
    classification = _required_text(outcome["classification"], 64, location)
    if classification not in _CLASSIFICATIONS:
        raise _fail("INVALID_INPUT", "UNSUPPORTED_GOPPI_CLASSIFICATION", location)
    _literal(outcome["producer_recorded"], True, location)
    _literal(outcome["independently_regraded"], False, location)
    return classification, _text(outcome["reason"], 256, location, nullable=True)


def _usage(value: object, location: str) -> int | float | None:
    usage_keys = {
        "status",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "pi_reported_cost_usd",
    }
    usage = _object(value, usage_keys, location)
    if usage["status"] not in (
        "observed_within_authorized_limit",
        "reported_cost_limit_exceeded",
        "unknown",
    ):
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_ROW", location)
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    ):
        _nullable_integer(usage[key], location)
    return _number(usage["pi_reported_cost_usd"], location, nullable=True)


def _record_state(classification: str, record: dict[str, object]) -> None:
    if classification == "observed_true":
        record["outcome"] = 1
    elif classification == "observed_false":
        record["outcome"] = 0
    elif classification == "infra_missing":
        record["missing_reason"] = "infra"
    elif classification == "unknown":
        record["missing_reason"] = "unknown"
    else:
        record["non_binary_classification"] = "endpoint_failure"


def _row(value: object, include_answers: bool, location: str) -> dict[str, object]:
    keys = {
        "schema",
        "group_id",
        "source_ref",
        "source_manifest_sha256",
        "study_id",
        "draft_digest",
        "evalset_digest",
        "evalset_classification",
        "benchmark_claims",
        "assignment_digest",
        "harness",
        "case",
        "arm",
        "repeat_index",
        "attempt",
        "outcome",
        "usage",
        "answer",
    }
    data = _object(value, keys, location)
    for key, expected in (
        ("schema", _ROW_SCHEMA),
        ("study_id", "grounded-extraction"),
        ("benchmark_claims", "prohibited"),
        ("arm", "baseline"),
        ("repeat_index", 0),
    ):
        _literal(data[key], expected, location)
    _required_digest(data["draft_digest"], location)
    _required_digest(data["assignment_digest"], location)
    evalset_classification = data["evalset_classification"]
    if evalset_classification not in ("synthetic_fixture", "curated_development"):
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_ROW", location)
    harness = _harness(data["harness"], location)
    case = _case(data["case"], location)
    attempt = _attempt(data["attempt"], location)
    classification, reason = _outcome(data["outcome"], location)
    cost = _usage(data["usage"], location)
    answer_digest = _answer(data["answer"], include_answers, location)
    source_ref = _required_text(data["source_ref"], 9, location)
    if _PILOT.fullmatch(source_ref) is None:
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_SOURCE_REFS", location)
    source_manifest = _required_digest(data["source_manifest_sha256"], location)
    attempt_id = cast(str, attempt["attempt_id"])
    answer_ref = (
        "authenticated_stdout_terminal_answer"
        if answer_digest
        else "not_included"
        if not include_answers
        else "unavailable"
    )
    record: dict[str, object] = {
        "unit_id": attempt_id,
        "case_id": case["case_id"],
        "arm": "baseline",
        "repeat_index": 0,
        "source_reason": reason,
        "source_refs": [
            {"kind": "case_family", "ref": case["family"], "digest": None},
            {"kind": "split", "ref": "explore", "digest": None},
            {"kind": "material", "ref": case["case_id"], "digest": case["content_digest"]},
            {"kind": "profile", "ref": source_ref, "digest": harness["profile_identity_digest"]},
            {"kind": "snapshot", "ref": source_ref, "digest": harness["snapshot_digest"]},
            {"kind": "request", "ref": attempt_id, "digest": attempt["request_digest"]},
            {"kind": "receipt", "ref": attempt_id, "digest": attempt["receipt_digest"]},
            {
                "kind": "answer",
                "ref": answer_ref,
                "digest": answer_digest,
            },
            {"kind": "source_manifest", "ref": source_ref, "digest": source_manifest},
        ],
    }
    _record_state(classification, record)
    return {
        "record": record,
        "group_id": _required_digest(data["group_id"], location),
        "source_ref": source_ref,
        "source_manifest": source_manifest,
        "evalset_digest": _required_digest(data["evalset_digest"], location),
        "evalset_classification": cast(str, evalset_classification),
        "harness_version": harness["harness_version"],
        "profile_digest": harness["profile_identity_digest"],
        "snapshot_digest": harness["snapshot_digest"],
        "authenticated_receipt": attempt["authenticated"],
        "duration": attempt["duration"],
        "cost": cost,
    }


def _rows(data: bytes, include_answers: bool) -> list[dict[str, object]]:
    location = "/goppi-grounded-pilot-exchange/rows.jsonl"
    if not data or not data.endswith(b"\n"):
        raise _fail("INVALID_INPUT", "INVALID_GOPPI_ROWS", location)
    lines = data.splitlines()
    if not 1 <= len(lines) <= MAX_ROWS:
        raise _fail("INPUT_LIMIT_EXCEEDED", "GOPPI_EXCHANGE_LIMIT_EXCEEDED", location)
    result: list[dict[str, object]] = []
    for index, line in enumerate(lines, start=1):
        if not line or len(line) > ROW_MAX_BYTES:
            raise _fail("INPUT_LIMIT_EXCEEDED", "GOPPI_EXCHANGE_LIMIT_EXCEEDED", location)
        row_location = f"{location}/{index}"
        result.append(_row(_decode(line, row_location), include_answers, row_location))
    return result


def _summary_integer(summary: dict[str, object], key: str) -> int:
    return cast(int, summary[key])


def _reconcile_counts(rows: list[dict[str, object]], summary: dict[str, object]) -> None:
    location = "/goppi-grounded-pilot-exchange"
    if _summary_integer(summary, "rows") != len(rows) or _summary_integer(
        summary, "unique_attempt_ids"
    ) != len(rows):
        raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_COUNT_MISMATCH", location)
    durable = sum(cast(bool, row["authenticated_receipt"]) for row in rows)
    if _summary_integer(summary, "durable_attempts") != durable:
        raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_COUNT_MISMATCH", location)
    if _summary_integer(summary, "authenticated_native_receipts") != durable:
        raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_COUNT_MISMATCH", location)
    if _summary_integer(summary, "rows_without_native_receipt") != len(rows) - durable:
        raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_COUNT_MISMATCH", location)
    durations = [cast(int | float, row["duration"]) for row in rows if row["duration"] is not None]
    if _summary_integer(summary, "observed_duration_count") != len(durations):
        raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_COUNT_MISMATCH", location)
    costs = [cast(int | float, row["cost"]) for row in rows if row["cost"] is not None]
    if _summary_integer(summary, "observed_cost_count") != len(costs):
        raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_COUNT_MISMATCH", location)
    expected_duration = None if not durations else sum(durations)
    expected_cost = None if not costs else sum(costs)
    if summary["total_observed_duration_ms"] != expected_duration:
        raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_COUNT_MISMATCH", location)
    if summary["total_pi_reported_cost_usd"] != expected_cost:
        raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_COUNT_MISMATCH", location)


def _reconcile_sources(rows: list[dict[str, object]], sources: dict[str, str]) -> None:
    location = "/goppi-grounded-pilot-exchange"
    records = [cast(dict[str, object], row["record"]) for row in rows]
    attempts = [cast(str, record["unit_id"]) for record in records]
    refs = [cast(str, row["source_ref"]) for row in rows]
    if len(attempts) != len(set(attempts)) or len(refs) != len(set(refs)):
        raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_SOURCE_MISMATCH", location)
    if set(refs) != set(sources):
        raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_SOURCE_MISMATCH", location)
    for row in rows:
        if sources.get(cast(str, row["source_ref"])) != row["source_manifest"]:
            raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_SOURCE_MISMATCH", location)


def _reconcile_groups(groups: dict[str, dict[str, object]], rows: list[dict[str, object]]) -> None:
    location = "/goppi-grounded-pilot-exchange"
    by_group: dict[str, list[dict[str, object]]] = {identifier: [] for identifier in groups}
    for row in rows:
        identifier = cast(str, row["group_id"])
        if identifier not in by_group:
            raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_GROUP_MISMATCH", location)
        by_group[identifier].append(row)
    for identifier, header in groups.items():
        selected = by_group[identifier]
        if len(selected) != header["row_count"]:
            raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_GROUP_MISMATCH", location)
        if any(
            row["evalset_digest"] != header["evalset_digest"]
            or row["harness_version"] != header["harness_version"]
            for row in selected
        ):
            raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_GROUP_MISMATCH", location)
        if {row["profile_digest"] for row in selected} != set(cast(list[str], header["profiles"])):
            raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_GROUP_MISMATCH", location)
        if {row["snapshot_digest"] for row in selected} != set(
            cast(list[str], header["snapshots"])
        ):
            raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_GROUP_MISMATCH", location)


def _reconcile(
    groups: dict[str, dict[str, object]],
    rows: list[dict[str, object]],
    sources: dict[str, str],
    summary: dict[str, object],
) -> None:
    _reconcile_counts(rows, summary)
    _reconcile_sources(rows, sources)
    _reconcile_groups(groups, rows)


def _inventory(
    manifest_raw: bytes,
    file_data: dict[str, bytes],
    groups: dict[str, dict[str, object]],
    rows: list[dict[str, object]],
) -> dict[str, object]:
    files = [
        {"name": "manifest.json", "sha256": _raw_digest(manifest_raw), "bytes": len(manifest_raw)}
    ]
    files.extend(
        {"name": name, "sha256": _raw_digest(file_data[name]), "bytes": len(file_data[name])}
        for name in _FILES
    )
    rendered_groups: list[dict[str, object]] = []
    for identifier in sorted(groups):
        header = groups[identifier]
        selected = [row for row in rows if row["group_id"] == identifier]
        classifications = {cast(str, row["evalset_classification"]) for row in selected}
        if len(classifications) != 1:
            raise _fail("INVALID_INPUT", "GOPPI_EXCHANGE_GROUP_MISMATCH")
        rendered_groups.append(
            {
                "group_id": identifier,
                "context": {
                    "study_id": "grounded-extraction",
                    "evalset_digest": header["evalset_digest"],
                    "evalset_classification": classifications.pop(),
                    "harness_version": header["harness_version"],
                    "conditions_compatibility_established": False,
                    "pooled_accuracy_or_effect_claimed": False,
                    "automatic_comparability_claimed": False,
                    "cross_version_pairing_performed": False,
                },
                "planned_units": None,
                "records": [row["record"] for row in selected],
            }
        )
    return {
        "schema": "binary_run_inventory.v1",
        "source": {
            "producer": "goppi",
            "format": "goppi.grounded_pilot_exchange",
            "format_version": "v1",
            "artifact_digest": _raw_digest(manifest_raw),
            "files": sorted(files, key=lambda item: cast(str, item["name"])),
        },
        "outcome_definition": {
            "name": "goppi_grounded_pilot_binary_outcome",
            "positive_means": "producer classification observed_true",
        },
        "unit": "producer_execution_unit",
        "groups": rendered_groups,
    }


def load_inventory(path: Path) -> dict[str, object]:
    """Normalize one exact exchange without reading locators or executing its schema."""
    directory = _open_directory(path)
    try:
        manifest_raw = _read_file(
            directory,
            "manifest.json",
            MANIFEST_MAX_BYTES,
            "/goppi-grounded-pilot-exchange/manifest.json",
        )
        claims, sources, manifest_generated_at = _manifest(
            _decode(manifest_raw, "/goppi-grounded-pilot-exchange/manifest.json")
        )
        file_data: dict[str, bytes] = {}
        limits = {
            "execution.json": EXECUTION_MAX_BYTES,
            "rows.jsonl": ROWS_MAX_BYTES,
            "schema.json": SCHEMA_MAX_BYTES,
        }
        for name in _FILES:
            raw = _read_file(
                directory, name, limits[name], f"/goppi-grounded-pilot-exchange/{name}"
            )
            claim = claims[name]
            if len(raw) != claim["bytes"] or _raw_digest(raw) != claim["sha256"]:
                raise _fail(
                    "INVALID_INPUT",
                    "GOPPI_EXCHANGE_FILE_MISMATCH",
                    f"/goppi-grounded-pilot-exchange/{name}",
                )
            file_data[name] = raw
    finally:
        os.close(directory)
    groups, include_answers, summary = _execution(
        _decode(file_data["execution.json"], "/goppi-grounded-pilot-exchange/execution.json")
    )
    if summary["generated_at"] != manifest_generated_at:
        raise _fail(
            "INVALID_INPUT",
            "GOPPI_EXCHANGE_SUMMARY_MISMATCH",
            "/goppi-grounded-pilot-exchange",
        )
    rows = _rows(file_data["rows.jsonl"], include_answers)
    _reconcile(groups, rows, sources, summary)
    return _inventory(manifest_raw, file_data, groups, rows)

"""Descriptive accounting for pinned Langfuse CLI list captures.

Only selected local rows and their source-declared pagination are assessed. The
two independently selected files do not establish a common export snapshot.
"""

from __future__ import annotations

import unicodedata
from types import MappingProxyType
from typing import Any, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes

from underwrite.instrument.evidence.langfuse_accounting import (
    MAX_OBSERVATIONS,
    MAX_SCORES,
    MAX_STRING_VALUE_LENGTH,
    LangfuseAccountingError,
    langfuse_identifier,
    langfuse_label,
)

CLI_PROFILE = "cli-1.2.4"
RULE_PROFILE = "langfuse.cli-accounting.v1"
ACCOUNTING_SCHEMA = "langfuse_cli_accounting.v1"
ROW_INDEX_SCHEMA = "langfuse_cli_row_index.v1"
SOURCE_VERSION = "1.2.4-api-4.35.0"
HTTP_OK = 200
# Pinned CLI 1.2.4 caps collection at 100 requests; API 4.35.0 limits each
# observations/scores request to 1000/100 rows respectively.
MAX_CLI_PAGES = 100
PAGE_LIMITS = MappingProxyType({"observations": 1000, "scores": 100})
SOURCE_FORMATS = MappingProxyType(
    {
        "observations": "langfuse.cli.observations-v2",
        "scores": "langfuse.cli.scores-v3",
    }
)
VALUE_TYPES = MappingProxyType(
    {
        "NUMERIC": "number",
        "BOOLEAN": "boolean",
        "CATEGORICAL": "string",
        "TEXT": "string",
        "CORRECTION": "string",
    }
)
SUBJECT_KINDS = ("observation", "trace", "session", "experiment")
SUBJECT_AVAILABILITY = ("present", "null", "absent")
ASSOCIATIONS = (
    "observation_local",
    "unresolved_observation_reference",
    "observation_context_unknown",
    "other_subject",
    "subject_unknown",
)
CAPTURE_KINDS = ("json_envelope", "output_body")
PAGINATION_MODES = ("one_page", "cli_all")
CURSOR_STATES = ("present", "null", "absent", "not_applicable")
SCOPE = MappingProxyType(
    {
        "population": "supplied_rows",
        "selected_file_binding": "independent_selected_files",
        "export_completeness": "unknown",
        "planned_evaluations": "unknown",
        "claims_verified": False,
        "score_values": "source_claim",
    }
)
ROW_SCOPE = MappingProxyType(
    {
        "population": "supplied_rows",
        "selected_file_binding": "independent_selected_files",
        "export_completeness": "unknown",
        "planned_evaluations": "unknown",
        "values": "withheld",
    }
)
LIMITATIONS = (
    "SOURCE_CLAIMS_UNVERIFIED",
    "SELECTED_FILES_NOT_ATOMICALLY_BOUND",
    "PAGINATION_NOT_EXPORT_COMPLETENESS",
    "REQUEST_FILTERS_NOT_VERIFIED",
    "PLANNED_EVALUATIONS_UNKNOWN",
    "LOCAL_ASSOCIATIONS_ONLY",
    "SCORE_VALUES_NOT_TASK_VERDICTS",
    "NO_STATISTICAL_OR_APPROVAL_AUTHORITY",
)
ROW_LIMITATIONS = (
    "SCORE_CONTENT_WITHHELD",
    "SOURCE_HASHES_AND_ROW_PATTERNS_LINKABLE",
    *LIMITATIONS,
)
NEXT_ACTIONS = (
    "SUPPLY_PLANNED_INVENTORY_FOR_MISSINGNESS",
    "FETCH_REMAINING_LANGFUSE_PAGES",
    "INSPECT_LANGFUSE_SUBJECT_CONTEXT",
    "INSPECT_UNRESOLVED_OBSERVATION_REFERENCES",
)


def _error(reason: str, location: str, code: str = "INVALID_INPUT") -> LangfuseAccountingError:
    return LangfuseAccountingError(code, reason, location)


def _object(value: object, location: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _error("LANGFUSE_CLI_OBJECT_REQUIRED", location)
    return cast(dict[str, object], value)


def _field(row: dict[str, object], key: str, location: str) -> object:
    if key not in row:
        raise _error("LANGFUSE_CLI_FIELD_REQUIRED", f"{location}/{key}")
    return row[key]


def _identifier(row: dict[str, object], key: str, location: str) -> str:
    return langfuse_identifier(_field(row, key, location), f"{location}/{key}")


def _label(row: dict[str, object], key: str, location: str) -> str:
    return langfuse_label(_field(row, key, location), f"{location}/{key}")


def _positive_integer(value: object, location: str, *, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise _error("LANGFUSE_CLI_PAGINATION_INVALID", location)
    return value


def _collected_pagination(
    meta: dict[str, object], role: str, count: int, location: str
) -> dict[str, object]:
    collected = ("pages", "fetchedItems", "truncated")
    if not all(key in meta for key in collected) or "cursor" in meta or "limit" in meta:
        raise _error("LANGFUSE_CLI_PAGINATION_INVALID", location)
    pages = _positive_integer(meta["pages"], location + "/pages", maximum=MAX_CLI_PAGES)
    if count > pages * PAGE_LIMITS[role]:
        raise _error("LANGFUSE_CLI_PAGINATION_INVALID", location + "/pages")
    fetched = meta["fetchedItems"]
    if type(fetched) is not int or fetched != count:
        raise _error("LANGFUSE_CLI_FETCHED_COUNT_MISMATCH", location + "/fetchedItems")
    truncated = meta["truncated"]
    if type(truncated) is not bool:
        raise _error("LANGFUSE_CLI_PAGINATION_INVALID", location + "/truncated")
    return {
        "mode": "cli_all",
        "cursor_state": "not_applicable",
        "pages": pages,
        "fetched_items": fetched,
        "truncated": truncated,
    }


def _page_pagination(
    meta: dict[str, object], role: str, count: int, location: str
) -> dict[str, object]:
    if "limit" in meta:
        limit = _positive_integer(meta["limit"], location + "/limit", maximum=PAGE_LIMITS[role])
        if count > limit:
            raise _error("LANGFUSE_CLI_PAGINATION_INVALID", location + "/limit")
    elif role == "scores":
        raise _error("LANGFUSE_CLI_FIELD_REQUIRED", location + "/limit")
    if "cursor" not in meta:
        cursor_state = "absent"
    elif meta["cursor"] is None:
        cursor_state = "null"
    elif type(meta["cursor"]) is str and meta["cursor"]:
        cursor_state = "present"
    else:
        raise _error("LANGFUSE_CLI_PAGINATION_INVALID", location + "/cursor")
    return {
        "mode": "one_page",
        "cursor_state": cursor_state,
        "pages": None,
        "fetched_items": None,
        "truncated": None,
    }


def _pagination(meta: dict[str, object], role: str, count: int) -> dict[str, object]:
    location = f"/{role}/meta"
    if any(key in meta for key in ("pages", "fetchedItems", "truncated")):
        return _collected_pagination(meta, role, count, location)
    return _page_pagination(meta, role, count, location)


def _capture(document: object, role: str, maximum: int) -> tuple[list[object], dict[str, object]]:
    location = "/" + role
    selected = _object(document, location)
    if set(selected) == {"status", "headers", "body"}:
        if type(selected["status"]) is not int or selected["status"] != HTTP_OK:
            raise _error("LANGFUSE_CLI_NON_SUCCESS_STATUS", location + "/status")
        headers = _object(selected["headers"], location + "/headers")
        if any(type(key) is not str or type(value) is not str for key, value in headers.items()):
            raise _error("LANGFUSE_CLI_HEADERS_INVALID", location + "/headers")
        body = _object(selected["body"], location + "/body")
        capture_kind, http_status = "json_envelope", HTTP_OK
    elif set(selected) == {"data", "meta"}:
        body = selected
        capture_kind, http_status = "output_body", None
    else:
        raise _error("LANGFUSE_CLI_CONTAINER_INVALID", location)
    if set(body) != {"data", "meta"}:
        raise _error("LANGFUSE_CLI_BODY_INVALID", location + "/body")
    rows = body["data"]
    if type(rows) is not list:
        raise _error("LANGFUSE_CLI_ARRAY_REQUIRED", location + "/data")
    owned = cast(list[object], rows)
    if len(owned) > maximum:
        raise _error(
            "LANGFUSE_CLI_POPULATION_LIMIT_EXCEEDED", location + "/data", "INPUT_LIMIT_EXCEEDED"
        )
    meta = _object(body["meta"], location + "/meta")
    return owned, {
        "capture_kind": capture_kind,
        "http_status": http_status,
        "pagination": _pagination(meta, role, len(owned)),
    }


def _observations(
    rows: list[object],
) -> tuple[list[dict[str, object]], dict[tuple[str, str | None, str], int]]:
    output: list[dict[str, object]] = []
    indexes: dict[tuple[str, str | None, str], int] = {}
    for index, value in enumerate(rows):
        location = f"/observations/{index}"
        row = _object(value, location)
        identifier = _identifier(row, "id", location)
        project_id = _identifier(row, "projectId", location)
        trace = _field(row, "traceId", location)
        trace_id = None if trace is None else langfuse_identifier(trace, location + "/traceId")
        key = (project_id, trace_id, identifier)
        if key in indexes:
            raise _error("LANGFUSE_CLI_AMBIGUOUS_OBSERVATION_KEY", location + "/id")
        indexes[key] = index
        output.append(
            {
                "observation_index": index,
                "id": identifier,
                "project_id": project_id,
                "trace_id": trace_id,
                "type": _label(row, "type", location),
                "local_score_count": 0,
            }
        )
    return output, indexes


def _score_value(row: dict[str, object], kind: str, location: str) -> int | float | bool | str:
    value = _field(row, "value", location)
    field = location + "/value"
    if kind == "NUMERIC":
        if type(value) not in (int, float):
            raise _error("LANGFUSE_CLI_SCORE_VALUE_INVALID", field)
        try:
            canonical_bytes(value)
        except CanonicalizationError:
            raise _error("LANGFUSE_CLI_SCORE_VALUE_INVALID", field) from None
        return cast(int | float, value)
    if kind == "BOOLEAN":
        if type(value) is not bool:
            raise _error("LANGFUSE_CLI_SCORE_VALUE_INVALID", field)
        return value
    if type(value) is not str:
        raise _error("LANGFUSE_CLI_SCORE_VALUE_INVALID", field)
    text = value
    if len(text) > MAX_STRING_VALUE_LENGTH:
        raise _error("LANGFUSE_CLI_SCORE_VALUE_LIMIT_EXCEEDED", field, "INPUT_LIMIT_EXCEEDED")
    try:
        text.encode("utf-8")
    except UnicodeError:
        raise _error("LANGFUSE_CLI_SCORE_VALUE_INVALID", field) from None
    if not unicodedata.is_normalized("NFC", text):
        raise _error("LANGFUSE_CLI_NONCANONICAL_SCORE_VALUE", field, "UNSUPPORTED_PROFILE")
    return text


def _subject(row: dict[str, object], location: str) -> dict[str, object]:
    if "subject" not in row:
        return {"availability": "absent", "kind": None, "id": None, "trace_id": None}
    if row["subject"] is None:
        return {"availability": "null", "kind": None, "id": None, "trace_id": None}
    selected = _object(row["subject"], location + "/subject")
    kind = _field(selected, "kind", location + "/subject")
    if type(kind) is not str or kind not in SUBJECT_KINDS:
        raise _error(
            "LANGFUSE_CLI_SUBJECT_KIND_UNSUPPORTED",
            location + "/subject/kind",
            "UNSUPPORTED_PROFILE",
        )
    identifier = _identifier(selected, "id", location + "/subject")
    if kind == "observation":
        trace = selected.get("traceId")
        trace_id = (
            None if trace is None else langfuse_identifier(trace, location + "/subject/traceId")
        )
    else:
        if "traceId" in selected:
            raise _error("LANGFUSE_CLI_SUBJECT_INVALID", location + "/subject/traceId")
        trace_id = None
    return {"availability": "present", "kind": kind, "id": identifier, "trace_id": trace_id}


def _scores(
    rows: list[object],
    observations: list[dict[str, object]],
    indexes: dict[tuple[str, str | None, str], int],
) -> tuple[list[dict[str, object]], dict[str, int], dict[str, int], dict[str, int]]:
    output: list[dict[str, object]] = []
    kinds = dict.fromkeys(VALUE_TYPES, 0)
    subjects: dict[str, int] = dict.fromkeys((*SUBJECT_KINDS, "unknown"), 0)
    associations: dict[str, int] = dict.fromkeys(ASSOCIATIONS, 0)
    score_keys: set[tuple[str, str]] = set()
    for index, value in enumerate(rows):
        location = f"/scores/{index}"
        row = _object(value, location)
        identifier = _identifier(row, "id", location)
        project_id = _identifier(row, "projectId", location)
        score_key = (project_id, identifier)
        if score_key in score_keys:
            raise _error("LANGFUSE_CLI_AMBIGUOUS_SCORE_KEY", location + "/id")
        score_keys.add(score_key)
        kind = _field(row, "dataType", location)
        if type(kind) is not str or kind not in VALUE_TYPES:
            raise _error(
                "LANGFUSE_CLI_SCORE_KIND_UNSUPPORTED", location + "/dataType", "UNSUPPORTED_PROFILE"
            )
        data_type = kind
        score_value = _score_value(row, data_type, location)
        subject = _subject(row, location)
        subject_kind = cast(str | None, subject["kind"])
        target: int | None = None
        if subject_kind is None:
            association = "subject_unknown"
        elif subject_kind != "observation":
            association = "other_subject"
        elif subject["trace_id"] is None:
            association = "observation_context_unknown"
        else:
            target = indexes.get(
                (project_id, cast(str, subject["trace_id"]), cast(str, subject["id"]))
            )
            association = (
                "observation_local" if target is not None else "unresolved_observation_reference"
            )
        kinds[data_type] += 1
        subjects[subject_kind if subject_kind is not None else "unknown"] += 1
        associations[association] += 1
        if target is not None:
            observed = observations[target]
            observed["local_score_count"] = cast(int, observed["local_score_count"]) + 1
        output.append(
            {
                "score_index": index,
                "id": identifier,
                "project_id": project_id,
                "name": _label(row, "name", location),
                "data_type": data_type,
                "value": score_value,
                "subject": subject,
                "association": association,
                "target_observation_index": target,
            }
        )
    return output, kinds, subjects, associations


def account_langfuse_cli(
    observations_document: object, scores_document: object
) -> dict[str, object]:
    """Check two complete native CLI captures without asserting export completeness."""
    observation_rows, observation_retrieval = _capture(
        observations_document, "observations", MAX_OBSERVATIONS
    )
    score_rows, score_retrieval = _capture(scores_document, "scores", MAX_SCORES)
    observed, indexes = _observations(observation_rows)
    scored, kinds, subjects, associations = _scores(score_rows, observed, indexes)
    pagination = [observation_retrieval["pagination"], score_retrieval["pagination"]]
    partial = any(
        cast(dict[str, object], item)["cursor_state"] == "present"
        or cast(dict[str, object], item)["truncated"] is True
        for item in pagination
    )
    actions = [NEXT_ACTIONS[0]]
    if partial:
        actions.append(NEXT_ACTIONS[1])
    if associations["subject_unknown"] or associations["observation_context_unknown"]:
        actions.append(NEXT_ACTIONS[2])
    if associations["unresolved_observation_reference"]:
        actions.append(NEXT_ACTIONS[3])
    return {
        "rule_profile": RULE_PROFILE,
        "retrieval": {"observations": observation_retrieval, "scores": score_retrieval},
        "scope": dict(SCOPE),
        "observations": observed,
        "scores": scored,
        "counts": {
            "observations": len(observed),
            "scores": len(scored),
            "by_data_type": kinds,
            "by_subject_kind": subjects,
            "associations": associations,
            "observations_without_local_scores": sum(
                item["local_score_count"] == 0 for item in observed
            ),
        },
        "planned": {"status": "unknown", "count": None, "missing_execution_count": None},
        "limitations": list(LIMITATIONS),
        "next_actions": actions,
    }


def index_langfuse_cli_accounting(accounting: dict[str, object]) -> dict[str, object]:
    """Allowlist structural row facts already decided by full accounting."""
    sources = cast(dict[str, dict[str, object]], accounting["sources"])
    rows = cast(list[dict[str, Any]], accounting["scores"])
    counts = cast(dict[str, Any], accounting["counts"])
    return {
        "schema": ROW_INDEX_SCHEMA,
        "source_rule_profile": RULE_PROFILE,
        "sources": {
            role: {
                "format": sources[role]["format"],
                "format_version": sources[role]["format_version"],
                "raw_hash": sources[role]["raw_hash"],
                "claims_verified": False,
            }
            for role in SOURCE_FORMATS
        },
        "retrieval": accounting["retrieval"],
        "scope": dict(ROW_SCOPE),
        "counts": {
            "observations": counts["observations"],
            "scores": counts["scores"],
            "by_data_type": {kind: counts["by_data_type"][kind] for kind in VALUE_TYPES},
            "by_subject_kind": {
                kind: counts["by_subject_kind"][kind] for kind in (*SUBJECT_KINDS, "unknown")
            },
            "associations": {name: counts["associations"][name] for name in ASSOCIATIONS},
            "observations_without_local_scores": counts["observations_without_local_scores"],
        },
        "score_rows": [
            {
                "score_index": row["score_index"],
                "data_type": row["data_type"],
                "subject_availability": row["subject"]["availability"],
                "subject_kind": row["subject"]["kind"],
                "association": row["association"],
                "target_observation_index": row["target_observation_index"],
            }
            for row in rows
        ],
        "limitations": list(ROW_LIMITATIONS),
        "next_actions": cast(list[str], accounting["next_actions"]).copy(),
    }

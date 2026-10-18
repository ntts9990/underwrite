"""Select one Langfuse wire family from two supplied documents, then reuse its accountant."""

from __future__ import annotations

from types import MappingProxyType
from typing import cast

from underwrite.instrument.evidence.langfuse_accounting import (
    MAX_OBSERVATIONS,
    MAX_SCORES,
    LangfuseAccountingError,
    account_langfuse,
)
from underwrite.instrument.evidence.langfuse_accounting import (
    RULE_PROFILE as BLOB_RULE_PROFILE,
)
from underwrite.instrument.evidence.langfuse_cli import (
    ROW_SCOPE as CLI_ROW_SCOPE,
)
from underwrite.instrument.evidence.langfuse_cli import (
    RULE_PROFILE as CLI_RULE_PROFILE,
)
from underwrite.instrument.evidence.langfuse_cli import (
    SOURCE_FORMATS as CLI_SOURCE_FORMATS,
)
from underwrite.instrument.evidence.langfuse_cli import (
    account_langfuse_cli,
    index_langfuse_cli_body,
)
from underwrite.instrument.evidence.langfuse_row_index import (
    SCOPE as BLOB_ROW_SCOPE,
)
from underwrite.instrument.evidence.langfuse_row_index import (
    index_langfuse_body,
)

AUTO_PROFILE = "auto"
ACCOUNTING_SCHEMA = "langfuse_accounting.v2"
ROW_INDEX_SCHEMA = "langfuse_row_index.v2"
BLOB_READER = "langfuse.blob-array.selected-fields.v1"
CLI_READER = "langfuse.cli-list.selected-fields.v1"
READERS = (BLOB_READER, CLI_READER)
ROLES = ("observations", "scores")
BLOB_SOURCE_FORMATS = MappingProxyType(
    {"observations": "langfuse.observations-v2", "scores": "langfuse.scores"}
)
SOURCE_FORMATS = MappingProxyType(
    {BLOB_READER: BLOB_SOURCE_FORMATS, CLI_READER: CLI_SOURCE_FORMATS}
)
ARRAY_OBSERVATION_ALIASES = frozenset({"projectId", "traceId", "parentObservationId"})
ARRAY_SCORE_ALIASES = frozenset(
    {
        "projectId",
        "traceId",
        "observationId",
        "sessionId",
        "datasetRunId",
        "dataType",
        "stringValue",
        "longStringValue",
        "subject",
    }
)
CLI_OBSERVATION_ALIASES = frozenset({"project_id", "trace_id", "parent_observation_id"})
CLI_SCORE_ALIASES = frozenset(
    {
        "project_id",
        "trace_id",
        "observation_id",
        "session_id",
        "dataset_run_id",
        "data_type",
        "string_value",
        "observationId",
        "traceId",
        "sessionId",
        "datasetRunId",
        "stringValue",
        "longStringValue",
    }
)
CLI_SUBJECT_ALIASES = frozenset({"trace_id", "observation_id", "session_id", "dataset_run_id"})
ARRAY_OBSERVATION_FIELDS = frozenset({"id", "project_id", "trace_id", "type"})
ARRAY_SCORE_FIELDS = frozenset(
    {
        "id",
        "project_id",
        "trace_id",
        "observation_id",
        "session_id",
        "dataset_run_id",
        "name",
        "data_type",
        "value",
        "string_value",
    }
)
CLI_OBSERVATION_FIELDS = frozenset({"id", "projectId", "traceId", "type"})
CLI_SCORE_FIELDS = frozenset({"id", "projectId", "name", "dataType", "value", "subject"})
EXTRA_SCOPE = MappingProxyType(
    {
        "selected_fields_only": True,
        "selected_file_binding": "independent_selected_files",
        "source_windows": "unknown",
    }
)
EXTRA_LIMITATIONS = (
    "SOURCE_VERSION_UNKNOWN",
    "SELECTED_FIELDS_ONLY",
    "SOURCE_WINDOWS_UNKNOWN",
    "SOURCE_HASHES_AND_ROW_PATTERNS_LINKABLE",
)


def _error(
    reason: str, location: str, code: str = "UNSUPPORTED_PROFILE"
) -> LangfuseAccountingError:
    return LangfuseAccountingError(code, reason, location)


def _mode(value: object, role: str) -> str:
    location = "/" + role
    if type(value) is list:
        return "array"
    if type(value) is not dict:
        raise _error("LANGFUSE_AUTO_CONTAINER_UNSUPPORTED", location)
    keys = set(cast(dict[str, object], value))
    envelope = {"status", "headers", "body"}
    body = {"data", "meta"}
    if envelope <= keys and not body & keys:
        return "cli"
    if body <= keys and not envelope & keys:
        return "cli"
    raise _error("LANGFUSE_AUTO_CONTAINER_UNSUPPORTED", location)


def _selected_rows(
    rows: object,
    role: str,
    *,
    aliases: frozenset[str],
    fields: frozenset[str],
) -> list[object]:
    location = "/" + role
    if type(rows) is not list:
        raise _error("LANGFUSE_AUTO_ARRAY_REQUIRED", location)
    selected = cast(list[object], rows)
    maximum = MAX_OBSERVATIONS if role == "observations" else MAX_SCORES
    if len(selected) > maximum:
        raise _error("LANGFUSE_POPULATION_LIMIT_EXCEEDED", location, "INPUT_LIMIT_EXCEEDED")
    projected: list[object] = []
    for index, value in enumerate(selected):
        if type(value) is not dict:
            projected.append(value)  # Existing family owner returns the structural refusal.
            continue
        row = cast(dict[str, object], value)
        if aliases.intersection(row):
            raise _error("LANGFUSE_AUTO_FIELD_ALIAS", f"{location}/{index}")
        selected_row = {key: item for key, item in row.items() if key in fields}
        if role == "scores" and "subject" in fields and type(selected_row.get("subject")) is dict:
            subject = cast(dict[str, object], selected_row["subject"])
            if CLI_SUBJECT_ALIASES.intersection(subject):
                raise _error("LANGFUSE_AUTO_FIELD_ALIAS", f"{location}/{index}/subject")
            selected_row["subject"] = {
                key: item for key, item in subject.items() if key in {"kind", "id", "traceId"}
            }
        projected.append(selected_row)
    return projected


def _selected_cli(document: object, role: str) -> dict[str, object]:
    source = cast(dict[str, object], document)
    if {"status", "headers", "body"} <= source.keys():
        body = source["body"]
        envelope = True
    else:
        body = source
        envelope = False
    if type(body) is not dict:
        raise _error("LANGFUSE_AUTO_BODY_UNSUPPORTED", f"/{role}/body")
    content = cast(dict[str, object], body)
    if not {"data", "meta"} <= content.keys():
        raise _error("LANGFUSE_AUTO_BODY_UNSUPPORTED", f"/{role}/body")
    meta = content["meta"]
    if type(meta) is not dict:
        raise _error("LANGFUSE_AUTO_PAGINATION_UNSUPPORTED", f"/{role}/meta")
    selected_meta = cast(dict[str, object], meta)
    collected = {"pages", "fetchedItems", "truncated"}
    allowed = collected if collected & selected_meta.keys() else {"cursor", "limit"}
    if not selected_meta.keys() <= allowed:
        raise _error("LANGFUSE_AUTO_PAGINATION_UNSUPPORTED", f"/{role}/meta")
    rows = _selected_rows(
        content["data"],
        role,
        aliases=CLI_OBSERVATION_ALIASES if role == "observations" else CLI_SCORE_ALIASES,
        fields=CLI_OBSERVATION_FIELDS if role == "observations" else CLI_SCORE_FIELDS,
    )
    selected_body: dict[str, object] = {"data": rows, "meta": selected_meta}
    return (
        {"status": source["status"], "headers": source["headers"], "body": selected_body}
        if envelope
        else selected_body
    )


def extended_limitations(values: object) -> list[str]:
    original = cast(list[str], values)
    return [*original, *(name for name in EXTRA_LIMITATIONS if name not in original)]


def account_langfuse_compatible(observations: object, scores: object) -> dict[str, object]:
    """Choose a wire family from both containers, then account selected rows once."""
    observation_mode = _mode(observations, "observations")
    score_mode = _mode(scores, "scores")
    if observation_mode != score_mode:
        raise _error("LANGFUSE_AUTO_MIXED_FAMILIES", "/scores")
    if observation_mode == "array":
        reader = BLOB_READER
        counted = account_langfuse(
            _selected_rows(
                observations,
                "observations",
                aliases=ARRAY_OBSERVATION_ALIASES,
                fields=ARRAY_OBSERVATION_FIELDS,
            ),
            _selected_rows(
                scores, "scores", aliases=ARRAY_SCORE_ALIASES, fields=ARRAY_SCORE_FIELDS
            ),
        )
    else:
        reader = CLI_READER
        counted = account_langfuse_cli(
            _selected_cli(observations, "observations"),
            _selected_cli(scores, "scores"),
        )
    scope = cast(dict[str, object], counted["scope"])
    return {
        "reader_profile": reader,
        "sources": {
            role: {
                "format": SOURCE_FORMATS[reader][role],
                "producer_version": None,
                "producer_version_state": "unknown",
                "claims_verified": False,
            }
            for role in ROLES
        },
        **counted,
        "scope": {**scope, **EXTRA_SCOPE},
        "limitations": extended_limitations(counted["limitations"]),
    }


def index_langfuse_compatible(full_report: dict[str, object]) -> dict[str, object]:
    """Project one already-accounted auto result without inventing old source identities."""
    reader = full_report.get("reader_profile")
    if reader == BLOB_READER:
        projected = index_langfuse_body(full_report)
        scope = dict(BLOB_ROW_SCOPE)
        rule = BLOB_RULE_PROFILE
    elif reader == CLI_READER:
        projected = index_langfuse_cli_body(full_report)
        scope = dict(CLI_ROW_SCOPE)
        rule = CLI_RULE_PROFILE
    else:
        raise _error("LANGFUSE_AUTO_READER_UNSUPPORTED", "/reader_profile")
    return {
        "reader_profile": reader,
        "source_rule_profile": rule,
        "sources": full_report["sources"],
        "scope": {**scope, **EXTRA_SCOPE},
        **projected,
        "limitations": extended_limitations(projected["limitations"]),
    }

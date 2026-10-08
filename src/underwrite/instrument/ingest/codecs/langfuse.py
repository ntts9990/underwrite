"""Read explicitly selected Langfuse 4.35.0 blob-export files; scores stay source claims.

One profile per exported table: observations_v2 (the v4 events table) and scores.
Each file is the JSON array the worker's transformStreamToJson wrote and declares
no version of its own, so drift is caught by the closed row key sets below. Rows
are preserved as received under one key; nothing is re-scored or re-typed."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from underwrite.instrument.ingest.codecs._shape import Shape
from underwrite.instrument.ingest.project import Projection
from underwrite.instrument.ingest.transport import Codec


class LangfusePayloadError(ValueError):
    """A selected Langfuse export file lacks the pinned minimal consumable structure."""


# Row keys of the 4.35.0 blob export (tag v4.35.0, worker
# handleBlobStorageIntegrationProjectJob + shared FIELD_SETS / scores export):
# observations_v2 = export ∪ io ∪ metadata field sets in snake_case, plus the
# enrichment prices and is_root_observation; scores = the documented 16 columns.
# Both sets were confirmed against the captured files in
# fixtures/golden/external/langfuse (SOURCE.md).
_OBSERVATION_KEYS = frozenset(
    {
        "bookmarked",
        "completion_start_time",
        "cost_details",
        "created_at",
        "end_time",
        "environment",
        "id",
        "input",
        "input_price",
        "is_root_observation",
        "latency",
        "level",
        "metadata",
        "model_id",
        "model_parameters",
        "name",
        "output",
        "output_price",
        "parent_observation_id",
        "project_id",
        "prompt_id",
        "prompt_name",
        "prompt_version",
        "provided_model_name",
        "public",
        "release",
        "session_id",
        "start_time",
        "status_message",
        "tags",
        "time_to_first_token",
        "tool_call_names",
        "tool_calls",
        "tool_definitions",
        "total_cost",
        "total_price",
        "trace_id",
        "trace_name",
        "type",
        "updated_at",
        "usage_details",
        "usage_pricing_tier_id",
        "usage_pricing_tier_name",
        "user_id",
        "version",
    }
)
_SCORE_KEYS = frozenset(
    {
        "comment",
        "created_at",
        "data_type",
        "dataset_run_id",
        "environment",
        "id",
        "name",
        "observation_id",
        "project_id",
        "session_id",
        "source",
        "string_value",
        "timestamp",
        "trace_id",
        "updated_at",
        "value",
    }
)
# LISTABLE_SCORE_TYPES at the tag (packages/shared/src/domain/scores.ts): the export
# query filters on these four, so CORRECTION never reaches a file and any other
# value is drift, not a new score kind.
_SCORE_DATA_TYPES = frozenset({"NUMERIC", "BOOLEAN", "CATEGORICAL", "TEXT"})


_SHAPE = Shape(LangfusePayloadError, "LANGFUSE")


def _exact_object(value: object, keys: frozenset[str], label: str) -> dict[str, object]:
    # Both exports write every column of their SELECT (nulls included), so a row
    # with fewer keys is drift just as much as a row with more -- the one reader
    # whose key check is an exact match, not the shared closed superset. For
    # observations that holds for the pinned profile only: an integration whose
    # exportFieldGroups is unset exports OBSERVATION_FIELD_GROUPS_FULL (all 11
    # groups); a narrowed group list writes shorter rows and is a different,
    # unsupported profile.
    row = _SHAPE.as_closed_object(value, keys, label)
    if not keys.issubset(row):
        _SHAPE.fail("MISSING_FIELD", label)
    return row


def _observation(value: object) -> None:
    row = _exact_object(value, _OBSERVATION_KEYS, "observation")
    for key in ("id", "trace_id", "project_id", "type", "start_time"):
        _SHAPE.identifier(row, key)


def _score(value: object) -> None:
    row = _exact_object(value, _SCORE_KEYS, "score")
    for key in ("id", "trace_id", "project_id", "name", "timestamp"):
        _SHAPE.identifier(row, key)
    data_type = row["data_type"]
    if not isinstance(data_type, str) or data_type not in _SCORE_DATA_TYPES:
        raise LangfusePayloadError("LANGFUSE_SCORE_DATA_TYPE_UNSUPPORTED")
    # The export writes value as a number for every data type (0/1 for
    # BOOLEAN/CATEGORICAL/TEXT) and keeps the text in string_value; a written
    # null there is the export's own "no text".
    _SHAPE.number(row, "value")
    _SHAPE.text_or_null(row, "string_value")


def read_observations_v2(value: object) -> Projection:
    """Preserve one observations_v2 export file, including an empty export."""
    rows = _SHAPE.as_array(value, "observations")
    for item in rows:
        _observation(item)
    return Projection("eval_run", {"observations": rows})


def read_scores(value: object) -> Projection:
    """Preserve one scores export file; every score data type stays as written."""
    rows = _SHAPE.as_array(value, "scores")
    for item in rows:
        _score(item)
    return Projection("eval_run", {"scores": rows})


CODECS: Mapping[tuple[str, str], Codec] = MappingProxyType(
    {
        ("langfuse.observations-v2", "4.35.0"): read_observations_v2,
        ("langfuse.scores", "4.35.0"): read_scores,
    }
)

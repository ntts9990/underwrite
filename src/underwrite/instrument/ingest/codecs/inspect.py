"""Read an explicitly selected Inspect AI 0.3.263 EvalLog JSON file; scores stay source claims.

The file is what ``inspect eval --log-format json`` writes: ``to_json_safe(EvalLog)``,
i.e. pydantic ``to_json(exclude_none=True)`` of the tag's model. ``None`` fields are
absent, so every level is checked as a closed *superset* of the tag's field set, not
an exact match. Non-finite numbers never reach this codec: the transport rejects the
``NaN``/``Infinity`` literals Python's serializer may write (NON_FINITE_JSON_NUMBER)."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from underwrite.instrument.ingest.codecs._shape import Shape, is_dict
from underwrite.instrument.ingest.project import Projection
from underwrite.instrument.ingest.transport import Codec


class InspectPayloadError(ValueError):
    """A selected Inspect EvalLog lacks the pinned minimal consumable structure."""


# Field sets of inspect_ai==0.3.263 (tag 0.3.263, commit
# 7aa7343e4a14fa7be07e5a09c7431df5e88c17ee, src/inspect_ai/log/_log.py). EvalLog's
# `location` and `etag` are `exclude=True` and never serialized. The file carries
# `version` (schema version 2 at the tag); an unknown key is drift.
_LOG_KEYS = frozenset(
    {
        "version",
        "status",
        "eval",
        "plan",
        "results",
        "stats",
        "error",
        "invalidated",
        "log_updates",
        "config_updates",
        "tags",
        "metadata",
        "samples",
        "reductions",
    }
)
_SPEC_KEYS = frozenset(
    {
        "eval_set_id",
        "eval_id",
        "run_id",
        "created",
        "task",
        "task_id",
        "task_version",
        "task_file",
        "task_display_name",
        "task_registry_name",
        "task_attribs",
        "task_args",
        "task_args_passed",
        "solver",
        "solver_args",
        "solver_args_passed",
        "tags",
        "dataset",
        "sandbox",
        "model",
        "model_generate_config",
        "model_base_url",
        "model_args",
        "model_roles",
        "config",
        "revision",
        "packages",
        "metadata",
        "viewer",
        "scorers",
        "metrics",
        "headline_metric",
    }
)
_PLAN_KEYS = frozenset({"name", "steps", "finish", "config"})
_RESULTS_KEYS = frozenset(
    {
        "total_samples",
        "completed_samples",
        "logged_samples",
        "early_stopping",
        "scores",
        "headline",
        "metadata",
    }
)
_SCORE_KEYS = frozenset(
    {
        "name",
        "scorer",
        "reducer",
        "scored_samples",
        "unscored_samples",
        "params",
        "metrics",
        "metadata",
    }
)
_METRIC_KEYS = frozenset({"name", "group", "value", "params", "metadata"})
_STATS_KEYS = frozenset(
    {"started_at", "completed_at", "model_usage", "role_usage", "connection_limit_history"}
)
_SAMPLE_KEYS = frozenset(
    {
        "id",
        "epoch",
        "input",
        "choices",
        "target",
        "sandbox",
        "files",
        "setup",
        "messages",
        "output",
        "scores",
        "metadata",
        "store",
        "events",
        "timelines",
        "model_usage",
        "role_usage",
        "model_fallbacks",
        "started_at",
        "completed_at",
        "total_time",
        "working_time",
        "uuid",
        "invalidation",
        "error",
        "error_retries",
        "attachments",
        "events_data",
        "limit",
        "turn_count",
        "token_limit",
        "token_limit_type",
        "token_limit_usage",
        "message_limit",
        "time_limit",
    }
)
# EvalStatus at the tag; an unknown status is drift, not a new run state.
_STATUSES = frozenset({"started", "success", "cancelled", "error"})
_LOG_VERSION = 2


_SHAPE = Shape(InspectPayloadError, "INSPECT")


def _metric(value: object, label: str) -> None:
    metric = _SHAPE.as_closed_object(value, _METRIC_KEYS, label)
    _SHAPE.identifier(metric, "name")
    # Metric values are floats or ints at the tag; a bool is not a number here.
    _SHAPE.number(metric, "value", label)


def _score(value: object) -> None:
    score = _SHAPE.as_closed_object(value, _SCORE_KEYS, "score")
    _SHAPE.identifier(score, "name")
    _SHAPE.identifier(score, "scorer")
    for name, metric in _SHAPE.as_object(score.get("metrics"), "metrics").items():
        _metric(metric, f"metric {name}")


def _sample(value: object) -> None:
    sample = _SHAPE.as_closed_object(value, _SAMPLE_KEYS, "sample")
    # EvalSample.id is `int | str` at the tag; a blank string names nothing.
    sample_id = sample.get("id")
    if type(sample_id) is not int and not (isinstance(sample_id, str) and sample_id.strip()):
        raise InspectPayloadError("INSPECT_SAMPLE_ID_REQUIRED")
    if type(sample.get("epoch")) is not int:
        raise InspectPayloadError("INSPECT_EPOCH_REQUIRED")
    if "scores" in sample:
        for name, score in _SHAPE.as_object(sample["scores"], "sample scores").items():
            # Score.value is str | int | float | bool | list | dict at the tag: a producer
            # claim preserved as written, but a score without a value is not a score.
            if not is_dict(score) or "value" not in score:
                _SHAPE.fail("SCORE_VALUE_REQUIRED", name)


def read_eval_log(value: object) -> Projection:
    """Preserve one pinned EvalLog; samples, events and scores stay as the runner wrote them."""
    payload = _SHAPE.as_closed_object(value, _LOG_KEYS, "eval log")
    version = payload.get("version")
    if type(version) is not int or version != _LOG_VERSION:
        raise InspectPayloadError("INSPECT_LOG_VERSION_UNSUPPORTED")
    status = payload.get("status")
    if not isinstance(status, str) or status not in _STATUSES:
        raise InspectPayloadError("INSPECT_STATUS_UNSUPPORTED")
    spec = _SHAPE.as_closed_object(payload.get("eval"), _SPEC_KEYS, "eval")
    for key in ("eval_id", "task", "model", "created"):
        _SHAPE.identifier(spec, key)
    # EvalSpec.run_id and task_id default to "" at the tag (the runner fills them, a
    # hand-built log may not), so they must be text but may be blank.
    for key in ("run_id", "task_id"):
        _SHAPE.text(spec, key)
    _SHAPE.as_closed_object(payload.get("plan"), _PLAN_KEYS, "plan")
    _SHAPE.as_closed_object(payload.get("stats"), _STATS_KEYS, "stats")
    if "results" in payload:
        results = _SHAPE.as_closed_object(payload["results"], _RESULTS_KEYS, "results")
        for item in _SHAPE.as_array(results.get("scores", []), "results scores"):
            _score(item)
    if "samples" in payload:
        for item in _SHAPE.as_array(payload["samples"], "samples"):
            _sample(item)
    return Projection("eval_run", payload)


CODECS: Mapping[tuple[str, str], Codec] = MappingProxyType(
    {("inspect.eval-log", "0.3.263"): read_eval_log}
)

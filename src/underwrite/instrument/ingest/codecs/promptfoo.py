"""Read an explicitly selected promptfoo 0.123.0 eval output file; scores stay source claims.

The file is what ``promptfoo eval -o <path>.json`` writes: ``JSON.stringify(OutputFile)``
where ``results`` is the ``EvaluateSummaryV3`` (``version: 3`` lives there, not at the
top level). Optional fields are simply absent, so every checked level is a closed
*superset* of the tag's field set. The file declares its producer version in
``metadata.promptfooVersion``; a different value is drift, not a newer profile."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from underwrite.instrument.ingest.codecs._shape import Shape
from underwrite.instrument.ingest.project import Projection
from underwrite.instrument.ingest.transport import Codec


class PromptfooPayloadError(ValueError):
    """A selected promptfoo output file lacks the pinned minimal consumable structure."""


_VERSION = "0.123.0"
_SUMMARY_VERSION = 3

# Field sets of promptfoo 0.123.0 (tag 0.123.0, commit
# 3ec974043eb9941af5b605f6f21f71f24bf46c3c): OutputFile, EvaluateSummaryV3,
# EvaluateResult, GradingResult, EvaluateStats and OutputMetadata in
# src/types/index.ts; CompletedPrompt = PromptSchema (src/contracts/validators/
# prompts.ts) + provider + PromptMetricsSchema. An unknown key is drift.
_OUTPUT_KEYS = frozenset(
    {
        "evalId",
        "results",
        "config",
        "shareableUrl",
        "metadata",
        "vars",
        "runtimeOptions",
        "traces",
        "blobAssets",
    }
)
_SUMMARY_KEYS = frozenset({"version", "timestamp", "results", "prompts", "stats"})
_METADATA_KEYS = frozenset(
    {
        "promptfooVersion",
        "nodeVersion",
        "platform",
        "arch",
        "exportedAt",
        "evaluationCreatedAt",
        "author",
    }
)
_RESULT_KEYS = frozenset(
    {
        "id",
        "description",
        "promptIdx",
        "testIdx",
        "testCase",
        "promptId",
        "provider",
        "prompt",
        "vars",
        "response",
        "error",
        "failureReason",
        "success",
        "score",
        "latencyMs",
        "gradingResult",
        "namedScores",
        "cost",
        "incurredCost",
        "metadata",
        "tokenUsage",
        "evaluationId",
        "traceId",
    }
)
_GRADING_KEYS = frozenset(
    {
        "pass",
        "score",
        "reason",
        "namedScores",
        "namedScoreWeights",
        "tokensUsed",
        "componentResults",
        "assertion",
        "comment",
        "suggestions",
        "metadata",
    }
)
_PROMPT_KEYS = frozenset(
    {"id", "raw", "template", "display", "label", "function", "config", "provider", "metrics"}
)
_METRICS_KEYS = frozenset(
    {
        "score",
        "testPassCount",
        "testFailCount",
        "testErrorCount",
        "assertPassCount",
        "assertFailCount",
        "totalLatencyMs",
        "tokenUsage",
        "namedScores",
        "namedScoresCount",
        "namedScoreWeights",
        "redteam",
        "cost",
        "incurredCost",
    }
)
# The PromptMetricsSchema counters; ordered so the first faulty one is the reported one.
_METRIC_COUNTS = (
    "testPassCount",
    "testFailCount",
    "testErrorCount",
    "assertPassCount",
    "assertFailCount",
)
_STATS_KEYS = frozenset(
    {
        "successes",
        "failures",
        "errors",
        "tokenUsage",
        "durationMs",
        "generationDurationMs",
        "evaluationDurationMs",
    }
)
# ResultFailureReason at the tag: NONE, ASSERT, ERROR.
_FAILURE_REASONS = frozenset({0, 1, 2})


_SHAPE = Shape(PromptfooPayloadError, "PROMPTFOO")


# This reader names a faulty field as "<owner> <key>", where `owner` is the label of the
# object the key was read from, so `_field` composes that detail once for every check.
def _field(owner: str, key: str) -> str:
    return f"{owner} {key}"


def _grading(value: object, label: str) -> None:
    grading = _SHAPE.as_closed_object(value, _GRADING_KEYS, label)
    _SHAPE.flag(grading, "pass", _field(label, "pass"))
    _SHAPE.number(grading, "score", _field(label, "score"))
    _SHAPE.text(grading, "reason", _field(label, "reason"))
    if "componentResults" in grading:
        components = _SHAPE.as_array(grading["componentResults"], f"{label} components")
        for index, item in enumerate(components):
            _grading(item, f"{label} component {index}")


def _result(value: object, index: int) -> None:
    label = f"result {index}"
    row = _SHAPE.as_closed_object(value, _RESULT_KEYS, label)
    _SHAPE.count(row, "promptIdx", _field(label, "promptIdx"))
    _SHAPE.count(row, "testIdx", _field(label, "testIdx"))
    _SHAPE.identifier(row, "promptId", _field(label, "promptId"))
    _SHAPE.flag(row, "success", _field(label, "success"))
    _SHAPE.number(row, "score", _field(label, "score"))
    _SHAPE.number(row, "latencyMs", _field(label, "latencyMs"))
    # `True == 1` would slip through a bare membership test; a bool is not a reason code.
    reason = row.get("failureReason")
    if type(reason) is not int or reason not in _FAILURE_REASONS:
        _SHAPE.fail("FAILURE_REASON_UNSUPPORTED", label)
    _SHAPE.as_object(row.get("namedScores"), f"{label} namedScores")
    if "error" in row:
        _SHAPE.text_or_null(row, "error", _field(label, "error"))
    # gradingResult is `GradingResult | null | undefined`: an errored row may carry none.
    if row.get("gradingResult") is not None:
        _grading(row["gradingResult"], f"{label} grading")


def _prompt(value: object, index: int) -> None:
    label = f"prompt {index}"
    prompt = _SHAPE.as_closed_object(value, _PROMPT_KEYS, label)
    for key in ("raw", "label", "provider"):
        _SHAPE.text(prompt, key, _field(label, key))
    if "metrics" in prompt:
        owner = f"{label} metrics"
        metrics = _SHAPE.as_closed_object(prompt["metrics"], _METRICS_KEYS, owner)
        _SHAPE.number(metrics, "score", _field(owner, "score"))
        for key in _METRIC_COUNTS:
            _SHAPE.count(metrics, key, _field(owner, key))


def read_eval_output(value: object) -> Projection:
    """Preserve one pinned promptfoo output file; rows, grading and config stay as written."""
    payload = _SHAPE.as_closed_object(value, _OUTPUT_KEYS, "output")
    _SHAPE.text_or_null(payload, "evalId")
    _SHAPE.text_or_null(payload, "shareableUrl")
    metadata = _SHAPE.as_closed_object(payload.get("metadata"), _METADATA_KEYS, "metadata")
    if metadata.get("promptfooVersion") != _VERSION:
        raise PromptfooPayloadError("PROMPTFOO_PRODUCER_VERSION_MISMATCH")
    _SHAPE.as_object(payload.get("config"), "config")
    # The version is checked before the key set so a real V2 summary (which carries
    # `table`) is refused for its version, not for an unknown field.
    summary = _SHAPE.as_object(payload.get("results"), "results")
    version = summary.get("version")
    if type(version) is not int or version != _SUMMARY_VERSION:
        raise PromptfooPayloadError("PROMPTFOO_SUMMARY_VERSION_UNSUPPORTED")
    if not _SUMMARY_KEYS.issuperset(summary):
        _SHAPE.fail("UNKNOWN_FIELD", "results")
    _SHAPE.identifier(summary, "timestamp", _field("results", "timestamp"))
    for index, item in enumerate(_SHAPE.as_array(summary.get("results"), "results rows")):
        _result(item, index)
    for index, item in enumerate(_SHAPE.as_array(summary.get("prompts"), "prompts")):
        _prompt(item, index)
    stats = _SHAPE.as_closed_object(summary.get("stats"), _STATS_KEYS, "stats")
    for key in ("successes", "failures", "errors"):
        _SHAPE.count(stats, key, _field("stats", key))
    return Projection("eval_run", payload)


CODECS: Mapping[tuple[str, str], Codec] = MappingProxyType(
    {("promptfoo.eval-output", _VERSION): read_eval_output}
)

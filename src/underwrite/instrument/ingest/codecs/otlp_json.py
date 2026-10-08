"""Read an explicitly selected OTLP/JSON trace export written by the OpenTelemetry Collector
0.160.0 file exporter; GenAI attributes stay source claims.

The file is one ``ExportTraceServiceRequest`` in the OTLP/JSON encoding (one line per
export batch; the capture is a single batch). Message shapes are closed supersets of the
proto field sets at opentelemetry-proto 1.44.0 — proto3 JSON omits defaults, so absence
is normal and an unknown key is drift. 64-bit integers are JSON strings, enums are ints.
``gen_ai.*`` attribute keys must belong to the pinned GenAI semantic-conventions registry
(Development stability, commit in the fixture's SOURCE.md): an unknown ``gen_ai.`` key is
drift in a convention that has not stabilised, not a new feature. Other attribute keys
are preserved unchecked."""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType

from underwrite.instrument.ingest.codecs._shape import Shape
from underwrite.instrument.ingest.project import Projection
from underwrite.instrument.ingest.transport import Codec


class OtlpJsonPayloadError(ValueError):
    """A selected OTLP/JSON trace export lacks the pinned minimal consumable structure."""


# Proto JSON field names (json_name) at opentelemetry-proto 1.44.0.
_REQUEST_KEYS = frozenset({"resourceSpans"})
_RESOURCE_SPANS_KEYS = frozenset({"resource", "scopeSpans", "schemaUrl"})
_RESOURCE_KEYS = frozenset({"attributes", "droppedAttributesCount", "entityRefs"})
_SCOPE_SPANS_KEYS = frozenset({"scope", "spans", "schemaUrl"})
_SCOPE_KEYS = frozenset({"name", "version", "attributes", "droppedAttributesCount"})
_SPAN_KEYS = frozenset(
    {
        "traceId",
        "spanId",
        "traceState",
        "parentSpanId",
        "flags",
        "name",
        "kind",
        "startTimeUnixNano",
        "endTimeUnixNano",
        "attributes",
        "droppedAttributesCount",
        "events",
        "droppedEventsCount",
        "links",
        "droppedLinksCount",
        "status",
    }
)
_EVENT_KEYS = frozenset({"timeUnixNano", "name", "attributes", "droppedAttributesCount"})
_LINK_KEYS = frozenset(
    {"traceId", "spanId", "traceState", "attributes", "droppedAttributesCount", "flags"}
)
_STATUS_KEYS = frozenset({"message", "code"})
_KEY_VALUE_KEYS = frozenset({"key", "value", "keyStrindex"})
_ANY_VALUE_KEYS = frozenset(
    {
        "stringValue",
        "boolValue",
        "intValue",
        "doubleValue",
        "arrayValue",
        "kvlistValue",
        "bytesValue",
        "stringValueStrindex",
    }
)
# All eight AnyValue fields form one oneof; exactly one is written for a value. The
# dictionary-encoded member (stringValueStrindex, with KeyValue.keyStrindex) is refused
# outright: the reader does not resolve string dictionaries, so such a file is unsupported.
_ANY_VALUE_ONEOF = _ANY_VALUE_KEYS
_DICTIONARY_KEYS = frozenset({"stringValueStrindex", "keyStrindex"})
# ArrayValue and KeyValueList each carry one repeated field.
_VALUES_KEYS = frozenset({"values"})
_SPAN_KINDS = frozenset(range(6))  # SPAN_KIND_UNSPECIFIED .. SPAN_KIND_CONSUMER
_STATUS_CODES = frozenset(range(3))  # STATUS_CODE_UNSET, OK, ERROR
# proto3 JSON decimal strings: no leading zeros, no sign on unsigned fields.
_INT64 = re.compile(r"0|-?[1-9][0-9]*")  # no "-0", no leading zeros
_UINT64 = re.compile(r"0|[1-9][0-9]*")
_INT64_RANGE = range(-(2**63), 2**63)
_UINT64_RANGE = range(0, 2**64)
_TRACE_ID = re.compile(r"[0-9a-f]{32}")
_SPAN_ID = re.compile(r"[0-9a-f]{16}")

# gen_ai.* attribute keys of the pinned GenAI semantic conventions registry
# (open-telemetry/semantic-conventions-genai, docs/registry/attributes/gen-ai.md at
# commit 0c87594975195608dc91b3f702e250a7b240c151, 2026-09-10). Development stability.
GEN_AI_ATTRIBUTE_KEYS = frozenset(
    {
        "gen_ai.agent.description",
        "gen_ai.agent.id",
        "gen_ai.agent.name",
        "gen_ai.agent.version",
        "gen_ai.conversation.compacted",
        "gen_ai.conversation.id",
        "gen_ai.data_source.id",
        "gen_ai.embeddings.dimension.count",
        "gen_ai.evaluation.explanation",
        "gen_ai.evaluation.name",
        "gen_ai.evaluation.score.label",
        "gen_ai.evaluation.score.value",
        "gen_ai.input.messages",
        "gen_ai.memory.query.text",
        "gen_ai.memory.record.count",
        "gen_ai.memory.record.id",
        "gen_ai.memory.records",
        "gen_ai.memory.store.id",
        "gen_ai.operation.name",
        "gen_ai.output.messages",
        "gen_ai.output.type",
        "gen_ai.prompt.name",
        "gen_ai.prompt.variable",
        "gen_ai.prompt.variable.language",
        "gen_ai.prompt.variable.user_name",
        "gen_ai.prompt.version",
        "gen_ai.provider.name",
        "gen_ai.request.choice.count",
        "gen_ai.request.encoding_formats",
        "gen_ai.request.frequency_penalty",
        "gen_ai.request.max_tokens",
        "gen_ai.request.model",
        "gen_ai.request.presence_penalty",
        "gen_ai.request.previous_response.id",
        "gen_ai.request.reasoning.level",
        "gen_ai.request.seed",
        "gen_ai.request.stop_sequences",
        "gen_ai.request.stream",
        "gen_ai.request.stream_cursor",
        "gen_ai.request.temperature",
        "gen_ai.request.top_k",
        "gen_ai.request.top_p",
        "gen_ai.response.finish_reasons",
        "gen_ai.response.id",
        "gen_ai.response.model",
        "gen_ai.response.status",
        "gen_ai.response.time_to_first_chunk",
        "gen_ai.retrieval.documents",
        "gen_ai.retrieval.query.text",
        "gen_ai.retrieval.top_k",
        "gen_ai.system_instructions",
        "gen_ai.token.type",
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.id",
        "gen_ai.tool.call.result",
        "gen_ai.tool.definitions",
        "gen_ai.tool.description",
        "gen_ai.tool.name",
        "gen_ai.tool.type",
        "gen_ai.usage.audio.cache_read.input_tokens",
        "gen_ai.usage.audio.input_tokens",
        "gen_ai.usage.audio.output_tokens",
        "gen_ai.usage.cache_read.input_tokens",
        "gen_ai.usage.cache_write.input_tokens",
        "gen_ai.usage.image.cache_read.input_tokens",
        "gen_ai.usage.image.input_tokens",
        "gen_ai.usage.image.output_tokens",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.reasoning.output_tokens",
        "gen_ai.usage.text.cache_read.input_tokens",
        "gen_ai.usage.text.input_tokens",
        "gen_ai.usage.text.output_tokens",
        "gen_ai.workflow.name",
    }
)


_SHAPE = Shape(OtlpJsonPayloadError, "OTLP")


# This reader names a faulty field as "<owner> <key>", where `owner` is the label of the
# message the key was read from, so each call site below names its key once.
def _field(owner: str, key: str) -> str:
    return f"{owner} {key}"


def _text(payload: dict[str, object], key: str, owner: str) -> None:
    _SHAPE.text(payload, key, _field(owner, key))


def _flag(payload: dict[str, object], key: str, owner: str) -> None:
    _SHAPE.flag(payload, key, _field(owner, key))


def _number(payload: dict[str, object], key: str, owner: str) -> None:
    _SHAPE.number(payload, key, _field(owner, key))


# The checks below encode proto3 JSON's own rules, which no other reader shares.
def _decimal_text(
    payload: dict[str, object], key: str, owner: str, digits: re.Pattern[str], allowed: range
) -> None:
    # proto3 JSON writes 64-bit integers as decimal strings; `allowed` says signed or not.
    value = payload.get(key)
    # Derive the text bound from the signed/unsigned range before regex/int work.
    max_length = max(len(str(allowed.start)), len(str(allowed.stop - 1)))
    if not isinstance(value, str) or len(value) > max_length or digits.fullmatch(value) is None:
        _SHAPE.fail("INT64_TEXT_REQUIRED", _field(owner, key))
    if int(value) not in allowed:
        _SHAPE.fail("INT64_TEXT_REQUIRED", _field(owner, key))


def _int64_text(payload: dict[str, object], key: str, owner: str) -> None:
    # AnyValue.intValue.
    _decimal_text(payload, key, owner, _INT64, _INT64_RANGE)


def _uint64_text(payload: dict[str, object], key: str, owner: str) -> None:
    # fixed64 timestamps: unsigned decimal strings within range.
    _decimal_text(payload, key, owner, _UINT64, _UINT64_RANGE)


def _hex_id(payload: dict[str, object], key: str, pattern: re.Pattern[str], owner: str) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _SHAPE.fail("ID_REQUIRED", _field(owner, key))


def _enum(payload: dict[str, object], key: str, allowed: frozenset[int], owner: str) -> None:
    # Absent means 0 in proto3 JSON; present must be one of the pinned enum numbers.
    if key not in payload:
        return
    value = payload[key]
    if type(value) is not int or value not in allowed:
        _SHAPE.fail("ENUM_UNSUPPORTED", _field(owner, key))


# The shape each scalar member of the AnyValue oneof must have; arrayValue and
# kvlistValue are the two members that recurse, and _any_value checks those itself.
_ANY_VALUE_SCALARS = (
    ("stringValue", _text),
    ("bytesValue", _text),
    ("intValue", _int64_text),
    ("boolValue", _flag),
    ("doubleValue", _number),
)


def _any_value(value: object, label: str) -> None:
    body = _SHAPE.as_closed_object(value, _ANY_VALUE_KEYS, label)
    if _DICTIONARY_KEYS & body.keys():
        _SHAPE.fail("DICTIONARY_ENCODING_UNSUPPORTED", label)
    if len(_ANY_VALUE_ONEOF & body.keys()) != 1:
        _SHAPE.fail("ANY_VALUE_REQUIRED", label)
    for key, check in _ANY_VALUE_SCALARS:
        if key in body:
            check(body, key, label)
    if "arrayValue" in body:
        owner = f"{label} arrayValue"
        items = _SHAPE.as_closed_object(body["arrayValue"], _VALUES_KEYS, owner)
        for index, item in enumerate(_SHAPE.as_array(items.get("values", []), owner)):
            _any_value(item, f"{label}[{index}]")
    if "kvlistValue" in body:
        items = _SHAPE.as_closed_object(body["kvlistValue"], _VALUES_KEYS, f"{label} kvlistValue")
        _attributes(items, f"{label} kvlistValue", field="values")


def _attributes(parent: dict[str, object], owner: str, field: str = "attributes") -> None:
    # The repeated KeyValue of a message: `attributes` everywhere, `values` in a kvlist.
    for index, item in enumerate(_SHAPE.as_array(parent.get(field, []), f"{owner} attributes")):
        label = f"{owner} attribute {index}"
        pair = _SHAPE.as_closed_object(item, _KEY_VALUE_KEYS, label)
        if _DICTIONARY_KEYS & pair.keys():
            _SHAPE.fail("DICTIONARY_ENCODING_UNSUPPORTED", label)
        key = pair.get("key")
        if not isinstance(key, str) or not key:
            _SHAPE.fail("TEXT_REQUIRED", _field(label, "key"))
        # The whole gen_ai namespace is pinned: the bare name and every dotted key.
        if (key == "gen_ai" or key.startswith("gen_ai.")) and key not in GEN_AI_ATTRIBUTE_KEYS:
            _SHAPE.fail("GEN_AI_ATTRIBUTE_UNKNOWN", key)
        _any_value(pair.get("value"), f"{label} value")


def _span(value: object, owner: str) -> None:
    span = _SHAPE.as_closed_object(value, _SPAN_KEYS, owner)
    _hex_id(span, "traceId", _TRACE_ID, owner)
    _hex_id(span, "spanId", _SPAN_ID, owner)
    # A root span carries no parentSpanId (proto3 omits the empty bytes).
    if "parentSpanId" in span:
        _hex_id(span, "parentSpanId", _SPAN_ID, owner)
    _text(span, "name", owner)
    _enum(span, "kind", _SPAN_KINDS, owner)
    _uint64_text(span, "startTimeUnixNano", owner)
    _uint64_text(span, "endTimeUnixNano", owner)
    _attributes(span, owner)
    for index, item in enumerate(_SHAPE.as_array(span.get("events", []), f"{owner} events")):
        label = f"{owner} event {index}"
        event = _SHAPE.as_closed_object(item, _EVENT_KEYS, label)
        _uint64_text(event, "timeUnixNano", label)
        _text(event, "name", label)
        _attributes(event, label)
    for index, item in enumerate(_SHAPE.as_array(span.get("links", []), f"{owner} links")):
        label = f"{owner} link {index}"
        link = _SHAPE.as_closed_object(item, _LINK_KEYS, label)
        _hex_id(link, "traceId", _TRACE_ID, label)
        _hex_id(link, "spanId", _SPAN_ID, label)
        _attributes(link, label)
    if "status" in span:
        status = _SHAPE.as_closed_object(span["status"], _STATUS_KEYS, f"{owner} status")
        _enum(status, "code", _STATUS_CODES, f"{owner} status")


def _scope_spans(value: object, owner: str) -> None:
    scope_spans = _SHAPE.as_closed_object(value, _SCOPE_SPANS_KEYS, owner)
    if "scope" in scope_spans:
        label = f"{owner} scope"
        _attributes(_SHAPE.as_closed_object(scope_spans["scope"], _SCOPE_KEYS, label), label)
    for index, item in enumerate(_SHAPE.as_array(scope_spans.get("spans"), f"{owner} spans")):
        _span(item, f"{owner}.spans[{index}]")


def _resource_spans(value: object, owner: str) -> None:
    resource_spans = _SHAPE.as_closed_object(value, _RESOURCE_SPANS_KEYS, owner)
    if "resource" in resource_spans:
        label = f"{owner} resource"
        resource = _SHAPE.as_closed_object(resource_spans["resource"], _RESOURCE_KEYS, label)
        _attributes(resource, label)
    scope_spans = _SHAPE.as_array(resource_spans.get("scopeSpans"), f"{owner} scopeSpans")
    for index, item in enumerate(scope_spans):
        _scope_spans(item, f"{owner}.scopeSpans[{index}]")


def read_traces(value: object) -> Projection:
    """Preserve one OTLP/JSON trace export; spans, attributes and GenAI claims stay as written."""
    payload = _SHAPE.as_closed_object(value, _REQUEST_KEYS, "export")
    for index, item in enumerate(_SHAPE.as_array(payload.get("resourceSpans"), "resourceSpans")):
        _resource_spans(item, f"resourceSpans[{index}]")
    return Projection("eval_run", payload)


CODECS: Mapping[tuple[str, str], Codec] = MappingProxyType(
    {("otlp-json.traces", "0.160.0"): read_traces}
)

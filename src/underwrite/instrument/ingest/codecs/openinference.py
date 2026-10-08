"""Read an explicitly selected OpenInference trace export (OTLP/JSON envelope from the
OpenTelemetry Collector 0.160.0 file exporter, OpenInference attribute conventions
0.1.38); every attribute value stays a source claim.

The envelope is exactly the OTLP/JSON profile and is validated by that codec, so the
proto rules live in one place. On top of it this codec pins the OpenInference vocabulary:
every span must carry ``openinference.span.kind`` with one of the spec's kinds, and every
attribute key in an OpenInference namespace must be a flat attribute of the pinned
``openinference-semantic-conventions`` package or match its indexed-prefix grammar
(``<list>.<index>.<item attribute>``, nested where the spec nests). Keys in other
namespaces (``service.*``, ``gen_ai.*``,...) pass through unchecked. The spec has no
version tag; the pin is the Python package release, whose tag names the spec commit."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from underwrite.instrument.ingest.codecs import otlp_json
from underwrite.instrument.ingest.codecs._shape import is_dict, is_list
from underwrite.instrument.ingest.project import Projection
from underwrite.instrument.ingest.transport import Codec


class OpenInferencePayloadError(ValueError):
    """A selected OpenInference export lacks the pinned minimal consumable structure."""


_VERSION = "0.1.38"
# Attribute constants of openinference-semantic-conventions 0.1.38 (tag
# python-openinference-semantic-conventions-v0.1.38 -> 5df9166304f9bf3ba93a146c879c25b7d43d0eba),
# extracted from the package's `openinference.semconv.trace` classes, not typed by hand.
_SPAN_FLAT = frozenset(
    {
        "agent.name",
        "annotations",
        "embedding.embeddings",
        "embedding.invocation_parameters",
        "embedding.model_name",
        "evaluations",
        "graph.node.id",
        "graph.node.name",
        "graph.node.parent_id",
        "input.images",
        "input.mime_type",
        "input.value",
        "llm.choices",
        "llm.cost.completion",
        "llm.cost.completion_details",
        "llm.cost.completion_details.audio",
        "llm.cost.completion_details.output",
        "llm.cost.completion_details.reasoning",
        "llm.cost.prompt",
        "llm.cost.prompt_details",
        "llm.cost.prompt_details.audio",
        "llm.cost.prompt_details.cache_input",
        "llm.cost.prompt_details.cache_read",
        "llm.cost.prompt_details.cache_write",
        "llm.cost.prompt_details.input",
        "llm.cost.total",
        "llm.finish_reason",
        "llm.function_call",
        "llm.input_messages",
        "llm.invocation_parameters",
        "llm.model_name",
        "llm.output_messages",
        "llm.prompt_template.template",
        "llm.prompt_template.variables",
        "llm.prompt_template.version",
        "llm.prompts",
        "llm.provider",
        "llm.request.model_name",
        "llm.response.model_name",
        "llm.system",
        "llm.token_count.completion",
        "llm.token_count.completion_details.audio",
        "llm.token_count.completion_details.reasoning",
        "llm.token_count.prompt",
        "llm.token_count.prompt_details",
        "llm.token_count.prompt_details.audio",
        "llm.token_count.prompt_details.cache_input",
        "llm.token_count.prompt_details.cache_read",
        "llm.token_count.prompt_details.cache_write",
        "llm.token_count.total",
        "llm.tools",
        "metadata",
        "openinference.span.kind",
        "output.images",
        "output.mime_type",
        "output.value",
        "prompt.id",
        "prompt.url",
        "prompt.vendor",
        "retrieval.documents",
        "session.annotations",
        "session.evaluations",
        "session.id",
        "tag.tags",
        "tool.description",
        "tool.id",
        "tool.name",
        "tool.parameters",
        "trace.annotations",
        "trace.evaluations",
        "user.id",
        "openinference.project.name",
        "reranker.input_documents",
        "reranker.model_name",
        "reranker.output_documents",
        "reranker.query",
        "reranker.top_k",
    }
)
_MESSAGE_FLAT = frozenset(
    {
        "message.content",
        "message.contents",
        "message.function_call_arguments_json",
        "message.function_call_name",
        "message.name",
        "message.role",
        "message.tool_call_id",
        "message.tool_calls",
    }
)
_CONTENT_FLAT = frozenset(
    {
        "message_content.audio",
        "message_content.data",
        "message_content.encrypted_content",
        "message_content.id",
        "message_content.image",
        "message_content.signature",
        "message_content.text",
        "message_content.type",
        "message_content.video",
        "tool_call.function.arguments",
        "tool_call.function.name",
        "tool_call.id",
        "tool_call.reasoning_signature",
    }
)
_TOOL_CALL = frozenset(
    {
        "tool_call.function.arguments",
        "tool_call.function.name",
        "tool_call.id",
        "tool_call.reasoning_signature",
    }
)
_TOOL = frozenset(
    {
        "tool.description",
        "tool.json_schema",
        "tool.name",
    }
)
_PROMPT = frozenset(
    {
        "prompt.text",
    }
)
_CHOICE = frozenset(
    {
        "completion.text",
    }
)
_DOCUMENT = frozenset(
    {
        "document.content",
        "document.id",
        "document.metadata",
        "document.score",
    }
)
_EMBEDDING = frozenset(
    {
        "embedding.text",
        "embedding.vector",
    }
)
_IMAGE = frozenset(
    {
        "image.url",
    }
)
_AUDIO = frozenset(
    {
        "audio.mime_type",
        "audio.transcript",
        "audio.url",
    }
)
_VIDEO = frozenset(
    {
        "video.url",
    }
)
_ANNOTATION = frozenset(
    {
        "annotation.annotator_kind",
        "annotation.explanation",
        "annotation.identifier",
        "annotation.label",
        "annotation.metadata",
        "annotation.name",
        "annotation.score",
    }
)
_EVALUATION = frozenset(
    {
        "evaluation.annotator_kind",
        "evaluation.explanation",
        "evaluation.identifier",
        "evaluation.label",
        "evaluation.metadata",
        "evaluation.name",
        "evaluation.score",
    }
)
SPAN_KINDS = frozenset(
    {
        "AGENT",
        "CHAIN",
        "EMBEDDING",
        "EVALUATOR",
        "GUARDRAIL",
        "LLM",
        "PROMPT",
        "RERANKER",
        "RETRIEVER",
        "TOOL",
        "UNKNOWN",
    }
)


def _under(key: str, prefix: str) -> str:
    """What follows ``prefix.`` in ``key``; empty when the key is not under that prefix."""
    return key[len(prefix) + 1 :] if key.startswith(prefix + ".") else ""


_NO_NESTING: Mapping[str, _Vocabulary] = MappingProxyType({})


class _Vocabulary:
    """Flat keys with their permitted indexed lists and nested objects.

    MappingProxyType fields prevent mutation of the configured vocabulary."""

    __slots__ = ("flat", "lists", "objects")

    flat: frozenset[str]
    lists: Mapping[str, _Vocabulary]
    objects: Mapping[str, _Vocabulary]

    def __init__(
        self,
        flat: frozenset[str],
        lists: Mapping[str, _Vocabulary] = _NO_NESTING,
        objects: Mapping[str, _Vocabulary] = _NO_NESTING,
    ) -> None:
        self.flat = flat
        self.lists = lists
        self.objects = objects

    def knows(self, key: str) -> bool:
        if key in self.flat:
            return True
        for prefix, item in self.lists.items():
            index, dot, rest = _under(key, prefix).partition(".")
            if _is_index(index) and dot and item.knows(rest):
                return True
        return any(nested.knows(_under(key, prefix)) for prefix, nested in self.objects.items())


_IMAGE_V = _Vocabulary(_IMAGE)
_CONTENT_V = _Vocabulary(
    _CONTENT_FLAT,
    objects=MappingProxyType(
        {
            "message_content.image": _IMAGE_V,
            "message_content.audio": _Vocabulary(_AUDIO),
            "message_content.video": _Vocabulary(_VIDEO),
        }
    ),
)
_MESSAGE_V = _Vocabulary(
    _MESSAGE_FLAT,
    lists=MappingProxyType(
        {"message.contents": _CONTENT_V, "message.tool_calls": _Vocabulary(_TOOL_CALL)}
    ),
)
_DOCUMENT_V = _Vocabulary(_DOCUMENT)
_ANNOTATION_V = _Vocabulary(_ANNOTATION)
_EVALUATION_V = _Vocabulary(_EVALUATION)
SPAN_VOCABULARY = _Vocabulary(
    _SPAN_FLAT,
    lists=MappingProxyType(
        {
            "llm.input_messages": _MESSAGE_V,
            "llm.output_messages": _MESSAGE_V,
            "llm.prompts": _Vocabulary(_PROMPT),
            "llm.choices": _Vocabulary(_CHOICE),
            "llm.tools": _Vocabulary(_TOOL),
            "retrieval.documents": _DOCUMENT_V,
            "embedding.embeddings": _Vocabulary(_EMBEDDING),
            "reranker.input_documents": _DOCUMENT_V,
            "reranker.output_documents": _DOCUMENT_V,
            "input.images": _IMAGE_V,
            "output.images": _IMAGE_V,
            "annotations": _ANNOTATION_V,
            "evaluations": _EVALUATION_V,
            "trace.annotations": _ANNOTATION_V,
            "trace.evaluations": _EVALUATION_V,
            "session.annotations": _ANNOTATION_V,
            "session.evaluations": _EVALUATION_V,
        }
    ),
)


def _namespaces(vocabulary: _Vocabulary) -> set[str]:
    """Every first path segment a vocabulary owns, at each depth it nests."""
    owned = {key.split(".")[0] for key in vocabulary.flat}
    for nested in (*vocabulary.lists.values(), *vocabulary.objects.values()):
        owned |= _namespaces(nested)
    return owned


# First path segments the OpenInference conventions own; keys under them must be known.
CLAIMED_NAMESPACES = frozenset(_namespaces(SPAN_VOCABULARY))


def _is_index(segment: str) -> bool:
    # The spec's zero-based integer index: ASCII digits, no leading zeros.
    return segment.isascii() and segment.isdigit() and (segment == "0" or segment[0] != "0")


def _objects(parent: dict[str, object], name: str) -> list[dict[str, object]]:
    """The objects of one repeated field; the envelope codec has refused any other shape."""
    # Absence is the proto3 default for a repeated field, so an empty result is normal.
    value = parent.get(name)
    return [item for item in value if is_dict(item)] if is_list(value) else []


def _attributes(parent: dict[str, object], owner: str) -> dict[str, object]:
    """Check the owned keys of one attribute list; return key -> AnyValue for the caller."""
    seen: dict[str, object] = {}
    for item in _objects(parent, "attributes"):
        key = str(item.get("key"))
        if key.split(".")[0] in CLAIMED_NAMESPACES and not SPAN_VOCABULARY.knows(key):
            raise OpenInferencePayloadError(f"OPENINFERENCE_ATTRIBUTE_UNKNOWN: {owner} {key}")
        seen[key] = item.get("value")
    return seen


def _span(span: dict[str, object], label: str) -> None:
    kind = _attributes(span, label).get("openinference.span.kind")
    if not is_dict(kind) or "stringValue" not in kind:
        raise OpenInferencePayloadError(f"OPENINFERENCE_SPAN_KIND_REQUIRED: {label}")
    if kind["stringValue"] not in SPAN_KINDS:
        raise OpenInferencePayloadError(f"OPENINFERENCE_SPAN_KIND_UNSUPPORTED: {label}")
    for index, event in enumerate(_objects(span, "events")):
        _attributes(event, f"{label} event {index}")


def read_traces(value: object) -> Projection:
    """Preserve one OpenInference export; the OTLP envelope is checked by the OTLP codec."""
    try:
        payload = otlp_json.read_traces(value).payload
    except otlp_json.OtlpJsonPayloadError as error:
        reason = f"OPENINFERENCE_ENVELOPE_INVALID: {error.args[0]}"
        raise OpenInferencePayloadError(reason) from error
    for r_index, resource_spans in enumerate(_objects(payload, "resourceSpans")):
        resource = resource_spans.get("resource")
        if is_dict(resource):
            _attributes(resource, f"resourceSpans[{r_index}] resource")
        for s_index, scope_spans in enumerate(_objects(resource_spans, "scopeSpans")):
            for index, span in enumerate(_objects(scope_spans, "spans")):
                _span(span, f"resourceSpans[{r_index}].scopeSpans[{s_index}].spans[{index}]")
    return Projection("eval_run", payload)


CODECS: Mapping[tuple[str, str], Codec] = MappingProxyType(
    {("openinference.traces", _VERSION): read_traces}
)

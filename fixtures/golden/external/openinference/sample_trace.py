"""Minimal OpenInference sender for the underwrite golden capture.

One trace shaped after the OpenInference semantic conventions: an AGENT span that wraps a
RETRIEVER span, an LLM span (flattened input/output messages, a tool call, token counts)
and a TOOL span. Attribute names are taken from the pinned
`openinference-semantic-conventions` package, not typed by hand; no OpenInference
instrumentor or model provider is involved, and every value is fixed. Spans go to a local
OpenTelemetry Collector over OTLP/HTTP on loopback and are exported in a single batch
(`force_flush` once), so the collector's file exporter writes exactly one JSON document.

Run with::

    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4318/v1/traces python sample_trace.py
"""

import json

from openinference.semconv.trace import (
    DocumentAttributes,
    MessageAttributes,
    OpenInferenceMimeTypeValues,
    OpenInferenceSpanKindValues,
    SpanAttributes,
    ToolAttributes,
    ToolCallAttributes,
)
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import StatusCode

KIND = SpanAttributes.OPENINFERENCE_SPAN_KIND
QUESTION = "대한민국의 수도는 어디입니까?"
ANSWER = "대한민국의 수도는 서울입니다."


def message(prefix: str, index: int, role: str, content: str) -> dict[str, str]:
    base = f"{prefix}.{index}"
    return {
        f"{base}.{MessageAttributes.MESSAGE_ROLE}": role,
        f"{base}.{MessageAttributes.MESSAGE_CONTENT}": content,
    }


def document(index: int, suffix: str) -> str:
    return f"{SpanAttributes.RETRIEVAL_DOCUMENTS}.{index}.{suffix}"


def tool_call(message_index: int, call_index: int, suffix: str) -> str:
    calls = MessageAttributes.MESSAGE_TOOL_CALLS
    return f"{SpanAttributes.LLM_OUTPUT_MESSAGES}.{message_index}.{calls}.{call_index}.{suffix}"


TOOL_CALL_ARGUMENTS = json.dumps({"country": "대한민국"}, ensure_ascii=False)


def main() -> None:
    resource = Resource.create(
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("underwrite.capture", "0.1.0")

    with tracer.start_as_current_span(
        "underwrite-capture-agent",
        attributes={
            KIND: OpenInferenceSpanKindValues.AGENT.value,
            SpanAttributes.AGENT_NAME: "underwrite-capture-agent",
            SpanAttributes.INPUT_VALUE: QUESTION,
            SpanAttributes.INPUT_MIME_TYPE: OpenInferenceMimeTypeValues.TEXT.value,
            SpanAttributes.OUTPUT_VALUE: ANSWER,
            SpanAttributes.OUTPUT_MIME_TYPE: OpenInferenceMimeTypeValues.TEXT.value,
            SpanAttributes.SESSION_ID: "session_underwrite_capture_0001",
            SpanAttributes.USER_ID: "user_capture",
            SpanAttributes.METADATA: json.dumps({"purpose": "golden capture"}),
        },
    ) as agent:
        with tracer.start_as_current_span(
            "lookup-documents",
            attributes={
                KIND: OpenInferenceSpanKindValues.RETRIEVER.value,
                SpanAttributes.INPUT_VALUE: QUESTION,
                document(0, DocumentAttributes.DOCUMENT_ID): "doc-kr-capital",
                document(0, DocumentAttributes.DOCUMENT_CONTENT): "서울은 대한민국의 수도이다.",
                document(0, DocumentAttributes.DOCUMENT_SCORE): 0.92,
            },
        ) as retriever:
            retriever.set_status(StatusCode.OK)
        with tracer.start_as_current_span(
            "chat mock-model",
            attributes={
                KIND: OpenInferenceSpanKindValues.LLM.value,
                SpanAttributes.LLM_MODEL_NAME: "mock-model",
                SpanAttributes.LLM_PROVIDER: "underwrite.mock",
                SpanAttributes.LLM_SYSTEM: "underwrite.mock",
                SpanAttributes.LLM_INVOCATION_PARAMETERS: json.dumps(
                    {"temperature": 0.0, "max_tokens": 64, "seed": 7}
                ),
                **message(SpanAttributes.LLM_INPUT_MESSAGES, 0, "system", "간결하게 답하십시오."),
                **message(SpanAttributes.LLM_INPUT_MESSAGES, 1, "user", QUESTION),
                **message(SpanAttributes.LLM_OUTPUT_MESSAGES, 0, "assistant", ANSWER),
                tool_call(0, 0, ToolCallAttributes.TOOL_CALL_ID): "call_underwrite_0001",
                tool_call(0, 0, ToolCallAttributes.TOOL_CALL_FUNCTION_NAME): "lookup_capital",
                tool_call(
                    0, 0, ToolCallAttributes.TOOL_CALL_FUNCTION_ARGUMENTS_JSON
                ): TOOL_CALL_ARGUMENTS,
                f"{SpanAttributes.LLM_TOOLS}.0.{ToolAttributes.TOOL_JSON_SCHEMA}": json.dumps(
                    {"type": "function", "function": {"name": "lookup_capital"}}
                ),
                SpanAttributes.LLM_TOKEN_COUNT_PROMPT: 21,
                SpanAttributes.LLM_TOKEN_COUNT_COMPLETION: 9,
                SpanAttributes.LLM_TOKEN_COUNT_TOTAL: 30,
                SpanAttributes.INPUT_VALUE: QUESTION,
                SpanAttributes.OUTPUT_VALUE: ANSWER,
            },
        ) as llm:
            llm.set_status(StatusCode.OK)
        with tracer.start_as_current_span(
            "lookup_capital",
            attributes={
                KIND: OpenInferenceSpanKindValues.TOOL.value,
                SpanAttributes.TOOL_NAME: "lookup_capital",
                SpanAttributes.TOOL_DESCRIPTION: "Returns the capital city of a country.",
                SpanAttributes.TOOL_PARAMETERS: TOOL_CALL_ARGUMENTS,
                SpanAttributes.INPUT_VALUE: TOOL_CALL_ARGUMENTS,
                SpanAttributes.INPUT_MIME_TYPE: OpenInferenceMimeTypeValues.JSON.value,
                SpanAttributes.OUTPUT_VALUE: json.dumps({"capital": "서울"}, ensure_ascii=False),
                SpanAttributes.OUTPUT_MIME_TYPE: OpenInferenceMimeTypeValues.JSON.value,
            },
        ) as tool:
            tool.set_status(StatusCode.OK)
        agent.set_status(StatusCode.OK)

    assert provider.force_flush(timeout_millis=10_000)
    provider.shutdown()


if __name__ == "__main__":
    main()

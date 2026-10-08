"""Minimal OTLP sender for the underwrite golden capture.

One trace shaped after the pinned GenAI semantic conventions
(open-telemetry/semantic-conventions-genai, commit in SOURCE.md):
`invoke_agent` (agent span) -> `chat {model}` (inference span) -> `execute_tool {tool}`.
No model or network call is made: every attribute is a fixed value written by this
script, and the spans go to a local OpenTelemetry Collector over OTLP/HTTP on loopback.
All spans are exported in a single batch (`force_flush` once), so the collector's file
exporter writes exactly one JSON document.

Run with::

    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4318/v1/traces python sample_trace.py
"""

import json

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, StatusCode

INPUT_MESSAGES = [
    {"role": "system", "parts": [{"type": "text", "content": "간결하게 답하십시오."}]},
    {"role": "user", "parts": [{"type": "text", "content": "대한민국의 수도는 어디입니까?"}]},
]
OUTPUT_MESSAGES = [
    {
        "role": "assistant",
        "parts": [
            {
                "type": "tool_call",
                "id": "call_underwrite_0001",
                "name": "lookup_capital",
                "arguments": {"country": "대한민국"},
            },
            {"type": "text", "content": "대한민국의 수도는 서울입니다."},
        ],
        "finish_reason": "stop",
    }
]


def main() -> None:
    resource = Resource.create(
        {
            "service.name": "underwrite-capture",
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("underwrite.capture", "0.1.0")

    with tracer.start_as_current_span(
        "invoke_agent underwrite-capture-agent",
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.provider.name": "underwrite.mock",
            "gen_ai.agent.name": "underwrite-capture-agent",
            "gen_ai.agent.id": "agent_underwrite_capture",
            "gen_ai.agent.description": "Deterministic capture agent; no model call is made.",
            "gen_ai.conversation.id": "conv_underwrite_capture_0001",
            "gen_ai.request.model": "mock-model",
        },
    ) as agent:
        with tracer.start_as_current_span(
            "chat mock-model",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "underwrite.mock",
                "gen_ai.request.model": "mock-model",
                "gen_ai.request.temperature": 0.0,
                "gen_ai.request.max_tokens": 64,
                "gen_ai.request.seed": 7,
                "gen_ai.response.model": "mock-model-2026-09",
                "gen_ai.response.id": "resp_underwrite_capture_0001",
                "gen_ai.response.finish_reasons": ["stop"],
                "gen_ai.usage.input_tokens": 21,
                "gen_ai.usage.output_tokens": 9,
                "gen_ai.output.type": "text",
                "gen_ai.input.messages": json.dumps(INPUT_MESSAGES, ensure_ascii=False),
                "gen_ai.output.messages": json.dumps(OUTPUT_MESSAGES, ensure_ascii=False),
                "server.address": "mock.invalid",
                "server.port": 443,
            },
        ) as chat:
            chat.set_status(StatusCode.OK)
            with tracer.start_as_current_span(
                "execute_tool lookup_capital",
                kind=SpanKind.INTERNAL,
                attributes={
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": "lookup_capital",
                    "gen_ai.tool.type": "function",
                    "gen_ai.tool.call.id": "call_underwrite_0001",
                    "gen_ai.tool.description": "Returns the capital city of a country.",
                    "gen_ai.tool.call.arguments": json.dumps(
                        {"country": "대한민국"}, ensure_ascii=False
                    ),
                    "gen_ai.tool.call.result": json.dumps({"capital": "서울"}, ensure_ascii=False),
                },
            ) as tool:
                tool.add_event("tool.resolved", {"capital": "서울"})
                tool.set_status(StatusCode.OK)
        agent.set_status(StatusCode.OK)

    assert provider.force_flush(timeout_millis=10_000)
    provider.shutdown()


if __name__ == "__main__":
    main()

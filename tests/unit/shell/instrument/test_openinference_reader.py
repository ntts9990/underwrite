"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from _external_formats import ingestor, receive
from _repo_paths import repo_root

from underwrite.instrument.ingest.codecs import otlp_json
from underwrite.instrument.ingest.codecs.openinference import (
    CODECS,
    SPAN_KINDS,
    SPAN_VOCABULARY,
    OpenInferencePayloadError,
    read_traces,
)
from underwrite.instrument.ingest.project import Source
from underwrite.instrument.ingest.transport import Ingestor, UnsupportedFormat

FORMAT = "openinference.traces"
VERSION = "0.1.38"
ROOT = repo_root(Path(__file__))
GOLDEN = ROOT / "fixtures/golden/external/openinference"
CAPTURE = GOLDEN / "capture.json"
SOURCE = Source(FORMAT, VERSION, "urn:test:openinference")


def _spans(export: object) -> list[dict[str, object]]:
    """The spans of the single resource/scope batch every export in this file carries."""
    resource_spans = cast(list[dict[str, object]], cast(dict[str, object], export)["resourceSpans"])
    scope_spans = cast(list[dict[str, object]], resource_spans[0]["scopeSpans"])
    return cast(list[dict[str, object]], scope_spans[0]["spans"])


def _attrs(span: dict[str, object]) -> dict[str, object]:
    """One span's attribute list as key -> AnyValue, in the order the capture wrote it."""
    return {str(a["key"]): a["value"] for a in cast(list[dict[str, object]], span["attributes"])}


# Read the capture once: every malformed case below is the real export with one change.
EXPORT = cast(dict[str, object], json.loads(CAPTURE.read_bytes()))
RESOURCE_SPANS = cast(list[dict[str, object]], EXPORT["resourceSpans"])[0]
RESOURCE = cast(dict[str, object], RESOURCE_SPANS["resource"])
SCOPE_SPANS = cast(list[dict[str, object]], RESOURCE_SPANS["scopeSpans"])[0]
SPANS = _spans(EXPORT)
LLM_SPAN = next(span for span in SPANS if str(span["name"]).startswith("chat"))
SPAN0 = "resourceSpans[0].scopeSpans[0].spans[0]"


def _ingestor() -> Ingestor:
    return ingestor(CODECS)


def _receive(payload: object) -> dict[str, object]:
    return receive(_ingestor(), payload, SOURCE, "captured:http-body-not-a-request")


def _attr(key: str, value: object) -> dict[str, object]:
    return {"key": key, "value": value}


def _text(key: str, value: str) -> dict[str, object]:
    return _attr(key, {"stringValue": value})


def _spans_export(spans: list[object]) -> dict[str, object]:
    return {"resourceSpans": [{**RESOURCE_SPANS, "scopeSpans": [{**SCOPE_SPANS, "spans": spans}]}]}


def _span_export(**overrides: object) -> dict[str, object]:
    return _spans_export([{**LLM_SPAN, **overrides}])


def _attrs_export(*attributes: dict[str, object]) -> dict[str, object]:
    """The LLM span with its kind kept and the given attributes replacing the rest."""
    kind = _text("openinference.span.kind", "LLM")
    return _span_export(attributes=[kind, *attributes])


def test_registry_pins_exactly_one_profile() -> None:
    assert dict(CODECS) == {(FORMAT, VERSION): read_traces}


def test_fixture_record_binds_the_codec_profile() -> None:
    record = json.loads((GOLDEN / "observations.json").read_text(encoding="utf-8"))
    entry = record["files"][CAPTURE.name]
    assert entry["source"] == {"format": FORMAT, "format_version": VERSION}
    assert record["tool"]["version"] == VERSION


def test_real_capture_payload_is_the_whole_document() -> None:
    # File/body identity, kind, source and raw hash are the matrix's claim for every
    # manifest entry; this reader states only how its codec wraps the whole document.
    raw = CAPTURE.read_bytes()
    assert raw.count(b"\n") <= 1
    ingestor = _ingestor()
    observation = ingestor.file(CAPTURE, SOURCE).observation
    assert observation["payload"] == json.loads(raw)


def test_real_capture_keeps_kinds_messages_and_korean_text_as_written() -> None:
    spans = _spans(_receive(EXPORT)["payload"])
    kinds = {str(span["name"]): _attrs(span)["openinference.span.kind"] for span in spans}
    assert kinds == {
        "underwrite-capture-agent": {"stringValue": "AGENT"},
        "lookup-documents": {"stringValue": "RETRIEVER"},
        "chat mock-model": {"stringValue": "LLM"},
        "lookup_capital": {"stringValue": "TOOL"},
    }
    llm = _attrs(LLM_SPAN)
    assert llm["llm.token_count.prompt"] == {"intValue": "21"}
    assert "대한민국" in str(llm["llm.input_messages.1.message.content"])
    assert llm["llm.output_messages.0.message.tool_calls.0.tool_call.function.name"] == {
        "stringValue": "lookup_capital"
    }


def test_same_file_is_also_a_valid_otlp_json_export() -> None:
    # The envelope is shared: the OTLP/JSON codec accepts the OpenInference capture too.
    assert otlp_json.read_traces(EXPORT).payload == EXPORT


@pytest.mark.parametrize(
    "selector",
    [
        (FORMAT, "0.1.37"),
        (FORMAT, "latest"),
        ("openinference", VERSION),
        ("otlp-json.traces", VERSION),
    ],
)
def test_unpinned_selector_is_unsupported(selector: tuple[str, str]) -> None:
    with pytest.raises(UnsupportedFormat):
        _ingestor().file(CAPTURE, Source(selector[0], selector[1], "urn:test"))


@pytest.mark.parametrize(
    "key",
    [
        "llm.input_messages.3.message.contents.0.message_content.image.image.url",
        "llm.output_messages.0.message.tool_calls.1.tool_call.reasoning_signature",
        "llm.input_messages.0.message.contents.2.tool_call.function.arguments",
        "reranker.output_documents.0.document.metadata",
        "embedding.embeddings.0.embedding.vector",
        "session.evaluations.0.evaluation.score",
        "llm.prompts.0.prompt.text",
        "llm.choices.1.completion.text",
        "output.images.0.image.url",
        "openinference.project.name",
        "reranker.top_k",
    ],
)
def test_indexed_and_nested_keys_of_the_pinned_grammar_are_known(key: str) -> None:
    assert SPAN_VOCABULARY.knows(key)
    payload = _receive(_attrs_export(_text(key, "x")))["payload"]
    assert cast(dict[str, object], payload)["resourceSpans"]


@pytest.mark.parametrize(
    "key",
    [
        "gen_ai.request.model",
        "service.name",
        "telemetry.sdk.language",
        "server.address",
        "custom.vendor.flag",
    ],
)
def test_keys_outside_openinference_namespaces_pass_through(key: str) -> None:
    payload = _receive(_attrs_export(_text(key, "x")))["payload"]
    assert cast(dict[str, object], payload)["resourceSpans"]


def test_every_flat_attribute_and_every_kind_is_accepted_as_written() -> None:
    attributes = [
        _text(key, key) for key in sorted(SPAN_VOCABULARY.flat) if key != "openinference.span.kind"
    ]
    spans: list[object] = [
        {**LLM_SPAN, "attributes": [_text("openinference.span.kind", kind), *attributes]}
        for kind in sorted(SPAN_KINDS)
    ]
    export = _spans_export(spans)
    assert _receive(export)["payload"] == export


NESTED_CALL = "llm.output_messages.0.message.tool_calls.0.tool_call.name"  # `.name` is not a key
NESTED_IMAGE = (
    "llm.input_messages.0.message.contents.0.message_content.image.url"  # needs image.url
)
KIND_REQUIRED = f"OPENINFERENCE_SPAN_KIND_REQUIRED: {SPAN0}"
KIND_UNSUPPORTED = f"OPENINFERENCE_SPAN_KIND_UNSUPPORTED: {SPAN0}"
# Keys the pinned grammar does not know, each written as the one attribute of the LLM span;
# the expected reason is derived from the key, so no case can drift from the one it names.
UNKNOWN_KEYS: dict[str, tuple[str, str]] = {
    "flat_key_unknown": ("llm.model", "m"),
    "flat_key_wrong_case": ("llm.model_Name", "m"),
    "indexed_key_not_numeric": ("llm.input_messages.first.message.role", "user"),
    "indexed_key_unknown_item": ("llm.input_messages.0.message.text", "x"),
    "indexed_key_no_item": ("llm.input_messages.0", "x"),
    "nested_key_unknown": (NESTED_CALL, "x"),
    "nested_object_unknown": (NESTED_IMAGE, "x"),
    "document_key_unknown": ("retrieval.documents.0.document.text", "x"),
    "bare_namespace_key": ("llm", "x"),
    "index_arabic_indic_digit": ("llm.input_messages.\u0663.message.role", "user"),
    "index_superscript": ("llm.input_messages.\u00b2.message.role", "user"),
    "index_leading_zero": ("llm.input_messages.007.message.role", "user"),
    "tool_key_unknown": ("tool.input", "x"),
}
MALFORMED: dict[str, tuple[object, str]] = {
    **{
        name: (_attrs_export(_text(key, value)), f"OPENINFERENCE_ATTRIBUTE_UNKNOWN: {SPAN0} {key}")
        for name, (key, value) in UNKNOWN_KEYS.items()
    },
    "envelope_root_not_object": (
        [],
        "OPENINFERENCE_ENVELOPE_INVALID: OTLP_OBJECT_REQUIRED: export",
    ),
    "envelope_unknown_field": (
        {**EXPORT, "resourceLogs": []},
        "OPENINFERENCE_ENVELOPE_INVALID: OTLP_UNKNOWN_FIELD: export",
    ),
    "envelope_span_id_short": (
        _span_export(spanId="ab"),
        f"OPENINFERENCE_ENVELOPE_INVALID: OTLP_ID_REQUIRED: {SPAN0} spanId",
    ),
    "envelope_gen_ai_unknown": (
        _attrs_export(_text("gen_ai.made.up", "x")),
        "OPENINFERENCE_ENVELOPE_INVALID: OTLP_GEN_AI_ATTRIBUTE_UNKNOWN: gen_ai.made.up",
    ),
    "span_kind_missing": (_span_export(attributes=[_text("llm.model_name", "m")]), KIND_REQUIRED),
    "span_kind_not_text": (
        _span_export(attributes=[_attr("openinference.span.kind", {"intValue": "1"})]),
        KIND_REQUIRED,
    ),
    "span_kind_lowercase": (
        _span_export(attributes=[_text("openinference.span.kind", "llm")]),
        KIND_UNSUPPORTED,
    ),
    "span_kind_other": (
        _span_export(attributes=[_text("openinference.span.kind", "WORKFLOW")]),
        KIND_UNSUPPORTED,
    ),
    "span_no_attributes": (_span_export(attributes=[]), KIND_REQUIRED),
    "event_key_unknown": (
        _span_export(
            events=[{"timeUnixNano": "1", "name": "e", "attributes": [_text("llm.event", "x")]}]
        ),
        f"OPENINFERENCE_ATTRIBUTE_UNKNOWN: {SPAN0} event 0 llm.event",
    ),
    "resource_key_unknown": (
        {
            "resourceSpans": [
                {
                    **RESOURCE_SPANS,
                    "resource": {
                        **RESOURCE,
                        "attributes": [_text("openinference.project", "p")],
                    },
                }
            ]
        },
        "OPENINFERENCE_ATTRIBUTE_UNKNOWN: resourceSpans[0] resource openinference.project",
    ),
}


@pytest.mark.parametrize("fault", sorted(MALFORMED))
def test_malformed_or_drifted_shape_fails_closed(fault: str) -> None:
    payload, reason = MALFORMED[fault]
    with pytest.raises(OpenInferencePayloadError) as caught:
        CODECS[(FORMAT, VERSION)](payload)
    assert caught.value.args == (reason,)

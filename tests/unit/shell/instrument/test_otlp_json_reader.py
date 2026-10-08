"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from _external_formats import ingestor, receive, without
from _repo_paths import repo_root

from underwrite.instrument.ingest.codecs.otlp_json import (
    CODECS,
    GEN_AI_ATTRIBUTE_KEYS,
    OtlpJsonPayloadError,
    read_traces,
)
from underwrite.instrument.ingest.project import Source
from underwrite.instrument.ingest.transport import Ingestor, UnsupportedFormat

FORMAT = "otlp-json.traces"
VERSION = "0.160.0"
CLIENT = 3  # SpanKind.SPAN_KIND_CLIENT at the tag
ROOT = repo_root(Path(__file__))
GOLDEN = ROOT / "fixtures/golden/external/otlp-json"
CAPTURE = GOLDEN / "capture.json"
SOURCE = Source(FORMAT, VERSION, "urn:test:otlp-json")

# Read the capture once: every malformed case below is the real export with one change.
EXPORT = cast(dict[str, object], json.loads(CAPTURE.read_bytes()))
RESOURCE_SPANS = cast(list[dict[str, object]], EXPORT["resourceSpans"])[0]
RESOURCE = cast(dict[str, object], RESOURCE_SPANS["resource"])
SCOPE_SPANS = cast(list[dict[str, object]], RESOURCE_SPANS["scopeSpans"])[0]
SPANS = cast(list[dict[str, object]], SCOPE_SPANS["spans"])
ROOT_SPAN = next(span for span in SPANS if "parentSpanId" not in span)
TOOL_SPAN = next(span for span in SPANS if "events" in span)


def _ingestor() -> Ingestor:
    return ingestor(CODECS)


def _receive(payload: object) -> dict[str, object]:
    return receive(_ingestor(), payload, SOURCE, "captured:http-body-not-a-request")


# One constructor per level of the captured export, each overriding the level below it:
# export > resourceSpans[0] > scopeSpans[0] > spans[0] > attributes[0] > value. A case
# that *drops* a key uses `_without` on the level's constant.
def _export(**overrides: object) -> dict[str, object]:
    return {"resourceSpans": [{**RESOURCE_SPANS, **overrides}]}


def _scope_export(**overrides: object) -> dict[str, object]:
    return _export(scopeSpans=[{**SCOPE_SPANS, **overrides}])


def _span_export(**overrides: object) -> dict[str, object]:
    return _scope_export(spans=[{**TOOL_SPAN, **overrides}])


def _span_without(key: str) -> dict[str, object]:
    return _scope_export(spans=[without(TOOL_SPAN, key)])


def _attr(key: str, value: object) -> dict[str, object]:
    return {"key": key, "value": value}


def _attr_export(attribute: dict[str, object]) -> dict[str, object]:
    return _span_export(attributes=[attribute])


def _value_export(body: object) -> dict[str, object]:
    return _attr_export(_attr("k", body))


@pytest.mark.parametrize("value", ["1" * 5000, "-" + "1" * 5000], ids=["unsigned", "signed"])
def test_oversized_int64_text_has_typed_error(value: str) -> None:
    with pytest.raises(OtlpJsonPayloadError, match="INT64_TEXT_REQUIRED"):
        _receive(_value_export({"intValue": value}))


def test_oversized_timestamp_has_typed_error() -> None:
    with pytest.raises(OtlpJsonPayloadError, match="INT64_TEXT_REQUIRED"):
        _receive(_span_export(startTimeUnixNano="1" * 5000))


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
    assert raw.count(b"\n") <= 1  # one export batch, one JSON document
    ingestor = _ingestor()
    observation = ingestor.file(CAPTURE, SOURCE).observation
    assert observation["payload"] == json.loads(raw)


def test_real_capture_keeps_the_trace_tree_and_gen_ai_claims_as_written() -> None:
    payload = cast(dict[str, object], _receive(EXPORT)["payload"])
    spans = cast(
        list[dict[str, object]],
        cast(
            list[dict[str, object]],
            cast(list[dict[str, object]], payload["resourceSpans"])[0]["scopeSpans"],
        )[0]["spans"],
    )
    by_name = {str(span["name"]).split(" ")[0]: span for span in spans}
    assert set(by_name) == {"invoke_agent", "chat", "execute_tool"}
    assert by_name["chat"]["parentSpanId"] == by_name["invoke_agent"]["spanId"]
    assert by_name["execute_tool"]["parentSpanId"] == by_name["chat"]["spanId"]
    assert len({span["traceId"] for span in spans}) == 1
    chat = {
        str(a["key"]): a["value"]
        for a in cast(list[dict[str, object]], by_name["chat"]["attributes"])
    }
    assert chat["gen_ai.usage.input_tokens"] == {"intValue": "21"}  # int64 stays text
    assert chat["gen_ai.request.temperature"] == {"doubleValue": 0.0}
    assert "대한민국" in str(chat["gen_ai.input.messages"])
    assert by_name["chat"]["kind"] == CLIENT
    assert by_name["execute_tool"]["status"] == {"code": 1}


@pytest.mark.parametrize(
    "selector",
    [(FORMAT, "0.159.0"), (FORMAT, "latest"), ("otlp-json", VERSION), ("otlp", VERSION)],
)
def test_unpinned_selector_is_unsupported(selector: tuple[str, str]) -> None:
    with pytest.raises(UnsupportedFormat):
        _ingestor().file(CAPTURE, Source(selector[0], selector[1], "urn:test"))


def test_non_finite_double_is_refused_by_the_transport_not_sanitized() -> None:
    body = CAPTURE.read_bytes().replace(b'"doubleValue":0', b'"doubleValue":NaN', 1)
    assert body != CAPTURE.read_bytes()
    with pytest.raises(ValueError, match="NON_FINITE_JSON_NUMBER"):
        _ingestor().http_body(body, "captured:http-body-not-a-request", SOURCE)


def test_empty_export_and_defaults_only_span_are_accepted() -> None:
    assert _receive({"resourceSpans": []})["payload"] == {"resourceSpans": []}
    minimal = {
        "traceId": "0" * 32,
        "spanId": "0" * 16,
        "name": "",
        "startTimeUnixNano": "0",
        "endTimeUnixNano": "0",
    }
    payload = _receive({"resourceSpans": [{"scopeSpans": [{"spans": [minimal]}]}]})["payload"]
    assert payload == {"resourceSpans": [{"scopeSpans": [{"spans": [minimal]}]}]}


# Every proto field of the pinned messages with a type-plausible value, listed here
# independently of the codec so a typo in its closed sets rejects this export — except the
# two dictionary-encoding fields, which the codec refuses by design.
FULL_VALUE: dict[str, object] = {
    "kvlistValue": {
        "values": [
            _attr("s", {"stringValue": "x"}),
            _attr("b", {"boolValue": True}),
            _attr("i", {"intValue": "-1"}),
            _attr("d", {"doubleValue": 1.5}),
            _attr("a", {"arrayValue": {"values": [{"bytesValue": "AQI="}]}}),
            _attr("min", {"intValue": "-9223372036854775808"}),
            _attr("max", {"intValue": "9223372036854775807"}),
        ]
    }
}
FULL_EVENT: dict[str, object] = {
    "timeUnixNano": "1",
    "name": "e",
    "attributes": [_attr("gen_ai.tool.call.result", {"stringValue": "{}"})],
    "droppedAttributesCount": 0,
}
FULL_LINK: dict[str, object] = {
    "traceId": "a" * 32,
    "spanId": "b" * 16,
    "traceState": "k=v",
    "attributes": [_attr("x", FULL_VALUE)],
    "droppedAttributesCount": 0,
    "flags": 256,
}
FULL_SPAN: dict[str, object] = {
    **TOOL_SPAN,
    "traceState": "k=v",
    "droppedAttributesCount": 0,
    "events": [FULL_EVENT],
    "droppedEventsCount": 0,
    "links": [FULL_LINK],
    "droppedLinksCount": 0,
    "status": {"message": "ok", "code": 1},
    "attributes": [_attr(key, {"stringValue": key}) for key in sorted(GEN_AI_ATTRIBUTE_KEYS)],
}
# A second span pins the far ends of the enum ranges and the unsigned time range.
EDGE_SPAN: dict[str, object] = {
    "traceId": "c" * 32,
    "spanId": "d" * 16,
    "parentSpanId": "b" * 16,
    "name": "consumer",
    "kind": 5,
    "startTimeUnixNano": "0",
    "endTimeUnixNano": "18446744073709551615",
    "status": {"code": 2, "message": "failed"},
}
FULL_EXPORT: dict[str, object] = {
    "resourceSpans": [
        {
            "resource": {
                "attributes": [_attr("service.name", {"stringValue": "s"})],
                "droppedAttributesCount": 0,
                "entityRefs": [],
            },
            "scopeSpans": [
                {
                    "scope": {
                        "name": "n",
                        "version": "1",
                        "attributes": [],
                        "droppedAttributesCount": 0,
                    },
                    "spans": [FULL_SPAN, EDGE_SPAN],
                    "schemaUrl": "https://opentelemetry.io/schemas/1.44.0",
                }
            ],
            "schemaUrl": "https://opentelemetry.io/schemas/1.44.0",
        }
    ]
}


def test_export_with_every_proto_field_and_every_gen_ai_key_is_accepted_as_written() -> None:
    assert _receive(FULL_EXPORT)["payload"] == FULL_EXPORT


# The codec names a fault by the path it was read from; these are the paths faulted below.
RS0 = "resourceSpans[0]"
SS0 = f"{RS0}.scopeSpans[0]"
SPAN0 = f"{SS0}.spans[0]"
ATTR0 = f"{SPAN0} attribute 0"
VALUE0 = f"{ATTR0} value"
MALFORMED: dict[str, tuple[object, str]] = {
    "root_not_object": ([], "OTLP_OBJECT_REQUIRED: export"),
    "root_unknown_field": ({**EXPORT, "resourceLogs": []}, "OTLP_UNKNOWN_FIELD: export"),
    "resource_spans_missing": ({}, "OTLP_ARRAY_REQUIRED: resourceSpans"),
    "resource_spans_not_array": ({"resourceSpans": {}}, "OTLP_ARRAY_REQUIRED: resourceSpans"),
    "resource_spans_unknown_field": (_export(resourceLogs=[]), f"OTLP_UNKNOWN_FIELD: {RS0}"),
    "resource_unknown_field": (
        _export(resource={**RESOURCE, "schemaUrl": "x"}),
        f"OTLP_UNKNOWN_FIELD: {RS0} resource",
    ),
    "scope_spans_missing": (
        {"resourceSpans": [without(RESOURCE_SPANS, "scopeSpans")]},
        f"OTLP_ARRAY_REQUIRED: {RS0} scopeSpans",
    ),
    "scope_spans_unknown_field": (
        _scope_export(instrumentationLibrary={"name": "x"}),
        f"OTLP_UNKNOWN_FIELD: {SS0}",
    ),
    "scope_unknown_field": (
        _scope_export(scope={"name": "n", "schemaUrl": "x"}),
        f"OTLP_UNKNOWN_FIELD: {SS0} scope",
    ),
    "spans_not_array": (_scope_export(spans={"count": 1}), f"OTLP_ARRAY_REQUIRED: {SS0} spans"),
    "span_not_object": (_scope_export(spans=["x"]), f"OTLP_OBJECT_REQUIRED: {SPAN0}"),
    "span_unknown_field": (_span_export(duration="1"), f"OTLP_UNKNOWN_FIELD: {SPAN0}"),
    "span_trace_id_short": (_span_export(traceId="abc"), f"OTLP_ID_REQUIRED: {SPAN0} traceId"),
    "span_trace_id_missing": (_span_without("traceId"), f"OTLP_ID_REQUIRED: {SPAN0} traceId"),
    "span_span_id_uppercase": (_span_export(spanId="A" * 16), f"OTLP_ID_REQUIRED: {SPAN0} spanId"),
    "span_parent_id_blank": (
        _span_export(parentSpanId=""),
        f"OTLP_ID_REQUIRED: {SPAN0} parentSpanId",
    ),
    "span_name_missing": (_span_without("name"), f"OTLP_TEXT_REQUIRED: {SPAN0} name"),
    "span_kind_six": (_span_export(kind=6), f"OTLP_ENUM_UNSUPPORTED: {SPAN0} kind"),
    "span_kind_negative": (_span_export(kind=-1), f"OTLP_ENUM_UNSUPPORTED: {SPAN0} kind"),
    "span_start_time_negative": (
        _span_export(startTimeUnixNano="-1"),
        f"OTLP_INT64_TEXT_REQUIRED: {SPAN0} startTimeUnixNano",
    ),
    "span_end_time_overflow": (
        _span_export(endTimeUnixNano="18446744073709551616"),
        f"OTLP_INT64_TEXT_REQUIRED: {SPAN0} endTimeUnixNano",
    ),
    "span_kind_text": (_span_export(kind="CLIENT"), f"OTLP_ENUM_UNSUPPORTED: {SPAN0} kind"),
    "span_kind_flag": (_span_export(kind=True), f"OTLP_ENUM_UNSUPPORTED: {SPAN0} kind"),
    "span_start_time_number": (
        _span_export(startTimeUnixNano=1),
        f"OTLP_INT64_TEXT_REQUIRED: {SPAN0} startTimeUnixNano",
    ),
    "span_end_time_missing": (
        _span_without("endTimeUnixNano"),
        f"OTLP_INT64_TEXT_REQUIRED: {SPAN0} endTimeUnixNano",
    ),
    "span_end_time_not_digits": (
        _span_export(endTimeUnixNano="1e9"),
        f"OTLP_INT64_TEXT_REQUIRED: {SPAN0} endTimeUnixNano",
    ),
    "span_status_unknown_field": (
        _span_export(status={"code": 1, "description": "x"}),
        f"OTLP_UNKNOWN_FIELD: {SPAN0} status",
    ),
    "span_status_code_three": (
        _span_export(status={"code": 3}),
        f"OTLP_ENUM_UNSUPPORTED: {SPAN0} status code",
    ),
    "span_events_not_array": (_span_export(events={}), f"OTLP_ARRAY_REQUIRED: {SPAN0} events"),
    "span_event_unknown_field": (
        _span_export(events=[{**FULL_EVENT, "severity": "x"}]),
        f"OTLP_UNKNOWN_FIELD: {SPAN0} event 0",
    ),
    "span_event_time_missing": (
        _span_export(events=[without(FULL_EVENT, "timeUnixNano")]),
        f"OTLP_INT64_TEXT_REQUIRED: {SPAN0} event 0 timeUnixNano",
    ),
    "span_event_name_number": (
        _span_export(events=[{**FULL_EVENT, "name": 1}]),
        f"OTLP_TEXT_REQUIRED: {SPAN0} event 0 name",
    ),
    "span_link_unknown_field": (
        _span_export(links=[{**FULL_LINK, "kind": 1}]),
        f"OTLP_UNKNOWN_FIELD: {SPAN0} link 0",
    ),
    "span_link_span_id_short": (
        _span_export(links=[{**FULL_LINK, "spanId": "b"}]),
        f"OTLP_ID_REQUIRED: {SPAN0} link 0 spanId",
    ),
    "attributes_not_array": (
        _span_export(attributes={}),
        f"OTLP_ARRAY_REQUIRED: {SPAN0} attributes",
    ),
    "attribute_not_object": (_span_export(attributes=["x"]), f"OTLP_OBJECT_REQUIRED: {ATTR0}"),
    "attribute_unknown_field": (
        _attr_export({"key": "k", "value": {"stringValue": "v"}, "type": "string"}),
        f"OTLP_UNKNOWN_FIELD: {ATTR0}",
    ),
    "attribute_key_blank": (
        _attr_export(_attr("", {"stringValue": "v"})),
        f"OTLP_TEXT_REQUIRED: {ATTR0} key",
    ),
    "attribute_key_missing": (
        _attr_export({"value": {"stringValue": "v"}}),
        f"OTLP_TEXT_REQUIRED: {ATTR0} key",
    ),
    "gen_ai_key_unknown": (
        _attr_export(_attr("gen_ai.request.model_family", {"stringValue": "x"})),
        "OTLP_GEN_AI_ATTRIBUTE_UNKNOWN: gen_ai.request.model_family",
    ),
    "gen_ai_key_deprecated_alias": (
        _attr_export(_attr("gen_ai.system", {"stringValue": "openai"})),
        "OTLP_GEN_AI_ATTRIBUTE_UNKNOWN: gen_ai.system",
    ),
    "gen_ai_key_unknown_in_event": (
        _span_export(
            events=[{**FULL_EVENT, "attributes": [_attr("gen_ai.event.x", {"stringValue": "v"})]}]
        ),
        "OTLP_GEN_AI_ATTRIBUTE_UNKNOWN: gen_ai.event.x",
    ),
    "gen_ai_key_unknown_in_resource": (
        _export(
            resource={**RESOURCE, "attributes": [_attr("gen_ai.deployment", {"stringValue": "v"})]}
        ),
        "OTLP_GEN_AI_ATTRIBUTE_UNKNOWN: gen_ai.deployment",
    ),
    "value_missing": (_attr_export({"key": "k"}), f"OTLP_OBJECT_REQUIRED: {VALUE0}"),
    "value_unknown_field": (
        _value_export({"stringValue": "v", "type": "s"}),
        f"OTLP_UNKNOWN_FIELD: {VALUE0}",
    ),
    "value_empty_oneof": (_value_export({}), f"OTLP_ANY_VALUE_REQUIRED: {VALUE0}"),
    "value_string_with_strindex": (
        _value_export({"stringValue": "v", "stringValueStrindex": 1}),
        f"OTLP_DICTIONARY_ENCODING_UNSUPPORTED: {VALUE0}",
    ),
    "value_strindex_only": (
        _value_export({"stringValueStrindex": 1}),
        f"OTLP_DICTIONARY_ENCODING_UNSUPPORTED: {VALUE0}",
    ),
    "attribute_key_strindex": (
        _attr_export({"key": "k", "value": {"stringValue": "v"}, "keyStrindex": 0}),
        f"OTLP_DICTIONARY_ENCODING_UNSUPPORTED: {ATTR0}",
    ),
    "value_int_negative_zero": (
        _value_export({"intValue": "-0"}),
        f"OTLP_INT64_TEXT_REQUIRED: {VALUE0} intValue",
    ),
    "value_int_leading_zero": (
        _value_export({"intValue": "007"}),
        f"OTLP_INT64_TEXT_REQUIRED: {VALUE0} intValue",
    ),
    "value_int_overflow": (
        _value_export({"intValue": "9223372036854775808"}),
        f"OTLP_INT64_TEXT_REQUIRED: {VALUE0} intValue",
    ),
    "value_int_underflow": (
        _value_export({"intValue": "-9223372036854775809"}),
        f"OTLP_INT64_TEXT_REQUIRED: {VALUE0} intValue",
    ),
    "gen_ai_bare_key": (
        _attr_export(_attr("gen_ai", {"stringValue": "x"})),
        "OTLP_GEN_AI_ATTRIBUTE_UNKNOWN: gen_ai",
    ),
    "value_two_oneof": (
        _value_export({"stringValue": "v", "intValue": "1"}),
        f"OTLP_ANY_VALUE_REQUIRED: {VALUE0}",
    ),
    "value_int_number": (
        _value_export({"intValue": 21}),
        f"OTLP_INT64_TEXT_REQUIRED: {VALUE0} intValue",
    ),
    "value_bool_text": (
        _value_export({"boolValue": "true"}),
        f"OTLP_FLAG_REQUIRED: {VALUE0} boolValue",
    ),
    "value_double_text": (
        _value_export({"doubleValue": "0.5"}),
        f"OTLP_NUMBER_REQUIRED: {VALUE0} doubleValue",
    ),
    "value_double_flag": (
        _value_export({"doubleValue": True}),
        f"OTLP_NUMBER_REQUIRED: {VALUE0} doubleValue",
    ),
    "value_string_number": (
        _value_export({"stringValue": 1}),
        f"OTLP_TEXT_REQUIRED: {VALUE0} stringValue",
    ),
    "value_bytes_number": (
        _value_export({"bytesValue": 1}),
        f"OTLP_TEXT_REQUIRED: {VALUE0} bytesValue",
    ),
    "value_array_unknown_field": (
        _value_export({"arrayValue": {"values": [], "count": 0}}),
        f"OTLP_UNKNOWN_FIELD: {VALUE0} arrayValue",
    ),
    "value_array_values_not_array": (
        _value_export({"arrayValue": {"values": {}}}),
        f"OTLP_ARRAY_REQUIRED: {VALUE0} arrayValue",
    ),
    "value_array_item_bad": (
        _value_export({"arrayValue": {"values": [{"intValue": 1}]}}),
        f"OTLP_INT64_TEXT_REQUIRED: {VALUE0}[0] intValue",
    ),
    "value_kvlist_unknown_field": (
        _value_export({"kvlistValue": {"values": [], "count": 0}}),
        f"OTLP_UNKNOWN_FIELD: {VALUE0} kvlistValue",
    ),
    "value_kvlist_gen_ai_unknown": (
        _value_export({"kvlistValue": {"values": [_attr("gen_ai.nested", {"stringValue": "v"})]}}),
        "OTLP_GEN_AI_ATTRIBUTE_UNKNOWN: gen_ai.nested",
    ),
}


@pytest.mark.parametrize("fault", sorted(MALFORMED))
def test_malformed_or_drifted_shape_fails_closed(fault: str) -> None:
    payload, reason = MALFORMED[fault]
    with pytest.raises(OtlpJsonPayloadError) as caught:
        CODECS[(FORMAT, VERSION)](payload)
    assert caught.value.args == (reason,)

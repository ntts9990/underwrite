"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from _repo_paths import repo_root

from underwrite.instrument.ingest.project import Source
from underwrite.instrument.ingest.transport import Codec, Ingestor

# Directory -> (producer distribution, format namespace, capture origin).
EXTERNAL_CAPTURES = {
    "otlp-json": ("otelcol-contrib", "otlp-json", "sanitized_tool_payload_example"),
    "openinference": (
        "openinference-semantic-conventions",
        "openinference",
        "sanitized_tool_payload_example",
    ),
    "langfuse": ("langfuse", "langfuse", "sanitized_tool_payload_example"),
    "deepeval": ("deepeval", "deepeval", "sanitized_tool_payload_example"),
    "deepeval-4.2.8": ("deepeval", "deepeval", "unchanged_synthetic_tool_export"),
    "inspect": ("inspect_ai", "inspect", "sanitized_tool_payload_example"),
    "promptfoo": ("promptfoo", "promptfoo", "sanitized_tool_payload_example"),
    "promptfoo-0.124.1": ("promptfoo", "promptfoo", "sanitized_tool_payload_example"),
}
EXTERNAL_DIRECTORIES = frozenset(EXTERNAL_CAPTURES)
OBSERVATION_SCHEMA = repo_root(Path(__file__)) / "contracts/observation.v1.schema.json"


def without(record: dict[str, object], key: str) -> dict[str, object]:
    """``record`` with ``key`` dropped -- the "this field is missing" malformed case."""
    return {name: value for name, value in record.items() if name != key}


def ingestor(
    codecs: Mapping[tuple[str, str], Codec],
    *,
    max_bytes: int = 1024 * 1024,
    max_depth: int = 64,
) -> Ingestor:
    """One test's ingest path: its own registry and limits against the one schema.

    The defaults are the limits four of the six readers use; a reader with tighter or
    wider limits passes its own, because that is a fact about what it captured.
    """
    return Ingestor(OBSERVATION_SCHEMA, codecs, max_bytes=max_bytes, max_depth=max_depth)


def receive(ingest: Ingestor, payload: object, source: Source, locator: str) -> dict[str, object]:
    """The observation an in-memory payload projects to when sent as an HTTP body."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return ingest.http_body(body, locator, source).observation

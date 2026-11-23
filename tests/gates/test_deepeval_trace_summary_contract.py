"""The trace summary follows its full count and action owner without coordinates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator

from scripts import build_deepeval_trace_summary as builder
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).parents[2]


def test_generated_trace_summary_is_installed_and_source_derived() -> None:
    expected = (json.dumps(builder.build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    packaged = ROOT / "src/underwrite/_contracts" / builder.TARGET.name
    assert builder.TARGET.read_bytes() == expected
    assert packaged.resolve() == builder.TARGET.resolve()
    assert packaged.read_bytes() == expected
    source = json.loads(builder.SOURCE.read_bytes())
    summary = json.loads(expected)
    for name in (
        "source", "raw_hash", "rule_profile", "scope", "counts", "next_actions", "reader_profile"
    ):
        assert summary["properties"][name] == source["properties"][name]
    assert "cases" not in summary["properties"]
    full_linkability, summary_linkability = builder.TRACE_SUMMARY_LINKABILITY
    assert full_linkability in source["properties"]["limitations"]["const"]
    assert summary_linkability in summary["properties"]["limitations"]["const"]
    assert full_linkability not in summary["properties"]["limitations"]["const"]
    packaged_validator("deepeval_trace_summary.v1")


def test_trace_summary_contract_rejects_coordinates_and_wrong_limit() -> None:
    from underwrite.cli.audit_deepeval import (
        _trace_summary_file,  # pyright: ignore[reportPrivateUsage]
    )

    source = ROOT / "fixtures/examples/deepeval-auto-4.3.1/recovery.json"
    result = _trace_summary_file(source)
    validator = Draft202012Validator(builder.build_schema())
    assert validator.is_valid(cast(Any, result))  # pyright: ignore[reportUnknownMemberType]
    invalid: dict[str, Any] = {**result, "cases": []}
    assert not validator.is_valid(invalid)  # pyright: ignore[reportUnknownMemberType]
    limitations = cast(list[str], result["limitations"])
    invalid = {**result, "limitations": [*limitations, "SOURCE_HASH_AND_POINTERS_LINKABLE"]}
    assert not validator.is_valid(invalid)  # pyright: ignore[reportUnknownMemberType]

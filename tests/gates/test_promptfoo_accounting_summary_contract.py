"""Promptfoo summary contract derives meaning from the installed actions owner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from scripts import build_promptfoo_accounting_summary as builder
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).parents[2]


def test_generated_summary_contract_is_installed_and_action_consistent() -> None:
    expected = (json.dumps(builder.build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    packaged = ROOT / "src/underwrite/_contracts" / builder.TARGET.name
    assert builder.TARGET.read_bytes() == expected
    assert packaged.resolve() == builder.TARGET.resolve()
    assert packaged.read_bytes() == expected
    schema = json.loads(expected)
    source = json.loads(builder.SOURCE.read_bytes())
    for name in (
        "source", "raw_hash", "reader_profile", "scope", "producer_stats",
        "row_tallies", "deltas", "declared_relation", "limitations", "next_actions",
    ):
        assert schema["properties"][name] == source["properties"][name]
    assert set(schema["properties"]["finding_counts"]["required"]) == set(
        source["$defs"]["finding"]["properties"]["code"]["enum"]
    )
    packaged_validator("promptfoo_accounting_summary.v1")


def test_summary_contract_requires_action_for_positive_finding_count() -> None:
    schema = builder.build_schema()
    validator = Draft202012Validator(schema)
    source = ROOT / "fixtures/examples/evidence/synthetic-promptfoo-count-mismatch.json"
    from underwrite.cli.audit_promptfoo import _summary_file  # pyright: ignore[reportPrivateUsage]

    valid = _summary_file(source)
    assert validator.is_valid(cast(Any, valid))  # pyright: ignore[reportUnknownMemberType]
    invalid: dict[str, Any] = {**valid, "next_actions": []}
    assert not validator.is_valid(invalid)  # pyright: ignore[reportUnknownMemberType]
    invalid = {
        **valid,
        "finding_counts": {"CATEGORY_COUNT_MISMATCH": 0, "SOURCE_FLAGS_DISAGREE": 0},
    }
    assert not validator.is_valid(invalid)  # pyright: ignore[reportUnknownMemberType]
    invalid = {
        **valid,
        "source_binding": {
            "kind": "direct_file", "manifest_hash": None,
            "expected_hash_check": "not_supplied",
        },
    }
    assert not validator.is_valid(invalid)  # pyright: ignore[reportUnknownMemberType]
    invalid = {
        **valid,
        "source_binding": {
            "kind": "manifest_case", "manifest_hash": valid["raw_hash"],
            "expected_hash_check": "matched",
        },
    }
    assert not validator.is_valid(invalid)  # pyright: ignore[reportUnknownMemberType]


def test_summary_action_conditions_follow_owner_mapping_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    swapped = tuple((code, action) for code, action in reversed(builder.ACTION_BY_FINDING))
    changed = (
        (swapped[0][0], swapped[1][1]),
        (swapped[1][0], swapped[0][1]),
    )
    monkeypatch.setattr(builder, "ACTION_BY_FINDING", changed)
    schema = builder.build_schema()
    conditions = cast(list[dict[str, Any]], schema["allOf"])
    assert [
        condition["then"]["properties"]["next_actions"]["contains"]["const"]
        for condition in conditions
        if "next_actions" in condition["then"]["properties"]
    ] == [
        action for _, action in changed
    ]

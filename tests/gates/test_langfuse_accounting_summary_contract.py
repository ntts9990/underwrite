"""Langfuse summary keeps both reader families and their retrieval limits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator

from scripts import build_langfuse_accounting_summary as builder
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).parents[2]


def test_generated_summary_is_installed_and_both_branches_follow_owner() -> None:
    expected = (json.dumps(builder.build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    packaged = ROOT / "src/underwrite/_contracts" / builder.TARGET.name
    assert builder.TARGET.read_bytes() == expected
    assert packaged.resolve() == builder.TARGET.resolve()
    assert packaged.read_bytes() == expected
    source = json.loads(builder.SOURCE.read_bytes())
    summary = json.loads(expected)
    assert summary["if"] == source["if"]
    old, new = builder.SUMMARY_LINKABILITY
    for side in ("then", "else"):
        fields = summary[side]["properties"]
        original = source[side]["properties"]
        assert "observations" not in fields and "scores" not in fields
        for name in ("sources", "scope", "counts", "planned", "next_actions", "reader_profile"):
            assert fields[name] == original[name]
        assert old in original["limitations"]["const"]
        assert new in fields["limitations"]["const"]
        assert old not in fields["limitations"]["const"]
    packaged_validator("langfuse_accounting_summary.v1")


def test_summary_contract_admits_blob_and_cli_but_rejects_rows() -> None:
    from underwrite.cli.audit_langfuse import _summary_files  # pyright: ignore[reportPrivateUsage]

    validator = Draft202012Validator(builder.build_schema())
    pairs = (
        (
            ROOT / "fixtures/golden/external/langfuse/observations_v2-2026-09-14T06-40-00.json",
            ROOT / "fixtures/golden/external/langfuse/scores-2026-09-14T06-40-00.json",
        ),
        (
            ROOT / "fixtures/examples/langfuse-cli/observations.json",
            ROOT / "fixtures/examples/langfuse-cli/scores.json",
        ),
    )
    for observations, scores in pairs:
        summary = _summary_files(observations, scores)
        assert validator.is_valid(cast(Any, summary))  # pyright: ignore[reportUnknownMemberType]
        invalid: dict[str, Any] = {**summary, "scores": []}
        assert not validator.is_valid(invalid)  # pyright: ignore[reportUnknownMemberType]

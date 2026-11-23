"""The bounded cross-artifact projection follows its authored review contract."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import build_cross_artifact_review_summary as builder
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).parents[2]


def test_summary_contract_is_generated_installed_and_owner_derived() -> None:
    authored = builder.TARGET
    packaged = ROOT / "src/underwrite/_contracts" / authored.name
    expected = (json.dumps(builder.build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    assert authored.read_bytes() == expected
    assert packaged.resolve() == authored.resolve()
    assert packaged.read_bytes() == expected
    full = json.loads(builder.SOURCE.read_text())
    summary = json.loads(expected)
    for field in (
        "relation",
        "source_identity",
        "populations",
        "finding_counts",
        "improvement",
        "checks",
    ):
        assert summary["properties"][field] == full["properties"][field]
    full_limits = full["properties"]["limitations"]
    summary_limits = summary["properties"]["limitations"]
    assert summary_limits["items"] == full_limits["items"]
    assert summary_limits["uniqueItems"] is True
    assert summary_limits["minItems"] == summary_limits["maxItems"] == len(
        full_limits["items"]["enum"]
    )
    assert summary["$defs"]["hash"] == full["$defs"]["hash"]
    assert summary["properties"]["next_verification_tasks"]["items"]["properties"]["code"] == (
        full["properties"]["next_verification_tasks"]["items"]["properties"]["code"]
    )
    packaged_validator("cross_artifact_review_summary.v1")

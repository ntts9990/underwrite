"""The bounded readiness projection follows its authored full contract."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import build_cross_artifact_readiness_summary as builder
from underwrite.instrument.ingest.cross_artifact import READINESS_LIMITATIONS
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).parents[2]


def test_readiness_summary_contract_is_generated_installed_and_owner_derived() -> None:
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
        "review_prerequisites",
        "finding_counts",
        "improvement",
        "checks",
    ):
        assert summary["properties"][field] == full["properties"][field]
    assert summary["allOf"][:6] == full["allOf"][:6]
    assert summary["$defs"]["hash"] == full["$defs"]["hash"]
    limits = summary["properties"]["limitations"]
    assert set(limits["items"]["enum"]) == set(READINESS_LIMITATIONS)
    assert limits["minItems"] == limits["maxItems"] == len(READINESS_LIMITATIONS)
    packaged_validator("cross_artifact_readiness_summary.v1")

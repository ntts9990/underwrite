"""The bounded record verification projection follows its full result contract."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import build_record_verification_summary as builder
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).parents[2]


def test_summary_contract_is_generated_installed_and_finding_code_derived() -> None:
    authored = builder.TARGET
    packaged = ROOT / "src/underwrite/_contracts" / authored.name
    expected = (json.dumps(builder.build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    assert authored.read_bytes() == expected
    assert packaged.resolve() == authored.resolve()
    assert packaged.read_bytes() == expected
    full = json.loads(builder.SOURCE.read_text())
    summary = json.loads(expected)
    for name in ("record_id", "manifest_digest", "valid", "verification_scope"):
        assert summary["properties"][name] == full["properties"][name]
    assert summary["properties"]["file_check_states"]["items"]["enum"] == [
        "CHECKED_NO_FINDING",
        *full["$defs"]["file_finding"]["properties"]["code"]["enum"],
    ]
    assert summary["properties"]["links_check_states"]["items"]["enum"] == [
        "CHECKED_NO_FINDING",
        *full["$defs"]["link_finding"]["properties"]["code"]["enum"],
    ]
    assert summary["properties"]["file_check_states"]["maxItems"] == (
        full["properties"]["files_checked"]["maximum"]
    )
    assert summary["properties"]["links_check_states"]["maxItems"] == (
        full["properties"]["links_checked"]["maximum"]
    )
    assert summary["$defs"]["record_id"] == full["$defs"]["record_id"]
    assert summary["$defs"]["digest"] == full["$defs"]["digest"]
    packaged_validator("record_verification_summary.v1")

"""The bounded record view follows its full manifest contract."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import build_record_view_summary as builder
from underwrite.cli.records import RECORD_FILE_PAGE_SIZE
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).parents[2]


def test_summary_contract_is_generated_installed_and_manifest_derived() -> None:
    authored = builder.TARGET
    packaged = ROOT / "src/underwrite/_contracts" / authored.name
    expected = (json.dumps(builder.build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    assert authored.read_bytes() == expected
    assert packaged.resolve() == authored.resolve()
    assert packaged.read_bytes() == expected
    full = json.loads(builder.SOURCE.read_text())
    summary = json.loads(expected)
    assert summary["properties"]["manifest_digest"] == full["properties"]["manifest_digest"]
    for name in ("record_id", "kind", "digest", "logical_name", "role", "media_type"):
        assert summary["$defs"][name] == full["$defs"][name]
    assert summary["properties"]["files"]["maxItems"] == RECORD_FILE_PAGE_SIZE
    file_fields = summary["properties"]["files"]["items"]["properties"]
    assert set(file_fields) == {"name", "role", "media_type", "declared_sha256", "declared_bytes"}
    assert "source_locator" not in file_fields
    packaged_validator("record_view_summary.v1")

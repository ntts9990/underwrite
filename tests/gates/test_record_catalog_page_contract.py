"""The bounded catalog page follows the full record catalog owner."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import build_record_catalog_page as builder
from underwrite.cli.records import CATALOG_PAGE_SIZE
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).parents[2]


def test_page_contract_is_generated_installed_and_uses_full_record_id_grammar() -> None:
    authored = builder.TARGET
    packaged = ROOT / "src/underwrite/_contracts" / authored.name
    expected = (json.dumps(builder.build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    assert authored.read_bytes() == expected
    assert packaged.resolve() == authored.resolve()
    assert packaged.read_bytes() == expected
    full = json.loads(builder.SOURCE.read_text())
    page = json.loads(expected)
    assert page["properties"]["record_count"] == full["properties"]["record_count"]
    assert page["$defs"]["record_id"] == full["$defs"]["record_id"]
    assert page["$defs"]["kind"] == full["$defs"]["kind"]
    assert page["$defs"]["digest"] == full["$defs"]["digest"]
    assert page["properties"]["records"]["maxItems"] == CATALOG_PAGE_SIZE
    assert "label" not in page["$defs"]["complete"]["properties"]
    assert "captured_at" not in page["$defs"]["complete"]["properties"]
    packaged_validator("record_catalog_page.v1")

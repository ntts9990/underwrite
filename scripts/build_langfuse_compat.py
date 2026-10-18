"""Generate current Langfuse views and captures from their direct shape owners."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

if __package__:
    from . import build_langfuse_accounting as blob
    from . import build_langfuse_cli_accounting as cli
    from .build_langfuse_record import build_auto_schema, build_row_schema
else:
    import build_langfuse_accounting as blob
    import build_langfuse_cli_accounting as cli
    from build_langfuse_record import build_auto_schema, build_row_schema

from underwrite.cli.record_langfuse import AUTO_RECORD_SCHEMA, ROW_RECORD_SCHEMA
from underwrite.instrument.evidence.langfuse_compat import (
    ACCOUNTING_SCHEMA,
    BLOB_READER,
    ROW_INDEX_SCHEMA,
)

ROOT = Path(__file__).resolve().parents[1]


def _view(
    name: str, blob_branch: dict[str, object], cli_branch: dict[str, object]
) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + name,
        "title": name,
        "description": (
            "Selected Langfuse array/blob or CLI-list rows; producer version and source windows "
            "remain unknown. The reader family is an Underwrite policy, not origin proof."
        ),
        "if": {
            "properties": {"reader_profile": {"const": BLOB_READER}},
            "required": ["reader_profile"],
        },
        "then": blob_branch,
        "else": cli_branch,
    }


def build_accounting_schema() -> dict[str, Any]:
    return _view(ACCOUNTING_SCHEMA, blob.build_accounting_branch(), cli.build_accounting_branch())


def build_row_index_schema() -> dict[str, Any]:
    return _view(ROW_INDEX_SCHEMA, blob.build_row_branch(), cli.build_row_branch())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, build in (
        (ACCOUNTING_SCHEMA, build_accounting_schema),
        (ROW_INDEX_SCHEMA, build_row_index_schema),
        (AUTO_RECORD_SCHEMA, build_auto_schema),
        (ROW_RECORD_SCHEMA, build_row_schema),
    ):
        target = ROOT / f"contracts/{name}.schema.json"
        raw = (json.dumps(build(), ensure_ascii=False, indent=2) + "\n").encode()
        if args.check:
            if not target.exists() or target.read_bytes() != raw:
                print(f"{target.name} is stale")
                return 1
        else:
            target.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

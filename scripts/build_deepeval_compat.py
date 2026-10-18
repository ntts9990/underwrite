"""Compose version-unknown DeepEval contracts from the existing counted owners."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

if __package__:
    from ._record_schema import build_record_schema
    from .build_deepeval_accounting import build_branch
    from .build_deepeval_trace_index import build_schema as build_trace
else:
    from _record_schema import build_record_schema
    from build_deepeval_accounting import build_branch
    from build_deepeval_trace_index import build_schema as build_trace

from underwrite.cli.record_deepeval import (
    AUTO_RECORD_SCHEMA,
    AUTO_TRACE_RECORD_SCHEMA,
    COMMAND,
)
from underwrite.instrument.evidence.deepeval_compat import (
    ACCOUNTING_SCHEMA,
    NO_FLAKY_READER,
    TRACE_SCHEMA,
)

ROOT = Path(__file__).resolve().parents[1]


def build_accounting() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:underwrite:contract:{ACCOUNTING_SCHEMA}",
        "title": ACCOUNTING_SCHEMA,
        "description": "Selected serialized DeepEval metric occurrences; producer version unknown.",
        "if": {
            "properties": {"reader_profile": {"const": NO_FLAKY_READER}},
            "required": ["reader_profile"],
        },
        "then": build_branch(flaky=False),
        "else": build_branch(flaky=True),
    }


def _record(name: str, analysis: str) -> dict[str, Any]:
    owner, version = analysis.rsplit(".", 1)
    return build_record_schema(
        ROOT,
        accounting_owner=owner,
        accounting_version=version,
        schema_name=name,
        command=COMMAND,
        description=(
            "Independent selected DeepEval analysis and private capture outcomes. "
            "A saved record verifies bytes, not producer execution or approval."
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    outputs = (
        (ACCOUNTING_SCHEMA, build_accounting()),
        (TRACE_SCHEMA, build_trace()),
    )
    stale = False
    for name, schema in outputs:
        path = ROOT / "contracts" / f"{name}.schema.json"
        raw = (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode()
        if args.check:
            if not path.exists() or path.read_bytes() != raw:
                print(f"{path.name} is stale")
                stale = True
        else:
            path.write_bytes(raw)
    if stale:
        return 1
    records = (
        (AUTO_RECORD_SCHEMA, _record(AUTO_RECORD_SCHEMA, ACCOUNTING_SCHEMA)),
        (AUTO_TRACE_RECORD_SCHEMA, _record(AUTO_TRACE_RECORD_SCHEMA, TRACE_SCHEMA)),
    )
    for name, schema in records:
        path = ROOT / "contracts" / f"{name}.schema.json"
        raw = (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode()
        if args.check:
            if not path.exists() or path.read_bytes() != raw:
                print(f"{path.name} is stale")
                stale = True
        else:
            path.write_bytes(raw)
    return int(stale)


if __name__ == "__main__":
    raise SystemExit(main())

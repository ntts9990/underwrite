"""Compose version-unknown DeepEval contracts from the existing counted owners."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _record_schema import build_record_schema
from _schema_compose import object_schema

from underwrite.cli.record_deepeval import (
    AUTO_RECORD_SCHEMA,
    AUTO_TRACE_RECORD_SCHEMA,
    COMMAND,
)
from underwrite.instrument.evidence.deepeval_accounting import LIMITATIONS, LIMITATIONS_V2
from underwrite.instrument.evidence.deepeval_compat import (
    ACCOUNTING_LIMITATIONS_EXTRA,
    ACCOUNTING_SCHEMA,
    ACCOUNTING_SCOPE_EXTRA,
    EMPTY_LIMITATION,
    FLAKY_READER,
    NO_FLAKY_READER,
    SOURCE_FORMAT,
    TRACE_LIMITATIONS_EXTRA,
    TRACE_READER,
    TRACE_SCHEMA,
    TRACE_SCOPE_EXTRA,
)
from underwrite.instrument.evidence.deepeval_trace import LIMITATIONS as TRACE_LIMITATIONS

ROOT = Path(__file__).resolve().parents[1]


def _authored(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "contracts" / f"{name}.schema.json").read_bytes())


def _identity() -> dict[str, object]:
    return object_schema(
        {
            "format": {"const": SOURCE_FORMAT},
            "producer_version": {"type": "null"},
            "producer_version_state": {"const": "unknown"},
            "claims_verified": {"const": False},
        }
    )


def _scope(base: dict[str, Any], extra: dict[str, object]) -> None:
    scope = base["properties"]["scope"]
    scope["properties"].update({key: {"const": value} for key, value in extra.items()})
    scope["required"].extend(extra)


def _limitations(values: list[str]) -> dict[str, object]:
    return {"type": "array", "const": values, "maxItems": len(values), "items": {"enum": values}}


def _body(name: str, reader: str, extra: dict[str, object]) -> dict[str, Any]:
    body = _authored(name)
    for key in ("$schema", "$id", "title", "description"):
        body.pop(key)
    properties = body["properties"]
    properties.pop("observation_digest", None)
    if "observation_digest" in body["required"]:
        body["required"].remove("observation_digest")
    schema_name = ACCOUNTING_SCHEMA if name.startswith("deepeval_accounting") else TRACE_SCHEMA
    properties["schema"] = {"const": schema_name}
    properties["source"] = _identity()
    properties["reader_profile"] = {"const": reader}
    body["required"].append("reader_profile")
    _scope(body, extra)
    return body


def _account_branch(name: str, reader: str, limitations: tuple[str, ...]) -> dict[str, Any]:
    branch = _body(name, reader, dict(ACCOUNTING_SCOPE_EXTRA))
    base = [*limitations, *ACCOUNTING_LIMITATIONS_EXTRA]
    branch["properties"]["limitations"] = _limitations(base)
    if reader == NO_FLAKY_READER:
        branch["allOf"] = [
            {
                "if": {
                    "properties": {
                        "counts": {"properties": {"cases": {"properties": {"total": {"const": 0}}}}}
                    }
                },
                "then": {"properties": {"limitations": _limitations([*base, EMPTY_LIMITATION])}},
                "else": {"properties": {"limitations": _limitations(base)}},
            }
        ]
        # The conditional owns both allowed lists; the unconditional property permits their union.
        branch["properties"]["limitations"] = {
            "type": "array",
            "maxItems": len(base) + 1,
            "items": {"enum": [*base, EMPTY_LIMITATION]},
        }
    return branch


def build_accounting() -> dict[str, Any]:
    no_flaky = _account_branch("deepeval_accounting.v1", NO_FLAKY_READER, LIMITATIONS)
    flaky = _account_branch("deepeval_accounting.v2", FLAKY_READER, LIMITATIONS_V2)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:underwrite:contract:{ACCOUNTING_SCHEMA}",
        "title": ACCOUNTING_SCHEMA,
        "description": "Selected serialized DeepEval metric occurrences; producer version unknown.",
        "if": {
            "properties": {"reader_profile": {"const": NO_FLAKY_READER}},
            "required": ["reader_profile"],
        },
        "then": no_flaky,
        "else": flaky,
    }


def build_trace() -> dict[str, Any]:
    trace = _body("deepeval_trace_index.v1", TRACE_READER, dict(TRACE_SCOPE_EXTRA))
    trace["properties"]["limitations"] = {"const": [*TRACE_LIMITATIONS, *TRACE_LIMITATIONS_EXTRA]}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:underwrite:contract:{TRACE_SCHEMA}",
        "title": TRACE_SCHEMA,
        "description": "Selected DeepEval case and span structure; producer version unknown.",
        **trace,
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

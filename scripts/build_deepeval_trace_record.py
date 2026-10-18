"""Generate the selected DeepEval trace capture envelope from existing contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _record_schema import build_record_schema, schema_main

from underwrite.cli.record_deepeval import COMMAND, TRACE_RECORD_SCHEMA
from underwrite.instrument.evidence.deepeval_trace import SCHEMA as TRACE_SCHEMA
from underwrite.instrument.evidence.deepeval_trace import SOURCE_VERSION

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / f"contracts/{TRACE_RECORD_SCHEMA}.schema.json"


def build_schema() -> dict[str, Any]:
    analysis_owner, analysis_version = TRACE_SCHEMA.rsplit(".", 1)
    schema = build_record_schema(
        ROOT,
        accounting_owner=analysis_owner,
        schema_name=TRACE_RECORD_SCHEMA,
        accounting_version=analysis_version,
        command=COMMAND,
        description=(
            "Selected DeepEval trace index and private capture outcomes. "
            "A saved record verifies stored bytes, not producer execution, trace completeness "
            "or approval. Null published_record means no identity was returned, not proven absence."
        ),
    )
    schema["properties"]["source_format_version"] = {"const": SOURCE_VERSION}
    schema["required"].append("source_format_version")
    return schema


def main() -> int:
    return schema_main(build_schema(), TARGET, __doc__)


if __name__ == "__main__":
    raise SystemExit(main())

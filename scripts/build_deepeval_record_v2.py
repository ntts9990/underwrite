"""Generate the explicit DeepEval 4.2.8 capture envelope."""

from __future__ import annotations

from typing import Any

from _record_schema import schema_main
from build_deepeval_record import ROOT
from build_deepeval_record import build_schema as build_record

from underwrite.instrument.ingest.deepeval_accounting import PROFILES


def build_schema() -> dict[str, Any]:
    profile = PROFILES["4.2.8"]
    schema = build_record(format_version=profile.format_version)
    schema["properties"]["source_format_version"] = {"const": profile.format_version}
    schema["required"].append("source_format_version")
    return schema


def main() -> int:
    return schema_main(build_schema(), ROOT / "contracts/deepeval_record.v2.schema.json", __doc__)


if __name__ == "__main__":
    raise SystemExit(main())

"""Card: record-reuse | Paper: deepeval_record.v1 | Stage: storage-capture

Generate the fixed DeepEval capture envelope from existing owner contracts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _record_schema import build_record_schema, schema_main

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts/deepeval_record.v1.schema.json"


def build_schema() -> dict[str, Any]:
    return build_record_schema(
        ROOT,
        accounting_owner="deepeval_accounting",
        schema_name="deepeval_record.v1",
        command="records audit-deepeval",
        description=(
            "Independent DeepEval accounting and private capture outcomes. "
            "A saved record verifies stored bytes, not producer execution or approval. "
            "Null published_record means no identity was returned, not proven absence."
        ),
    )


def main() -> int:
    return schema_main(build_schema(), TARGET, __doc__)


if __name__ == "__main__":
    raise SystemExit(main())

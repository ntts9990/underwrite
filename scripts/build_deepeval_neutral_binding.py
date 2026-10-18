"""Compose current DeepEval binding targets from explicitly authored contract owners.

Evidence-result v2 is authored directly in contracts/evidence_result.v2.schema.json.
Inspector generation belongs to its own migration stage, not this producer build.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

if __package__:
    from .build_deepeval_bound_record import build_schema as build_record_v2
    from .build_deepeval_metric_bindings import build_schema as build_pack_v2
else:
    from build_deepeval_bound_record import build_schema as build_record_v2
    from build_deepeval_metric_bindings import build_schema as build_pack_v2

from underwrite.cli.record_deepeval_bound import AUTO_SCHEMA as AUTO_RECORD_SCHEMA
from underwrite.instrument.evidence.deepeval_binding import AUTO_PACK_SCHEMA

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, build in (
        (AUTO_PACK_SCHEMA, build_pack_v2),
        (AUTO_RECORD_SCHEMA, build_record_v2),
    ):
        target = ROOT / "contracts" / f"{name}.schema.json"
        expected = (json.dumps(build(), ensure_ascii=False, indent=2) + "\n").encode()
        if args.check:
            if not target.exists() or target.read_bytes() != expected:
                print(f"{target.name} is stale")
                return 1
        else:
            target.write_bytes(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

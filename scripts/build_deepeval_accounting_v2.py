"""Generate the explicit DeepEval 4.2.8 accounting contract."""

from __future__ import annotations

from _record_schema import schema_main
from build_deepeval_accounting import ROOT
from build_deepeval_accounting import build_schema as build_accounting


def build_schema() -> dict[str, object]:
    return build_accounting(format_version="4.2.8")


def main() -> int:
    return schema_main(
        build_schema(),
        ROOT / "contracts/deepeval_accounting.v2.schema.json",
        __doc__,
    )


if __name__ == "__main__":
    raise SystemExit(main())

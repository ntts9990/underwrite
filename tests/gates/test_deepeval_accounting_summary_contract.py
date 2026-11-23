"""DeepEval summary retains the full owner's two reader-profile conditions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator

from scripts import build_deepeval_accounting_summary as builder
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).parents[2]


def test_generated_deepeval_summary_is_installed_and_profile_derived() -> None:
    expected = (json.dumps(builder.build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    packaged = ROOT / "src/underwrite/_contracts" / builder.TARGET.name
    assert builder.TARGET.read_bytes() == expected
    assert packaged.resolve() == builder.TARGET.resolve()
    assert packaged.read_bytes() == expected
    source = json.loads(builder.SOURCE.read_bytes())
    summary = json.loads(expected)
    conditional = summary["allOf"][0]
    assert conditional["if"] == source["if"]
    for side in ("then", "else"):
        assert conditional[side]["allOf"] == source[side]["allOf"]
        for name in ("rule_profile", "counts", "limitations", "reader_profile"):
            assert conditional[side]["properties"][name] == (
                source[side]["properties"][name]
            )
    assert "cases" not in summary["properties"]
    packaged_validator("deepeval_accounting_summary.v1")


def test_both_reader_profiles_validate_and_empty_population_limit_is_required() -> None:
    from underwrite.cli.audit_deepeval import _summary_file  # pyright: ignore[reportPrivateUsage]

    validator = Draft202012Validator(builder.build_schema())
    for path in (
        ROOT / "fixtures/golden/external/deepeval/test_run_20260914_132634.json",
        ROOT / "fixtures/examples/deepeval-auto-4.3.1/ordinary.json",
    ):
        summary = _summary_file(path)
        assert validator.is_valid(cast(Any, summary))  # pyright: ignore[reportUnknownMemberType]
        assert "cases" not in summary

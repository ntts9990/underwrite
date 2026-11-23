"""The bounded owner-binding view derives identities and count vocabularies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator

from scripts import build_deepeval_bound_summary as builder
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).parents[2]


def test_generated_bound_summary_is_installed_and_owner_derived() -> None:
    expected = (json.dumps(builder.build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    packaged = ROOT / "src/underwrite/_contracts" / builder.TARGET.name
    assert builder.TARGET.read_bytes() == expected
    assert packaged.resolve() == builder.TARGET.resolve()
    assert packaged.read_bytes() == expected
    result = json.loads(builder.RESULT.read_bytes())
    inspection = json.loads(builder.INSPECTION.read_bytes())
    summary = json.loads(expected)
    for name in ("source", "declaration_source", "processing", "population"):
        assert summary["properties"][name]["allOf"][0] == result["properties"][name]
    for name in (
        "availability_counts", "item_next_action_counts", "declared_semantic_binding_counts"
    ):
        assert summary["properties"][name] == inspection["properties"][name]["oneOf"][1]
    packaged_validator("deepeval_bound_summary.v1")


def test_bound_summary_contract_rejects_owner_and_population_conflation() -> None:
    from underwrite.cli.audit_deepeval import (
        _bound_summary_file,  # pyright: ignore[reportPrivateUsage]
    )

    source = ROOT / "fixtures/examples/deepeval-auto-4.3.1/ordinary.json"
    pack = ROOT / "fixtures/examples/evidence/synthetic-deepeval.bindings.v2.json"
    valid = _bound_summary_file(source, pack)
    validator = Draft202012Validator(builder.build_schema())
    assert validator.is_valid(cast(Any, valid))  # pyright: ignore[reportUnknownMemberType]
    declaration = cast(dict[str, object], valid["declaration_source"])
    invalid: dict[str, Any] = {
        **valid, "declaration_source": {**declaration, "authority": "verified"}
    }
    assert not validator.is_valid(invalid)  # pyright: ignore[reportUnknownMemberType]
    population = cast(dict[str, object], valid["population"])
    invalid = {**valid, "population": {**population, "planned_count": 3}}
    assert not validator.is_valid(invalid)  # pyright: ignore[reportUnknownMemberType]
    invalid = {**valid, "items": []}
    assert not validator.is_valid(invalid)  # pyright: ignore[reportUnknownMemberType]

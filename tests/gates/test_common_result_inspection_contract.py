"""Repeated projections of the common-result contract stay aligned with its owner."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, ValidationError

from underwrite.cli.inspect_result import LIMITATIONS, inspect_result_file


def _schema(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(Path(f"contracts/{name}.schema.json").read_text()))


def test_declared_processing_and_population_vocabulary_matches_input_contract() -> None:
    source = _schema("evidence_result.v1")
    output = _schema("common_result_inspection.v1")
    assert output["properties"]["declared_processing"] == source["properties"]["processing"]

    source_population = source["$defs"]["population"]
    output_population = output["properties"]["declared_population"]["oneOf"][1]
    assert output_population["required"] == source_population["required"]
    for name in source_population["required"]:
        assert output_population["properties"][name] == {
            key: value for key, value in source_population["properties"][name].items()
            if key != "description"
        }


def test_fixed_limitations_match_the_output_contract() -> None:
    output = _schema("common_result_inspection.v1")
    assert output["properties"]["limitations"]["const"] == LIMITATIONS


def test_action_contract_is_generated_from_base_and_input_vocabulary() -> None:
    subprocess.run(
        [sys.executable, "scripts/build_common_result_inspection_actions.py", "--check"],
        check=True,
    )
    source = _schema("evidence_result.v1")
    base = _schema("common_result_inspection.v1")
    actions = _schema("common_result_inspection_actions.v1")
    extra = ("item_next_action_counts", "declared_semantic_binding_counts")
    assert actions["additionalProperties"] is False
    assert actions["required"] == [*base["required"], *extra]
    for name in base["properties"]:
        if name == "schema":
            continue
        assert actions["properties"][name] == base["properties"][name]
    assert actions["properties"]["declared_processing"] == source["properties"]["processing"]
    expected = (
        source["$defs"]["item"]["properties"]["next_action"]["enum"],
        [variant["properties"]["semantic_binding"]["const"]
         for variant in source["$defs"]["metric_binding"]["oneOf"]],
    )
    for field, names in zip(extra, expected, strict=True):
        variant = actions["properties"][field]["oneOf"][1]
        assert variant["additionalProperties"] is False
        assert variant["required"] == names
        assert list(variant["properties"]) == names
        assert all(value == {"type": "integer", "minimum": 0}
                   for value in variant["properties"].values())
        assert actions["allOf"][0]["then"]["properties"][field] == {"type": "object"}
        assert actions["allOf"][0]["else"]["properties"][field] == {"type": "null"}


@pytest.mark.parametrize("mutation", ["missing-field", "extra-key", "negative", "null-analyzed"])
def test_action_contract_closes_histograms(mutation: str) -> None:
    report = inspect_result_file(
        Path("fixtures/examples/evidence/common-result-c55.json"), view="actions"
    )
    counts = cast(dict[str, int], report["item_next_action_counts"])
    if mutation == "missing-field":
        del counts["rerun_source"]
    elif mutation == "extra-key":
        counts["private_metric_id"] = 1
    elif mutation == "negative":
        counts["none"] = -1
    else:
        report["item_next_action_counts"] = None
    with pytest.raises(ValidationError):
        Draft202012Validator(_schema("common_result_inspection_actions.v1")).validate(  # pyright: ignore[reportUnknownMemberType]
            report
        )


def test_action_contract_requires_null_histograms_without_analyzed_population() -> None:
    report = inspect_result_file(
        Path("fixtures/examples/evidence/common-result-c55.json"), view="actions"
    )
    report["declared_processing"] = {
        "state": "insufficient", "reason": "MISSING_INPUT", "next_action": "supply_input"
    }
    report["declared_population"] = None
    report["availability_counts"] = None
    report["checks"] = {
        "contract_shape": "valid", "item_invariants": "not_applicable",
        "source_bytes": "not_checked", "producer_authentication": "not_checked",
        "verification_refs": "not_reperformed",
    }
    with pytest.raises(ValidationError):
        Draft202012Validator(_schema("common_result_inspection_actions.v1")).validate(  # pyright: ignore[reportUnknownMemberType]
            report
        )

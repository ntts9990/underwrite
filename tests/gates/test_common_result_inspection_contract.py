"""Repeated projections of the common-result contract stay aligned with its owner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from underwrite.cli.inspect_result import LIMITATIONS


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

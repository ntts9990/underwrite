"""Build a marginal comparison contract from the installed inspection vocabulary."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator

if __package__:
    from ._schema_compose import nullable, object_schema
else:
    from _schema_compose import nullable, object_schema
from underwrite.cli.compare_results import (
    COMPUTED_TASKS,
    INELIGIBLE_TASKS,
    LIMITATIONS,
    RESULT_SCHEMA,
)
from underwrite.cli.inspect_result import ACTIONS_SCHEMA
from underwrite.instrument.evidence.common_result import (
    MARGINAL_INELIGIBILITY_REASONS,
    POPULATION_UNIT_MISMATCH,
    RESULT_FORMAT_MISMATCH,
    RESULT_NOT_ANALYZED,
)

ROOT = Path(__file__).resolve().parents[1]


def _histogram_pair(source: dict[str, Any]) -> dict[str, Any]:
    histogram = cast(dict[str, Any], source["oneOf"][1])
    names = cast(list[str], histogram["required"])
    if set(names) != set(histogram["properties"]):
        raise ValueError("INVALID_INSPECTION_HISTOGRAM")
    return nullable(
        object_schema(
            {
                "baseline": deepcopy(histogram),
                "candidate": deepcopy(histogram),
                "delta": object_schema({name: {"type": "integer"} for name in names}),
            }
        )
    )


def _pair_values(root: str, field: str, baseline: str, candidate: str) -> dict[str, Any]:
    return {
        "properties": {
            root: {
                "properties": {
                    "baseline": {"properties": {field: {"const": baseline}}},
                    "candidate": {"properties": {field: {"const": candidate}}},
                }
            }
        }
    }


def _pair_relation(root: str, field: str, values: list[str], *, same: bool) -> dict[str, Any]:
    return {
        "oneOf": [
            _pair_values(root, field, baseline, candidate)
            for baseline in values
            for candidate in values
            if (baseline == candidate) is same
        ]
    }


def _calculation_branch(
    state: str, reason: str | None, tasks: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "properties": {
            "marginal_calculation": {
                "properties": {"state": {"const": state}, "reason": {"const": reason}}
            },
            "next_verification_tasks": {"const": tasks},
        }
    }


def _population_presence(side: str) -> dict[str, Any]:
    return {
        "if": {
            "properties": {
                "declared_processing": {
                    "properties": {side: {"properties": {"state": {"const": "analyzed"}}}}
                }
            }
        },
        "then": {"properties": {"declared_population": {"properties": {side: {"type": "object"}}}}},
        "else": {"properties": {"declared_population": {"properties": {side: {"type": "null"}}}}},
    }


def build_schema() -> dict[str, Any]:
    owner = cast(
        dict[str, Any],
        json.loads((ROOT / f"contracts/{ACTIONS_SCHEMA}.schema.json").read_bytes()),
    )
    fields = cast(dict[str, Any], owner["properties"])
    population = cast(dict[str, Any], fields["declared_population"])["oneOf"][1]
    unit = deepcopy(population["properties"]["unit"])
    units = cast(list[str], unit["enum"])
    formats = cast(list[str], fields["selected_result"]["properties"]["format"]["enum"])
    histograms = {
        name: _histogram_pair(cast(dict[str, Any], fields[name]))
        for name in (
            "availability_counts",
            "item_next_action_counts",
            "declared_semantic_binding_counts",
        )
    }
    calculation = object_schema(
        {
            "state": {"enum": ["computed", "not_computed"]},
            "reason": nullable(
                {
                    "enum": [
                        *MARGINAL_INELIGIBILITY_REASONS,
                    ]
                }
            ),
            "direction": {"const": "candidate_minus_baseline"},
            "population_unit": nullable(unit),
            "observed_population_count_delta": nullable({"type": "integer"}),
            "planned_population_count_delta": nullable({"type": "integer"}),
            **histograms,
        }
    )
    calculation["allOf"] = [
        {
            "if": {"properties": {"state": {"const": "computed"}}},
            "then": {
                "properties": {
                    "reason": {"type": "null"},
                    "population_unit": unit,
                    "observed_population_count_delta": {"type": "integer"},
                    **{name: {"type": "object"} for name in histograms},
                }
            },
            "else": {
                "properties": {
                    "reason": {"type": "string"},
                    "population_unit": {"type": "null"},
                    "observed_population_count_delta": {"type": "null"},
                    "planned_population_count_delta": {"type": "null"},
                    **{name: {"type": "null"} for name in histograms},
                }
            },
        }
    ]
    selected = cast(dict[str, Any], fields["selected_result"])
    processing = cast(dict[str, Any], fields["declared_processing"])
    declared_population = cast(dict[str, Any], fields["declared_population"])
    tasks = [
        COMPUTED_TASKS,
        INELIGIBLE_TASKS[RESULT_NOT_ANALYZED],
        INELIGIBLE_TASKS[RESULT_FORMAT_MISMATCH],
    ]
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + RESULT_SCHEMA,
        "title": RESULT_SCHEMA,
        "description": (
            "Descriptive differences between two selected common-result marginal summaries. "
            "No item correspondence, semantic equivalence or measured effect is established."
        ),
        **object_schema(
            {
                "schema": {"const": RESULT_SCHEMA},
                "selected_results": object_schema(
                    {"baseline": deepcopy(selected), "candidate": deepcopy(selected)}
                ),
                "declared_processing": object_schema(
                    {"baseline": deepcopy(processing), "candidate": deepcopy(processing)}
                ),
                "declared_population": object_schema(
                    {
                        "baseline": deepcopy(declared_population),
                        "candidate": deepcopy(declared_population),
                    }
                ),
                "marginal_calculation": calculation,
                "item_correspondence": {
                    "const": {
                        "state": "not_established",
                        "reason": "CROSS_RESULT_ITEM_BINDING_NOT_SUPPLIED",
                    }
                },
                "semantic_comparability": {"const": "unestablished"},
                "next_verification_tasks": {"oneOf": [{"const": task} for task in tasks]},
                "limitations": {"const": list(LIMITATIONS)},
            }
        ),
    }
    same_formats = _pair_relation("selected_results", "format", formats, same=True)
    different_formats = _pair_relation("selected_results", "format", formats, same=False)
    same_units = _pair_relation("declared_population", "unit", units, same=True)
    different_units = _pair_relation("declared_population", "unit", units, same=False)
    both_analyzed = _pair_values("declared_processing", "state", "analyzed", "analyzed")
    schema["allOf"] = [
        _population_presence("baseline"),
        _population_presence("candidate"),
        {
            "oneOf": [
                {
                    "allOf": [
                        same_formats,
                        both_analyzed,
                        same_units,
                        _calculation_branch("computed", None, COMPUTED_TASKS),
                    ]
                },
                {
                    "allOf": [
                        different_formats,
                        _calculation_branch(
                            "not_computed",
                            RESULT_FORMAT_MISMATCH,
                            INELIGIBLE_TASKS[RESULT_FORMAT_MISMATCH],
                        ),
                    ]
                },
                {
                    "allOf": [
                        same_formats,
                        {"not": both_analyzed},
                        _calculation_branch(
                            "not_computed",
                            RESULT_NOT_ANALYZED,
                            INELIGIBLE_TASKS[RESULT_NOT_ANALYZED],
                        ),
                    ]
                },
                {
                    "allOf": [
                        same_formats,
                        both_analyzed,
                        different_units,
                        _calculation_branch(
                            "not_computed",
                            POPULATION_UNIT_MISMATCH,
                            INELIGIBLE_TASKS[POPULATION_UNIT_MISMATCH],
                        ),
                    ]
                },
            ]
        },
    ]
    Draft202012Validator.check_schema(schema)
    return schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = ROOT / f"contracts/{RESULT_SCHEMA}.schema.json"
    encoded = (json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if not target.exists() or target.read_bytes() != encoded:
            print(f"{target.name} is stale")
            return 1
    else:
        target.write_bytes(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

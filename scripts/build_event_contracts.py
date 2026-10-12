"""Card: measurement-semantics | Paper: ADR-020 | Stage: semantic-repair.

Generate closed representations from shared measurement contract vocabulary.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _schema_compose import nullable, object_schema

from underwrite.measurement_contract.semantics import (
    ALPHA_TOTAL,
    ASSUMPTIONS,
    DECISION_REASONS,
    DIAGNOSTIC_REASONS,
    FORECAST_LIMITATION,
    LIMITATIONS,
    MAX_CASES,
    MAX_IDENTITY,
    MAX_TEXT,
    PROFILE,
    SIDE_ALPHA,
    STATUS_VERDICTS,
    THRESHOLD,
)

ROOT = Path(__file__).resolve().parents[1]
JSON = dict[str, Any]


def constant(value: object) -> JSON:
    return {"const": value}


def enum(values: tuple[str, ...]) -> JSON:
    return {"enum": list(values)}


def integer(minimum: int = 0, maximum: int = MAX_CASES) -> JSON:
    return {"type": "integer", "minimum": minimum, "maximum": maximum}


def array(item: JSON, maximum: int = MAX_CASES, minimum: int = 0, unique: bool = False) -> JSON:
    value: JSON = {"type": "array", "items": item, "minItems": minimum, "maxItems": maximum}
    if unique:
        value["uniqueItems"] = True
    return value


def string(maximum: int = MAX_IDENTITY) -> JSON:
    return {"type": "string", "minLength": 1, "maxLength": maximum, "pattern": r"\S"}


def probability() -> JSON:
    return {"type": "number", "minimum": 0, "maximum": 1}


def digest() -> JSON:
    return {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}


def assumptions() -> JSON:
    return object_schema({key: enum(("declared", "unknown")) for key in ASSUMPTIONS})


def subject() -> JSON:
    return object_schema({"subject_id": string(), "conditions": object_schema({})})


def semantics() -> JSON:
    provenance = object_schema({"id": string(), "version": string(), "reference": string(MAX_TEXT)})
    prediction = object_schema(
        {
            "status": constant("declared_probability_of_event"),
            "instrument": string(),
            "metric_id": string(),
            "event_id": string(),
            "timing": enum(("before_outcome", "unknown")),
            "reference": string(MAX_TEXT),
        }
    )
    return object_schema(
        {
            "schema": constant("measurement_semantics.v1"),
            "profile": constant(PROFILE),
            "construct": object_schema({"id": string(), "definition": string(MAX_TEXT)}),
            "event": object_schema({"id": string(), "positive_label_definition": string(MAX_TEXT)}),
            "unit": constant("proportion"),
            "sampling_unit": constant("case"),
            "desirable_direction": enum(("higher", "lower")),
            "outcome": object_schema(
                {
                    "instrument": string(),
                    "metric_id": string(),
                    "label_source": provenance,
                    "rubric": provenance,
                }
            ),
            "prediction": {
                "oneOf": [object_schema({"status": constant("unavailable")}), prediction]
            },
            "population": object_schema({"frame_id": string(), "description": string(MAX_TEXT)}),
        }
    )


def policy() -> JSON:
    return object_schema(
        {
            "schema": constant("measurement_policy.v3"),
            "profile": constant(PROFILE),
            "semantic_digest": digest(),
            "subject": subject(),
            "scope": constant("aggregate"),
            "expected_case_ids": array(string(), minimum=1, unique=True),
            "eprocess": object_schema(
                {
                    "p0": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
                    "alpha_total": constant(ALPHA_TOTAL),
                    "prior_a": constant(1),
                    "prior_b": constant(1),
                    "max_observations": nullable(integer(1, 2**53 - 1)),
                }
            ),
            "assumptions": assumptions(),
            "diagnostics": object_schema(
                {
                    "ece_bins": integer(1, 128),
                    "min_bin_n": integer(1, 2**53 - 1),
                    "all_k": integer(1),
                }
            ),
        }
    )


def diagnostic(method: str, unit: str, extras: JSON | None = None) -> JSON:
    return object_schema(
        {
            "method": constant(method),
            "unit": constant(unit),
            "availability": enum(("measured", "not_measured")),
            "value": nullable(probability()),
            "n": integer(),
            "reason_codes": array(
                enum(DIAGNOSTIC_REASONS), maximum=len(DIAGNOSTIC_REASONS), unique=True
            ),
            **(extras or {}),
        }
    )


def read() -> JSON:
    counts = {
        "observed_count": integer(),
        "missing_ids": array(string(), unique=True),
        "error_ids": array(string(), unique=True),
    }
    # Dispatch before traversing unique arrays; do not put them in exhaustive oneOf.
    predictions = {
        "type": "object",
        "required": ["status"],
        "properties": {"status": enum(("unavailable", "declared"))},
        "allOf": [
            {
                "if": {"properties": {"status": constant("unavailable")}},
                "then": object_schema(
                    {"status": constant("unavailable"), **{key: {"type": "null"} for key in counts}}
                ),
            },
            {
                "if": {"properties": {"status": constant("declared")}},
                "then": object_schema({"status": constant("declared"), **counts}),
            },
        ],
    }
    directional = object_schema(
        {
            "method": constant("beta-mixture-two-direction.v1"),
            "availability": enum(("measured", "not_measured")),
            "p0": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
            "alpha_total": constant(ALPHA_TOTAL),
            "side_alpha": constant(SIDE_ALPHA),
            "threshold": constant(THRESHOLD),
            "prior_a": constant(1),
            "prior_b": constant(1),
            "max_observations": nullable(integer(1, 2**53 - 1)),
            "desirable_direction": enum(("higher", "lower")),
            "error_control": object_schema(
                {
                    "method": constant("bonferroni_equal_two_sides"),
                    "upper_null": constant("conditional_mean_lte_p0"),
                    "lower_null": constant("conditional_mean_gte_p0"),
                    "per_side_alpha": constant(SIDE_ALPHA),
                    "point_null_union_alpha": constant(ALPHA_TOTAL),
                }
            ),
            "assumptions": assumptions(),
            "e_upper": nullable({"type": "number", "minimum": 0}),
            "e_lower": nullable({"type": "number", "minimum": 0}),
            "consumed_count": integer(),
            "successes": integer(),
            "failures": integer(),
            "unused_observed_count": integer(),
            "stop_reason": enum(
                (
                    "upper_crossing",
                    "lower_crossing",
                    "conflicting_crossing",
                    "budget_observations",
                    "end_of_population",
                    "withheld",
                )
            ),
        }
    )
    return object_schema(
        {
            "schema": constant("read.v2"),
            "profile": constant(PROFILE),
            "scope": constant("aggregate"),
            "subject": subject(),
            "semantics": semantics(),
            "semantic_digest": digest(),
            "sources": object_schema(
                {"observations": array(digest(), minimum=1, maximum=1), "policy": digest()}
            ),
            "population": object_schema(
                {
                    "expected": object_schema(
                        {"count": integer(1), "ordered_inventory_digest": digest()}
                    ),
                    "outcomes": object_schema(counts),
                    "predictions": predictions,
                }
            ),
            "directional": directional,
            "diagnostics": object_schema(
                {
                    "event_rate": diagnostic(
                        "observed-event-fraction.v1",
                        "proportion",
                        {"scope": constant("observed_expected_cases"), "positive_count": integer()},
                    ),
                    "brier": diagnostic("brier-mean-squared-error.v1", "squared_probability"),
                    "ece": diagnostic(
                        "fixed-width-absolute-gap.v1",
                        "probability",
                        {
                            "bins": integer(1, 128),
                            "min_bin_n": integer(1, 2**53 - 1),
                            "bin_counts": array(integer(), maximum=128, minimum=1),
                        },
                    ),
                    "all_k_success": diagnostic(
                        "uniform-subset-all-success.v1",
                        "fraction_of_k_subsets",
                        {"estimand": constant("all_k_satisfied"), "c": integer(), "k": integer(1)},
                    ),
                }
            ),
            "decision": object_schema({"status": enum(tuple(STATUS_VERDICTS))}),
            "verdict": enum(tuple(STATUS_VERDICTS.values())),
            "reason_codes": array(
                enum(DECISION_REASONS), minimum=1, maximum=len(DECISION_REASONS), unique=True
            ),
            "limitations": array(
                enum((*LIMITATIONS, FORECAST_LIMITATION)),
                minimum=len(LIMITATIONS),
                maximum=len(LIMITATIONS) + 1,
                unique=True,
            ),
        }
    )


def build_schemas() -> dict[str, JSON]:
    return {
        name: {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:underwrite:contract:" + name,
            "title": name,
            "description": (
                "Closed representation. Shared pure owners additionally enforce "
                "semantic and cross-field invariants."
            ),
            **body,
        }
        for name, body in (
            ("measurement_semantics.v1", semantics()),
            ("measurement_policy.v3", policy()),
            ("read.v2", read()),
        )
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = False
    for name, schema in build_schemas().items():
        target = ROOT / "contracts" / (name + ".schema.json")
        raw = (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode()
        if args.check:
            if not target.exists() or target.read_bytes() != raw:
                print(target.name + " is stale")
                stale = True
        else:
            target.write_bytes(raw)
    return int(stale)


if __name__ == "__main__":
    raise SystemExit(main())

"""Build the reusable DeepEval name binding pack from the metric definition owner."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _record_schema import schema_main
from _schema_compose import embed_contracts, object_schema, reference

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts/deepeval_metric_bindings.v1.schema.json"


def build_schema() -> dict[str, Any]:
    definitions, _ = embed_contracts(ROOT, ("metric_definition",))
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:deepeval_metric_bindings.v1",
        "title": "deepeval_metric_bindings.v1",
        "description": (
            "Owner-asserted stable DeepEval source-name mapping for one pinned profile. "
            "Names do not verify evaluator configuration or producer truth."
        ),
        **object_schema(
            {
                "schema": {"const": "deepeval_metric_bindings.v1"},
                "source": object_schema(
                    {
                        "format": {"const": "deepeval.test-run"},
                        "format_version": {"const": "4.2.8"},
                    }
                ),
                "name_binding_authority": {"const": "owner_asserted_stable_name_not_verified"},
                "bindings": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 64,
                    "items": object_schema(
                        {
                            "source_metric_name": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 256,
                                "pattern": "\\S",
                            },
                            "definition": reference("metric_definition"),
                        }
                    ),
                },
            }
        ),
        "$defs": definitions,
    }


if __name__ == "__main__":
    raise SystemExit(schema_main(build_schema(), TARGET, __doc__))

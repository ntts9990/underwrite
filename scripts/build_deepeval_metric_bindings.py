"""Build the reusable DeepEval name binding pack from the metric definition owner."""

from __future__ import annotations

from pathlib import Path
from typing import Any

if __package__:
    from ._record_schema import schema_main
    from ._schema_compose import embed_contracts, object_schema, reference
else:
    from _record_schema import schema_main
    from _schema_compose import embed_contracts, object_schema, reference

from underwrite.instrument.evidence.deepeval_binding import AUTO_PACK_SCHEMA
from underwrite.instrument.evidence.deepeval_compat import SOURCE_FORMAT

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / f"contracts/{AUTO_PACK_SCHEMA}.schema.json"


def build_schema() -> dict[str, Any]:
    definitions, _ = embed_contracts(ROOT, ("metric_definition",))
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + AUTO_PACK_SCHEMA,
        "title": AUTO_PACK_SCHEMA,
        "description": (
            "Version-neutral owner-asserted metric-name meanings for supplied ordinary "
            "DeepEval occurrences; no evaluator or producer-release verification."
        ),
        **object_schema(
            {
                "schema": {"const": AUTO_PACK_SCHEMA},
                "source": object_schema(
                    {
                        "format": {"const": SOURCE_FORMAT},
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

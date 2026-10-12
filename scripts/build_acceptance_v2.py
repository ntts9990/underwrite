"""Card: measurement-semantics | Paper: ADR-020 | Stage: semantic-repair.

Generate current acceptance contracts from retained structural contracts and owners.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from underwrite.acceptance import claims, decision
from underwrite.acceptance.classify import PARTIAL_FAIL
from underwrite.measurement_contract.semantics import DECISION_REASONS, PROFILE

ROOT = Path(__file__).resolve().parents[1]


def build_schemas() -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for name in ("declared_claims", "acceptance_decision"):
        value = json.loads((ROOT / f"contracts/{name}.v1.schema.json").read_bytes())
        current = f"{name}.v2"
        value["$id"] = f"urn:underwrite:contract:{current}"
        value["title"] = current
        value["properties"]["schema"] = {"const": current}
        schemas[current] = value
    declared = schemas[claims.SCHEMA]
    claim = declared["$defs"]["claim"]
    claim["required"].append("semantic_digest")
    claim["properties"]["semantic_digest"] = {"$ref": "#/$defs/sha256_digest"}
    result = schemas[decision.SCHEMA]
    summary_claim = result["$defs"]["claim"]
    summary_claim["required"].append("semantic_digest")
    summary_claim["properties"]["semantic_digest"] = {"$ref": "#/$defs/sha256_digest"}
    result["$defs"]["read_reason_codes"] = {
        "type": "array",
        "minItems": 1,
        "maxItems": len(DECISION_REASONS),
        "items": {"type": "string", "enum": list(DECISION_REASONS)},
        "uniqueItems": True,
        "description": "Shared read.v2 decision reasons.",
    }
    for key in ("measured_read", "not_measured_read"):
        summary = result["$defs"][key]
        summary["required"].remove("escalate")
        summary["required"].extend(["schema", "profile"])
        del summary["properties"]["escalate"]
        summary["properties"].update(schema={"const": "read.v2"}, profile={"const": PROFILE})
    limitations = result["properties"]["limitations"]
    limitations["items"]["enum"] = [*decision.LIMITATIONS, PARTIAL_FAIL]
    limitations["allOf"] = [{"contains": {"const": item}} for item in decision.LIMITATIONS]
    return schemas


if __name__ == "__main__":
    for name, schema in build_schemas().items():
        (ROOT / f"contracts/{name}.schema.json").write_text(json.dumps(schema, indent=2) + "\n")

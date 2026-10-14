"""Generate Langfuse accounting's contract from the owned rules and limits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from underwrite.instrument.evidence.langfuse_accounting import (
    ASSOCIATION_RULES,
    KIND_RULES,
    LIMITATIONS,
    MAX_ID_LENGTH,
    MAX_LABEL_LENGTH,
    MAX_OBSERVATIONS,
    MAX_SCORES,
    MAX_STRING_VALUE_LENGTH,
    NEXT_ACTIONS,
    RULE_PROFILE,
    SCOPE,
)
from underwrite.instrument.evidence.langfuse_row_index import (
    LIMITATIONS as ROW_INDEX_LIMITATIONS,
)
from underwrite.instrument.evidence.langfuse_row_index import (
    SCOPE as ROW_INDEX_SCOPE,
)
from underwrite.instrument.ingest.langfuse_accounting import SOURCES

ROOT = Path(__file__).resolve().parents[1]


def _object(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _count(maximum: int) -> dict[str, object]:
    return {"type": "integer", "minimum": 0, "maximum": maximum}


def _tag(field: str, value: str, properties: dict[str, object]) -> dict[str, object]:
    return {
        "if": {"properties": {field: {"const": value}}, "required": [field]},
        "then": {"properties": properties},
    }


def build_schema() -> dict[str, object]:
    identifier = {"type": "string", "minLength": 1, "maxLength": MAX_ID_LENGTH}
    label = {"type": "string", "maxLength": MAX_LABEL_LENGTH}
    digest = {"type": "string", "pattern": "^sha256:[a-f0-9]{64}(?![\\s\\S])"}
    score = _object(
        {
            "score_index": _count(MAX_SCORES - 1),
            "id": identifier,
            "project_id": identifier,
            "trace_id": identifier,
            "observation_id": {
                "type": ["string", "null"],
                "minLength": 1,
                "maxLength": MAX_ID_LENGTH,
            },
            "name": label,
            "data_type": {"enum": list(KIND_RULES)},
            "value": {"type": "number"},
            "string_value": {"type": ["string", "null"], "maxLength": MAX_STRING_VALUE_LENGTH},
            "kind_rule": {"enum": list(KIND_RULES.values())},
            "association": {"enum": list(ASSOCIATION_RULES)},
            "association_rule": {"enum": list(ASSOCIATION_RULES.values())},
            "target_observation_index": {
                "type": ["integer", "null"],
                "minimum": 0,
                "maximum": MAX_OBSERVATIONS - 1,
            },
        }
    )
    branches = [
        _tag("data_type", kind, {"kind_rule": {"const": rule}}) for kind, rule in KIND_RULES.items()
    ]
    for association, rule in ASSOCIATION_RULES.items():
        branches.append(
            _tag(
                "association",
                association,
                {
                    "association_rule": {"const": rule},
                    "observation_id": {"type": "null"}
                    if association == "trace_level"
                    else identifier,
                    "target_observation_index": _count(MAX_OBSERVATIONS - 1)
                    if association == "observation_local"
                    else {"type": "null"},
                },
            )
        )
    score["allOf"] = branches
    observation = _object(
        {
            "observation_index": _count(MAX_OBSERVATIONS - 1),
            "id": identifier,
            "project_id": identifier,
            "trace_id": identifier,
            "type": label,
            "local_score_count": _count(MAX_SCORES),
        }
    )
    schema = _object(
        {
            "schema": {"const": "langfuse_accounting.v1"},
            "rule_profile": {"const": RULE_PROFILE},
            "sources": _object(
                {
                    role: _object(
                        {
                            "format": {"const": selector[0]},
                            "format_version": {"const": selector[1]},
                            "raw_hash": digest,
                            "observation_digest": digest,
                            "claims_verified": {"const": False},
                        }
                    )
                    for role, selector in SOURCES.items()
                }
            ),
            "scope": _object({key: {"const": value} for key, value in SCOPE.items()}),
            "observations": {"type": "array", "maxItems": MAX_OBSERVATIONS, "items": observation},
            "scores": {"type": "array", "maxItems": MAX_SCORES, "items": score},
            "counts": _object(
                {
                    "observations": _count(MAX_OBSERVATIONS),
                    "scores": _count(MAX_SCORES),
                    "by_data_type": _object({key: _count(MAX_SCORES) for key in KIND_RULES}),
                    "associations": _object({key: _count(MAX_SCORES) for key in ASSOCIATION_RULES}),
                    "observations_without_local_scores": _count(MAX_OBSERVATIONS),
                }
            ),
            "planned": _object(
                {
                    "status": {"const": "unknown"},
                    "count": {"const": None},
                    "missing_execution_count": {"const": None},
                }
            ),
            "limitations": {
                "type": "array",
                "maxItems": len(LIMITATIONS),
                "items": {"type": "string", "enum": list(LIMITATIONS)},
                "const": list(LIMITATIONS),
            },
            "next_actions": {
                "type": "array",
                "maxItems": len(NEXT_ACTIONS),
                "items": {"type": "string", "enum": list(NEXT_ACTIONS)},
                "uniqueItems": True,
            },
        }
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:langfuse_accounting.v1",
        "title": "langfuse_accounting.v1",
        "description": "Supplied score rows and exact field links; no task verdict.",
        **schema,
    }


def build_row_index_schema() -> dict[str, object]:
    digest = {"type": "string", "pattern": "^sha256:[a-f0-9]{64}(?![\\s\\S])"}
    row = _object(
        {
            "score_index": _count(MAX_SCORES - 1),
            "data_type": {"enum": list(KIND_RULES)},
            "numeric_field_presence": {"const": "present"},
            "string_field_presence": {"enum": ["present", "null"]},
            "association": {"enum": list(ASSOCIATION_RULES)},
            "target_observation_index": {
                "type": ["integer", "null"],
                "minimum": 0,
                "maximum": MAX_OBSERVATIONS - 1,
            },
        }
    )
    row["allOf"] = [
        _tag(
            "association",
            association,
            {
                "target_observation_index": _count(MAX_OBSERVATIONS - 1)
                if association == "observation_local"
                else {"type": "null"}
            },
        )
        for association in ASSOCIATION_RULES
    ]
    schema = _object(
        {
            "schema": {"const": "langfuse_row_index.v1"},
            "source_rule_profile": {"const": RULE_PROFILE},
            "sources": _object(
                {
                    role: _object(
                        {
                            "format": {"const": selector[0]},
                            "format_version": {"const": selector[1]},
                            "raw_hash": digest,
                            "claims_verified": {"const": False},
                        }
                    )
                    for role, selector in SOURCES.items()
                }
            ),
            "scope": _object({key: {"const": value} for key, value in ROW_INDEX_SCOPE.items()}),
            "counts": _object(
                {
                    "observations": _count(MAX_OBSERVATIONS),
                    "scores": _count(MAX_SCORES),
                    "by_data_type": _object({key: _count(MAX_SCORES) for key in KIND_RULES}),
                    "associations": _object({key: _count(MAX_SCORES) for key in ASSOCIATION_RULES}),
                    "observations_without_local_scores": _count(MAX_OBSERVATIONS),
                }
            ),
            "score_rows": {"type": "array", "maxItems": MAX_SCORES, "items": row},
            "verification": {"const": []},
            "limitations": {"const": list(ROW_INDEX_LIMITATIONS)},
            "next_actions": {
                "type": "array",
                "maxItems": len(NEXT_ACTIONS),
                "items": {"enum": list(NEXT_ACTIONS)},
                "uniqueItems": True,
            },
        }
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:langfuse_row_index.v1",
        "title": "langfuse_row_index.v1",
        "description": "Content-minimized structural view of two independently selected exports.",
        **schema,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    targets = {
        ROOT / "contracts/langfuse_accounting.v1.schema.json": build_schema(),
        ROOT / "contracts/langfuse_row_index.v1.schema.json": build_row_index_schema(),
    }
    if args.check:
        return 0 if all(
            target.exists()
            and target.read_text(encoding="utf-8")
            == json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
            for target, schema in targets.items()
        ) else 1
    for target, schema in targets.items():
        target.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

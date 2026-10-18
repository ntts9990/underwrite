"""Build current Langfuse blob branches from their score and association owners."""

from __future__ import annotations

from collections.abc import Mapping

if __package__:
    from ._schema_compose import object_schema as _object
else:
    from _schema_compose import object_schema as _object

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
from underwrite.instrument.evidence.langfuse_compat import (
    ACCOUNTING_SCHEMA,
    BLOB_READER,
    EXTRA_SCOPE,
    ROLES,
    ROW_INDEX_SCHEMA,
    SOURCE_FORMATS,
    extended_limitations,
)
from underwrite.instrument.evidence.langfuse_row_index import (
    LIMITATIONS as ROW_INDEX_LIMITATIONS,
)
from underwrite.instrument.evidence.langfuse_row_index import (
    SCOPE as ROW_INDEX_SCOPE,
)

HASH = {"type": "string", "pattern": "^sha256:[a-f0-9]{64}(?![\\s\\S])"}


def source_schema(reader: str) -> dict[str, object]:
    return _object(
        {
            role: _object(
                {
                    "format": {"const": SOURCE_FORMATS[reader][role]},
                    "raw_hash": dict(HASH),
                    "producer_version": {"type": "null"},
                    "producer_version_state": {"const": "unknown"},
                    "claims_verified": {"const": False},
                }
            )
            for role in ROLES
        }
    )


def scope_schema(values: Mapping[str, object]) -> dict[str, object]:
    return _object({key: {"const": value} for key, value in {**values, **EXTRA_SCOPE}.items()})


def count_schema(maximum: int) -> dict[str, object]:
    return {"type": "integer", "minimum": 0, "maximum": maximum}


def tag_schema(field: str, value: str, properties: dict[str, object]) -> dict[str, object]:
    return {
        "if": {"properties": {field: {"const": value}}, "required": [field]},
        "then": {"properties": properties},
    }


def build_accounting_branch() -> dict[str, object]:
    identifier = {"type": "string", "minLength": 1, "maxLength": MAX_ID_LENGTH}
    label = {"type": "string", "maxLength": MAX_LABEL_LENGTH}
    score = _object(
        {
            "score_index": count_schema(MAX_SCORES - 1),
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
        tag_schema("data_type", kind, {"kind_rule": {"const": rule}})
        for kind, rule in KIND_RULES.items()
    ]
    for association, rule in ASSOCIATION_RULES.items():
        branches.append(
            tag_schema(
                "association",
                association,
                {
                    "association_rule": {"const": rule},
                    "observation_id": {"type": "null"}
                    if association == "trace_level"
                    else identifier,
                    "target_observation_index": count_schema(MAX_OBSERVATIONS - 1)
                    if association == "observation_local"
                    else {"type": "null"},
                },
            )
        )
    score["allOf"] = branches
    observation = _object(
        {
            "observation_index": count_schema(MAX_OBSERVATIONS - 1),
            "id": identifier,
            "project_id": identifier,
            "trace_id": identifier,
            "type": label,
            "local_score_count": count_schema(MAX_SCORES),
        }
    )
    schema = _object(
        {
            "schema": {"const": ACCOUNTING_SCHEMA},
            "rule_profile": {"const": RULE_PROFILE},
            "sources": source_schema(BLOB_READER),
            "scope": scope_schema(SCOPE),
            "observations": {"type": "array", "maxItems": MAX_OBSERVATIONS, "items": observation},
            "scores": {"type": "array", "maxItems": MAX_SCORES, "items": score},
            "counts": _object(
                {
                    "observations": count_schema(MAX_OBSERVATIONS),
                    "scores": count_schema(MAX_SCORES),
                    "by_data_type": _object({key: count_schema(MAX_SCORES) for key in KIND_RULES}),
                    "associations": _object(
                        {key: count_schema(MAX_SCORES) for key in ASSOCIATION_RULES}
                    ),
                    "observations_without_local_scores": count_schema(MAX_OBSERVATIONS),
                }
            ),
            "planned": _object(
                {
                    "status": {"const": "unknown"},
                    "count": {"const": None},
                    "missing_execution_count": {"const": None},
                }
            ),
            "limitations": {"const": extended_limitations(list(LIMITATIONS))},
            "next_actions": {
                "type": "array",
                "maxItems": len(NEXT_ACTIONS),
                "items": {"type": "string", "enum": list(NEXT_ACTIONS)},
                "uniqueItems": True,
            },
            "reader_profile": {"const": BLOB_READER},
        }
    )
    return schema


def build_row_branch() -> dict[str, object]:
    row = _object(
        {
            "score_index": count_schema(MAX_SCORES - 1),
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
        tag_schema(
            "association",
            association,
            {
                "target_observation_index": count_schema(MAX_OBSERVATIONS - 1)
                if association == "observation_local"
                else {"type": "null"}
            },
        )
        for association in ASSOCIATION_RULES
    ]
    schema = _object(
        {
            "schema": {"const": ROW_INDEX_SCHEMA},
            "source_rule_profile": {"const": RULE_PROFILE},
            "sources": source_schema(BLOB_READER),
            "scope": scope_schema(ROW_INDEX_SCOPE),
            "counts": _object(
                {
                    "observations": count_schema(MAX_OBSERVATIONS),
                    "scores": count_schema(MAX_SCORES),
                    "by_data_type": _object({key: count_schema(MAX_SCORES) for key in KIND_RULES}),
                    "associations": _object(
                        {key: count_schema(MAX_SCORES) for key in ASSOCIATION_RULES}
                    ),
                    "observations_without_local_scores": count_schema(MAX_OBSERVATIONS),
                }
            ),
            "score_rows": {"type": "array", "maxItems": MAX_SCORES, "items": row},
            "verification": {"const": []},
            "limitations": {"const": extended_limitations(list(ROW_INDEX_LIMITATIONS))},
            "next_actions": {
                "type": "array",
                "maxItems": len(NEXT_ACTIONS),
                "items": {"enum": list(NEXT_ACTIONS)},
                "uniqueItems": True,
            },
            "reader_profile": {"const": BLOB_READER},
        }
    )
    return schema


if __name__ == "__main__":
    raise SystemExit("Use build_langfuse_compat.py to generate current Langfuse contracts.")

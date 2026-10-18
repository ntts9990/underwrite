"""Build current CLI-list branches from their typed source and retrieval rules."""

from __future__ import annotations

if __package__:
    from ._schema_compose import object_schema as _object
    from .build_langfuse_accounting import count_schema as _count
    from .build_langfuse_accounting import scope_schema, source_schema
    from .build_langfuse_accounting import tag_schema as _tag
else:
    from _schema_compose import object_schema as _object
    from build_langfuse_accounting import count_schema as _count
    from build_langfuse_accounting import scope_schema, source_schema
    from build_langfuse_accounting import tag_schema as _tag

from underwrite.instrument.evidence.langfuse_accounting import (
    MAX_ID_LENGTH,
    MAX_LABEL_LENGTH,
    MAX_OBSERVATIONS,
    MAX_SCORES,
    MAX_STRING_VALUE_LENGTH,
)
from underwrite.instrument.evidence.langfuse_cli import (
    ASSOCIATIONS,
    CAPTURE_KINDS,
    CURSOR_STATES,
    HTTP_OK,
    LIMITATIONS,
    MAX_CLI_PAGES,
    NEXT_ACTIONS,
    PAGINATION_MODES,
    ROW_LIMITATIONS,
    ROW_SCOPE,
    RULE_PROFILE,
    SCOPE,
    SOURCE_FORMATS,
    SUBJECT_AVAILABILITY,
    SUBJECT_KINDS,
    VALUE_TYPES,
)
from underwrite.instrument.evidence.langfuse_compat import (
    ACCOUNTING_SCHEMA,
    CLI_READER,
    ROW_INDEX_SCHEMA,
    extended_limitations,
)


def _identity() -> dict[str, object]:
    return {"type": "string", "minLength": 1, "maxLength": MAX_ID_LENGTH}


def _optional_identity() -> dict[str, object]:
    return {"type": ["string", "null"], "minLength": 1, "maxLength": MAX_ID_LENGTH}


def _pagination(maximum: int) -> dict[str, object]:
    result = _object(
        {
            "mode": {"enum": list(PAGINATION_MODES)},
            "cursor_state": {"enum": list(CURSOR_STATES)},
            "pages": {"type": ["integer", "null"], "minimum": 1, "maximum": MAX_CLI_PAGES},
            "fetched_items": {"type": ["integer", "null"], "minimum": 0, "maximum": maximum},
            "truncated": {"type": ["boolean", "null"]},
        }
    )
    result["allOf"] = [
        _tag(
            "mode",
            "one_page",
            {
                "cursor_state": {"enum": ["present", "null", "absent"]},
                "pages": {"type": "null"},
                "fetched_items": {"type": "null"},
                "truncated": {"type": "null"},
            },
        ),
        _tag(
            "mode",
            "cli_all",
            {
                "cursor_state": {"const": "not_applicable"},
                "pages": {"type": "integer", "minimum": 1, "maximum": MAX_CLI_PAGES},
                "fetched_items": _count(maximum),
                "truncated": {"type": "boolean"},
            },
        ),
    ]
    return result


def _retrieval() -> dict[str, object]:
    roles: dict[str, object] = {}
    for role in SOURCE_FORMATS:
        selected = _object(
            {
                "capture_kind": {"enum": list(CAPTURE_KINDS)},
                "http_status": {"enum": [HTTP_OK, None]},
                "pagination": _pagination(
                    MAX_OBSERVATIONS if role == "observations" else MAX_SCORES
                ),
            }
        )
        selected["allOf"] = [
            _tag("capture_kind", "json_envelope", {"http_status": {"const": HTTP_OK}}),
            _tag("capture_kind", "output_body", {"http_status": {"type": "null"}}),
        ]
        roles[role] = selected
    return _object(roles)


def _counts() -> dict[str, object]:
    return _object(
        {
            "observations": _count(MAX_OBSERVATIONS),
            "scores": _count(MAX_SCORES),
            "by_data_type": _object({kind: _count(MAX_SCORES) for kind in VALUE_TYPES}),
            "by_subject_kind": _object(
                {kind: _count(MAX_SCORES) for kind in (*SUBJECT_KINDS, "unknown")}
            ),
            "associations": _object({kind: _count(MAX_SCORES) for kind in ASSOCIATIONS}),
            "observations_without_local_scores": _count(MAX_OBSERVATIONS),
        }
    )


def _subject() -> dict[str, object]:
    result = _object(
        {
            "availability": {"enum": list(SUBJECT_AVAILABILITY)},
            "kind": {"enum": [*SUBJECT_KINDS, None]},
            "id": _optional_identity(),
            "trace_id": _optional_identity(),
        }
    )
    result["allOf"] = [
        _tag(
            "availability",
            "present",
            {"kind": {"enum": list(SUBJECT_KINDS)}, "id": _identity()},
        ),
        *(
            _tag(
                "availability",
                missing,
                {"kind": {"type": "null"}, "id": {"type": "null"}, "trace_id": {"type": "null"}},
            )
            for missing in ("null", "absent")
        ),
        *(
            _tag("kind", kind, {"trace_id": {"type": "null"}})
            for kind in SUBJECT_KINDS
            if kind != "observation"
        ),
    ]
    return result


def _association_rules(*, index: bool) -> list[dict[str, object]]:
    subject_field = "subject_kind" if index else "subject"
    target = "target_observation_index"

    def match_subject(kind: str | None) -> dict[str, object]:
        if index:
            return {subject_field: {"const": kind}}
        return {subject_field: {"properties": {"kind": {"const": kind}}}}

    rules: list[dict[str, object]] = []
    for association in ASSOCIATIONS:
        properties: dict[str, object] = {target: {"type": "null"}}
        if association == "observation_local":
            properties.update(match_subject("observation"))
            properties[target] = _count(MAX_OBSERVATIONS - 1)
        elif association in (
            "unresolved_observation_reference",
            "observation_context_unknown",
        ):
            properties.update(match_subject("observation"))
        elif association == "other_subject":
            if index:
                properties[subject_field] = {
                    "enum": [kind for kind in SUBJECT_KINDS if kind != "observation"]
                }
            else:
                properties[subject_field] = {
                    "properties": {
                        "kind": {"enum": [kind for kind in SUBJECT_KINDS if kind != "observation"]}
                    }
                }
        else:
            properties.update(match_subject(None))
        if not index and association in (
            "observation_local",
            "unresolved_observation_reference",
        ):
            properties[subject_field] = {
                "properties": {"kind": {"const": "observation"}, "trace_id": _identity()}
            }
        elif not index and association == "observation_context_unknown":
            properties[subject_field] = {
                "properties": {"kind": {"const": "observation"}, "trace_id": {"type": "null"}}
            }
        rules.append(_tag("association", association, properties))
    return rules


def _score() -> dict[str, object]:
    value = {"type": ["number", "boolean", "string"], "maxLength": MAX_STRING_VALUE_LENGTH}
    result = _object(
        {
            "score_index": _count(MAX_SCORES - 1),
            "id": _identity(),
            "project_id": _identity(),
            "name": {"type": "string", "maxLength": MAX_LABEL_LENGTH},
            "data_type": {"enum": list(VALUE_TYPES)},
            "value": value,
            "subject": _subject(),
            "association": {"enum": list(ASSOCIATIONS)},
            "target_observation_index": {
                "type": ["integer", "null"],
                "minimum": 0,
                "maximum": MAX_OBSERVATIONS - 1,
            },
        }
    )
    result["allOf"] = [
        *(
            _tag(
                "data_type",
                kind,
                {
                    "value": {
                        "type": value_type,
                        **(
                            {"maxLength": MAX_STRING_VALUE_LENGTH} if value_type == "string" else {}
                        ),
                    }
                },
            )
            for kind, value_type in VALUE_TYPES.items()
        ),
        *_association_rules(index=False),
    ]
    return result


def _score_row() -> dict[str, object]:
    result = _object(
        {
            "score_index": _count(MAX_SCORES - 1),
            "data_type": {"enum": list(VALUE_TYPES)},
            "subject_availability": {"enum": list(SUBJECT_AVAILABILITY)},
            "subject_kind": {"enum": [*SUBJECT_KINDS, None]},
            "association": {"enum": list(ASSOCIATIONS)},
            "target_observation_index": {
                "type": ["integer", "null"],
                "minimum": 0,
                "maximum": MAX_OBSERVATIONS - 1,
            },
        }
    )
    result["allOf"] = [
        *_association_rules(index=True),
        _tag("subject_availability", "present", {"subject_kind": {"enum": list(SUBJECT_KINDS)}}),
        *(
            _tag("subject_availability", missing, {"subject_kind": {"type": "null"}})
            for missing in ("null", "absent")
        ),
    ]
    return result


def _shared_properties(*, row: bool) -> dict[str, object]:
    return {
        "sources": source_schema(CLI_READER),
        "retrieval": _retrieval(),
        "scope": scope_schema(ROW_SCOPE if row else SCOPE),
        "counts": _counts(),
        "limitations": {
            "const": extended_limitations(list(ROW_LIMITATIONS if row else LIMITATIONS))
        },
        "next_actions": {
            "type": "array",
            "minItems": 1,
            "maxItems": len(NEXT_ACTIONS),
            "items": {"enum": list(NEXT_ACTIONS)},
            "uniqueItems": True,
        },
    }


def build_accounting_branch() -> dict[str, object]:
    schema = _object(
        {
            "schema": {"const": ACCOUNTING_SCHEMA},
            "rule_profile": {"const": RULE_PROFILE},
            **_shared_properties(row=False),
            "observations": {
                "type": "array",
                "maxItems": MAX_OBSERVATIONS,
                "items": _object(
                    {
                        "observation_index": _count(MAX_OBSERVATIONS - 1),
                        "id": _identity(),
                        "project_id": _identity(),
                        "trace_id": _optional_identity(),
                        "type": {"type": "string", "maxLength": MAX_LABEL_LENGTH},
                        "local_score_count": _count(MAX_SCORES),
                    }
                ),
            },
            "scores": {"type": "array", "maxItems": MAX_SCORES, "items": _score()},
            "planned": _object(
                {
                    "status": {"const": "unknown"},
                    "count": {"const": None},
                    "missing_execution_count": {"const": None},
                }
            ),
            "reader_profile": {"const": CLI_READER},
        }
    )
    return schema


def build_row_branch() -> dict[str, object]:
    schema = _object(
        {
            "schema": {"const": ROW_INDEX_SCHEMA},
            "source_rule_profile": {"const": RULE_PROFILE},
            **_shared_properties(row=True),
            "score_rows": {"type": "array", "maxItems": MAX_SCORES, "items": _score_row()},
            "reader_profile": {"const": CLI_READER},
        }
    )
    return schema


if __name__ == "__main__":
    raise SystemExit("Use build_langfuse_compat.py to generate current Langfuse contracts.")

"""Describe supplied Langfuse score rows and exact original-ID associations."""

from __future__ import annotations

import json
import unicodedata
from types import MappingProxyType
from typing import cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes

MAX_OBSERVATIONS = 512
MAX_SCORES = 2048
MAX_ID_LENGTH = 256
MAX_LABEL_LENGTH = 256
MAX_STRING_VALUE_LENGTH = 1024
RULE_PROFILE = "langfuse.score-accounting.v1"
KIND_RULES = MappingProxyType(
    {
        "NUMERIC": "NUMERIC_SCORE",
        "BOOLEAN": "BOOLEAN_SCORE",
        "CATEGORICAL": "CATEGORICAL_SCORE",
        "TEXT": "TEXT_SCORE",
    }
)
ASSOCIATION_RULES = MappingProxyType(
    {
        "observation_local": "MATCHED_OBSERVATION_SCORE",
        "trace_level": "TRACE_LEVEL_SCORE",
        "unresolved_observation_reference": "UNRESOLVED_OBSERVATION_SCORE",
    }
)
SCOPE = MappingProxyType(
    {
        "population": "supplied_rows",
        "export_completeness": "unknown",
        "planned_evaluations": "unknown",
        "unit": "score_row",
        "claims_verified": False,
        "display_text": "nfc_normalized",
    }
)
LIMITATIONS = (
    "SOURCE_CLAIMS_UNVERIFIED",
    "EXPORT_COMPLETENESS_UNKNOWN",
    "PLANNED_EVALUATIONS_UNKNOWN",
    "SCORE_VALUES_NOT_TASK_VERDICTS",
    "NO_STATISTICAL_OR_APPROVAL_AUTHORITY",
)
NEXT_ACTIONS = (
    "SUPPLY_PLANNED_INVENTORY_FOR_MISSINGNESS",
    "INSPECT_UNRESOLVED_OBSERVATION_REFERENCES",
)


class LangfuseAccountingError(ValueError):
    """A code-owned refusal whose location never contains source identifiers."""

    def __init__(self, code: str, reason: str, location: str) -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def _error(reason: str, location: str, code: str = "INVALID_INPUT") -> LangfuseAccountingError:
    return LangfuseAccountingError(code, reason, location)


def _array(value: object, maximum: int, location: str) -> list[object]:
    if type(value) is not list:
        raise _error("LANGFUSE_ARRAY_REQUIRED", location)
    rows = cast(list[object], value)
    if len(rows) > maximum:
        raise _error("LANGFUSE_POPULATION_LIMIT_EXCEEDED", location, "INPUT_LIMIT_EXCEEDED")
    return rows


def _object(value: object, location: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _error("LANGFUSE_OBJECT_REQUIRED", location)
    return cast(dict[str, object], value)


def _field(row: dict[str, object], name: str, location: str) -> object:
    if name not in row:
        raise _error("LANGFUSE_FIELD_REQUIRED", location + "/" + name)
    return row[name]


def _text(value: object, maximum: int, location: str, *, identifier: bool = False) -> str:
    invalid = "LANGFUSE_IDENTIFIER_INVALID" if identifier else "LANGFUSE_TEXT_INVALID"
    if type(value) is not str or (identifier and not value.strip()):
        raise _error(invalid, location)
    if len(value) > maximum:
        raise _error("LANGFUSE_TEXT_LIMIT_EXCEEDED", location, "INPUT_LIMIT_EXCEEDED")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _error(invalid, location) from None
    normalized = unicodedata.normalize("NFC", value)
    if identifier:
        if value != normalized:
            raise _error("LANGFUSE_NONCANONICAL_IDENTIFIER", location, "UNSUPPORTED_PROFILE")
        return value
    if len(normalized) > maximum:
        raise _error("LANGFUSE_TEXT_LIMIT_EXCEEDED", location, "INPUT_LIMIT_EXCEEDED")
    return normalized


def langfuse_identifier(value: object, location: str) -> str:
    """Admit one opaque ID under the shared Langfuse byte/NFC/length policy."""
    return _text(value, MAX_ID_LENGTH, location, identifier=True)


def langfuse_label(value: object, location: str) -> str:
    """Admit a display label without using it as an identity key."""
    return _text(value, MAX_LABEL_LENGTH, location)


def _identifier(row: dict[str, object], key: str, location: str) -> str:
    return langfuse_identifier(_field(row, key, location), location + "/" + key)


def _number(value: object, location: str) -> int | float:
    if type(value) is not int and type(value) is not float:
        raise _error("LANGFUSE_NUMBER_INVALID", location)
    try:
        return cast(int | float, json.loads(canonical_bytes(value)))
    except CanonicalizationError:
        raise _error("LANGFUSE_NUMBER_INVALID", location) from None


def _observations(
    rows: list[object],
) -> tuple[list[dict[str, object]], dict[tuple[str, str, str], int]]:
    output: list[dict[str, object]] = []
    indexes: dict[tuple[str, str, str], int] = {}
    for index, value in enumerate(rows):
        location = f"/observations/{index}"
        row = _object(value, location)
        identifier = _identifier(row, "id", location)
        project_id = _identifier(row, "project_id", location)
        trace_id = _identifier(row, "trace_id", location)
        key = (project_id, trace_id, identifier)
        if key in indexes:
            raise _error("LANGFUSE_AMBIGUOUS_OBSERVATION_KEY", location)
        indexes[key] = index
        output.append(
            {
                "observation_index": index,
                "id": identifier,
                "project_id": project_id,
                "trace_id": trace_id,
                "type": _text(_field(row, "type", location), MAX_LABEL_LENGTH, location + "/type"),
                "local_score_count": 0,
            }
        )
    return output, indexes


def _score(
    value: object, index: int, indexes: dict[tuple[str, str, str], int]
) -> dict[str, object]:
    location = f"/scores/{index}"
    row = _object(value, location)
    identifier = _identifier(row, "id", location)
    project_id = _identifier(row, "project_id", location)
    trace_id = _identifier(row, "trace_id", location)
    for target in ("session_id", "dataset_run_id"):
        selected = row.get(target)
        if selected is None:
            continue
        if type(selected) is not str:
            raise _error("LANGFUSE_SCORE_TARGET_INVALID", location + "/" + target)
        if selected != "":
            raise _error("LANGFUSE_SCORE_TARGET_CONFLICT", location, "UNSUPPORTED_PROFILE")
    observation_id = _field(row, "observation_id", location)
    if observation_id is not None:
        observation_id = _text(
            observation_id, MAX_ID_LENGTH, location + "/observation_id", identifier=True
        )
    data_type = _field(row, "data_type", location)
    if type(data_type) is not str or data_type not in KIND_RULES:
        raise _error(
            "LANGFUSE_SCORE_KIND_UNSUPPORTED", location + "/data_type", "UNSUPPORTED_PROFILE"
        )
    string_value = _field(row, "string_value", location)
    if string_value is not None:
        string_value = _text(string_value, MAX_STRING_VALUE_LENGTH, location + "/string_value")
    target = (
        indexes.get((project_id, trace_id, observation_id)) if observation_id is not None else None
    )
    association = (
        "trace_level"
        if observation_id is None
        else "observation_local"
        if target is not None
        else "unresolved_observation_reference"
    )
    return {
        "score_index": index,
        "id": identifier,
        "project_id": project_id,
        "trace_id": trace_id,
        "observation_id": observation_id,
        "name": _text(_field(row, "name", location), MAX_LABEL_LENGTH, location + "/name"),
        "data_type": data_type,
        "value": _number(_field(row, "value", location), location + "/value"),
        "string_value": string_value,
        "kind_rule": KIND_RULES[data_type],
        "association": association,
        "association_rule": ASSOCIATION_RULES[association],
        "target_observation_index": target,
    }


def account_langfuse(observations: object, scores: object) -> dict[str, object]:
    """Account supplied rows, preserving source values without verdict or completeness inference."""
    observation_rows = _array(observations, MAX_OBSERVATIONS, "/observations")
    score_rows = _array(scores, MAX_SCORES, "/scores")
    observed, indexes = _observations(observation_rows)
    scored: list[dict[str, object]] = []
    kinds = dict.fromkeys(KIND_RULES, 0)
    associations = dict.fromkeys(ASSOCIATION_RULES, 0)
    for index, value in enumerate(score_rows):
        score = _score(value, index, indexes)
        kinds[cast(str, score["data_type"])] += 1
        associations[cast(str, score["association"])] += 1
        target = score["target_observation_index"]
        if target is not None:
            item = observed[cast(int, target)]
            item["local_score_count"] = cast(int, item["local_score_count"]) + 1
        scored.append(score)
    actions = [NEXT_ACTIONS[0]]
    if associations["unresolved_observation_reference"]:
        actions.append(NEXT_ACTIONS[1])
    return {
        "rule_profile": RULE_PROFILE,
        "scope": dict(SCOPE),
        "observations": observed,
        "scores": scored,
        "counts": {
            "observations": len(observed),
            "scores": len(scored),
            "by_data_type": kinds,
            "associations": associations,
            "observations_without_local_scores": sum(
                row["local_score_count"] == 0 for row in observed
            ),
        },
        "planned": {"status": "unknown", "count": None, "missing_execution_count": None},
        "limitations": list(LIMITATIONS),
        "next_actions": actions,
    }

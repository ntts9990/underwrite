"""The Langfuse row index exposes only structural, loss-marked facts."""

from __future__ import annotations

from typing import Any, cast

from underwrite.instrument.evidence.langfuse_accounting import account_langfuse
from underwrite.instrument.evidence.langfuse_row_index import index_langfuse_accounting


def test_allowlist_withholds_values_names_and_ids() -> None:
    canary = "secret-score-canary"
    observation = {
        "id": canary,
        "project_id": canary,
        "trace_id": canary,
        "type": "SPAN",
    }
    score: dict[str, Any] = {
        "id": canary,
        "project_id": canary,
        "trace_id": canary,
        "observation_id": canary,
        "name": canary,
        "data_type": "CATEGORICAL",
        "value": 0,
        "string_value": canary,
    }
    accounting = {
        "sources": {
            role: {
                "format": role,
                "format_version": "4.35.0",
                "raw_hash": "sha256:" + "a" * 64,
                "observation_digest": "sha256:" + "b" * 64,
                "claims_verified": False,
            }
            for role in ("observations", "scores")
        },
        **account_langfuse([observation], [score, {**score, "string_value": None}]),
    }
    result = index_langfuse_accounting(accounting)
    assert result["score_rows"] == [
        {
            "score_index": 0,
            "data_type": "CATEGORICAL",
            "numeric_field_presence": "present",
            "string_field_presence": "present",
            "association": "observation_local",
            "target_observation_index": 0,
        },
        {
            "score_index": 1,
            "data_type": "CATEGORICAL",
            "numeric_field_presence": "present",
            "string_field_presence": "null",
            "association": "observation_local",
            "target_observation_index": 0,
        },
    ]
    assert canary not in str(result)
    score_rows = cast(list[dict[str, Any]], result["score_rows"])
    scope = cast(dict[str, object], result["scope"])
    assert "value" not in score_rows[0]
    assert scope["values"] == "withheld"
    assert result["verification"] == []
    cast(list[str], result["next_actions"]).clear()
    assert accounting["next_actions"] == ["SUPPLY_PLANNED_INVENTORY_FOR_MISSINGNESS"]

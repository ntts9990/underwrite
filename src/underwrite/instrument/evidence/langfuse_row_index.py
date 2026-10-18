"""Content-minimized row index over already-accounted Langfuse scores."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, cast

from underwrite.instrument.evidence.langfuse_accounting import (
    ASSOCIATION_RULES,
    KIND_RULES,
)

SCOPE = MappingProxyType(
    {
        "population": "supplied_rows",
        "planned_evaluations": "unknown",
        "export_completeness": "unknown",
        "selected_file_binding": "independent_selected_files",
        "values": "withheld",
    }
)
LIMITATIONS = (
    "SCORE_CONTENT_WITHHELD",
    "SOURCE_HASHES_AND_ROW_PATTERNS_LINKABLE",
    "SELECTED_FILES_NOT_ATOMICALLY_BOUND",
    "SOURCE_CLAIMS_UNVERIFIED",
    "EXPORT_COMPLETENESS_UNKNOWN",
    "PLANNED_EVALUATIONS_UNKNOWN",
    "NO_TASK_VERDICT_OR_APPROVAL_AUTHORITY",
)


def index_langfuse_body(accounting: dict[str, object]) -> dict[str, object]:
    """Allowlist blob score facts without asserting a source-file version."""
    rows = cast(list[dict[str, Any]], accounting["scores"])
    counts = cast(dict[str, Any], accounting["counts"])
    return {
        "counts": {
            "observations": counts["observations"],
            "scores": counts["scores"],
            "by_data_type": {kind: counts["by_data_type"][kind] for kind in KIND_RULES},
            "associations": {
                association: counts["associations"][association]
                for association in ASSOCIATION_RULES
            },
            "observations_without_local_scores": counts["observations_without_local_scores"],
        },
        "score_rows": [
            {
                "score_index": row["score_index"],
                "data_type": row["data_type"],
                "numeric_field_presence": "present",
                "string_field_presence": "null" if row["string_value"] is None else "present",
                "association": row["association"],
                "target_observation_index": row["target_observation_index"],
            }
            for row in rows
        ],
        "verification": [],
        "limitations": list(LIMITATIONS),
        "next_actions": cast(list[str], accounting["next_actions"]).copy(),
    }

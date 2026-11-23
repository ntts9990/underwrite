"""Generate the bounded cross-artifact review projection from its full contract."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from underwrite.instrument.evidence.cross_artifact_relation import TASK_OWNERS

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts/cross_artifact_review.v1.schema.json"
TARGET = ROOT / "contracts/cross_artifact_review_summary.v1.schema.json"


def build_schema() -> dict[str, Any]:
    full = cast(dict[str, Any], json.loads(SOURCE.read_text(encoding="utf-8")))
    fields = cast(dict[str, Any], full["properties"])
    correspondence = cast(dict[str, Any], fields["correspondence"]["properties"])
    task = cast(dict[str, Any], fields["next_verification_tasks"]["items"]["properties"])
    rules = cast(list[dict[str, Any]], full["allOf"])
    relation_rule = deepcopy(rules[0])
    no_subject_rule = deepcopy(rules[1])
    then_correspondence = relation_rule["then"]["properties"]["correspondence"]["properties"]
    del then_correspondence["pairs"]
    task_prefix = no_subject_rule["then"]["properties"]["next_verification_tasks"].pop(
        "prefixItems"
    )
    no_subject_rule["then"]["properties"]["next_verification_tasks"]["contains"] = (
        task_prefix[0]
    )
    reader = cast(dict[str, Any], fields["readers"]["properties"])
    promptfoo_reader = cast(dict[str, Any], reader["promptfoo"])
    task_owners = {code: TASK_OWNERS[code] for code in task["code"]["enum"]}
    summary_limits = deepcopy(cast(dict[str, Any], fields["limitations"]))
    summary_limits["minItems"] = len(summary_limits["items"]["enum"])
    summary_limits["maxItems"] = summary_limits["minItems"]
    relation_rule["then"]["properties"]["finding_counts"] = {
        "properties": {
            name: {"const": 0} for name in ("equal", "different", "ineligible")
        }
    }
    producer_version_rule = {
        "if": {
            "properties": {
                "readers": {
                    "properties": {
                        "promptfoo": {
                            "properties": {"producer_version_state": {"const": "unknown"}}
                        }
                    }
                }
            }
        },
        "then": {
            "properties": {
                "detail_state": {"properties": {"producer_version": {"const": "not_applicable"}}}
            }
        },
        "else": {
            "properties": {
                "detail_state": {
                    "properties": {"producer_version": {"const": "intentionally_omitted"}}
                }
            }
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:cross_artifact_review_summary.v1",
        "title": "cross_artifact_review_summary.v1",
        "description": (
            "Bounded source-claim alignment; per-pair coordinates and source-declared "
            "strings are omitted. No equivalence, improvement or approval is established."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema",
            "view",
            "relation",
            "source_identity",
            "readers",
            "populations",
            "correspondence",
            "finding_counts",
            "improvement",
            "checks",
            "next_verification_tasks",
            "limitations",
            "detail_state",
        ],
        "properties": {
            "schema": {"const": "cross_artifact_review_summary.v1"},
            "view": {"const": "summary"},
            **{
                name: deepcopy(fields[name])
                for name in (
                    "relation",
                    "source_identity",
                    "finding_counts",
                    "improvement",
                    "checks",
                )
            },
            "limitations": summary_limits,
            "readers": {
                "type": "object",
                "additionalProperties": False,
                "required": ["goppi", "promptfoo"],
                "properties": {
                    "goppi": deepcopy(reader["goppi"]),
                    "promptfoo": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["profile", "producer_version_state"],
                        "properties": {
                            name: deepcopy(promptfoo_reader["properties"][name])
                            for name in ("profile", "producer_version_state")
                        },
                    },
                },
            },
            "populations": deepcopy(fields["populations"]),
            "correspondence": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "declaration_sha256",
                    "coverage",
                    "input_correspondence",
                    "semantic_equivalence",
                    "condition_alignment",
                    "unmatched_case_count",
                    "unmatched_subject_row_count",
                ],
                "properties": {
                    **{
                        name: deepcopy(correspondence[name])
                        for name in (
                            "declaration_sha256",
                            "coverage",
                            "input_correspondence",
                            "semantic_equivalence",
                            "condition_alignment",
                        )
                    },
                    "unmatched_case_count": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": correspondence["unmatched_case_indices"]["maxItems"],
                    },
                    "unmatched_subject_row_count": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": correspondence["unmatched_subject_row_indices"]["maxItems"],
                    },
                },
            },
            "next_verification_tasks": {
                "type": "array",
                "minItems": 1,
                "maxItems": len(task_owners),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["code", "owner", "count"],
                    "properties": {
                        "code": deepcopy(task["code"]),
                        "owner": deepcopy(task["owner"]),
                        "count": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": fields["next_verification_tasks"]["maxItems"],
                        },
                    },
                    "oneOf": [
                        {
                            "properties": {
                                "code": {"const": code},
                                "owner": {"const": owner},
                            }
                        }
                        for code, owner in task_owners.items()
                    ],
                },
                "allOf": [
                    {
                        "contains": {"properties": {"code": {"const": code}}},
                        "minContains": 0,
                        "maxContains": 1,
                    }
                    for code in task_owners
                ],
            },
            "detail_state": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "pair_details",
                    "unmatched_coordinates",
                    "task_coordinates",
                    "producer_version",
                    "full_view",
                ],
                "properties": {
                    "pair_details": {"const": "intentionally_omitted"},
                    "unmatched_coordinates": {"const": "intentionally_omitted"},
                    "task_coordinates": {"const": "intentionally_omitted"},
                    "producer_version": {
                        "enum": ["intentionally_omitted", "not_applicable"]
                    },
                    "full_view": {"const": "review-artifacts --view review"},
                },
            },
        },
        "allOf": [relation_rule, no_subject_rule, producer_version_rule],
        "$defs": {"hash": deepcopy(cast(dict[str, Any], full["$defs"])["hash"])},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = (json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != raw:
            print("cross_artifact_review_summary.v1.schema.json is stale")
            return 1
    else:
        TARGET.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

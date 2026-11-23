"""Generate bounded cross-artifact readiness from its authored full contract."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from underwrite.instrument.evidence.cross_artifact_relation import TASK_OWNERS
from underwrite.instrument.ingest.cross_artifact import READINESS_LIMITATIONS

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts/cross_artifact_readiness.v1.schema.json"
TARGET = ROOT / "contracts/cross_artifact_readiness_summary.v1.schema.json"


def build_schema() -> dict[str, Any]:
    full = cast(dict[str, Any], json.loads(SOURCE.read_text(encoding="utf-8")))
    fields = cast(dict[str, Any], full["properties"])
    metadata = cast(dict[str, Any], fields["metadata"]["properties"])
    blocker = cast(dict[str, Any], metadata["blockers"]["items"]["properties"])
    task = cast(dict[str, Any], fields["next_verification_tasks"]["items"]["properties"])
    task_owners = {code: TASK_OWNERS[code] for code in task["code"]["enum"]}
    reader = cast(dict[str, Any], fields["readers"]["properties"])
    promptfoo_reader = cast(dict[str, Any], reader["promptfoo"])
    summary_limits = deepcopy(cast(dict[str, Any], fields["limitations"]))
    full_limit_codes = cast(list[str], summary_limits["items"]["enum"])
    summary_limits["items"]["enum"] = [
        code for code in full_limit_codes if code in READINESS_LIMITATIONS
    ]
    if set(summary_limits["items"]["enum"]) != set(READINESS_LIMITATIONS):
        raise ValueError("READINESS_LIMITATION_CONTRACT_DRIFT")
    summary_limits["minItems"] = len(READINESS_LIMITATIONS)
    summary_limits["maxItems"] = len(READINESS_LIMITATIONS)
    rules = deepcopy(cast(list[dict[str, Any]], full["allOf"]))
    for rule in rules[6:]:
        tasks = rule["then"]["properties"]["next_verification_tasks"]
        tasks["contains"] = tasks.pop("prefixItems")[0]
    version_rule = {
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
        "$id": "urn:underwrite:contract:cross_artifact_readiness_summary.v1",
        "title": "cross_artifact_readiness_summary.v1",
        "description": (
            "Bounded source-bound readiness counts without row coordinates or source strings. "
            "No case comparison, improvement, provenance or approval is established."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema", "view", "relation", "source_identity", "readers", "populations",
            "metadata", "review_prerequisites", "finding_counts", "improvement", "checks",
            "next_verification_tasks", "limitations", "detail_state",
        ],
        "properties": {
            "schema": {"const": "cross_artifact_readiness_summary.v1"},
            "view": {"const": "readiness-summary"},
            **{
                name: deepcopy(fields[name])
                for name in (
                    "relation", "source_identity", "populations", "review_prerequisites",
                    "finding_counts", "improvement", "checks",
                )
            },
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
            "metadata": {
                "type": "object",
                "additionalProperties": False,
                "required": ["case_ids", "row_roles", "blockers"],
                "properties": {
                    "case_ids": deepcopy(metadata["case_ids"]),
                    "row_roles": deepcopy(metadata["row_roles"]),
                    "blockers": {
                        "type": "array",
                        "maxItems": len(blocker["code"]["enum"]),
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["code", "count"],
                            "properties": {
                                "code": deepcopy(blocker["code"]),
                                "count": {
                                    "type": "integer", "minimum": 1,
                                    "maximum": metadata["blockers"]["maxItems"],
                                },
                            },
                        },
                        "allOf": [
                            {
                                "contains": {"properties": {"code": {"const": code}}},
                                "minContains": 0,
                                "maxContains": 1,
                            }
                            for code in blocker["code"]["enum"]
                        ],
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
                            "type": "integer", "minimum": 1,
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
            "limitations": summary_limits,
            "detail_state": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "blocker_coordinates",
                    "task_coordinates",
                    "producer_version",
                    "full_view",
                ],
                "properties": {
                    "blocker_coordinates": {"const": "intentionally_omitted"},
                    "task_coordinates": {"const": "intentionally_omitted"},
                    "producer_version": {"enum": ["intentionally_omitted", "not_applicable"]},
                    "full_view": {"const": "review-artifacts --view readiness"},
                },
            },
        },
        "allOf": [*rules, version_rule],
        "$defs": {"hash": deepcopy(cast(dict[str, Any], full["$defs"])["hash"])},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = (json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != raw:
            print("cross_artifact_readiness_summary.v1.schema.json is stale")
            return 1
    else:
        TARGET.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

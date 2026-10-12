"""Generate one standalone handoff envelope from its existing owner contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _schema_compose import embed_contracts
from _schema_compose import nullable as _nullable
from _schema_compose import object_schema as _object
from _schema_compose import reference as _ref
from _schema_compose import rewrite as _rewrite
from _schema_compose import tagged as _tagged

from underwrite.cli.handoff_limits import MAX_FILE_BYTES, MAX_SOURCE_FILES, PROFILE

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts/paired_handoff_review.v1.schema.json"
OWNERS = (
    "paired_binary_evidence",
    "paired_review",
    "evidence_error",
    "record_verification",
    "record_error",
)


def build_schema() -> dict[str, Any]:
    definitions, owners = embed_contracts(ROOT, OWNERS)
    digest = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
    text = {"type": "string", "minLength": 1, "maxLength": 256}
    count = {"type": "integer", "minimum": 0}
    finding = _object(
        {
            "code": {
                "enum": [
                    "PRODUCER_POLICY_INCLUDED_FLAG_CONTRADICTION",
                    "CONDITIONS_NOT_SUPPLIED",
                    "SOURCE_AUTHENTICATION_NOT_INDEPENDENTLY_VERIFIED",
                ]
            },
            "source": text,
        }
    )
    paired = owners["paired_binary_evidence"]["properties"]
    review = owners["paired_review"]["properties"]
    definitions["summary"] = _object(
        {
            "baseline": _object({"observed_one": count, "observed_zero": count}),
            "candidate": _object({"observed_one": count, "observed_zero": count}),
            "transitions": _rewrite(
                paired["accounting"]["properties"]["primary_transitions"], "paired_binary_evidence"
            ),
            "measurement_status": _rewrite(paired["status"], "paired_binary_evidence"),
            "measurement_reason": _rewrite(paired["reason"], "paired_binary_evidence"),
            "conditions_coverage": _rewrite(
                review["conditions"]["properties"]["coverage"], "paired_review"
            ),
        }
    )
    record_id = _rewrite(
        owners["record_verification"]["properties"]["record_id"], "record_verification"
    )
    source = _object(
        {
            "handoff_digest": digest,
            "files": {
                "type": "array",
                "maxItems": MAX_SOURCE_FILES,
                "items": _object(
                    {
                        "name": text,
                        "sha256": digest,
                        "bytes": {"type": "integer", "minimum": 0, "maximum": MAX_FILE_BYTES},
                    }
                ),
            },
            "authentication_independently_verified": {"const": False},
        }
    )
    result = _object(
        {
            "state": {"const": "result"},
            "paired_evidence": _ref("paired_binary_evidence"),
            "paired_review": _ref("paired_review"),
            "summary": _ref("summary"),
        }
    )
    error = _object(
        {
            "state": {"const": "error"},
            "phase": {"enum": ["acquisition", "analysis"]},
            "error": _ref("evidence_error"),
        }
    )
    storage = _tagged(
        [
            _object({"state": {"const": "not_requested"}}),
            _object(
                {
                    "state": {"const": "saved"},
                    "record_id": record_id,
                    "manifest_digest": digest,
                    "verification": {
                        "allOf": [
                            _ref("record_verification"),
                            {"properties": {"valid": {"const": True}}},
                        ]
                    },
                }
            ),
            _object(
                {
                    "state": {"const": "error"},
                    "error": _ref("record_error"),
                    "published_record": _nullable(
                        _object({"record_id": record_id, "manifest_digest": digest})
                    ),
                }
            ),
        ]
    )
    schema = _object(
        {
            "schema": {"const": "paired_handoff_review.v1"},
            "exit_code": {"enum": [0, 2, 3]},
            "profile": {"const": PROFILE},
            "scope": _nullable(
                _object(
                    {
                        "stage": {"const": "same_batch"},
                        "split": {"const": "explore"},
                        "repeat": {"const": 0},
                        "cases": count,
                        "planned_slots": count,
                    }
                )
            ),
            "source": _nullable(source),
            "context_provenance": _nullable(
                _object(
                    {
                        "source": {"const": "consumer_derived_empty"},
                        "conditions_supplied_by_producer": {"const": False},
                        "semantic_completeness_verified": {"const": False},
                        "source_authentication_independently_verified": {"const": False},
                    }
                )
            ),
            "findings": {"type": "array", "maxItems": 3, "items": finding},
            "analysis": _tagged([result, error]),
            "storage": storage,
            "next_actions": {
                "type": "array",
                "maxItems": 8,
                "items": {"type": "string", "maxLength": 128, "pattern": "^[A-Z][A-Z0-9_]*$"},
            },
        }
    )
    schema.update(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:underwrite:contract:paired_handoff_review.v1",
            "title": "paired_handoff_review.v1",
            "$defs": definitions,
            "allOf": [
                {
                    "if": {
                        "properties": {"storage": {"properties": {"state": {"const": "error"}}}}
                    },
                    "then": {"properties": {"exit_code": {"const": 3}}},
                    "else": {
                        "if": {
                            "properties": {
                                "analysis": {"properties": {"state": {"const": "error"}}}
                            }
                        },
                        "then": {"properties": {"exit_code": {"const": 2}}},
                        "else": {"properties": {"exit_code": {"const": 0}}},
                    },
                },
                {
                    "if": {
                        "properties": {
                            "analysis": {
                                "properties": {"phase": {"const": "acquisition"}},
                                "required": ["phase"],
                            }
                        }
                    },
                    "then": {
                        "properties": {
                            "source": {"type": "null"},
                            "scope": {"type": "null"},
                            "context_provenance": {"type": "null"},
                            "findings": {"maxItems": 0},
                        }
                    },
                },
                {
                    "if": {
                        "properties": {"analysis": {"properties": {"state": {"const": "result"}}}}
                    },
                    "then": {
                        "properties": {
                            "source": source,
                            "scope": {"type": "object"},
                            "context_provenance": {"type": "object"},
                        }
                    },
                },
            ],
        }
    )
    return schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = (json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != raw:
            print("paired_handoff_review.v1.schema.json is stale")
            return 1
    else:
        TARGET.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

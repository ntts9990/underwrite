"""Generate read-only measurement replay comparison from existing semantic owners."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _record_schema import schema_main
from _schema_compose import embed_contracts, object_schema, reference, tagged

from underwrite.cli.measure import OBSERVATION_MAX_BYTES, POLICY_MAX_BYTES
from underwrite.cli.replay_measure import (
    COMPARISON_SCOPE,
    ELIGIBLE_DIAGNOSTIC_CODES,
    EXECUTION_MODE,
    LIMITATIONS,
    VERIFICATION_SCOPE,
)
from underwrite.service.records import RECORD_READ_MAX_BYTES

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts/measurement_replay.v1.schema.json"


def build_schema() -> dict[str, Any]:
    definitions, _ = embed_contracts(
        ROOT, ("read", "measurement_error", "record_verification"), versions={"read": "v2"}
    )
    definitions["outcome"] = tagged(
        [
            object_schema({"state": {"const": "result"}, "report": reference("read")}),
            object_schema(
                {
                    "state": {"const": "error"},
                    "error": {
                        "allOf": [
                            reference("measurement_error"),
                            {"properties": {"code": {"enum": sorted(ELIGIBLE_DIAGNOSTIC_CODES)}}},
                        ]
                    },
                }
            ),
        ]
    )
    sources = {
        role: object_schema(
            {
                "name": {"const": f"source/{role}.bin"},
                "sha256": reference("record_verification__digest"),
                "bytes": {"type": "integer", "minimum": 0, "maximum": maximum},
            }
        )
        for role, maximum in (("observation", OBSERVATION_MAX_BYTES), ("policy", POLICY_MAX_BYTES))
    }
    canonical = {
        "canonical_digest": reference("record_verification__digest"),
        "outcome": reference("outcome"),
    }
    stored = object_schema(
        {
            "name": {"enum": ["analysis/read.json", "analysis/error.json"]},
            "sha256": reference("record_verification__digest"),
            "bytes": {"type": "integer", "minimum": 1, "maximum": RECORD_READ_MAX_BYTES},
            **canonical,
        }
    )
    stored["allOf"] = [
        {
            "if": {"properties": {"name": {"const": "analysis/read.json"}}},
            "then": {"properties": {"outcome": {"properties": {"state": {"const": "result"}}}}},
            "else": {"properties": {"outcome": {"properties": {"state": {"const": "error"}}}}},
        }
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:measurement_replay.v1",
        "title": "measurement_replay.v1",
        "description": (
            "Current in-process recalculation compared with a stored complete outcome. "
            "Selected-byte integrity and canonical agreement do not authenticate producers "
            "or establish historical implementation identity. Runtime owns digest correctness "
            "and full canonical outcome equality."
        ),
        **object_schema(
            {
                "schema": {"const": "measurement_replay.v1"},
                "command": {"const": "records replay-measure"},
                "record_id": reference("record_verification__record_id"),
                "manifest_digest": reference("record_verification__digest"),
                "verification_scope": {"const": VERIFICATION_SCOPE},
                "sources": object_schema(sources),
                "stored": stored,
                "recomputed": object_schema(canonical),
                "comparison": object_schema(
                    {
                        "state": {"enum": ["match", "mismatch"]},
                        "scope": {"const": COMPARISON_SCOPE},
                    }
                ),
                "execution_mode": {"const": EXECUTION_MODE},
                "limitations": {"const": list(LIMITATIONS)},
                "exit_code": {"enum": [0, 1]},
            }
        ),
        "$defs": definitions,
        "allOf": [
            {
                "if": {"properties": {"comparison": {"properties": {"state": {"const": "match"}}}}},
                "then": {"properties": {"exit_code": {"const": 0}}},
                "else": {"properties": {"exit_code": {"const": 1}}},
            },
            *[
                {
                    "if": {
                        "properties": {
                            "comparison": {"properties": {"state": {"const": "match"}}},
                            "stored": {
                                "properties": {
                                    "outcome": {"properties": {"state": {"const": state}}}
                                }
                            },
                        }
                    },
                    "then": {
                        "properties": {
                            "recomputed": {
                                "properties": {
                                    "outcome": {"properties": {"state": {"const": state}}}
                                }
                            }
                        }
                    },
                }
                for state in ("result", "error")
            ],
        ],
    }


if __name__ == "__main__":
    raise SystemExit(schema_main(build_schema(), TARGET, __doc__))

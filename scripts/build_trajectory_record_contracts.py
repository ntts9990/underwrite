"""Compose retained trajectory outputs from analysis, history and storage contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _schema_compose import embed_contracts, nullable, object_schema, reference, tagged

from underwrite.cli.record_trajectory import LAYOUT, LAYOUT_V2, READ_SCHEMA_V2, SCHEMA_V2
from underwrite.instrument.evidence.goppi_trajectory import MEMBERS
from underwrite.instrument.ingest.goppi_trajectory import (
    HISTORY_SCHEMA,
    HISTORY_SCHEMA_V2,
    trajectory_member_limits,
)

ROOT = Path(__file__).resolve().parents[1]


def _source_members_schema() -> dict[str, Any]:
    digest = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
    return {
        "type": "array",
        "minItems": len(MEMBERS),
        "maxItems": len(MEMBERS),
        "items": object_schema(
            {
                "name": {"enum": list(MEMBERS)},
                "raw_hash": digest,
                "bytes": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": max(trajectory_member_limits().values()),
                },
            }
        ),
    }


def build_history() -> dict[str, Any]:
    common = {
        "schema": {"const": HISTORY_SCHEMA},
        "source_members": reference("source_members"),
        "source_authentication_independently_verified": {"const": False},
        "producer_projection_independently_verified": {"const": False},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + HISTORY_SCHEMA,
        **tagged(
            [
                object_schema(
                    {
                        **common,
                        "state": {"const": state},
                        "integrity_scope": {"const": integrity},
                        "authenticity": {"const": authenticity},
                    }
                )
                for state, integrity, authenticity in (
                    ("not_performed", None, None),
                    ("verified", "self_consistent_content_closure", "content_digest_only"),
                )
            ]
        ),
        "$defs": {"source_members": _source_members_schema()},
    }


def build_history_v2() -> dict[str, Any]:
    common = {
        "schema": {"const": HISTORY_SCHEMA_V2},
        "verifier_identity_verified": {"const": False},
        "source_members": reference("source_members"),
        "source_authentication_independently_verified": {"const": False},
        "producer_projection_independently_verified": {"const": False},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + HISTORY_SCHEMA_V2,
        **tagged(
            [
                object_schema(
                    {
                        **common,
                        "verifier_report_state": {"const": "not_performed"},
                        "verifier_source": {"const": None},
                        "response_snapshot_binding": {"const": "not_checked"},
                        "snapshot_postcheck": {"const": "not_checked"},
                        "content_closure": {"const": "not_checked"},
                        "authenticity": {"const": "not_checked"},
                    }
                ),
                object_schema(
                    {
                        **common,
                        "verifier_report_state": {"const": "report_accepted"},
                        "verifier_source": {
                            "enum": [
                                "caller_supplied_python_port",
                                "operator_configured_executable",
                            ]
                        },
                        "response_snapshot_binding": {"const": "selected_fields_matched"},
                        "snapshot_postcheck": {"const": "unchanged"},
                        "content_closure": {"const": "verifier_reported_valid"},
                        "authenticity": {"const": "verifier_reported_content_digest_only"},
                    }
                ),
            ],
            field="verifier_report_state",
        ),
        "$defs": {"source_members": _source_members_schema()},
    }


def build_envelopes(root: Path) -> dict[str, dict[str, Any]]:
    definitions, _ = embed_contracts(
        root,
        (
            "goppi_trajectory_review",
            "trajectory_verification_history",
            "evidence_error",
            "record_error",
            "record_verification",
        ),
    )
    definitions["analysis"] = tagged(
        [
            object_schema(
                {"state": {"const": "result"}, "report": reference("goppi_trajectory_review")}
            ),
            object_schema({"state": {"const": "error"}, "error": reference("evidence_error")}),
        ]
    )
    definitions["source"] = object_schema(
        {
            "state": {"const": "captured"},
            "source_members": reference("trajectory_verification_history__source_members"),
        }
    )
    identity = {
        "record_id": reference("record_verification__record_id"),
        "manifest_digest": reference("record_verification__digest"),
    }
    verification = {
        "allOf": [
            reference("record_verification"),
            {
                "properties": {
                    "valid": {"const": True},
                    "files_checked": {"const": len(MEMBERS) + 3},
                    "links_checked": {"const": 0},
                    "findings": {"maxItems": 0},
                }
            },
        ]
    }
    definitions["storage"] = tagged(
        [
            object_schema({"state": {"const": "saved"}, **identity, "verification": verification}),
            object_schema(
                {
                    "state": {"const": "error"},
                    "error": reference("record_error"),
                    "published_record": nullable(object_schema(identity)),
                }
            ),
        ]
    )
    shared = {
        "source": reference("source"),
        "analysis": reference("analysis"),
        "native_verification_history": reference("trajectory_verification_history"),
    }
    schemas: dict[str, dict[str, Any]] = {}
    for name, fields in (
        (
            "trajectory_record.v1",
            {
                "command": {"const": "records audit-trajectory"},
                **shared,
                "storage": reference("storage"),
                "exit_code": {"enum": [0, 2, 3]},
            },
        ),
        (
            "trajectory_record_read.v1",
            {
                "command": {"const": "records read-trajectory"},
                **identity,
                **shared,
                "current_storage_verification": verification,
                "native_verification_performed_now": {"const": False},
            },
        ),
    ):
        schemas[name] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:underwrite:contract:" + name,
            **object_schema({"schema": {"const": name}, **fields}),
            "$defs": definitions,
        }
    schemas["trajectory_record.v1"]["allOf"] = [
        {
            "if": {"properties": {"storage": {"properties": {"state": {"const": "error"}}}}},
            "then": {"properties": {"exit_code": {"const": 3}}},
            "else": {
                "if": {"properties": {"analysis": {"properties": {"state": {"const": "error"}}}}},
                "then": {"properties": {"exit_code": {"const": 2}}},
                "else": {"properties": {"exit_code": {"const": 0}}},
            },
        }
    ]
    return schemas


def _analysis(report: str) -> dict[str, Any]:
    return tagged(
        [
            object_schema({"state": {"const": "result"}, "report": reference(report)}),
            object_schema({"state": {"const": "error"}, "error": reference("evidence_error")}),
        ]
    )


def _verification() -> dict[str, Any]:
    return {
        "allOf": [
            reference("record_verification"),
            {
                "properties": {
                    "valid": {"const": True},
                    "files_checked": {"const": len(MEMBERS) + 3},
                    "links_checked": {"const": 0},
                    "findings": {"maxItems": 0},
                }
            },
        ]
    }


def _interpretation(*, historical: bool) -> dict[str, Any]:
    states = (
        ["legacy_not_performed", "legacy_unattributed_report"]
        if historical
        else ["current_not_performed", "current_report_only"]
    )
    return object_schema(
        {
            "state": {"enum": states},
            "verifier_identity_verified": {"const": False},
            "current_native_verification": {"const": "not_performed"},
            "limitations": {"const": ["verifier_identity_not_recorded"] if historical else []},
        }
    )


def _history_interpretation_binding(
    history_field: str, history_state_field: str, pairs: tuple[tuple[str, str], ...]
) -> list[dict[str, Any]]:
    return [
        {
            "if": {
                "properties": {
                    history_field: {"properties": {history_state_field: {"const": history_state}}}
                }
            },
            "then": {
                "properties": {
                    "verification_interpretation": {
                        "properties": {"state": {"const": interpretation_state}}
                    }
                }
            },
        }
        for history_state, interpretation_state in pairs
    ]


def build_envelopes_v2(root: Path) -> dict[str, dict[str, Any]]:
    definitions, _ = embed_contracts(
        root,
        (
            "goppi_trajectory_review",
            "trajectory_verification_history",
            "evidence_error",
            "record_error",
            "record_verification",
        ),
        versions={
            "goppi_trajectory_review": "v2",
            "trajectory_verification_history": "v2",
        },
    )
    historical, _ = embed_contracts(
        root,
        ("goppi_trajectory_review", "trajectory_verification_history"),
        namespaces={
            "goppi_trajectory_review": "historical_goppi_trajectory_review",
            "trajectory_verification_history": "historical_trajectory_verification_history",
        },
    )
    definitions.update(historical)
    definitions["analysis"] = _analysis("goppi_trajectory_review")
    definitions["historical_analysis"] = _analysis("historical_goppi_trajectory_review")
    definitions["source"] = object_schema(
        {
            "state": {"const": "captured"},
            "source_members": reference("trajectory_verification_history__source_members"),
        }
    )
    identity = {
        "record_id": reference("record_verification__record_id"),
        "manifest_digest": reference("record_verification__digest"),
    }
    verification = _verification()
    definitions["storage"] = tagged(
        [
            object_schema({"state": {"const": "saved"}, **identity, "verification": verification}),
            object_schema(
                {
                    "state": {"const": "error"},
                    "error": reference("record_error"),
                    "published_record": nullable(object_schema(identity)),
                }
            ),
        ]
    )

    record = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + SCHEMA_V2,
        **object_schema(
            {
                "schema": {"const": SCHEMA_V2},
                "command": {"const": "records audit-trajectory"},
                "source": reference("source"),
                "analysis": reference("analysis"),
                "verifier_report_history": reference("trajectory_verification_history"),
                "storage": reference("storage"),
                "exit_code": {"enum": [0, 2, 3]},
            }
        ),
        "$defs": definitions,
        "allOf": [
            {
                "if": {"properties": {"storage": {"properties": {"state": {"const": "error"}}}}},
                "then": {"properties": {"exit_code": {"const": 3}}},
                "else": {
                    "if": {
                        "properties": {"analysis": {"properties": {"state": {"const": "error"}}}}
                    },
                    "then": {"properties": {"exit_code": {"const": 2}}},
                    "else": {"properties": {"exit_code": {"const": 0}}},
                },
            }
        ],
    }

    read_common = {
        "schema": {"const": READ_SCHEMA_V2},
        "command": {"const": "records read-trajectory"},
        **identity,
        "source": reference("source"),
        "current_storage_verification": verification,
        "native_verification_performed_now": {"const": False},
    }
    current_read = object_schema(
        {
            **read_common,
            "record_layout": {"const": LAYOUT_V2},
            "analysis": reference("analysis"),
            "verifier_report_history": reference("trajectory_verification_history"),
            "verification_interpretation": _interpretation(historical=False),
        }
    )
    current_read["allOf"] = _history_interpretation_binding(
        "verifier_report_history",
        "verifier_report_state",
        (
            ("not_performed", "current_not_performed"),
            ("report_accepted", "current_report_only"),
        ),
    )
    historical_read = object_schema(
        {
            **read_common,
            "record_layout": {"const": LAYOUT},
            "historical_analysis": reference("historical_analysis"),
            "historical_native_verification_history": reference(
                "historical_trajectory_verification_history"
            ),
            "verification_interpretation": _interpretation(historical=True),
        }
    )
    historical_read["allOf"] = _history_interpretation_binding(
        "historical_native_verification_history",
        "state",
        (
            ("not_performed", "legacy_not_performed"),
            ("verified", "legacy_unattributed_report"),
        ),
    )
    read = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + READ_SCHEMA_V2,
        **tagged([historical_read, current_read], field="record_layout"),
        "$defs": definitions,
    }
    return {SCHEMA_V2: record, READ_SCHEMA_V2: read}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    schemas = {HISTORY_SCHEMA: build_history(), HISTORY_SCHEMA_V2: build_history_v2()}
    for history_name, history_schema in schemas.items():
        history_path = ROOT / "contracts" / (history_name + ".schema.json")
        history_raw = json.dumps(history_schema, ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if history_path.read_text() != history_raw:
                raise SystemExit("STALE_TRAJECTORY_RECORD_CONTRACT")
        else:
            history_path.write_text(history_raw)
    schemas.update(build_envelopes(ROOT))
    schemas.update(build_envelopes_v2(ROOT))
    for name, schema in schemas.items():
        path = ROOT / "contracts" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if path.read_text() != raw:
                raise SystemExit("STALE_TRAJECTORY_RECORD_CONTRACT")
        else:
            path.write_text(raw)


if __name__ == "__main__":
    main()

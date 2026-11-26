"""Compose retained trajectory outputs from analysis, history and storage contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from _schema_compose import embed_contracts, nullable, object_schema, reference, tagged

from underwrite.cli.record_trajectory import (
    LAYOUT,
    LAYOUT_V2,
    LAYOUT_V4,
    READ_SCHEMA_V3,
    READ_SCHEMA_V4,
    RETAINED_ANALYSIS_LIMITATION,
    SCHEMA_V3,
    SCHEMA_V4,
    SUMMARY_LIMITATIONS,
    SUMMARY_POPULATION_FIELDS,
    SUMMARY_SCHEMA,
    SUMMARY_SCHEMA_V2,
)
from underwrite.instrument.evidence.goppi_trajectory import (
    MEMBERS,
    RESULT_SCHEMA_V2,
    RESULT_SCHEMA_V3,
    RESULT_SCHEMA_V4,
)
from underwrite.instrument.ingest.goppi_trajectory import (
    HISTORY_SCHEMA,
    HISTORY_SCHEMA_V2,
    SOURCE_FORMAT_UNKNOWN,
    SOURCE_FORMAT_V2,
    V4_SOURCE_FORMATS,
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


def _summary_current_storage_verification_schema() -> dict[str, Any]:
    return {
        "const": {
            "status": "verified",
            "scope": "stored_bytes_and_direct_links",
            "files_checked": len(MEMBERS) + 3,
            "links_checked": 0,
            "findings_count": 0,
        }
    }


def _summary_detail_state_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "minProperties": 5,
        "properties": {
            "outcome_details": {"const": "intentionally_omitted"},
            "comparison_details": {"const": "intentionally_omitted"},
            "outcome_missingness_state": {"const": "intentionally_omitted"},
            "analysis_diagnostic": {},
            "full_view": {"const": "records read-trajectory --view full"},
        }
    }


def _summary_missingness_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "minProperties": 2,
        "properties": {
            "population_state": {},
            "missing_population_fields": {
                "type": "array",
            },
        }
    }


def _summary_population_pattern() -> str:
    return "^(?:" + "|".join(SUMMARY_POPULATION_FIELDS) + ")$"


def _summary_populations_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "minProperties": len(SUMMARY_POPULATION_FIELDS),
        "patternProperties": {
            _summary_population_pattern(): reference("population_value")
        },
    }


def _summary_available_populations_schema() -> dict[str, Any]:
    return {
        "patternProperties": {_summary_population_pattern(): {"type": "integer"}}
    }


def _summary_missing_populations_schema() -> dict[str, Any]:
    return {
        "patternProperties": {_summary_population_pattern(): {"type": "null"}}
    }


def _summary_authority_schema() -> dict[str, Any]:
    return {
        "const": {
            "human_review": "required",
            "execution_authorized": False,
            "merge_authorized": False,
            "revert_authorized": False,
        }
    }


def _summary_verifier_claim_flag_pattern() -> str:
    return (
        "^(?:verifier_identity_verified|source_authentication_independently_verified|"
        "producer_projection_independently_verified)$"
    )


def _summary_historical_verifier_claim_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "minProperties": 5,
        "properties": {"state": {}, "reported_scope": {}},
        "patternProperties": {
            _summary_verifier_claim_flag_pattern(): {
                "const": False
            }
        },
        "oneOf": [
            {
                "properties": {
                    "state": {"const": "not_performed"},
                    "reported_scope": {"const": "not_performed"},
                }
            },
            {
                "properties": {
                    "state": {"const": "report_accepted"},
                    "reported_scope": {"const": "report_only_content_closure"},
                }
            },
            {
                "properties": {
                    "state": {"const": "legacy_unattributed_report"},
                    "reported_scope": {
                        "const": "legacy_self_consistent_content_closure"
                    },
                }
            },
        ],
    }


def build_mcp_summary_output_schema() -> dict[str, Any]:
    """Build the exact summary union advertised by the MCP trajectory tool."""
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "minProperties": 15,
        "properties": {
            "schema": {"enum": [SUMMARY_SCHEMA, SUMMARY_SCHEMA_V2]},
            "command": {"const": "records read-trajectory"},
            "view": {"const": "summary"},
            "record_id": reference("record_id"),
            "manifest_digest": reference("digest"),
            "record_layout": {"enum": [LAYOUT, LAYOUT_V2, LAYOUT_V4]},
            "source_format": {"enum": list(V4_SOURCE_FORMATS)},
            "analysis_state": {"enum": ["result", "error"]},
            "analysis_limitations": {
                "enum": [[], [RETAINED_ANALYSIS_LIMITATION]]
            },
            "current_storage_verification": _summary_current_storage_verification_schema(),
            "historical_verifier_claim": _summary_historical_verifier_claim_schema(),
            "populations": reference("populations"),
            "missingness": _summary_missingness_schema(),
            "detail_state": _summary_detail_state_schema(),
            "native_verification_performed_now": {"const": False},
            "authority": _summary_authority_schema(),
            "limitations": {"const": SUMMARY_LIMITATIONS},
        },
        "$defs": {
            "record_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 96,
                "pattern": r"^[a-z0-9][a-z0-9._-]{0,95}(?![\s\S])",
            },
            "digest": {
                "type": "string",
                "pattern": "^sha256:[0-9a-f]{64}(?![\\s\\S])",
            },
            "population_value": {
                "type": ["integer", "null"],
                "minimum": 0,
                "maximum": 10000,
            },
            "populations": _summary_populations_schema(),
            "available_populations": _summary_available_populations_schema(),
            "missing_populations": _summary_missing_populations_schema(),
            "legacy_summary_fields_absent": {
                "not": {
                    "anyOf": [
                        {"required": ["source_format"]},
                        {"required": ["analysis_limitations"]},
                    ]
                }
            },
        },
        "allOf": [
            {
                "if": {"properties": {"record_layout": {"const": LAYOUT}}},
                "then": {
                    "properties": {
                        "historical_verifier_claim": {
                            "properties": {
                                "state": {
                                    "enum": ["not_performed", "legacy_unattributed_report"]
                                }
                            }
                        }
                    }
                },
                "else": {
                    "properties": {
                        "historical_verifier_claim": {
                            "properties": {
                                "state": {"enum": ["not_performed", "report_accepted"]}
                            }
                        }
                    }
                },
            },
            {
                "if": {"properties": {"analysis_state": {"const": "result"}}},
                "then": {
                    "properties": {
                        "missingness": {
                            "properties": {
                                "population_state": {"const": "available"},
                                "missing_population_fields": {"maxItems": 0},
                            }
                        },
                        "populations": reference("available_populations"),
                        "detail_state": {
                            "properties": {"analysis_diagnostic": {"const": "not_applicable"}}
                        },
                    }
                },
            },
            {
                "if": {"properties": {"analysis_state": {"const": "error"}}},
                "then": {
                    "properties": {
                        "analysis_limitations": {"const": []},
                        "missingness": {
                            "properties": {
                                "population_state": {"const": "not_available_analysis_error"},
                                "missing_population_fields": {
                                    "const": list(SUMMARY_POPULATION_FIELDS)
                                },
                            }
                        },
                        "populations": reference("missing_populations"),
                        "detail_state": {
                            "properties": {
                                "analysis_diagnostic": {
                                    "const": "intentionally_omitted_use_full_view"
                                }
                            }
                        },
                    }
                },
            },
            {
                "if": {"properties": {"schema": {"const": SUMMARY_SCHEMA}}},
                "then": {
                    "allOf": [
                        reference("legacy_summary_fields_absent"),
                        {"properties": {"record_layout": {"enum": [LAYOUT, LAYOUT_V2]}}},
                    ]
                },
                "else": {
                    "properties": {"record_layout": {"const": LAYOUT_V4}},
                    "required": ["source_format", "analysis_limitations"],
                    "minProperties": 17,
                },
            },
            {
                "if": {"properties": {"analysis_state": {"const": "result"}}},
                "then": {
                    "if": {"properties": {"record_layout": {"const": LAYOUT}}},
                    "then": {
                        "properties": {
                            "historical_verifier_claim": {
                                "properties": {"state": {"const": "legacy_unattributed_report"}}
                            }
                        }
                    },
                    "else": {
                        "properties": {
                            "historical_verifier_claim": {
                                "properties": {"state": {"const": "report_accepted"}}
                            },
                            "analysis_limitations": {"const": []},
                        }
                    },
                },
            },
            {
                "if": {
                    "required": ["source_format"],
                    "properties": {"source_format": {"const": SOURCE_FORMAT_UNKNOWN}},
                },
                "then": {
                    "properties": {
                        "analysis_state": {"const": "error"},
                        "historical_verifier_claim": {
                            "properties": {"state": {"const": "not_performed"}}
                        },
                    }
                },
            },
        ],
    }
    schema["allOf"][0] = {
        "if": {"properties": {"record_layout": {"const": LAYOUT}}},
        "then": {
            "not": {
                "properties": {
                    "historical_verifier_claim": {
                        "properties": {"state": {"const": "report_accepted"}}
                    }
                }
            }
        },
        "else": {
            "not": {
                "properties": {
                    "historical_verifier_claim": {
                        "properties": {
                            "state": {"const": "legacy_unattributed_report"}
                        }
                    }
                }
            }
        },
    }
    return schema


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


def _analysis_limitations_binding(analysis_field: str, *, prior_only: bool) -> dict[str, Any]:
    """A retained result without the current review's disclosure must say so."""
    report: dict[str, Any] = {"required": ["state"], "properties": {"state": {"const": "result"}}}
    if prior_only:
        report["required"].append("report")
        report["properties"]["report"] = {
            "required": ["schema"],
            "properties": {"schema": {"const": RESULT_SCHEMA_V2}},
        }
    return {
        "if": {"required": [analysis_field], "properties": {analysis_field: report}},
        "then": {"properties": {"analysis_limitations": {"const": [RETAINED_ANALYSIS_LIMITATION]}}},
        "else": {"properties": {"analysis_limitations": {"const": []}}},
    }


def _source_format_analysis_binding(
    analysis_field: str, history_field: str, *, result_report_schema: str | None = None
) -> list[dict[str, Any]]:
    """Bind v4 source labels to analysis and verifier states."""
    result_properties: dict[str, Any] = {
        "source": {"properties": {"source_format": {"const": SOURCE_FORMAT_V2}}}
    }
    if result_report_schema is not None:
        result_properties[analysis_field] = {
            "properties": {
                "report": {"properties": {"schema": {"const": result_report_schema}}}
            }
        }
    return [
        {
            "if": {
                "properties": {
                    "source": {
                        "properties": {
                            "source_format": {"const": SOURCE_FORMAT_UNKNOWN}
                        }
                    }
                }
            },
            "then": {
                "properties": {
                    analysis_field: {"properties": {"state": {"const": "error"}}},
                    history_field: {
                        "properties": {"verifier_report_state": {"const": "not_performed"}}
                    },
                }
            },
        },
        {
            "if": {
                "properties": {
                    analysis_field: {"properties": {"state": {"const": "result"}}}
                }
            },
            "then": {
                "properties": result_properties
            },
        },
    ]


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


def _retained_report() -> dict[str, Any]:
    """Layout-v2 records keep the analysis generation they were written with."""
    generations = {
        RESULT_SCHEMA_V3: "goppi_trajectory_review",
        RESULT_SCHEMA_V2: "prior_goppi_trajectory_review",
    }
    return {
        "type": "object",
        "required": ["schema"],
        "properties": {"schema": {"enum": list(generations)}},
        "allOf": [
            {"if": {"properties": {"schema": {"const": name}}}, "then": reference(definition)}
            for name, definition in generations.items()
        ],
    }


def _retained_report_v4() -> dict[str, Any]:
    generations = {
        RESULT_SCHEMA_V4: "goppi_trajectory_review",
        RESULT_SCHEMA_V3: "prior_v3_goppi_trajectory_review",
        RESULT_SCHEMA_V2: "prior_goppi_trajectory_review",
    }
    return {
        "type": "object",
        "required": ["schema"],
        "properties": {"schema": {"enum": list(generations)}},
        "allOf": [
            {"if": {"properties": {"schema": {"const": name}}}, "then": reference(definition)}
            for name, definition in generations.items()
        ],
    }


def build_envelopes_v3(root: Path) -> dict[str, dict[str, Any]]:
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
            "goppi_trajectory_review": "v3",
            "trajectory_verification_history": "v2",
        },
    )
    prior, _ = embed_contracts(
        root,
        ("goppi_trajectory_review",),
        versions={"goppi_trajectory_review": "v2"},
        namespaces={"goppi_trajectory_review": "prior_goppi_trajectory_review"},
    )
    definitions.update(prior)
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
    definitions["retained_report"] = _retained_report()
    definitions["retained_analysis"] = _analysis("retained_report")
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
        "$id": "urn:underwrite:contract:" + SCHEMA_V3,
        **object_schema(
            {
                "schema": {"const": SCHEMA_V3},
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

    limitations = {"enum": [[], [RETAINED_ANALYSIS_LIMITATION]]}
    read_common = {
        "schema": {"const": READ_SCHEMA_V3},
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
            "analysis": reference("retained_analysis"),
            "verifier_report_history": reference("trajectory_verification_history"),
            "verification_interpretation": _interpretation(historical=False),
            "analysis_limitations": limitations,
        }
    )
    current_read["allOf"] = [
        *_history_interpretation_binding(
            "verifier_report_history",
            "verifier_report_state",
            (
                ("not_performed", "current_not_performed"),
                ("report_accepted", "current_report_only"),
            ),
        ),
        _analysis_limitations_binding("analysis", prior_only=True),
    ]
    historical_read = object_schema(
        {
            **read_common,
            "record_layout": {"const": LAYOUT},
            "historical_analysis": reference("historical_analysis"),
            "historical_native_verification_history": reference(
                "historical_trajectory_verification_history"
            ),
            "verification_interpretation": _interpretation(historical=True),
            "analysis_limitations": limitations,
        }
    )
    historical_read["allOf"] = [
        *_history_interpretation_binding(
            "historical_native_verification_history",
            "state",
            (
                ("not_performed", "legacy_not_performed"),
                ("verified", "legacy_unattributed_report"),
            ),
        ),
        _analysis_limitations_binding("historical_analysis", prior_only=False),
    ]
    read = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + READ_SCHEMA_V3,
        **tagged([historical_read, current_read], field="record_layout"),
        "$defs": definitions,
    }
    return {SCHEMA_V3: record, READ_SCHEMA_V3: read}



def build_envelopes_v4(root: Path) -> dict[str, dict[str, Any]]:
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
            "goppi_trajectory_review": "v4",
            "trajectory_verification_history": "v2",
        },
    )
    prior, _ = embed_contracts(
        root,
        ("goppi_trajectory_review",),
        versions={"goppi_trajectory_review": "v2"},
        namespaces={"goppi_trajectory_review": "prior_goppi_trajectory_review"},
    )
    definitions.update(prior)
    prior_v3, _ = embed_contracts(
        root,
        ("goppi_trajectory_review",),
        versions={"goppi_trajectory_review": "v3"},
        namespaces={"goppi_trajectory_review": "prior_v3_goppi_trajectory_review"},
    )
    definitions.update(prior_v3)
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
    definitions["retained_report"] = _retained_report_v4()
    definitions["retained_analysis"] = _analysis("retained_report")
    definitions["historical_analysis"] = _analysis("historical_goppi_trajectory_review")
    definitions["source"] = object_schema(
        {
            "state": {"const": "captured"},
            "source_members": reference("trajectory_verification_history__source_members"),
        }
    )
    definitions["source_with_format"] = object_schema(
        {
            "state": {"const": "captured"},
            "source_members": reference("trajectory_verification_history__source_members"),
            "source_format": {"enum": list(V4_SOURCE_FORMATS)},
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
        "$id": "urn:underwrite:contract:" + SCHEMA_V4,
        **object_schema(
            {
                "schema": {"const": SCHEMA_V4},
                "command": {"const": "records audit-trajectory"},
                "source": reference("source_with_format"),
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
            },
            *_source_format_analysis_binding("analysis", "verifier_report_history"),
        ],
    }

    limitations = {"enum": [[], [RETAINED_ANALYSIS_LIMITATION]]}
    read_common = {
        "schema": {"const": READ_SCHEMA_V4},
        "command": {"const": "records read-trajectory"},
        **identity,
        "source": reference("source"),
        "current_storage_verification": verification,
        "native_verification_performed_now": {"const": False},
    }
    current_read = object_schema(
        {
            **read_common,
            "record_layout": {"const": LAYOUT_V4},
            "source": reference("source_with_format"),
            "analysis": reference("retained_analysis"),
            "verifier_report_history": reference("trajectory_verification_history"),
            "verification_interpretation": _interpretation(historical=False),
            "analysis_limitations": limitations,
        }
    )
    current_read["allOf"] = [
        *_history_interpretation_binding(
            "verifier_report_history",
            "verifier_report_state",
            (
                ("not_performed", "current_not_performed"),
                ("report_accepted", "current_report_only"),
            ),
        ),
        _analysis_limitations_binding("analysis", prior_only=True),
        *_source_format_analysis_binding(
            "analysis",
            "verifier_report_history",
            result_report_schema=RESULT_SCHEMA_V4,
        ),
    ]
    historical_read = object_schema(
        {
            **read_common,
            "record_layout": {"const": LAYOUT},
            "historical_analysis": reference("historical_analysis"),
            "historical_native_verification_history": reference(
                "historical_trajectory_verification_history"
            ),
            "verification_interpretation": _interpretation(historical=True),
            "analysis_limitations": limitations,
        }
    )
    historical_read["allOf"] = [
        *_history_interpretation_binding(
            "historical_native_verification_history",
            "state",
            (
                ("not_performed", "legacy_not_performed"),
                ("verified", "legacy_unattributed_report"),
            ),
        ),
        _analysis_limitations_binding("historical_analysis", prior_only=False),
    ]
    read = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + READ_SCHEMA_V4,
        **tagged([historical_read, current_read], field="record_layout"),
        "$defs": definitions,
    }
    return {SCHEMA_V4: record, READ_SCHEMA_V4: read}


def build_read_summary_v2() -> dict[str, Any]:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + SUMMARY_SCHEMA_V2,
        "title": SUMMARY_SCHEMA_V2,
        "description": (
            "Content-minimized trajectory record reopen result. Current stored-byte "
            "verification is distinct from retained historical verifier claims."
        ),
        **object_schema(
            {
                "schema": {"enum": [SUMMARY_SCHEMA, SUMMARY_SCHEMA_V2]},
                "command": {"const": "records read-trajectory"},
                "view": {"const": "summary"},
                "record_id": reference("record_id"),
                "manifest_digest": reference("digest"),
                "record_layout": {"enum": [LAYOUT, LAYOUT_V2, LAYOUT_V4]},
                "source_format": {"enum": list(V4_SOURCE_FORMATS)},
                "analysis_state": {"enum": ["result", "error"]},
                "analysis_limitations": {
                    "enum": [[], [RETAINED_ANALYSIS_LIMITATION]]
                },
                "current_storage_verification": _summary_current_storage_verification_schema(),
                "historical_verifier_claim": {
                    **object_schema(
                        {
                            "state": {
                                "enum": [
                                    "not_performed",
                                    "report_accepted",
                                    "legacy_unattributed_report",
                                ]
                            },
                            "reported_scope": {
                                "enum": [
                                    "not_performed",
                                    "report_only_content_closure",
                                    "legacy_self_consistent_content_closure",
                                ]
                            },
                            "verifier_identity_verified": {"const": False},
                            "source_authentication_independently_verified": {"const": False},
                            "producer_projection_independently_verified": {"const": False},
                        }
                    ),
                    "allOf": [
                        {
                            "if": {"properties": {"state": {"const": "not_performed"}}},
                            "then": {
                                "properties": {"reported_scope": {"const": "not_performed"}}
                            },
                        },
                        {
                            "if": {"properties": {"state": {"const": "report_accepted"}}},
                            "then": {
                                "properties": {
                                    "reported_scope": {
                                        "const": "report_only_content_closure"
                                    }
                                }
                            },
                        },
                        {
                            "if": {
                                "properties": {
                                    "state": {"const": "legacy_unattributed_report"}
                                }
                            },
                            "then": {
                                "properties": {
                                    "reported_scope": {
                                        "const": "legacy_self_consistent_content_closure"
                                    }
                                }
                            },
                        },
                    ],
                },
                "populations": reference("populations"),
                "missingness": _summary_missingness_schema(),
                "detail_state": _summary_detail_state_schema(),
                "native_verification_performed_now": {"const": False},
                "authority": _summary_authority_schema(),
                "limitations": {"const": SUMMARY_LIMITATIONS},
            }
        ),
        "$defs": {
            "record_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 96,
                "pattern": r"^[a-z0-9][a-z0-9._-]{0,95}(?![\s\S])",
            },
            "digest": {
                "type": "string",
                "pattern": "^sha256:[0-9a-f]{64}(?![\\s\\S])",
            },
            "population_field": {"enum": list(SUMMARY_POPULATION_FIELDS)},
            "population_value": {
                "type": ["integer", "null"],
                "minimum": 0,
                "maximum": 10000,
            },
            "populations": _summary_populations_schema(),
            "available_populations": _summary_available_populations_schema(),
            "missing_populations": _summary_missing_populations_schema(),
            "summary_legacy_fields_absent": {
                "not": {
                    "anyOf": [
                        {"required": ["source_format"]},
                        {"required": ["analysis_limitations"]},
                    ]
                }
            },
        },
    }
    required = cast(list[str], schema["required"])
    required.remove("source_format")
    required.remove("analysis_limitations")
    schema["allOf"] = [
        {
            "if": {"properties": {"analysis_state": {"const": "result"}}},
            "then": {
                "properties": {
                    "populations": reference("available_populations"),
                    "missingness": {
                        "properties": {
                            "population_state": {"const": "available"},
                            "missing_population_fields": {"maxItems": 0},
                        }
                    },
                    "detail_state": {
                        "properties": {"analysis_diagnostic": {"const": "not_applicable"}}
                    },
                }
            },
        },
        {
            "if": {"properties": {"analysis_state": {"const": "error"}}},
            "then": {
                "properties": {
                    "analysis_limitations": {"const": []},
                    "populations": reference("missing_populations"),
                    "missingness": {
                        "properties": {
                            "population_state": {"const": "not_available_analysis_error"},
                            "missing_population_fields": {
                                "const": list(SUMMARY_POPULATION_FIELDS)
                            },
                        }
                    },
                    "detail_state": {
                        "properties": {
                            "analysis_diagnostic": {
                                "const": "intentionally_omitted_use_full_view"
                            }
                        }
                    },
                }
            },
        },
    ]
    schema["allOf"].extend(
        [
            {
                "if": {"properties": {"record_layout": {"const": LAYOUT}}},
                "then": {
                    "allOf": [
                        reference("summary_legacy_fields_absent"),
                        {
                            "properties": {
                                "schema": {"const": SUMMARY_SCHEMA},
                                "historical_verifier_claim": {
                                    "properties": {
                                        "state": {
                                            "enum": [
                                                "not_performed",
                                                "legacy_unattributed_report",
                                            ]
                                        }
                                    }
                                },
                            }
                        },
                    ]
                },
                "else": {
                    "if": {"properties": {"record_layout": {"const": LAYOUT_V2}}},
                    "then": {
                        "allOf": [
                            reference("summary_legacy_fields_absent"),
                            {
                                "properties": {
                                    "schema": {"const": SUMMARY_SCHEMA},
                                    "historical_verifier_claim": {
                                        "properties": {
                                            "state": {
                                                "enum": ["not_performed", "report_accepted"]
                                            }
                                        }
                                    },
                                }
                            },
                        ]
                    },
                    "else": {
                        "required": ["source_format", "analysis_limitations"],
                        "properties": {
                            "record_layout": {"const": LAYOUT_V4},
                            "schema": {"const": SUMMARY_SCHEMA_V2},
                            "source_format": {"enum": list(V4_SOURCE_FORMATS)},
                            "historical_verifier_claim": {
                                "properties": {
                                    "state": {
                                        "enum": ["not_performed", "report_accepted"]
                                    }
                                }
                            },
                        },
                    },
                },
            },
            {
                "if": {"properties": {"analysis_state": {"const": "result"}}},
                "then": {
                    "if": {"properties": {"record_layout": {"const": LAYOUT}}},
                    "then": {
                        "properties": {
                            "historical_verifier_claim": {
                                "properties": {"state": {"const": "legacy_unattributed_report"}}
                            }
                        }
                    },
                    "else": {
                        "properties": {
                            "historical_verifier_claim": {
                                "properties": {"state": {"const": "report_accepted"}}
                            },
                            "analysis_limitations": {"const": []},
                        }
                    },
                },
            },
            {
                "if": {
                    "required": ["source_format"],
                    "properties": {"source_format": {"const": SOURCE_FORMAT_UNKNOWN}},
                },
                "then": {
                    "properties": {
                        "analysis_state": {"const": "error"},
                        "historical_verifier_claim": {
                            "properties": {"state": {"const": "not_performed"}}
                        },
                    }
                },
            },
        ]
    )
    cast(dict[str, Any], schema["$defs"])["mcp_output_schema"] = (
        build_mcp_summary_output_schema()
    )
    return schema


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
    schemas.update(build_envelopes_v3(ROOT))
    schemas.update(build_envelopes_v4(ROOT))
    schemas[SUMMARY_SCHEMA_V2] = build_read_summary_v2()
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

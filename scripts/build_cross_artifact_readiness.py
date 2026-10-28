"""Derive the closed readiness result contract from the authored review contract."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts/cross_artifact_review.v1.schema.json"
TARGET = ROOT / "contracts/cross_artifact_readiness.v1.schema.json"
RESULT_SCHEMA = "cross_artifact_readiness.v1"


def _blocker_matches(codes: tuple[str, ...]) -> dict[str, Any]:
    return {"contains": {"properties": {"code": {"enum": list(codes)}}}}


def _prerequisite_task(state: str, code: str) -> dict[str, Any]:
    return {
        "if": {"properties": {"review_prerequisites": {"const": state}}},
        "then": {
            "properties": {
                "next_verification_tasks": {
                    "prefixItems": [
                        {
                            "properties": {
                                "code": {"const": code},
                                "owner": {"const": "evaluation_owner"},
                            }
                        }
                    ]
                }
            }
        },
    }


def build() -> dict[str, Any]:
    review = json.loads(SOURCE.read_text())
    source = review["properties"]
    promptfoo = deepcopy(source["populations"]["properties"]["promptfoo"])
    promptfoo["properties"]["supplied_rows"]["maximum"] = 1000
    for field in ("subject_rows", "control_rows"):
        promptfoo["properties"][field]["type"] = ["integer", "null"]

    populations = deepcopy(source["populations"])
    populations["properties"]["promptfoo"] = promptfoo

    finding_counts = deepcopy(source["finding_counts"])
    for field in ("equal", "different", "ineligible"):
        finding_counts["properties"][field] = {"const": 0}

    checks = deepcopy(source["checks"])
    checks["required"].append("promptfoo_accounting")
    checks["properties"]["declaration_source_hashes"] = {"const": "not_supplied"}
    checks["properties"]["promptfoo_accounting"] = {"const": "consistent"}

    tasks = deepcopy(source["next_verification_tasks"])
    tasks["items"]["properties"]["code"] = {
        "enum": [
            "PROVIDE_PROMPTFOO_CASE_METADATA",
            "PROVIDE_PROMPTFOO_SUBJECT_ROWS",
            "PROVIDE_SOURCE_BOUND_CASE_CORRESPONDENCE",
            "VERIFY_GOPPI_PORTABLE_CLOSURE",
            "VERIFY_SOURCE_PROVENANCE",
        ]
    }

    metadata = {
        "type": "object",
        "additionalProperties": False,
        "required": ["case_ids", "row_roles", "blockers"],
        "properties": {
            "case_ids": {"enum": ["complete", "incomplete"]},
            "row_roles": {"enum": ["source_declared", "incomplete"]},
            "blockers": {
                "type": "array",
                "maxItems": 2000,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["row_index", "code", "location"],
                    "properties": {
                        "row_index": {"type": "integer", "minimum": 0, "maximum": 999},
                        "code": {
                            "enum": [
                                "PROMPTFOO_CASE_METADATA_REQUIRED",
                                "PROMPTFOO_CASE_ID_REQUIRED",
                                "PROMPTFOO_SLOT_KIND_REQUIRED",
                            ]
                        },
                        "location": {
                            "type": "string",
                            "pattern": (
                                "^/promptfoo/results/results/(?:0|[1-9][0-9]{0,2})"
                                "/testCase(?:/metadata(?:/(?:case_id|slot_kind))?)?$"
                            ),
                        },
                    },
                },
            },
        },
    }

    return {
        "$schema": review["$schema"],
        "$id": f"urn:underwrite:contract:{RESULT_SCHEMA}",
        "title": RESULT_SCHEMA,
        "description": (
            "Source metadata readiness for cross-artifact review; no case correspondence, "
            "improvement, provenance, or approval decision."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema",
            "relation",
            "source_identity",
            "readers",
            "populations",
            "metadata",
            "review_prerequisites",
            "finding_counts",
            "improvement",
            "checks",
            "next_verification_tasks",
            "limitations",
        ],
        "properties": {
            "schema": {"const": RESULT_SCHEMA},
            "relation": {"const": "incomparable_on_supplied_evidence"},
            "source_identity": deepcopy(source["source_identity"]),
            "readers": deepcopy(source["readers"]),
            "populations": populations,
            "metadata": metadata,
            "review_prerequisites": {
                "enum": [
                    "missing_owner_metadata",
                    "missing_subject_rows",
                    "needs_owner_correspondence",
                ]
            },
            "finding_counts": finding_counts,
            "improvement": deepcopy(source["improvement"]),
            "checks": checks,
            "next_verification_tasks": tasks,
            "limitations": deepcopy(source["limitations"]),
        },
        "allOf": [
            {
                "if": {
                    "properties": {
                        "metadata": {"properties": {"row_roles": {"const": "incomplete"}}}
                    }
                },
                "then": {
                    "properties": {
                        "populations": {
                            "properties": {
                                "promptfoo": {
                                    "properties": {
                                        "subject_rows": {"type": "null"},
                                        "control_rows": {"type": "null"},
                                    }
                                }
                            }
                        }
                    }
                },
                "else": {
                    "properties": {
                        "populations": {
                            "properties": {
                                "promptfoo": {
                                    "properties": {
                                        "subject_rows": {"type": "integer"},
                                        "control_rows": {"type": "integer"},
                                    }
                                }
                            }
                        }
                    }
                },
            },
            {
                "if": {
                    "properties": {
                        "metadata": {
                            "properties": {
                                "case_ids": {"const": "complete"},
                                "row_roles": {"const": "source_declared"},
                            }
                        }
                    }
                },
                "then": {
                    "properties": {
                        "review_prerequisites": {
                            "enum": ["missing_subject_rows", "needs_owner_correspondence"]
                        },
                        "metadata": {"properties": {"blockers": {"maxItems": 0}}},
                    }
                },
                "else": {
                    "properties": {
                        "review_prerequisites": {"const": "missing_owner_metadata"},
                        "metadata": {"properties": {"blockers": {"minItems": 1}}},
                    }
                },
            },
            {
                "if": {
                    "properties": {"metadata": {"properties": {"case_ids": {"const": "complete"}}}}
                },
                "then": {
                    "properties": {
                        "metadata": {
                            "properties": {
                                "blockers": {
                                    "not": _blocker_matches(
                                        (
                                            "PROMPTFOO_CASE_METADATA_REQUIRED",
                                            "PROMPTFOO_CASE_ID_REQUIRED",
                                        )
                                    )
                                }
                            }
                        }
                    }
                },
                "else": {
                    "properties": {
                        "metadata": {
                            "properties": {
                                "blockers": _blocker_matches(
                                    (
                                        "PROMPTFOO_CASE_METADATA_REQUIRED",
                                        "PROMPTFOO_CASE_ID_REQUIRED",
                                    )
                                )
                            }
                        }
                    }
                },
            },
            {
                "if": {
                    "properties": {
                        "metadata": {"properties": {"row_roles": {"const": "source_declared"}}}
                    }
                },
                "then": {
                    "properties": {
                        "metadata": {
                            "properties": {
                                "blockers": {
                                    "not": _blocker_matches(
                                        (
                                            "PROMPTFOO_CASE_METADATA_REQUIRED",
                                            "PROMPTFOO_SLOT_KIND_REQUIRED",
                                        )
                                    )
                                }
                            }
                        }
                    }
                },
                "else": {
                    "properties": {
                        "metadata": {
                            "properties": {
                                "blockers": _blocker_matches(
                                    (
                                        "PROMPTFOO_CASE_METADATA_REQUIRED",
                                        "PROMPTFOO_SLOT_KIND_REQUIRED",
                                    )
                                )
                            }
                        }
                    }
                },
            },
            {
                "if": {"properties": {"review_prerequisites": {"const": "missing_subject_rows"}}},
                "then": {
                    "properties": {
                        "populations": {
                            "properties": {
                                "promptfoo": {"properties": {"subject_rows": {"const": 0}}}
                            }
                        },
                        "metadata": {
                            "properties": {
                                "case_ids": {"const": "complete"},
                                "row_roles": {"const": "source_declared"},
                                "blockers": {"maxItems": 0},
                            }
                        },
                    }
                },
            },
            {
                "if": {
                    "properties": {"review_prerequisites": {"const": "needs_owner_correspondence"}}
                },
                "then": {
                    "properties": {
                        "populations": {
                            "properties": {
                                "promptfoo": {
                                    "properties": {
                                        "subject_rows": {"type": "integer", "minimum": 1}
                                    }
                                }
                            }
                        }
                    }
                },
            },
            _prerequisite_task("missing_owner_metadata", "PROVIDE_PROMPTFOO_CASE_METADATA"),
            _prerequisite_task("missing_subject_rows", "PROVIDE_PROMPTFOO_SUBJECT_ROWS"),
            _prerequisite_task(
                "needs_owner_correspondence", "PROVIDE_SOURCE_BOUND_CASE_CORRESPONDENCE"
            ),
        ],
        "$defs": deepcopy(review["$defs"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if TARGET.read_text() != rendered:
            raise SystemExit("STALE_CROSS_ARTIFACT_READINESS_CONTRACT")
    else:
        TARGET.write_text(rendered)


if __name__ == "__main__":
    main()

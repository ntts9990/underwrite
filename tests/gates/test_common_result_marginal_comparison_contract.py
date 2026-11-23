"""Marginal comparison is a new closed contract; v3 inspection stays byte stable."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, ValidationError

from scripts import build_common_result_comparison as builder
from underwrite.cli.compare_results import INELIGIBLE_TASKS, RESULT_SCHEMA, compare_result_files
from underwrite.instrument.evidence.common_result import MARGINAL_INELIGIBILITY_REASONS
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).resolve().parents[2]
V3_SHA256 = "b3ed0fce0f9ef7ec1f90f43058503afb8550c10c92cc1abbe77c9310f5f6249d"


def test_generated_contract_has_one_owner_and_installed_bytes() -> None:
    path = ROOT / f"contracts/{RESULT_SCHEMA}.schema.json"
    authored = path.read_bytes()
    expected = (json.dumps(builder.build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    assert authored == expected
    assert (
        ROOT / f"src/underwrite/_contracts/{RESULT_SCHEMA}.schema.json"
    ).read_bytes() == authored
    schema = cast(dict[str, Any], json.loads(authored))
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["item_correspondence"] == {
        "const": {
            "state": "not_established",
            "reason": "CROSS_RESULT_ITEM_BINDING_NOT_SUPPLIED",
        }
    }
    assert schema == packaged_validator(RESULT_SCHEMA).schema


def test_existing_v3_inspection_contract_bytes_do_not_change() -> None:
    path = ROOT / "contracts/common_result_inspection_actions.v3.schema.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == V3_SHA256


def test_ineligibility_reason_tasks_and_schema_share_one_closed_owner() -> None:
    assert set(INELIGIBLE_TASKS) == set(MARGINAL_INELIGIBILITY_REASONS)
    reason = builder.build_schema()["properties"]["marginal_calculation"]["properties"]["reason"]
    assert reason["oneOf"][1]["enum"] == list(MARGINAL_INELIGIBILITY_REASONS)


@pytest.mark.parametrize("mutation", ["task", "processing", "format", "unit"])
def test_contract_refuses_computed_result_with_ineligible_declarations(mutation: str) -> None:
    path = ROOT / "fixtures/examples/evidence/common-result-c55.json"
    report = cast(dict[str, Any], deepcopy(compare_result_files(path, path)))
    if mutation == "task":
        report["next_verification_tasks"] = INELIGIBLE_TASKS["RESULT_NOT_ANALYZED"]
    elif mutation == "processing":
        report["declared_processing"]["baseline"] = {
            "state": "insufficient",
            "reason": "MISSING_INPUT",
            "next_action": "supply_input",
        }
        report["declared_population"]["baseline"] = None
    elif mutation == "format":
        report["selected_results"]["candidate"]["format"] = "evidence_result.v2"
    else:
        report["declared_population"]["candidate"]["unit"] = "row"
    with pytest.raises(ValidationError):
        Draft202012Validator(builder.build_schema()).validate(report)  # pyright: ignore[reportUnknownMemberType]


@pytest.mark.parametrize("field", ["item_correspondence", "semantic_comparability", "limitations"])
def test_contract_refuses_unearned_relation_claims(field: str) -> None:
    schema = builder.build_schema()
    # A minimal ineligible result has no deltas or item correspondence proof.
    exemplar: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "selected_results": {
            side: {"format": "evidence_result.v1", "raw_hash": "sha256:" + "0" * 64}
            for side in ("baseline", "candidate")
        },
        "declared_processing": {
            side: {
                "state": "insufficient",
                "reason": "MISSING_INPUT",
                "next_action": "supply_input",
            }
            for side in ("baseline", "candidate")
        },
        "declared_population": {"baseline": None, "candidate": None},
        "marginal_calculation": {
            "state": "not_computed",
            "reason": "RESULT_NOT_ANALYZED",
            "direction": "candidate_minus_baseline",
            "population_unit": None,
            "observed_population_count_delta": None,
            "planned_population_count_delta": None,
            "availability_counts": None,
            "item_next_action_counts": None,
            "declared_semantic_binding_counts": None,
        },
        "item_correspondence": {
            "state": "not_established",
            "reason": "CROSS_RESULT_ITEM_BINDING_NOT_SUPPLIED",
        },
        "semantic_comparability": "unestablished",
        "next_verification_tasks": [
            {"code": "RESOLVE_SOURCE_PROCESSING", "owner": "producer_owner"}
        ],
        "limitations": [
            "SOURCE_CLAIMS_UNVERIFIED",
            "MARGINAL_DELTAS_NOT_ITEM_TRANSITIONS",
            "EQUAL_TOTALS_NOT_ITEM_IDENTITY",
            "ITEM_CORRESPONDENCE_NOT_ESTABLISHED",
            "SEMANTIC_COMPARABILITY_UNESTABLISHED",
            "SELECTED_FILES_NOT_ATOMIC_PAIR",
            "NO_MEASUREMENT_OR_ACCEPTANCE",
            "RAW_HASH_AND_COUNTS_LINKABLE",
        ],
    }
    Draft202012Validator(schema).validate(exemplar)  # pyright: ignore[reportUnknownMemberType]
    changed = deepcopy(exemplar)
    if field == "item_correspondence":
        changed[field]["state"] = "established"
    elif field == "semantic_comparability":
        changed[field] = "established"
    else:
        changed[field].remove("MARGINAL_DELTAS_NOT_ITEM_TRANSITIONS")
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(changed)  # pyright: ignore[reportUnknownMemberType]

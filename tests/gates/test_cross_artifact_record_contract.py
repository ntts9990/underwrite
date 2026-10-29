"""The mapped four-source capture contract is closed, generated, and packaged."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[2]
NAME = "cross_artifact_record.v1.schema.json"
DIGEST = "sha256:" + "a" * 64
MEMBERS = (
    "goppi-assessment.json",
    "goppi-result.json",
    "promptfoo.json",
    "correspondence.json",
)


def _schema() -> dict[str, Any]:
    return json.loads((ROOT / "contracts" / NAME).read_bytes())


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _validator() -> Validator:
    return cast("Validator", Draft202012Validator(_schema()))


def _report() -> dict[str, Any]:
    return {
        "schema": "cross_artifact_review.v1",
        "relation": "declared_case_mapping_descriptive",
        "source_identity": {
            "goppi_assessment_sha256": DIGEST,
            "goppi_result_sha256": DIGEST,
            "promptfoo_export_sha256": DIGEST,
        },
        "readers": {
            "goppi": {
                "profile": "goppi.c55-selected-fields.v1",
                "assessment_schema": "goppi.operator_answer_exchange_assessment.v1",
                "result_schema": "evidence_result.v1",
            },
            "promptfoo": {
                "profile": "promptfoo.summary-v3.selected-fields.v1",
                "producer_version": None,
                "producer_version_state": "unknown",
            },
        },
        "populations": {
            "goppi": {
                "unit": "case",
                "planned_count": 1,
                "item_count": 1,
                "availability": {"observed": 1, "missing": 0, "error": 0, "abstained": 0},
            },
            "promptfoo": {
                "unit": "row",
                "supplied_rows": 1,
                "subject_rows": 1,
                "control_rows": 0,
                "planned_count": None,
                "category_counts": {"successes": 1, "failures": 0, "errors": 0},
            },
        },
        "correspondence": {
            "declaration_sha256": DIGEST,
            "coverage": "complete",
            "input_correspondence": "declared_unverified",
            "semantic_equivalence": "unestablished",
            "condition_alignment": "not_established",
            "pairs": [],
            "unmatched_case_indices": [],
            "unmatched_subject_row_indices": [],
        },
        "finding_counts": {"equal": 0, "different": 0, "ineligible": 0},
        "improvement": "unknown",
        "checks": {
            "declaration_source_hashes": "matched",
            "goppi_assessment_result_binding": "checked_selected_fields",
            "goppi_terminal_controls": "not_reperformed",
            "goppi_portable_closure": "not_reperformed",
            "source_authentication": "not_independently_verified",
        },
        "next_verification_tasks": [
            {
                "code": "VERIFY_INPUT_AND_METRIC_SEMANTICS",
                "owner": "evaluation_owner",
                "case_index": None,
                "row_index": None,
            }
        ],
        "limitations": ["SOURCE_CLAIMS_UNVERIFIED", "NO_IMPROVEMENT_OR_APPROVAL"],
    }


def _evidence_error() -> dict[str, Any]:
    return {
        "schema": "evidence_error.v1",
        "code": "INVALID_INPUT",
        "reason": "INVALID_INPUT",
        "location": "/correspondence",
        "next_action": "CORRECT_INPUT",
        "retryable": False,
        "exit_code": 2,
    }


def _record_error() -> dict[str, Any]:
    return {
        "schema": "record_error.v1",
        "code": "RECORD_EXISTS",
        "reason": "RECORD_ALREADY_EXISTS",
        "location": "/record_id",
        "next_action": "USE_NEW_RECORD_ID",
        "retryable": False,
        "exit_code": 2,
    }


def _envelope(*, analysis_error: bool = False, storage_error: bool = False) -> dict[str, Any]:
    return {
        "schema": "cross_artifact_record.v1",
        "command": "records review-artifacts",
        "source": {
            "state": "captured",
            "members": [{"name": name, "raw_hash": DIGEST, "bytes": 1} for name in MEMBERS],
        },
        "analysis": (
            {"state": "error", "error": _evidence_error()}
            if analysis_error
            else {"state": "result", "report": _report()}
        ),
        "storage": (
            {"state": "error", "error": _record_error(), "published_record": None}
            if storage_error
            else {
                "state": "saved",
                "record_id": "sample",
                "manifest_digest": DIGEST,
                "verification": {
                    "schema": "record_verification.v1",
                    "record_id": "sample",
                    "manifest_digest": DIGEST,
                    "valid": True,
                    "files_checked": 6,
                    "links_checked": 0,
                    "findings": [],
                    "verification_scope": "stored_bytes_and_direct_links",
                },
            }
        ),
        "exit_code": 3 if storage_error else 2 if analysis_error else 0,
    }


def test_generated_authored_and_packaged_contracts_match() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/build_cross_artifact_record.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    authored = ROOT / "contracts" / NAME
    packaged = ROOT / "src/underwrite/_contracts" / NAME
    assert packaged.is_symlink()
    assert packaged.readlink() == Path("../../../contracts") / NAME
    assert packaged.read_bytes() == authored.read_bytes()
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert f"contracts/{NAME}" in config["tool"]["uv"]["build-backend"]["source-include"]
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    assert schema["$defs"]["cross_artifact_review"]["properties"]["schema"] == {
        "const": "cross_artifact_review.v1"
    }


@pytest.mark.parametrize(
    "analysis_error,storage_error",
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_analysis_storage_and_exit_matrix(analysis_error: bool, storage_error: bool) -> None:
    envelope = _envelope(analysis_error=analysis_error, storage_error=storage_error)
    _validator().validate(envelope)
    for exit_code in {0, 2, 3} - {envelope["exit_code"]}:
        with pytest.raises(ValidationError):
            _validator().validate({**envelope, "exit_code": exit_code})
    if storage_error:
        envelope["storage"]["published_record"] = {
            "record_id": "sample",
            "manifest_digest": DIGEST,
        }
        _validator().validate(envelope)


@pytest.mark.parametrize("index,maximum", [(0, 4 * 1024 * 1024), (3, 128 * 1024)])
def test_member_byte_bounds(index: int, maximum: int) -> None:
    envelope = _envelope()
    envelope["source"]["members"][index]["bytes"] = maximum
    _validator().validate(envelope)
    envelope["source"]["members"][index]["bytes"] = maximum + 1
    with pytest.raises(ValidationError):
        _validator().validate(envelope)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_member",
        "extra_member",
        "wrong_order",
        "unavailable_source",
        "private_source_path",
    ],
)
def test_refuses_incomplete_or_private_source(mutation: str) -> None:
    envelope = copy.deepcopy(_envelope())
    members = envelope["source"]["members"]
    if mutation == "missing_member":
        members.pop()
    elif mutation == "extra_member":
        members.append(copy.deepcopy(members[-1]))
    elif mutation == "wrong_order":
        members[0], members[1] = members[1], members[0]
    elif mutation == "unavailable_source":
        envelope["source"] = {"state": "unavailable"}
    else:
        members[0]["path"] = "/private/assessment.json"
    with pytest.raises(ValidationError):
        _validator().validate(envelope)


@pytest.mark.parametrize(
    "mutation",
    [
        "unmapped_report",
        "unmatched_hashes",
        "verification_not_valid",
        "verification_extra_file",
        "verification_link",
        "private_report_field",
        "private_storage_field",
    ],
)
def test_refuses_unmapped_or_private_analysis_storage(mutation: str) -> None:
    envelope = copy.deepcopy(_envelope())
    if mutation == "unmapped_report":
        envelope["analysis"]["report"]["relation"] = "incomparable_on_supplied_evidence"
    elif mutation == "unmatched_hashes":
        envelope["analysis"]["report"]["checks"]["declaration_source_hashes"] = "not_supplied"
    elif mutation == "verification_not_valid":
        envelope["storage"]["verification"]["valid"] = False
    elif mutation == "verification_extra_file":
        envelope["storage"]["verification"]["files_checked"] = 7
    elif mutation == "verification_link":
        envelope["storage"]["verification"]["links_checked"] = 1
    elif mutation == "private_report_field":
        envelope["analysis"]["report"]["source_identity"]["promptfoo_export_path"] = "/private"
    else:
        envelope["storage"]["private_path"] = "/private/store"
    with pytest.raises(ValidationError):
        _validator().validate(envelope)

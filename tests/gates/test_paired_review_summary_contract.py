"""The bounded review summary composes the full review's trusted fields."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import ValidationError

from scripts import build_paired_review_summary as builder
from underwrite.cli.review_pair import produce
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).parents[2]
EXAMPLES = ROOT / "fixtures/examples/evidence"


def test_summary_contract_is_generated_and_installed_from_full_owner() -> None:
    authored = builder.TARGET
    packaged = ROOT / "src/underwrite/_contracts" / authored.name
    expected = (json.dumps(builder.build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    assert authored.read_bytes() == expected
    assert packaged.resolve() == authored.resolve()
    assert packaged.read_bytes() == expected
    full = json.loads(builder.SOURCE.read_text())
    summary = json.loads(expected)
    for field in (
        "paired_evidence_ref",
        "context_digest",
        "declarations_verified",
        "actual_conditions_verified",
        "semantic_equivalence_verified",
        "limitations",
    ):
        assert summary["properties"][field] == full["properties"][field]
    assert summary["$defs"]["digest"] == full["$defs"]["digest"]
    packaged_validator("paired_review_summary.v1")


def test_summary_contract_refuses_authority_and_state_drift() -> None:
    report = produce(
        argparse.Namespace(
            input=str(EXAMPLES / "synthetic-paired-complete.json"),
            policy=str(EXAMPLES / "paired-binary-policy.json"),
            context=str(EXAMPLES / "synthetic-review-context.json"),
            view="summary",
        )
    )
    validator = packaged_validator("paired_review_summary.v1")
    validator.validate(report)  # pyright: ignore[reportUnknownMemberType]
    mutations: list[dict[str, Any]] = []
    authority = cast(dict[str, Any], deepcopy(report))
    authority["limitations"].remove("NO_APPROVAL_OR_DEPLOYMENT_AUTHORITY")
    mutations.append(authority)
    verified = cast(dict[str, Any], deepcopy(report))
    verified["actual_conditions_verified"] = True
    mutations.append(verified)
    invalid_history = cast(dict[str, Any], deepcopy(report))
    invalid_history["history"]["state"] = "present"
    mutations.append(invalid_history)
    for code in (
        "NO_CONDITIONS_DECLARED",
        "HISTORY_RESULT_REFERENCES_NOT_VERIFIED",
        "NO_RESCORING_PERFORMED",
        "HISTORY_COMPLETENESS_UNVERIFIED",
        "CASE_PARTITION_DECLARED_NOT_VERIFIED",
    ):
        contradiction = cast(dict[str, Any], deepcopy(report))
        contradiction["limitations"].append(code)
        mutations.append(contradiction)
    for changed in mutations:
        with pytest.raises(ValidationError):
            validator.validate(changed)  # pyright: ignore[reportUnknownMemberType]


def test_summary_contract_requires_conditional_limitations_when_present(tmp_path: Path) -> None:
    context = json.loads((EXAMPLES / "synthetic-review-partition.json").read_text())
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context))
    args = argparse.Namespace(
        input=str(EXAMPLES / "synthetic-paired-complete.json"),
        policy=str(EXAMPLES / "paired-binary-policy.json"),
        context=str(context_path),
        view="summary",
    )
    report = produce(args)
    validator = packaged_validator("paired_review_summary.v1")
    validator.validate(report)  # pyright: ignore[reportUnknownMemberType]
    for code in (
        "HISTORY_RESULT_REFERENCES_NOT_VERIFIED",
        "NO_RESCORING_PERFORMED",
        "HISTORY_COMPLETENESS_UNVERIFIED",
        "CASE_PARTITION_DECLARED_NOT_VERIFIED",
    ):
        contradiction = cast(dict[str, Any], deepcopy(report))
        contradiction["limitations"].remove(code)
        with pytest.raises(ValidationError):
            validator.validate(contradiction)  # pyright: ignore[reportUnknownMemberType]

    context["conditions"] = []
    context.pop("evaluation_history", None)
    context.pop("case_partition", None)
    context_path.write_text(json.dumps(context))
    unknown = produce(args)
    assert cast(dict[str, Any], unknown["declared_conditions"])["coverage"] == "unknown"
    missing = cast(dict[str, Any], deepcopy(unknown))
    missing["limitations"].remove("NO_CONDITIONS_DECLARED")
    with pytest.raises(ValidationError):
        validator.validate(missing)  # pyright: ignore[reportUnknownMemberType]

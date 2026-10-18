"""Closed installed result schema and generated contract agreement."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import ValidationError

from underwrite.cli.local_json import Validator
from underwrite.instrument.evidence.goppi_trajectory import (
    MEMBERS,
    RESULT_SCHEMA,
    analyze_goppi_trajectory,
)
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures/conformance/goppi-trajectory/exchange"


@pytest.fixture
def report() -> dict[str, Any]:
    return analyze_goppi_trajectory(
        json.loads((FIXTURE / "paired/execution.json").read_bytes()),
        tuple(
            json.loads(line) for line in (FIXTURE / "paired/rows.jsonl").read_text().splitlines()
        ),
        json.loads((FIXTURE / "trajectory/trajectory.json").read_bytes()),
        {
            p: (
                "sha256:" + hashlib.sha256((FIXTURE / p).read_bytes()).hexdigest(),
                (FIXTURE / p).stat().st_size,
            )
            for p in MEMBERS
        },
    )


def test_closed_schema_accepts_direct_pure_and_admitted_result(report: dict[str, Any]) -> None:
    validator = cast(Validator, packaged_validator(RESULT_SCHEMA))
    validator.validate(report)
    report["trust_scope"].update(
        native_verification="local_goppi_same_byte_snapshot",
        integrity_scope="self_consistent_content_closure",
        authenticity="content_digest_only",
    )
    validator.validate(report)
    report["trust_scope"]["native_verification"] = "not_performed"
    with pytest.raises(ValidationError):
        validator.validate(report)


@pytest.mark.parametrize(
    "mutation",
    ["extra", "authority", "unavailable_zero", "false_number", "process_extra", "task_command"],
)
def test_schema_refuses_semantic_masquerades(report: dict[str, Any], mutation: str) -> None:
    first = report["comparisons"][0]
    if mutation == "extra":
        report["raw_trace"] = "not allowed"
    elif mutation == "authority":
        report["authority"]["merge_authorized"] = True
    elif mutation == "unavailable_zero":
        first["baseline"].update(
            source_availability="not_retained",
            capture_completeness="incomplete",
            unavailable_reason="capture_policy_unrecorded",
            llm_span_count=0,
            tool_status_counts=[],
        )
    elif mutation == "false_number":
        first["baseline"]["outcome"]["scored_value"] = 0
    elif mutation == "process_extra":
        first["baseline"]["verifier_process"] = {
            "state": "unlinked",
            "receipt_identity_digest": "sha256:" + "a" * 64,
        }
    else:
        report["next_verification_tasks"][0]["command"] = "execute"
    with pytest.raises(ValidationError):
        cast(Validator, packaged_validator(RESULT_SCHEMA)).validate(report)


def test_schema_is_generated_from_policy_vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "path", [str(ROOT / "scripts"), *sys.path])
    name = "build_goppi_trajectory_contract"
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    assert module.build() == json.loads(
        (ROOT / "contracts" / f"{RESULT_SCHEMA}.schema.json").read_bytes()
    )


def test_context_missing_and_unavailable_outcome_variants(report: dict[str, Any]) -> None:
    validator = cast(Validator, packaged_validator(RESULT_SCHEMA))
    for outcome in (
        {
            "status": "endpoint_failure",
            "reason": "agent_timeout",
            "scored_value": False,
            "verifier_value": None,
        },
        {
            "status": "missing",
            "reason": "not_started",
            "scored_value": None,
            "verifier_value": None,
        },
        {"status": "observed", "value_kind": "scalar", "scored_value": 0.5, "verifier_value": 0.5},
    ):
        variant = copy.deepcopy(report)
        variant["comparisons"][0]["baseline"]["outcome"] = outcome
        validator.validate(variant)

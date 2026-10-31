"""Generated retained trajectory envelopes compose installed contract owners."""

from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import ValidationError

from underwrite.cli.local_json import Validator
from underwrite.cli.record_trajectory import LAYOUT, LAYOUT_V2, READ_SCHEMA_V2, SCHEMA_V2
from underwrite.instrument.ingest.goppi_trajectory import HISTORY_SCHEMA, HISTORY_SCHEMA_V2
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).parents[2]


def test_generated_trajectory_record_contracts_match_authored_owners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "path", [str(ROOT / "scripts"), *sys.path])
    name = "build_trajectory_record_contracts"
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    expected = {
        HISTORY_SCHEMA: module.build_history(),
        HISTORY_SCHEMA_V2: module.build_history_v2(),
        **module.build_envelopes(ROOT),
        **module.build_envelopes_v2(ROOT),
    }
    for schema_name, schema in expected.items():
        authored = ROOT / "contracts" / (schema_name + ".schema.json")
        packaged = ROOT / "src/underwrite/_contracts" / authored.name
        assert json.loads(authored.read_bytes()) == schema
        assert packaged.resolve() == authored.resolve()
        assert packaged.read_bytes() == authored.read_bytes()
        cast(Validator, packaged_validator(schema_name))
        # Nested large results dispatch by a discriminator, not exhaustive oneOf validation.
        definitions = cast(dict[str, Any], schema["$defs"])
        if "analysis" in definitions:
            assert "oneOf" not in definitions["analysis"]
            assert "anyOf" not in definitions["analysis"]
            assert definitions["analysis"]["properties"]["state"]["enum"] == ["result", "error"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "refused"),
        ("source_authentication_independently_verified", 0),
        ("authenticity", "independent_provenance"),
    ],
)
def test_history_contract_refuses_unadopted_states_and_authenticity(
    field: str, value: object
) -> None:
    from underwrite.instrument.ingest.goppi_trajectory import capture_goppi_trajectory

    captured = capture_goppi_trajectory(ROOT / "fixtures/conformance/goppi-trajectory/exchange")
    history: dict[str, Any] = {
        "schema": HISTORY_SCHEMA,
        "state": "not_performed",
        "source_members": [
            {"name": name, "raw_hash": item.sha256, "bytes": item.size}
            for name, item in captured.items()
        ],
        "integrity_scope": None,
        "authenticity": None,
        "source_authentication_independently_verified": False,
        "producer_projection_independently_verified": False,
    }
    validator = cast(Validator, packaged_validator(HISTORY_SCHEMA))
    validator.validate(history)
    history[field] = value
    with pytest.raises(ValidationError):
        validator.validate(history)


def _source_members() -> list[dict[str, object]]:
    from underwrite.instrument.ingest.goppi_trajectory import capture_goppi_trajectory

    captured = capture_goppi_trajectory(ROOT / "fixtures/conformance/goppi-trajectory/exchange")
    return [
        {"name": name, "raw_hash": item.sha256, "bytes": item.size}
        for name, item in captured.items()
    ]


def _history_v2(state: str = "not_performed") -> dict[str, object]:
    common: dict[str, object] = {
        "schema": HISTORY_SCHEMA_V2,
        "verifier_report_state": state,
        "verifier_identity_verified": False,
        "source_members": _source_members(),
        "source_authentication_independently_verified": False,
        "producer_projection_independently_verified": False,
    }
    if state == "not_performed":
        return {
            **common,
            "verifier_source": None,
            "response_snapshot_binding": "not_checked",
            "snapshot_postcheck": "not_checked",
            "content_closure": "not_checked",
            "authenticity": "not_checked",
        }
    return {
        **common,
        "verifier_source": "operator_configured_executable",
        "response_snapshot_binding": "selected_fields_matched",
        "snapshot_postcheck": "unchanged",
        "content_closure": "verifier_reported_valid",
        "authenticity": "verifier_reported_content_digest_only",
    }


def _history_v1(state: str = "not_performed") -> dict[str, object]:
    return {
        "schema": HISTORY_SCHEMA,
        "state": state,
        "source_members": _source_members(),
        "integrity_scope": None if state == "not_performed" else "self_consistent_content_closure",
        "authenticity": None if state == "not_performed" else "content_digest_only",
        "source_authentication_independently_verified": False,
        "producer_projection_independently_verified": False,
    }


def _error_analysis(field: str = "error") -> dict[str, object]:
    return {
        "state": "error",
        field: {
            "schema": "evidence_error.v1",
            "code": "INVALID_INPUT",
            "reason": "TRAJECTORY_CAPTURE_INVALID",
            "location": "/exchange",
            "next_action": "CORRECT_INPUT",
            "retryable": False,
            "exit_code": 2,
        },
    }


def _verification() -> dict[str, object]:
    digest = "sha256:" + "0" * 64
    return {
        "schema": "record_verification.v1",
        "record_id": "trajectory.test",
        "manifest_digest": digest,
        "valid": True,
        "files_checked": 10,
        "links_checked": 0,
        "findings": [],
        "verification_scope": "stored_bytes_and_direct_links",
    }


@pytest.mark.parametrize("state", ["not_performed", "report_accepted"])
def test_history_v2_accepts_only_the_two_closed_trust_combinations(state: str) -> None:
    validator = cast(Validator, packaged_validator(HISTORY_SCHEMA_V2))
    history = _history_v2(state)
    validator.validate(history)

    for field, value in (
        ("state", state),
        ("verifier_identity_verified", True),
        ("source_authentication_independently_verified", True),
        ("producer_projection_independently_verified", True),
    ):
        invalid = deepcopy(history)
        invalid[field] = value
        with pytest.raises(ValidationError):
            validator.validate(invalid)

    crossed = deepcopy(history)
    crossed["response_snapshot_binding"] = (
        "selected_fields_matched" if state == "not_performed" else "not_checked"
    )
    with pytest.raises(ValidationError):
        validator.validate(crossed)


def _read_envelope(layout: str, history_state: str) -> dict[str, object]:
    verification = _verification()
    common: dict[str, object] = {
        "schema": READ_SCHEMA_V2,
        "command": "records read-trajectory",
        "record_id": verification["record_id"],
        "manifest_digest": verification["manifest_digest"],
        "record_layout": layout,
        "source": {"state": "captured", "source_members": _source_members()},
        "current_storage_verification": verification,
        "native_verification_performed_now": False,
    }
    if layout == LAYOUT_V2:
        return {
            **common,
            "analysis": _error_analysis(),
            "verifier_report_history": _history_v2(history_state),
            "verification_interpretation": {
                "state": "current_not_performed"
                if history_state == "not_performed"
                else "current_report_only",
                "verifier_identity_verified": False,
                "current_native_verification": "not_performed",
                "limitations": [],
            },
        }
    return {
        **common,
        "historical_analysis": _error_analysis(),
        "historical_native_verification_history": _history_v1(history_state),
        "verification_interpretation": {
            "state": "legacy_not_performed"
            if history_state == "not_performed"
            else "legacy_unattributed_report",
            "verifier_identity_verified": False,
            "current_native_verification": "not_performed",
            "limitations": ["verifier_identity_not_recorded"],
        },
    }


@pytest.mark.parametrize(
    "layout,history_state",
    [
        (LAYOUT_V2, "not_performed"),
        (LAYOUT_V2, "report_accepted"),
        (LAYOUT, "not_performed"),
        (LAYOUT, "verified"),
    ],
)
def test_read_v2_is_an_exact_layout_and_history_tagged_union(
    layout: str, history_state: str
) -> None:
    validator = cast(Validator, packaged_validator(READ_SCHEMA_V2))
    envelope = _read_envelope(layout, history_state)
    validator.validate(envelope)

    wrong_interpretation = deepcopy(envelope)
    wrong_interpretation["verification_interpretation"]["state"] = "current_report_only"  # type: ignore[index]
    if envelope["verification_interpretation"]["state"] == "current_report_only":  # type: ignore[index]
        wrong_interpretation["verification_interpretation"]["state"] = "current_not_performed"  # type: ignore[index]
    with pytest.raises(ValidationError):
        validator.validate(wrong_interpretation)

    mixed = deepcopy(envelope)
    mixed["analysis" if layout == LAYOUT else "historical_analysis"] = _error_analysis()
    with pytest.raises(ValidationError):
        validator.validate(mixed)


def test_record_v2_uses_report_history_and_preserves_exit_semantics() -> None:
    validator = cast(Validator, packaged_validator(SCHEMA_V2))
    verification = _verification()
    envelope: dict[str, object] = {
        "schema": SCHEMA_V2,
        "command": "records audit-trajectory",
        "source": {"state": "captured", "source_members": _source_members()},
        "analysis": _error_analysis(),
        "verifier_report_history": _history_v2(),
        "storage": {
            "state": "saved",
            "record_id": verification["record_id"],
            "manifest_digest": verification["manifest_digest"],
            "verification": verification,
        },
        "exit_code": 2,
    }
    validator.validate(envelope)

    legacy_name = deepcopy(envelope)
    legacy_name["native_verification_history"] = legacy_name.pop("verifier_report_history")
    with pytest.raises(ValidationError):
        validator.validate(legacy_name)

    wrong_exit = deepcopy(envelope)
    wrong_exit["exit_code"] = 0
    with pytest.raises(ValidationError):
        validator.validate(wrong_exit)

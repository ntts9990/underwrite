"""Replay envelopes bind comparable outcomes, selected identities, and comparison exits."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from jsonschema import Draft202012Validator, ValidationError

from underwrite.cli.measure import OBSERVATION_MAX_BYTES, POLICY_MAX_BYTES
from underwrite.cli.replay_measure import (
    COMPARISON_SCOPE,
    ELIGIBLE_DIAGNOSTIC_CODES,
    EXECUTION_MODE,
    LIMITATIONS,
    VERIFICATION_SCOPE,
)
from underwrite.service.records import RECORD_READ_MAX_BYTES

ROOT = Path(__file__).resolve().parents[2]
NAME = "measurement_replay.v1.schema.json"
DIGEST = "sha256:" + "0" * 64


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _schema() -> dict[str, Any]:
    return json.loads((ROOT / "contracts" / NAME).read_bytes())


def _validator() -> Validator:
    return cast("Validator", Draft202012Validator(_schema()))


def _outcome(state: str) -> dict[str, Any]:
    if state == "result":
        return {
            "state": state,
            "report": json.loads(
                (ROOT / "fixtures/examples/event-measurement/read.json").read_bytes()
            ),
        }
    return {
        "state": "error",
        "error": {
            "schema": "measurement_error.v1",
            "code": "INVALID_OBSERVATION",
            "reason": "INVALID_JSON",
            "location": "/observation",
            "next_action": "CORRECT_OBSERVATION",
            "retryable": False,
            "exit_code": 2,
        },
    }


def _envelope(stored: str = "result", current: str = "result") -> dict[str, Any]:
    match = stored == current
    return {
        "schema": "measurement_replay.v1",
        "command": "records replay-measure",
        "record_id": "sample",
        "manifest_digest": DIGEST,
        "verification_scope": VERIFICATION_SCOPE,
        "sources": {
            role: {"name": f"source/{role}.bin", "sha256": DIGEST, "bytes": 1}
            for role in ("observation", "policy")
        },
        "stored": {
            "name": "analysis/read.json" if stored == "result" else "analysis/error.json",
            "sha256": DIGEST,
            "bytes": 1,
            "canonical_digest": DIGEST,
            "outcome": _outcome(stored),
        },
        "recomputed": {"canonical_digest": DIGEST, "outcome": _outcome(current)},
        "comparison": {"state": "match" if match else "mismatch", "scope": COMPARISON_SCOPE},
        "execution_mode": EXECUTION_MODE,
        "limitations": list(LIMITATIONS),
        "exit_code": 0 if match else 1,
    }


def test_generated_replay_and_packaged_asset_are_current() -> None:
    subprocess.run(
        [sys.executable, "scripts/build_measurement_replay.py", "--check"], cwd=ROOT, check=True
    )
    packaged = ROOT / "src/underwrite/_contracts" / NAME
    assert packaged.is_symlink()
    assert packaged.readlink() == Path("../../../contracts") / NAME
    assert packaged.read_bytes() == (ROOT / "contracts" / NAME).read_bytes()
    Draft202012Validator.check_schema(_schema())


@pytest.mark.parametrize("stored", ["result", "error"])
@pytest.mark.parametrize("current", ["result", "error"])
def test_outcome_kind_and_exit_states(stored: str, current: str) -> None:
    validator = _validator()
    envelope = _envelope(stored, current)
    validator.validate(envelope)
    for code in {0, 1, 2} - {envelope["exit_code"]}:
        with pytest.raises(ValidationError):
            validator.validate({**envelope, "exit_code": code})
    if stored != current:
        envelope["comparison"]["state"] = "match"
        envelope["exit_code"] = 0
        with pytest.raises(ValidationError):
            validator.validate(envelope)


@pytest.mark.parametrize("side", ["stored", "recomputed"])
def test_deterministic_diagnostic_codes_only(side: str) -> None:
    validator = _validator()
    envelope = _envelope("error", "error")
    codes = json.loads((ROOT / "contracts/measurement_error.v1.schema.json").read_bytes())[
        "properties"
    ]["code"]["enum"]
    for code in codes:
        envelope[side]["outcome"]["error"]["code"] = code
        if code in ELIGIBLE_DIAGNOSTIC_CODES:
            validator.validate(envelope)
        else:
            with pytest.raises(ValidationError):
                validator.validate(envelope)


@pytest.mark.parametrize("side", ["stored", "recomputed"])
@pytest.mark.parametrize("field,value", [("schema", "read.v1"), ("profile", "unknown.v1")])
def test_unsupported_read_schema_and_profile_are_rejected(
    side: str, field: str, value: str
) -> None:
    envelope = _envelope()
    envelope[side]["outcome"]["report"][field] = value
    with pytest.raises(ValidationError):
        _validator().validate(envelope)


@pytest.mark.parametrize("state", ["result", "error"])
def test_stored_name_must_select_actual_outcome_kind(state: str) -> None:
    envelope = _envelope(state, state)
    envelope["stored"]["name"] = (
        "analysis/error.json" if state == "result" else "analysis/read.json"
    )
    with pytest.raises(ValidationError):
        _validator().validate(envelope)


@pytest.mark.parametrize(
    "role,maximum", [("observation", OBSERVATION_MAX_BYTES), ("policy", POLICY_MAX_BYTES)]
)
def test_fixed_source_names_and_owned_limits(role: str, maximum: int) -> None:
    validator = _validator()
    envelope = _envelope()
    envelope["sources"][role]["bytes"] = maximum
    validator.validate(envelope)
    for size in (-1, maximum + 1, True):
        invalid = copy.deepcopy(envelope)
        invalid["sources"][role]["bytes"] = size
        with pytest.raises(ValidationError):
            validator.validate(invalid)
    envelope["sources"][role]["name"] = "invocation.json"
    with pytest.raises(ValidationError):
        validator.validate(envelope)


def test_stored_byte_limit_and_closed_claims() -> None:
    validator = _validator()
    envelope = _envelope()
    envelope["stored"]["bytes"] = RECORD_READ_MAX_BYTES
    validator.validate(envelope)
    envelope["stored"]["bytes"] += 1
    with pytest.raises(ValidationError):
        validator.validate(envelope)
    invalid_claims: tuple[tuple[str, object], ...] = (
        ("verification_scope", "whole_record"),
        ("execution_mode", "historical_environment"),
        ("limitations", []),
        ("rerun_verified", True),
    )
    for field, value in invalid_claims:
        with pytest.raises(ValidationError):
            validator.validate({**_envelope(), field: value})

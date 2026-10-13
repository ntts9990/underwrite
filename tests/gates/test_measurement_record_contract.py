"""Native capture composition preserves owner versions and closed outcome invariants."""

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
from underwrite.cli.records import record_error_payload
from underwrite.service.records import RecordError

ROOT = Path(__file__).resolve().parents[2]
NAME = "measurement_record.v1.schema.json"
DIGEST = "sha256:" + "0" * 64


def _schema() -> dict[str, Any]:
    return json.loads((ROOT / "contracts" / NAME).read_bytes())


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _validator() -> Validator:
    return cast("Validator", Draft202012Validator(_schema()))


def _error(location: str) -> dict[str, Any]:
    return {
        "schema": "measurement_error.v1",
        "code": "SOURCE_READ_FAILED",
        "reason": "SOURCE_READ_FAILED",
        "location": location,
        "next_action": "PROVIDE_REGULAR_LOCAL_FILE",
        "retryable": False,
        "exit_code": 2,
    }


def _envelope(observation: bool, policy: bool, phase: str | None) -> dict[str, Any]:
    sources = {
        role: (
            {"state": "captured", "raw_hash": DIGEST, "bytes": 1}
            if captured
            else {"state": "unavailable", "error": _error("/" + role)}
        )
        for role, captured in (("observation", observation), ("policy", policy))
    }
    analysis = (
        {
            "state": "result",
            "report": json.loads(
                (ROOT / "fixtures/examples/event-measurement/read.json").read_bytes()
            ),
        }
        if phase is None
        else {
            "state": "error",
            "phase": phase,
            "error": _error(
                "/policy" if observation and phase == "acquisition" else "/observation"
            ),
        }
    )
    return {
        "schema": "measurement_record.v1",
        "command": "records measure",
        "sources": sources,
        "analysis": analysis,
        "storage": {
            "state": "saved",
            "record_id": "sample",
            "manifest_digest": DIGEST,
            "verification": {
                "schema": "record_verification.v1",
                "record_id": "sample",
                "manifest_digest": DIGEST,
                "valid": True,
                "files_checked": 2 + observation + policy,
                "links_checked": 0,
                "findings": [],
                "verification_scope": "stored_bytes_and_direct_links",
            },
        },
        "exit_code": 0 if phase is None else 2,
    }


def test_generated_contract_and_packaged_asset_are_current() -> None:
    subprocess.run(
        [sys.executable, "scripts/build_measurement_record.py", "--check"], cwd=ROOT, check=True
    )
    packaged = ROOT / "src/underwrite/_contracts" / NAME
    assert packaged.is_symlink()
    assert packaged.readlink() == Path("../../../contracts") / NAME
    assert packaged.read_bytes() == (ROOT / "contracts" / NAME).read_bytes()
    Draft202012Validator.check_schema(_schema())
    assert _schema()["$defs"]["read"]["properties"]["schema"] == {"const": "read.v2"}


@pytest.mark.parametrize(
    ("observation", "policy", "phase"),
    [
        (True, True, None),
        (True, True, "analysis"),
        (True, False, "analysis"),
        (True, False, "acquisition"),
        (False, True, "acquisition"),
        (False, False, "acquisition"),
    ],
)
def test_source_phase_count_and_exit_matrix(
    observation: bool, policy: bool, phase: str | None
) -> None:
    validator = _validator()
    envelope = _envelope(observation, policy, phase)
    validator.validate(envelope)
    for count in set(range(2, 5)) - {2 + observation + policy}:
        invalid = copy.deepcopy(envelope)
        invalid["storage"]["verification"]["files_checked"] = count
        with pytest.raises(ValidationError):
            validator.validate(invalid)
    for code in {0, 2, 3} - {envelope["exit_code"]}:
        with pytest.raises(ValidationError):
            validator.validate({**envelope, "exit_code": code})
    envelope["storage"] = {
        "state": "error",
        "error": record_error_payload(RecordError("INTERNAL_ERROR")),
        "published_record": None,
    }
    envelope["exit_code"] = 3
    validator.validate(envelope)
    envelope["storage"]["published_record"] = {"record_id": "sample", "manifest_digest": DIGEST}
    validator.validate(envelope)
    for code in (0, 2):
        with pytest.raises(ValidationError):
            validator.validate({**envelope, "exit_code": code})


@pytest.mark.parametrize(
    ("observation", "policy", "phase"),
    [
        (False, True, None),
        (True, False, None),
        (False, False, None),
        (False, True, "analysis"),
        (False, False, "analysis"),
        (True, True, "acquisition"),
    ],
)
def test_contradictory_source_analysis_states_are_rejected(
    observation: bool, policy: bool, phase: str | None
) -> None:
    with pytest.raises(ValidationError):
        _validator().validate(_envelope(observation, policy, phase))


@pytest.mark.parametrize("phase", ["acquisition", "analysis"])
def test_selected_error_location_respects_consumed_source(phase: str) -> None:
    validator = _validator()
    envelope = _envelope(True, False, phase)
    envelope["analysis"]["error"]["location"] = ""
    validator.validate(envelope)
    envelope["analysis"]["error"]["location"] = (
        "/observation" if phase == "acquisition" else "/policy"
    )
    with pytest.raises(ValidationError):
        validator.validate(envelope)


@pytest.mark.parametrize(
    "role,maximum", [("observation", OBSERVATION_MAX_BYTES), ("policy", POLICY_MAX_BYTES)]
)
def test_source_byte_bounds_follow_measure_owner(role: str, maximum: int) -> None:
    validator = _validator()
    envelope = _envelope(True, True, None)
    envelope["sources"][role]["bytes"] = maximum
    validator.validate(envelope)
    envelope["sources"][role]["bytes"] = maximum + 1
    with pytest.raises(ValidationError):
        validator.validate(envelope)

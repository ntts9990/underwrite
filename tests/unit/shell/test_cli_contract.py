"""Standalone behavior and boundary checks."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import SAMPLE_PINS_ROOT
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from underwrite.cli import boundary, commands

REPO_ROOT = Path(__file__).resolve().parents[3]

REAL_PINS_ROOT = SAMPLE_PINS_ROOT
SCHEMA_PATH = REPO_ROOT / "contracts" / "cli_result.v1.schema.json"


def _schema() -> dict[str, Any]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def _validate(payload: dict[str, Any]) -> None:
    """Standalone behavior and boundary checks."""
    Draft202012Validator(_schema()).validate(payload)  # pyright: ignore[reportUnknownMemberType]


def _write_exemptions(path: Path, *, key: str, expiry: str) -> None:
    path.write_text(
        json.dumps(
            {"schema": "exemptions.v1", "exemptions": {key: {"reason": "test", "expiry": expiry}}}
        ),
        encoding="utf-8",
    )


def _absence_args(root: Path, **overrides: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "root": root,
        "glob": "*.txt",
        "min": 1,
        "exemptions": None,
        "exemption_key": None,
        "now": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _drift_args(**overrides: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "pins_root": REAL_PINS_ROOT,
        "siblings_root": None,
        "exemptions": None,
        "now": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# --- absence: all six states it can emit ------------------------------------


def test_absence_pass_payload_validates(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    _exit_code, payload = commands.cmd_absence(_absence_args(tmp_path))
    assert payload["state"] == "PASS"
    _validate(payload)


def test_absence_negative_min_emitted_payload_validates(tmp_path: Path) -> None:
    exit_code, payload = commands.cmd_absence(_absence_args(tmp_path, min=-1))

    assert exit_code == 0
    assert payload["state"] == "PASS"
    assert payload["details"]["min"] == -1
    _validate(payload)


def test_absence_empty_population_payload_validates(tmp_path: Path) -> None:
    _exit_code, payload = commands.cmd_absence(_absence_args(tmp_path))
    assert payload["state"] == "EMPTY_POPULATION"
    _validate(payload)


def test_absence_skipped_with_reason_payload_validates(tmp_path: Path) -> None:
    exemptions_path = tmp_path / "exemptions.json"
    _write_exemptions(exemptions_path, key="k", expiry="2099-01-01")
    args = _absence_args(tmp_path, exemptions=exemptions_path, exemption_key="k", now="2026-09-04")
    _exit_code, payload = commands.cmd_absence(args)
    assert payload["state"] == "SKIPPED_WITH_REASON"
    _validate(payload)


def test_absence_exemption_expired_payload_validates(tmp_path: Path) -> None:
    exemptions_path = tmp_path / "exemptions.json"
    _write_exemptions(exemptions_path, key="k", expiry="2020-01-01")
    args = _absence_args(tmp_path, exemptions=exemptions_path, exemption_key="k", now="2026-09-04")
    _exit_code, payload = commands.cmd_absence(args)
    assert payload["state"] == "EXEMPTION_EXPIRED"
    _validate(payload)


def test_absence_now_required_payload_validates(tmp_path: Path) -> None:
    exemptions_path = tmp_path / "exemptions.json"
    _write_exemptions(exemptions_path, key="k", expiry="2099-01-01")
    args = _absence_args(tmp_path, exemptions=exemptions_path, exemption_key="k")
    _exit_code, payload = commands.cmd_absence(args)
    assert payload["state"] == "NOW_REQUIRED"
    _validate(payload)


def test_absence_unconfigured_payload_validates(tmp_path: Path) -> None:
    args = _absence_args(tmp_path / "does-not-exist")
    _exit_code, payload = commands.cmd_absence(args)
    assert payload["state"] == "UNCONFIGURED"
    _validate(payload)


# --- drift check: DRIFT (the one state absence can't produce) + its own PASS shape


def test_drift_check_pass_payload_validates(
    pins_tree: Path, hermetic_siblings: Callable[..., Path]
) -> None:
    """Also exercises the extra fields (repos/retired) drift check's PASS payload
    carries beyond absence's -- the schema's `details` is unconstrained, but this
    proves a real, richer payload still satisfies the rest of the envelope."""
    siblings_root = hermetic_siblings()
    args = _drift_args(pins_root=pins_tree, siblings_root=siblings_root)

    _exit_code, payload = commands.cmd_drift_check(args)

    assert payload["state"] == "PASS"
    assert "repos" in payload["details"]
    _validate(payload)


def test_drift_check_drift_payload_validates(
    pins_tree: Path, hermetic_siblings: Callable[..., Path]
) -> None:
    siblings_root = hermetic_siblings("sample-metrics")
    target = next(p for p in sorted((siblings_root / "sample-metrics").rglob("*")) if p.is_file())
    mutated = bytearray(target.read_bytes())
    mutated[0] ^= 0xFF
    target.write_bytes(bytes(mutated))
    args = _drift_args(pins_root=pins_tree, siblings_root=siblings_root)

    _exit_code, payload = commands.cmd_drift_check(args)

    assert payload["state"] == "DRIFT"
    _validate(payload)


def test_drift_check_unconfigured_payload_validates(empty_siblings: Path) -> None:
    args = _drift_args(siblings_root=empty_siblings)

    _exit_code, payload = commands.cmd_drift_check(args)

    assert payload["state"] == "UNCONFIGURED"
    _validate(payload)


# --- pins refresh: its one state, PASS, through the real handler -----------


def test_pins_refresh_pass_payload_validates(
    pins_tree: Path, hermetic_git_sibling: Callable[[str], Path]
) -> None:
    sibling_dir = hermetic_git_sibling("sample-tool")
    args = argparse.Namespace(
        repo="sample-tool", pins_root=pins_tree, siblings_root=sibling_dir.parent
    )

    _exit_code, payload = commands.cmd_pins_refresh(args)

    assert payload["state"] == "PASS"
    assert payload["command"] == "pins refresh"
    _validate(payload)


def _boundary_args(*paths: Path, **overrides: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {"paths": list(paths), "allowlist": None, "now": None}
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_boundary_scan_pass_payload_validates(tmp_path: Path) -> None:
    target = tmp_path / "doc.json"
    target.write_text(json.dumps({"note": "measured evidence only"}), encoding="utf-8")

    _exit_code, payload = boundary.cmd_boundary_scan(_boundary_args(target))

    assert payload["state"] == "PASS"
    _validate(payload)


def test_boundary_scan_leak_payload_validates(tmp_path: Path) -> None:
    target = tmp_path / "doc.json"
    target.write_text(json.dumps({"note": "certify this"}), encoding="utf-8")

    _exit_code, payload = boundary.cmd_boundary_scan(_boundary_args(target))

    assert payload["state"] == "LEAK"
    _validate(payload)


def test_boundary_scan_empty_population_payload_validates(tmp_path: Path) -> None:
    _exit_code, payload = boundary.cmd_boundary_scan(_boundary_args(tmp_path))

    assert payload["state"] == "EMPTY_POPULATION"
    _validate(payload)


def test_boundary_scan_now_required_payload_validates(tmp_path: Path) -> None:
    target = tmp_path / "doc.json"
    target.write_text(json.dumps({"note": "certify this"}), encoding="utf-8")
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "path_glob": str(target),
                        "term": "certify",
                        "reason": "arbitrary operator reason",
                        "expiry": "2099-01-01",
                        "adr": "example-rule",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    _exit_code, payload = boundary.cmd_boundary_scan(_boundary_args(target, allowlist=allowlist))

    assert payload["state"] == "NOW_REQUIRED"
    _validate(payload)


def test_boundary_scan_unconfigured_payload_validates(tmp_path: Path) -> None:
    _exit_code, payload = boundary.cmd_boundary_scan(_boundary_args(tmp_path / "missing.json"))

    assert payload["state"] == "UNCONFIGURED"
    _validate(payload)


@pytest.mark.parametrize(
    ("state", "details"),
    [
        ("NOW_REQUIRED", {"file": "doc.json", "path": "$.note"}),
        ("UNCONFIGURED", {}),
    ],
)
def test_boundary_state_required_detail_fails_validation(
    state: str, details: dict[str, Any]
) -> None:
    offender: dict[str, Any] = {
        "schema": "cli_result.v1",
        "command": "boundary scan",
        "state": state,
        "exit_code": 1,
        "details": details,
    }

    with pytest.raises(ValidationError):
        _validate(offender)


def test_boundary_allowlist_reason_remains_free_text(tmp_path: Path) -> None:
    target = tmp_path / "doc.json"
    target.write_text(json.dumps({"note": "certify this"}), encoding="utf-8")
    _exit_code, payload = boundary.cmd_boundary_scan(_boundary_args(target))
    payload["details"]["leaks"][0]["reason"] = "operator supplied free text"

    _validate(payload)


def test_boundary_scan_old_family_fails_validation(tmp_path: Path) -> None:
    target = tmp_path / "doc.json"
    target.write_text(json.dumps({"note": "certify this"}), encoding="utf-8")
    _exit_code, payload = boundary.cmd_boundary_scan(_boundary_args(target))
    payload["details"]["leaks"][0]["family"] = "gate"

    with pytest.raises(ValidationError):
        _validate(payload)


def test_drift_check_invalid_repo_state_fails_validation(
    pins_tree: Path, hermetic_siblings: Callable[..., Path]
) -> None:
    siblings_root = hermetic_siblings()
    _exit_code, payload = commands.cmd_drift_check(
        _drift_args(pins_root=pins_tree, siblings_root=siblings_root)
    )
    first_repo = next(iter(payload["details"]["repos"].values()))
    first_repo["state"] = "RETIRED"

    with pytest.raises(ValidationError):
        _validate(payload)


def test_command_details_mismatch_fails_validation(tmp_path: Path) -> None:
    target = tmp_path / "doc.json"
    target.write_text(json.dumps({"note": "measured evidence only"}), encoding="utf-8")
    _exit_code, payload = boundary.cmd_boundary_scan(_boundary_args(target))
    payload["command"] = "drift check"

    with pytest.raises(ValidationError):
        _validate(payload)


# --- red-injection: an unregistered state must fail validation -------------


def test_unregistered_state_and_wrong_exit_code_fail_validation() -> None:
    offender: dict[str, Any] = {
        "schema": "cli_result.v1",
        "command": "absence",
        "state": "__NOT_A_REAL_STATE__",
        "exit_code": 0,
        "details": {},
    }
    with pytest.raises(ValidationError):
        _validate(offender)
    offender["state"] = "PASS"
    offender["exit_code"] = 1
    with pytest.raises(ValidationError):
        _validate(offender)


# --- the schema's state enum is exactly underwrite.cli.commands.STATES -----


def test_schema_state_enum_equals_states_constant() -> None:
    schema_states = set(_schema()["properties"]["state"]["enum"])
    assert schema_states == set(commands.STATES)

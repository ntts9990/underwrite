"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_UNCONFIGURED_EXIT = 2


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "underwrite.cli.main", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def _payload(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return json.loads(result.stdout)


def _write_exemptions(tmp_path: Path, *, key: str, reason: str, expiry: str) -> Path:
    path = tmp_path / "exemptions.json"
    path.write_text(
        json.dumps(
            {"schema": "exemptions.v1", "exemptions": {key: {"reason": reason, "expiry": expiry}}}
        ),
        encoding="utf-8",
    )
    return path


# --- the five core outcomes (underwrite_core.absence.check_population) -----


def test_pass_when_population_meets_minimum(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")

    result = _run_cli("absence", "--root", str(tmp_path), "--glob", "*.txt", "--min", "1", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = _payload(result)
    assert payload["state"] == "PASS"
    assert payload["exit_code"] == 0
    assert payload["details"]["count"] == 1


def test_empty_population_without_exemption_fails(tmp_path: Path) -> None:
    result = _run_cli("absence", "--root", str(tmp_path), "--glob", "*.txt", "--min", "1", "--json")

    assert result.returncode == 1
    payload = _payload(result)
    assert payload["state"] == "EMPTY_POPULATION"
    assert payload["exit_code"] == 1


def test_skipped_with_reason_when_exemption_covers_and_unexpired(tmp_path: Path) -> None:
    exemptions = _write_exemptions(tmp_path, key="k", reason="testing gap", expiry="2026-12-31")

    result = _run_cli(
        "absence",
        "--root",
        str(tmp_path),
        "--glob",
        "*.txt",
        "--min",
        "1",
        "--exemptions",
        str(exemptions),
        "--exemption-key",
        "k",
        "--now",
        "2026-09-04",
        "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = _payload(result)
    assert payload["state"] == "SKIPPED_WITH_REASON"
    assert payload["details"]["reason"] == "testing gap"
    assert payload["details"]["expiry"] == "2026-12-31"


def test_exemption_expired_after_expiry(tmp_path: Path) -> None:
    exemptions = _write_exemptions(tmp_path, key="k", reason="testing gap", expiry="2026-01-01")

    result = _run_cli(
        "absence",
        "--root",
        str(tmp_path),
        "--glob",
        "*.txt",
        "--min",
        "1",
        "--exemptions",
        str(exemptions),
        "--exemption-key",
        "k",
        "--now",
        "2026-09-04",
        "--json",
    )

    assert result.returncode == 1
    payload = _payload(result)
    assert payload["state"] == "EXEMPTION_EXPIRED"


def test_now_required_when_exemption_present_but_now_missing(tmp_path: Path) -> None:
    exemptions = _write_exemptions(tmp_path, key="k", reason="testing gap", expiry="2026-12-31")

    result = _run_cli(
        "absence",
        "--root",
        str(tmp_path),
        "--glob",
        "*.txt",
        "--min",
        "1",
        "--exemptions",
        str(exemptions),
        "--exemption-key",
        "k",
        "--json",
    )

    assert result.returncode == 1
    payload = _payload(result)
    assert payload["state"] == "NOW_REQUIRED"


# --- CLI-only shortcut: missing --root -------------------------------------


def test_missing_root_is_unconfigured_exit_2(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    result = _run_cli("absence", "--root", str(missing), "--glob", "*.txt", "--min", "1", "--json")

    assert result.returncode == _UNCONFIGURED_EXIT
    payload = _payload(result)
    assert payload["state"] == "UNCONFIGURED"


# --- output shape ------------------------------------------------------------


def test_json_payload_has_cli_result_v1_shape(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")

    result = _run_cli("absence", "--root", str(tmp_path), "--glob", "*.txt", "--min", "1", "--json")

    payload = _payload(result)
    assert payload["schema"] == "cli_result.v1"
    assert payload["command"] == "absence"
    assert set(payload) == {"schema", "command", "state", "exit_code", "details"}


def test_human_readable_output_is_not_json(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")

    result = _run_cli("absence", "--root", str(tmp_path), "--glob", "*.txt", "--min", "1")

    assert result.returncode == 0
    assert "PASS" in result.stdout
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)

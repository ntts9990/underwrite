"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from conftest import SAMPLE_PINS_ROOT

from underwrite.fleet.pins import load_manifests

REPO_ROOT = Path(__file__).resolve().parents[3]

REAL_PINS_ROOT = SAMPLE_PINS_ROOT
EXEMPTIONS_PATH = REPO_ROOT / "rules" / "exemptions.json"
SCHEMA_PATH = REPO_ROOT / "contracts" / "exemptions.v1.schema.json"
_UNCONFIGURED_EXIT = 2


def _active_manifest_file_count(pins_root: Path) -> int:
    population = sum(
        len(manifest.files) for manifest in load_manifests(pins_root) if not manifest.retired
    )
    assert population >= 1, "EMPTY_POPULATION: no files in active pin manifests"
    return population


def _active_repo_pins(pins_root: Path) -> dict[str, int]:
    """Standalone behavior and boundary checks."""
    return {m.repo: len(m.files) for m in load_manifests(pins_root) if not m.retired}


def _active_repo_names(pins_root: Path) -> list[str]:
    return sorted(_active_repo_pins(pins_root))


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "underwrite.cli.main", "drift", "check", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def _payload(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return json.loads(result.stdout)


# --- Local sample pin bytes: PASS exit 0 ---


def test_hermetic_pinned_bytes_pass_exit_0(
    pins_tree: Path, hermetic_siblings: Callable[..., Path]
) -> None:
    repo_pins = _active_repo_pins(pins_tree)
    siblings_root = hermetic_siblings(*repo_pins)

    result = _run_cli(
        "--pins-root", str(pins_tree), "--siblings-root", str(siblings_root), "--json"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = _payload(result)
    assert payload["state"] == "PASS"
    details = payload["details"]
    assert details["active_population"] == _active_manifest_file_count(pins_tree)
    assert details["drifted"] == []
    for repo, expected_pins in repo_pins.items():
        row = details["repos"][repo]
        assert row["state"] == "PASS"
        assert row["pins"] == expected_pins


# --- every sibling missing, no exemptions: UNCONFIGURED exit 2 -------------


def test_empty_siblings_root_is_unconfigured_exit_2(empty_siblings: Path) -> None:
    result = _run_cli(
        "--pins-root", str(REAL_PINS_ROOT), "--siblings-root", str(empty_siblings), "--json"
    )

    assert result.returncode == _UNCONFIGURED_EXIT
    payload = _payload(result)
    assert payload["state"] == "UNCONFIGURED"
    assert sorted(payload["details"]["unconfigured_repos"]) == _active_repo_names(REAL_PINS_ROOT)


# (형제 부재 -> UNCONFIGURED is the test block above; these three cover the FAIL half:

# the empty-pins exemption path that downgrades it to a reasoned skip.)


def test_zero_active_manifests_is_empty_population_exit_1(tmp_path: Path) -> None:
    no_pins_root = tmp_path / "no-pins"
    no_pins_root.mkdir()

    result = _run_cli("--pins-root", str(no_pins_root), "--siblings-root", str(tmp_path), "--json")

    assert result.returncode == 1, result.stdout + result.stderr
    payload = _payload(result)
    assert payload["state"] == "EMPTY_POPULATION"


def test_retired_only_manifests_is_empty_population_exit_1(pins_tree: Path) -> None:
    expected_retired_population = _active_manifest_file_count(pins_tree)
    active_dir = pins_tree / "active"
    retired_dir = pins_tree / "retired"
    for repo_dir in list(active_dir.iterdir()):
        shutil.move(str(repo_dir), str(retired_dir / repo_dir.name))

    result = _run_cli("--pins-root", str(pins_tree), "--siblings-root", str(pins_tree), "--json")

    assert result.returncode == 1, result.stdout + result.stderr
    payload = _payload(result)
    assert payload["state"] == "EMPTY_POPULATION"
    assert payload["details"]["retired_population"] == expected_retired_population


def test_empty_pins_covered_by_exemption_is_skipped_with_reason_exit_0(tmp_path: Path) -> None:
    no_pins_root = tmp_path / "no-pins"
    no_pins_root.mkdir()
    exemptions_path = tmp_path / "exemptions.json"
    exemptions_path.write_text(
        json.dumps(
            {
                "schema": "exemptions.v1",
                "exemptions": {
                    "empty-pins": {
                        "reason": "test fixture",
                        "expiry": "2099-01-01",
                        "adr": "example-rule",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = _run_cli(
        "--pins-root",
        str(no_pins_root),
        "--siblings-root",
        str(tmp_path),
        "--exemptions",
        str(exemptions_path),
        "--now",
        "2026-09-04",
        "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = _payload(result)
    assert payload["state"] == "SKIPPED_WITH_REASON"


# --- $UNDERWRITE_SIBLINGS_ROOT overrides the --siblings-root default -------


def test_env_override_points_the_default_siblings_root(empty_siblings: Path) -> None:
    """Proves the override is honored by the product surface (main.py's argparse
    default), not just by tests reaching into it directly."""
    result = _run_cli(
        "--pins-root",
        str(REAL_PINS_ROOT),
        "--json",
        env={**os.environ, "UNDERWRITE_SIBLINGS_ROOT": str(empty_siblings)},
    )

    assert result.returncode == _UNCONFIGURED_EXIT, result.stdout + result.stderr
    payload = _payload(result)
    assert payload["state"] == "UNCONFIGURED"
    assert sorted(payload["details"]["unconfigured_repos"]) == _active_repo_names(REAL_PINS_ROOT)


# --- ci-no-siblings exemption: covers before expiry, expires after ---------


# --- one drifted (hermetic) sibling: DRIFT exit 1, file + repo row named (red-injection) ---


def test_one_drifted_sibling_file_is_named_in_drift(
    pins_tree: Path, hermetic_siblings: Callable[..., Path]
) -> None:
    siblings_root = hermetic_siblings("sample-metrics")
    relpath = "contracts/sample.schema.json"
    target = siblings_root / "sample-metrics" / relpath
    mutated = bytearray(target.read_bytes())
    mutated[0] ^= 0xFF
    target.write_bytes(bytes(mutated))

    result = _run_cli(
        "--pins-root", str(pins_tree), "--siblings-root", str(siblings_root), "--json"
    )

    assert result.returncode == 1
    payload = _payload(result)
    assert payload["state"] == "DRIFT"
    assert payload["details"]["drifted"] == [f"sample-metrics/{relpath}"]
    sample_metrics_row = payload["details"]["repos"]["sample-metrics"]
    assert sample_metrics_row["state"] == "DRIFT"
    assert sample_metrics_row["drifted"] == [relpath]


# --- output shape ------------------------------------------------------------


def test_json_payload_has_cli_result_v1_shape() -> None:
    result = _run_cli("--json")

    payload = _payload(result)
    assert payload["schema"] == "cli_result.v1"
    assert payload["command"] == "drift check"
    assert set(payload) == {"schema", "command", "state", "exit_code", "details"}
    assert "repos" in payload["details"]
    assert "retired" in payload["details"]


# --- rules/exemptions.json validates against contracts/exemptions.v1.schema.json

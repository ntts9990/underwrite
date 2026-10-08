"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_STALE_SHA256 = "sha256:" + "0" * 64


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "underwrite.cli.main", "pins", "refresh", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


# --- rewrites a temp manifest (red-injection: stale hashes get corrected) --


def test_pins_refresh_rewrites_temp_manifest(
    stale_sample_pins: tuple[Path, Path], hermetic_git_sibling: Callable[[str], Path]
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")

    result = _run_cli(
        "--repo",
        "sample-tool",
        "--pins-root",
        str(pins_root),
        "--siblings-root",
        str(sibling_dir.parent),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert on_disk["source_commit"] != "stale"
    assert on_disk["files"], "refreshed manifest lost its files"
    for entry in on_disk["files"].values():
        assert entry["sha256"] != _STALE_SHA256


def test_pins_refresh_human_output_names_command_and_repo(
    stale_sample_pins: tuple[Path, Path], hermetic_git_sibling: Callable[[str], Path]
) -> None:
    pins_root, _manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")

    result = _run_cli(
        "--repo",
        "sample-tool",
        "--pins-root",
        str(pins_root),
        "--siblings-root",
        str(sibling_dir.parent),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "pins refresh" in result.stdout
    assert "PASS" in result.stdout
    assert "sample-tool" in result.stdout

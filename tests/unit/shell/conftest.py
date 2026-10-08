"""Hermetic sample repositories for local pin and drift behavior."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from underwrite_core.canonical import digest_bytes

_SAMPLE = TemporaryDirectory(prefix="underwrite-pins-")
SAMPLE_PINS_ROOT = Path(_SAMPLE.name) / "pins"
SAMPLE_SOURCE_PATH = "contracts/sample.schema.json"

for name in ("sample-tool", "sample-metrics"):
    directory = SAMPLE_PINS_ROOT / "active" / name
    target = directory / SAMPLE_SOURCE_PATH
    target.parent.mkdir(parents=True)
    payload = json.dumps({"description": "sample" * 2500}).encode()
    target.write_bytes(payload)
    (directory / "PINS.json").write_text(
        json.dumps(
            {
                "schema": "pins.v1",
                "repo": name,
                "source_root": name,
                "source_commit": "local-fixture",
                "captured_at": "2026-01-01T00:00:00Z",
                "files": {
                    SAMPLE_SOURCE_PATH: {
                        "source_path": SAMPLE_SOURCE_PATH,
                        "sha256": digest_bytes(payload),
                    }
                },
            }
        )
    )
(SAMPLE_PINS_ROOT / "retired").mkdir()


def write_sibling_files(siblings_root: Path, repo: str) -> Path:
    destination = siblings_root / repo
    shutil.copytree(SAMPLE_PINS_ROOT / "active" / repo, destination)
    (destination / "PINS.json").unlink()
    return destination


def git_init_commit(repo_dir: Path) -> None:
    git = ["git", "-C", str(repo_dir)]
    subprocess.run([*git, "init", "-q"], check=True, capture_output=True)
    subprocess.run([*git, "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        [
            *git,
            "-c",
            "user.email=fixture@test.invalid",
            "-c",
            "user.name=fixture",
            "commit",
            "-q",
            "-m",
            "Local fixture",
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def pins_tree(tmp_path: Path) -> Path:
    destination = tmp_path / "pins"
    shutil.copytree(SAMPLE_PINS_ROOT, destination)
    return destination


@pytest.fixture
def empty_siblings(tmp_path: Path) -> Path:
    destination = tmp_path / "empty-siblings"
    destination.mkdir()
    return destination


@pytest.fixture
def hermetic_siblings(tmp_path: Path) -> Callable[..., Path]:
    def make(*repos: str) -> Path:
        destination = tmp_path / "siblings"
        for repo in repos or ("sample-tool", "sample-metrics"):
            write_sibling_files(destination, repo)
        return destination

    return make


@pytest.fixture
def hermetic_git_sibling(tmp_path: Path) -> Callable[[str], Path]:
    def make(repo: str) -> Path:
        destination = write_sibling_files(tmp_path / "siblings", repo)
        git_init_commit(destination)
        return destination

    return make


@pytest.fixture
def stale_sample_pins(pins_tree: Path) -> tuple[Path, Path]:
    path = pins_tree / "active/sample-tool/PINS.json"
    data = json.loads(path.read_text())
    data["source_commit"] = "stale"
    for entry in data["files"].values():
        entry["sha256"] = "sha256:" + "0" * 64
    path.write_text(json.dumps(data))
    return pins_tree, path

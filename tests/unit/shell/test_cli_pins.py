"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_STALE_SHA256 = "sha256:" + "0" * 64
REFUSAL_EXIT = 2


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "underwrite.cli.main", "pins", "refresh", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def _refresh(
    pins_root: Path, sibling_dir: Path, repo: str = "sample-tool"
) -> subprocess.CompletedProcess[str]:
    return _run_cli(
        "--repo",
        repo,
        "--pins-root",
        str(pins_root),
        "--siblings-root",
        str(sibling_dir.parent),
    )


def _replace_file_key(manifest_path: Path, key: str) -> None:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected = next(iter(payload["files"]))
    payload["files"][key] = payload["files"].pop(selected)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")


def _registry_bytes(pins_root: Path) -> dict[Path, bytes]:
    return {p.relative_to(pins_root): p.read_bytes() for p in pins_root.rglob("*") if p.is_file()}


def _assert_refused(result: subprocess.CompletedProcess[str], reason: str) -> None:
    assert result.returncode == REFUSAL_EXIT, result.stdout + result.stderr
    assert result.stderr == ""
    assert f"reason       {reason}" in result.stdout
    assert "not_started" in result.stdout


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


def test_refresh_refuses_registry_escape_before_any_write(
    stale_sample_pins: tuple[Path, Path], hermetic_git_sibling: Callable[[str], Path]
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    outside = pins_root.parent / "outside-sentinel.txt"
    outside.write_bytes(b"outside sentinel")
    _replace_file_key(manifest_path, "../../../outside-sentinel.txt")
    before = _registry_bytes(pins_root)

    result = _refresh(pins_root, sibling_dir)

    _assert_refused(result, "INVALID_PIN_PATH")
    assert "outside-sentinel" not in result.stdout
    assert outside.read_bytes() == b"outside sentinel"
    assert _registry_bytes(pins_root) == before


@pytest.mark.parametrize(
    "name",
    [
        "/absolute.txt",
        "PINS.json",
        "pins.JSON",
        "a/../b",
        "a//b",
        "a\\b",
        "c1\u0085-control.json",
        "bidi\u202e-control.json",
        "/".join(["nested"] * 65),
    ],
)
def test_refresh_rejects_invalid_destinations_without_mutation(
    stale_sample_pins: tuple[Path, Path], hermetic_git_sibling: Callable[[str], Path], name: str
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    _replace_file_key(manifest_path, name)
    before = _registry_bytes(pins_root)
    result = _refresh(pins_root, sibling_dir)
    _assert_refused(result, "INVALID_PIN_PATH")
    assert name not in result.stdout
    assert _registry_bytes(pins_root) == before
    assert not list(pins_root.rglob(".pins-refresh-*"))


@pytest.mark.parametrize("repo", ["../sample-tool", "/sample-tool", "sample/tool", "C:escape"])
def test_refresh_rejects_repo_selection_before_registry_access(
    stale_sample_pins: tuple[Path, Path], hermetic_git_sibling: Callable[[str], Path], repo: str
) -> None:
    pins_root, _ = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    before = _registry_bytes(pins_root)
    _assert_refused(_refresh(pins_root, sibling_dir, repo), "INVALID_REPO")
    assert _registry_bytes(pins_root) == before


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "wrong-type", "source-traversal", "source-c1", "source-bidi", "repo-mismatch"],
)
def test_refresh_rejects_ambiguous_manifest_before_mutation(
    stale_sample_pins: tuple[Path, Path], hermetic_git_sibling: Callable[[str], Path], mutation: str
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(iter(payload["files"].values()))
    if mutation == "duplicate":
        raw = manifest_path.read_text(encoding="utf-8")
        manifest_path.write_text(
            raw.replace('"repo": "sample-tool"', '"repo": "sample-tool", "repo": "sample-tool"')
        )
    else:
        if mutation == "wrong-type":
            entry["source_path"] = 42
        elif mutation == "source-traversal":
            entry["source_path"] = "../outside"
        elif mutation == "source-c1":
            entry["source_path"] = "source\u0085-control.json"
        elif mutation == "source-bidi":
            entry["source_path"] = "source\u202e-control.json"
        else:
            payload["repo"] = "other-repo"
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    before = _registry_bytes(pins_root)
    reason = "INVALID_PIN_PATH" if mutation.startswith("source-") else "INVALID_MANIFEST"
    _assert_refused(_refresh(pins_root, sibling_dir), reason)
    assert _registry_bytes(pins_root) == before


@pytest.mark.parametrize(
    "key", ["Contracts/sample.schema.json", "contracts", "contracts/sample.schema.json/child"]
)
def test_refresh_rejects_casefold_or_ancestor_collisions(
    stale_sample_pins: tuple[Path, Path], hermetic_git_sibling: Callable[[str], Path], key: str
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["files"][key] = next(iter(payload["files"].values()))
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    before = _registry_bytes(pins_root)
    _assert_refused(_refresh(pins_root, sibling_dir), "INVALID_PIN_PATH")
    assert _registry_bytes(pins_root) == before


def test_refresh_rejects_unicode_normalization_alias(
    stale_sample_pins: tuple[Path, Path], hermetic_git_sibling: Callable[[str], Path]
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(iter(payload["files"].values()))
    payload["files"]["café.json"] = entry
    payload["files"]["cafe\u0301.json"] = entry
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    before = _registry_bytes(pins_root)
    _assert_refused(_refresh(pins_root, sibling_dir), "INVALID_PIN_PATH")
    assert _registry_bytes(pins_root) == before


def test_refresh_accepts_noncontrol_unicode_destination(
    stale_sample_pins: tuple[Path, Path], hermetic_git_sibling: Callable[[str], Path]
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(iter(payload["files"].values()))
    payload["files"]["café/结果.json"] = entry
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    result = _refresh(pins_root, sibling_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    created = manifest_path.parent / "café/结果.json"
    source = manifest_path.parent / "contracts/sample.schema.json"
    assert created.read_bytes() == source.read_bytes()


def test_missing_committed_blob_cli_refusal_has_no_mutation(
    stale_sample_pins: tuple[Path, Path], hermetic_git_sibling: Callable[[str], Path]
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["files"]["new/missing.json"] = {
        "source_path": "not-in-commit.json",
        "sha256": _STALE_SHA256,
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    before = _registry_bytes(pins_root)
    _assert_refused(_refresh(pins_root, sibling_dir), "GIT_SOURCE_UNAVAILABLE")
    assert _registry_bytes(pins_root) == before
    assert not (manifest_path.parent / "new").exists()
    assert not list(pins_root.rglob(".pins-refresh-*"))


@pytest.mark.parametrize("target", ["root", "active", "repo", "parent", "leaf", "manifest"])
def test_refresh_refuses_selected_symlink_components(
    stale_sample_pins: tuple[Path, Path],
    hermetic_git_sibling: Callable[[str], Path],
    target: str,
    tmp_path: Path,
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"outside")
    selected_root = pins_root
    if target == "root":
        selected_root = tmp_path / "selected-root"
        selected_root.symlink_to(pins_root, target_is_directory=True)
    elif target == "active":
        active = pins_root / "active"
        active.rename(pins_root / "active-real")
        active.symlink_to(pins_root / "active-real", target_is_directory=True)
    elif target == "repo":
        repo = manifest_path.parent
        repo.rename(repo.with_name("sample-tool-real"))
        repo.symlink_to(repo.with_name("sample-tool-real"), target_is_directory=True)
    elif target == "parent":
        parent = manifest_path.parent / "contracts"
        shutil.rmtree(parent)
        parent.symlink_to(outside, target_is_directory=True)
    elif target == "leaf":
        leaf = manifest_path.parent / "contracts/sample.schema.json"
        leaf.unlink()
        leaf.symlink_to(sentinel)
    else:
        manifest_path.unlink()
        manifest_path.symlink_to(sentinel)
    before = _registry_bytes(pins_root)
    _assert_refused(_refresh(selected_root, sibling_dir), "PIN_TARGET_UNSAFE")
    assert sentinel.read_bytes() == b"outside"
    assert _registry_bytes(pins_root) == before


def test_refresh_hardlinks_replace_only_selected_directory_entries(
    stale_sample_pins: tuple[Path, Path],
    hermetic_git_sibling: Callable[[str], Path],
    tmp_path: Path,
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    pin = manifest_path.parent / "contracts/sample.schema.json"
    outside_pin = tmp_path / "outside-pin"
    outside_manifest = tmp_path / "outside-manifest"
    os.link(pin, outside_pin)
    os.link(manifest_path, outside_manifest)
    before_pin, before_manifest = outside_pin.read_bytes(), outside_manifest.read_bytes()
    assert _refresh(pins_root, sibling_dir).returncode == 0
    assert outside_pin.read_bytes() == before_pin
    assert outside_manifest.read_bytes() == before_manifest
    assert pin.stat().st_ino != outside_pin.stat().st_ino
    assert manifest_path.stat().st_ino != outside_manifest.stat().st_ino


def test_unsafe_target_after_valid_target_does_not_replace_first_pin(
    stale_sample_pins: tuple[Path, Path],
    hermetic_git_sibling: Callable[[str], Path],
    tmp_path: Path,
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    outside = tmp_path / "outside-sentinel"
    outside.write_bytes(b"outside")
    bad_leaf = manifest_path.parent / "unsafe.json"
    bad_leaf.symlink_to(outside)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["files"]["unsafe.json"] = next(iter(payload["files"].values()))
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    before = _registry_bytes(pins_root)

    _assert_refused(_refresh(pins_root, sibling_dir), "PIN_TARGET_UNSAFE")

    assert outside.read_bytes() == b"outside"
    assert _registry_bytes(pins_root) == before
    assert not list(pins_root.rglob(".pins-refresh-*"))


@pytest.mark.parametrize("target", ["pin", "manifest"])
def test_refresh_refuses_special_leaf_without_blocking(
    stale_sample_pins: tuple[Path, Path],
    hermetic_git_sibling: Callable[[str], Path],
    target: str,
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    selected = (
        manifest_path
        if target == "manifest"
        else manifest_path.parent / "contracts/sample.schema.json"
    )
    selected.unlink()
    os.mkfifo(selected)
    before = _registry_bytes(pins_root)
    _assert_refused(_refresh(pins_root, sibling_dir), "PIN_TARGET_UNSAFE")
    assert _registry_bytes(pins_root) == before

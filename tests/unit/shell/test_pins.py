"""Standalone behavior and boundary checks."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import SAMPLE_PINS_ROOT
from underwrite_core.canonical import digest_bytes

from underwrite.fleet.pins import Manifest, PinState, load_manifests, refresh_pins, verify_pins

REPO_ROOT = Path(__file__).resolve().parents[3]

REAL_PINS_ROOT = SAMPLE_PINS_ROOT
PINS_PY = REPO_ROOT / "src" / "underwrite" / "fleet" / "pins.py"
_RED_INJECTION_MIN_BYTES = 10_000


def _active_manifest_file_count(pins_root: Path) -> int:
    return sum(
        len(manifest.files) for manifest in load_manifests(pins_root) if not manifest.retired
    )


def _assert_active_manifest_population(pins_root: Path) -> int:
    population = _active_manifest_file_count(pins_root)
    assert population >= 1, "EMPTY_POPULATION: no files in active pin manifests"
    return population


# --- (a) real pins: pinned bytes match their own recorded sha256 (hermetic) ------


@pytest.mark.parametrize("manifest", load_manifests(REAL_PINS_ROOT), ids=lambda m: m.repo)
def test_real_manifest_sha256_matches_pinned_bytes(manifest: Manifest) -> None:
    for relpath, file in manifest.files.items():
        pinned_bytes = (manifest.manifest_dir / relpath).read_bytes()
        assert digest_bytes(pinned_bytes) == file.sha256, relpath


# --- Local sample repositories verify their pinned bytes ---


def test_verify_pins_hermetic_all_pass(
    pins_tree: Path, hermetic_siblings: Callable[..., Path]
) -> None:
    siblings_root = hermetic_siblings()

    report = verify_pins(pins_tree, siblings_root)

    expected_population = _assert_active_manifest_population(pins_tree)

    assert report.ok
    assert report.active_population == expected_population
    assert all(r.state is PinState.PASS for r in report.results)


# --- (c) one sibling byte changed -> exactly that file is DRIFT -----------------


def test_one_byte_sibling_change_yields_exactly_that_file_drift(
    pins_tree: Path, hermetic_siblings: Callable[..., Path]
) -> None:
    siblings_root = hermetic_siblings()
    relpath = "contracts/sample.schema.json"
    target = siblings_root / "sample-metrics" / relpath
    mutated = bytearray(target.read_bytes())
    mutated[0] ^= 0xFF
    target.write_bytes(bytes(mutated))

    report = verify_pins(pins_tree, siblings_root)

    drifted = {(r.repo, r.relpath) for r in report.failures()}
    assert drifted == {("sample-metrics", relpath)}
    others = [r for r in report.results if (r.repo, r.relpath) not in drifted]
    assert len(others) == _assert_active_manifest_population(pins_tree) - 1
    assert all(r.state is PinState.PASS for r in others)


# --- (d) sibling repo entirely absent -> UNCONFIGURED for that repo only --------


def test_missing_sibling_repo_yields_unconfigured_for_that_repo_only(
    pins_tree: Path, hermetic_siblings: Callable[..., Path]
) -> None:
    present = ("sample-tool", "sample-metrics")
    siblings_root = hermetic_siblings(*present)
    all_repos = {m.repo for m in load_manifests(pins_tree) if not m.retired}
    missing = all_repos - set(present)

    report = verify_pins(pins_tree, siblings_root)

    by_repo: dict[str, set[PinState]] = {}
    for r in report.results:
        by_repo.setdefault(r.repo, set()).add(r.state)
    for repo in missing:
        assert by_repo[repo] == {PinState.UNCONFIGURED}
    assert by_repo["sample-tool"] == {PinState.PASS}
    assert by_repo["sample-metrics"] == {PinState.PASS}
    assert report.active_population == _assert_active_manifest_population(pins_tree)
    assert {r.repo for r in report.unconfigured()} == missing


# --- (e) retired manifest -> RETIRED, excluded from active_population ----------


def test_retired_manifest_yields_retired_and_is_excluded_from_active_population(
    pins_tree: Path, hermetic_siblings: Callable[..., Path]
) -> None:
    siblings_root = hermetic_siblings()
    (pins_tree / "retired").mkdir(parents=True, exist_ok=True)
    shutil.move(
        str(pins_tree / "active" / "sample-metrics"), str(pins_tree / "retired" / "sample-metrics")
    )

    report = verify_pins(pins_tree, siblings_root)

    native_results = [r for r in report.results if r.repo == "sample-metrics"]
    assert native_results
    assert all(r.state is PinState.RETIRED for r in native_results)
    assert report.retired_population == len(native_results)
    assert report.active_population == _assert_active_manifest_population(pins_tree)


def test_active_manifest_population_tracks_added_and_removed_files(pins_tree: Path) -> None:
    manifest_path = pins_tree / "active" / "sample-tool" / "PINS.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    baseline = _assert_active_manifest_population(pins_tree)
    files = payload["files"]
    added_relpath = "synthetic/added.json"
    files[added_relpath] = {"source_path": added_relpath, "sha256": "sha256:synthetic"}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    assert _assert_active_manifest_population(pins_tree) == baseline + 1

    del files[added_relpath]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    assert _assert_active_manifest_population(pins_tree) == baseline


def test_active_manifest_population_rejects_zero_active_files(pins_tree: Path) -> None:
    for manifest_path in (pins_tree / "active").glob("*/PINS.json"):
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["files"] = {}
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AssertionError, match="EMPTY_POPULATION"):
        _assert_active_manifest_population(pins_tree)


# --- (f) pinned copy itself tampered -> DRIFT "pin file tampered" --------------


def test_tampered_pin_copy_is_drift_with_pin_file_tampered_detail(
    pins_tree: Path, hermetic_siblings: Callable[..., Path]
) -> None:
    siblings_root = hermetic_siblings()
    relpath = "contracts/sample.schema.json"
    pinned_path = pins_tree / "active" / "sample-tool" / relpath
    mutated = bytearray(pinned_path.read_bytes())
    mutated[-1] ^= 0xFF
    pinned_path.write_bytes(bytes(mutated))

    report = verify_pins(pins_tree, siblings_root)

    result = next(r for r in report.results if r.repo == "sample-tool" and r.relpath == relpath)
    assert result.state is PinState.DRIFT
    assert result.detail == "pin file tampered"


# --- (g) refresh_pins rewrites sha256 and source_commit -------------------------


def test_refresh_pins_rewrites_sha256_and_source_commit(
    stale_sample_pins: tuple[Path, Path], hermetic_git_sibling: Callable[[str], Path]
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")

    refreshed = refresh_pins(pins_root, sibling_dir.parent, "sample-tool")

    expected_commit = subprocess.run(
        ["git", "-C", str(sibling_dir), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert refreshed.source_commit == expected_commit
    assert refreshed.source_commit != "stale"
    for relpath, file in refreshed.files.items():
        pinned_bytes = (refreshed.manifest_dir / relpath).read_bytes()
        assert file.sha256 == digest_bytes(pinned_bytes)
        assert file.sha256 != "sha256:" + "0" * 64

    on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert on_disk["source_commit"] == refreshed.source_commit


# --- (g') refresh_pins records the sibling repo name, not a machine path --------


def test_refresh_pins_writes_repo_name_as_source_root_not_absolute_path(
    stale_sample_pins: tuple[Path, Path], hermetic_git_sibling: Callable[[str], Path]
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")

    refreshed = refresh_pins(pins_root, sibling_dir.parent, "sample-tool")

    assert refreshed.source_root == "sample-tool"
    on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert on_disk["source_root"] == "sample-tool"


def test_refresh_pins_captures_commit_bytes_not_dirty_worktree(
    stale_sample_pins: tuple[Path, Path], hermetic_git_sibling: Callable[[str], Path]
) -> None:
    pins_root, _ = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    manifest = next(m for m in load_manifests(pins_root) if m.repo == "sample-tool")
    relpath, entry = next(iter(manifest.files.items()))
    source = sibling_dir / entry.source_path
    committed = source.read_bytes()
    source.write_bytes(b'{"dirty":true}\n')

    refreshed = refresh_pins(pins_root, sibling_dir.parent, "sample-tool")

    blob = subprocess.check_output(
        ["git", "-C", str(sibling_dir), "show", f"{refreshed.source_commit}:{entry.source_path}"]
    )
    assert blob == committed == (refreshed.manifest_dir / relpath).read_bytes()
    assert refreshed.files[relpath].sha256 == digest_bytes(blob)
    assert source.read_bytes() == b'{"dirty":true}\n'
    assert not verify_pins(pins_root, sibling_dir.parent).ok


def test_refresh_missing_committed_source_preserves_all_existing_pins(
    stale_sample_pins: tuple[Path, Path], hermetic_git_sibling: Callable[[str], Path]
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["files"]["missing.json"] = {
        "source_path": "missing.json",
        "sha256": "sha256:" + "0" * 64,
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    before = {p.relative_to(pins_root): p.read_bytes() for p in pins_root.rglob("*") if p.is_file()}

    with pytest.raises(subprocess.CalledProcessError):
        refresh_pins(pins_root, sibling_dir.parent, "sample-tool")

    after = {p.relative_to(pins_root): p.read_bytes() for p in pins_root.rglob("*") if p.is_file()}
    assert after == before


# --- (h) red-injection: a single flipped byte in a >=10KB file is caught -------


def test_single_byte_change_in_a_10kb_file_is_detected(
    pins_tree: Path, hermetic_siblings: Callable[..., Path]
) -> None:
    siblings_root = hermetic_siblings("sample-metrics")
    relpath = "contracts/sample.schema.json"
    target = siblings_root / "sample-metrics" / relpath
    original = target.read_bytes()
    assert len(original) >= _RED_INJECTION_MIN_BYTES
    mutated = bytearray(original)
    mutated[len(mutated) // 2] ^= 0x01
    target.write_bytes(bytes(mutated))

    report = verify_pins(pins_tree, siblings_root)

    result = next(r for r in report.results if r.repo == "sample-metrics" and r.relpath == relpath)
    assert result.state is PinState.DRIFT


# --- (i) source_root is a repo name, never a machine-specific absolute path -----


def _assert_hermetic_source_root(source_root: str) -> None:
    """Standalone behavior and boundary checks."""
    assert not source_root.startswith("/"), source_root
    assert "/Users/" not in source_root, source_root


@pytest.mark.parametrize("manifest", load_manifests(REAL_PINS_ROOT), ids=lambda m: m.repo)
def test_real_manifest_source_root_is_repo_name_not_absolute_path(manifest: Manifest) -> None:
    _assert_hermetic_source_root(manifest.source_root)
    assert manifest.source_root == manifest.repo


def test_absolute_source_root_red_injection_fails_the_check(pins_tree: Path) -> None:
    manifest_path = pins_tree / "active" / "sample-tool" / "PINS.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["source_root"] = "/example/workspace/sample-tool"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    manifest = next(m for m in load_manifests(pins_tree) if m.repo == "sample-tool")

    with pytest.raises(AssertionError):
        _assert_hermetic_source_root(manifest.source_root)


# --- attribution + import boundary (AST) -----------------------------------


def test_pins_module_has_attribution_docstring_and_imports_only_stdlib_and_core() -> None:
    tree = ast.parse(PINS_PY.read_text(encoding="utf-8"), filename=str(PINS_PY))
    docstring = ast.get_docstring(tree)
    assert docstring is not None
    assert docstring

    stdlib = set(sys.stdlib_module_names)
    allowed = {*stdlib, "underwrite_core"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0 or node.module is None:
                continue
            assert node.module.split(".")[0] in allowed, node.module

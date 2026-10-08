"""Verify user-configured contract pins against local repository checkouts.

Each PINS.json manifest binds copied bytes and source commit metadata. Verification
hashes both the pin and live file; missing checkouts are unconfigured. Retired
manifests remain visible while excluded from the active population. Only this
shell layer performs filesystem and Git subprocess I/O."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast

from underwrite_core.canonical import digest_bytes


class PinState(Enum):
    """Per-file pin verification outcome (missing configuration)."""

    PASS = "PASS"
    DRIFT = "DRIFT"
    UNCONFIGURED = "UNCONFIGURED"
    RETIRED = "RETIRED"


@dataclass(frozen=True)
class PinResult:
    """The verdict for one pinned file, plus the evidence behind it."""

    repo: str
    relpath: str
    state: PinState
    expected_sha256: str | None
    actual_sha256: str | None
    detail: str


@dataclass(frozen=True)
class PinReport:
    """The full verdict of one `verify_pins` run."""

    results: tuple[PinResult, ...]
    active_population: int
    retired_population: int

    def failures(self) -> tuple[PinResult, ...]:
        """Every DRIFT result."""
        return tuple(r for r in self.results if r.state is PinState.DRIFT)

    def unconfigured(self) -> tuple[PinResult, ...]:
        """Every UNCONFIGURED result."""
        return tuple(r for r in self.results if r.state is PinState.UNCONFIGURED)

    @property
    def ok(self) -> bool:
        """True unless at least one result is DRIFT."""
        return not any(r.state is PinState.DRIFT for r in self.results)


@dataclass(frozen=True)
class ManifestFile:
    """One `PINS.json` `files` entry: the sibling source path and its pinned sha256."""

    source_path: str
    sha256: str


@dataclass(frozen=True)
class Manifest:
    """A parsed `PINS.json` (pins.v1) plus the directory it and its pinned bytes live in."""

    schema: str
    repo: str
    source_root: str
    source_commit: str
    captured_at: str
    files: dict[str, ManifestFile]
    manifest_dir: Path
    retired: bool


def _parse_file_entry(entry: object) -> ManifestFile:
    entry_dict = cast("dict[str, Any]", entry)
    return ManifestFile(
        source_path=str(entry_dict["source_path"]), sha256=str(entry_dict["sha256"])
    )


def _parse_manifest(manifest_path: Path, *, retired: bool) -> Manifest:
    raw = cast("dict[str, Any]", json.loads(manifest_path.read_text(encoding="utf-8")))
    raw_files = cast("dict[str, Any]", raw["files"])
    files = {relpath: _parse_file_entry(entry) for relpath, entry in raw_files.items()}
    return Manifest(
        schema=str(raw["schema"]),
        repo=str(raw["repo"]),
        source_root=str(raw["source_root"]),
        source_commit=str(raw["source_commit"]),
        captured_at=str(raw["captured_at"]),
        files=files,
        manifest_dir=manifest_path.parent,
        retired=retired,
    )


def load_manifests(pins_root: Path) -> list[Manifest]:
    """Every `PINS.json` under `pins_root/active/*` and `pins_root/retired/*`."""
    manifests: list[Manifest] = []
    for retired, subdir_name in ((False, "active"), (True, "retired")):
        subdir = pins_root / subdir_name
        if not subdir.is_dir():
            continue
        for manifest_path in sorted(subdir.glob("*/PINS.json")):
            manifests.append(_parse_manifest(manifest_path, retired=retired))
    return manifests


def _retired_results(manifest: Manifest) -> list[PinResult]:
    return [
        PinResult(manifest.repo, relpath, PinState.RETIRED, file.sha256, None, "retired")
        for relpath, file in manifest.files.items()
    ]


def _unconfigured_results(manifest: Manifest, sibling_dir: Path) -> list[PinResult]:
    detail = f"sibling repo not found: {sibling_dir}"
    return [
        PinResult(manifest.repo, relpath, PinState.UNCONFIGURED, file.sha256, None, detail)
        for relpath, file in manifest.files.items()
    ]


def _file_result(
    manifest: Manifest, relpath: str, file: ManifestFile, sibling_dir: Path
) -> PinResult:
    source_path = sibling_dir / file.source_path
    if not source_path.is_file():
        detail = f"sibling source file missing: {source_path}"
        return PinResult(manifest.repo, relpath, PinState.DRIFT, file.sha256, None, detail)

    actual_sha256 = digest_bytes(source_path.read_bytes())
    pinned_sha256 = digest_bytes((manifest.manifest_dir / relpath).read_bytes())
    reasons: list[str] = []
    if actual_sha256 != file.sha256:
        reasons.append("sibling source diverged from pin")
    if pinned_sha256 != file.sha256:
        reasons.append("pin file tampered")
    if not reasons:
        return PinResult(manifest.repo, relpath, PinState.PASS, file.sha256, actual_sha256, "")
    detail = "; ".join(reasons)
    return PinResult(manifest.repo, relpath, PinState.DRIFT, file.sha256, actual_sha256, detail)


def verify_pins(pins_root: Path, siblings_root: Path) -> PinReport:
    """Verify every pin under `pins_root` against sibling checkouts under `siblings_root`."""
    results: list[PinResult] = []
    active_population = 0
    retired_population = 0
    for manifest in load_manifests(pins_root):
        if manifest.retired:
            retired_population += len(manifest.files)
            results.extend(_retired_results(manifest))
            continue
        active_population += len(manifest.files)
        sibling_dir = siblings_root / manifest.repo
        if not sibling_dir.is_dir():
            results.extend(_unconfigured_results(manifest, sibling_dir))
            continue
        for relpath, file in manifest.files.items():
            results.append(_file_result(manifest, relpath, file, sibling_dir))
    results.sort(key=lambda r: (r.repo, r.relpath))
    return PinReport(tuple(results), active_population, retired_population)


def _git_output(repo_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _dump_manifest(manifest: Manifest) -> None:
    payload = {
        "schema": manifest.schema,
        "repo": manifest.repo,
        "source_root": manifest.source_root,
        "source_commit": manifest.source_commit,
        "captured_at": manifest.captured_at,
        "files": {
            relpath: {"source_path": file.source_path, "sha256": file.sha256}
            for relpath, file in sorted(manifest.files.items())
        },
    }
    manifest_path = manifest.manifest_dir / "PINS.json"
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def refresh_pins(pins_root: Path, siblings_root: Path, repo: str) -> Manifest:
    """Copy blobs from one resolved HEAD commit, never uncommitted working-tree bytes.

    Resolve every source before writing, so a missing committed source leaves the
    previous pins intact. Subsequent drift checks still compare the live checkout.
    """
    manifest = _parse_manifest(pins_root / "active" / repo / "PINS.json", retired=False)
    sibling_dir = siblings_root / repo
    commit = _git_output(sibling_dir, "rev-parse", "HEAD")
    captured_at = _git_output(sibling_dir, "log", "-1", "--format=%cI", commit)
    blobs = {
        relpath: subprocess.check_output(
            ["git", "-C", str(sibling_dir), "cat-file", "blob", f"{commit}:{file.source_path}"],
            stderr=subprocess.PIPE,
        )
        for relpath, file in manifest.files.items()
    }
    files: dict[str, ManifestFile] = {}
    for relpath, file in manifest.files.items():
        data = blobs[relpath]
        pinned_path = manifest.manifest_dir / relpath
        pinned_path.parent.mkdir(parents=True, exist_ok=True)
        pinned_path.write_bytes(data)
        files[relpath] = ManifestFile(source_path=file.source_path, sha256=digest_bytes(data))
    refreshed = Manifest(
        schema=manifest.schema,
        repo=repo,
        source_root=sibling_dir.name,
        source_commit=commit,
        captured_at=captured_at,
        files=files,
        manifest_dir=manifest.manifest_dir,
        retired=False,
    )
    _dump_manifest(refreshed)
    return refreshed

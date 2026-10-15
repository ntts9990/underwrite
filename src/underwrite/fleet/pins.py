"""Verify user-configured contract pins against local repository checkouts.

Each PINS.json manifest binds copied bytes and source commit metadata. Verification
hashes both the pin and live file; missing checkouts are unconfigured. Retired
manifests remain visible while excluded from the active population. Only this
shell layer performs filesystem and Git subprocess I/O."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import cast

from underwrite_core.canonical import digest_bytes

from underwrite._fileio import TransportError, read_descriptor
from underwrite._json import DecodeError, decode_json
from underwrite.fleet._pin_storage import MANIFEST_MAX_BYTES, PinError, PinRegistry

PIN_FILE_MAX_BYTES = 4 * 1024 * 1024
PIN_TOTAL_MAX_BYTES = 32 * 1024 * 1024
MANIFESTS_MAX = 256
DECLARED_FILES_MAX = 4096
VISITED_ENTRIES_MAX = 100_000


class PinReadError(ValueError):
    """Finite read-only registry refusal without refresh publication semantics."""

    def __init__(
        self, reason: str, *, limit_kind: str | None = None, limit: int | None = None
    ) -> None:
        self.reason = reason
        self.limit_kind = limit_kind
        self.limit = limit
        super().__init__(reason)

    def details(self) -> dict[str, object]:
        details: dict[str, object] = {"reason": self.reason, "location": "/pins"}
        if self.limit_kind is not None and self.limit is not None:
            details.update({"limit_kind": self.limit_kind, "limit": self.limit})
        return details


def _read_limit(kind: str, limit: int) -> PinReadError:
    return PinReadError("INPUT_LIMIT_EXCEEDED", limit_kind=kind, limit=limit)


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


def _manifest_from_bytes(raw: bytes, directory: Path, repo: str, retired: bool) -> Manifest:
    """One strict semantic parser for refresh and read-only inspection."""
    try:
        value = decode_json(raw, 64)
    except DecodeError:
        raise PinError("INVALID_MANIFEST") from None
    if not isinstance(value, dict):
        raise PinError("INVALID_MANIFEST")
    document = cast(dict[str, object], value)
    if (
        document.get("schema") != "pins.v1"
        or document.get("repo") != repo
        or not isinstance(document.get("source_root"), str)
        or not isinstance(document.get("source_commit"), str)
        or not isinstance(document.get("captured_at"), str)
        or not isinstance(document.get("files"), dict)
    ):
        raise PinError("INVALID_MANIFEST")
    entries = cast(dict[str, object], document["files"])
    if len(entries) > DECLARED_FILES_MAX:
        raise PinError("DECLARED_FILE_LIMIT_EXCEEDED")
    files: dict[str, ManifestFile] = {}
    for relpath, entry in entries.items():
        if not isinstance(entry, dict):
            raise PinError("INVALID_MANIFEST")
        fields = cast(dict[str, object], entry)
        source_path, sha256 = fields.get("source_path"), fields.get("sha256")
        if not isinstance(source_path, str) or not isinstance(sha256, str):
            raise PinError("INVALID_MANIFEST")
        _path_parts(relpath)
        _path_parts(source_path)
        files[relpath] = ManifestFile(source_path, sha256)
    manifest = Manifest(
        "pins.v1",
        repo,
        cast(str, document["source_root"]),
        cast(str, document["source_commit"]),
        cast(str, document["captured_at"]),
        files,
        directory,
        retired,
    )
    _inventory(manifest)
    return manifest


def _open_read_fd(path: Path, missing_ok: bool) -> int | None:
    nonblocking = getattr(os, "O_NONBLOCK", None)
    if nonblocking is None:
        raise PinReadError("UNSUPPORTED_PLATFORM")
    flags = os.O_RDONLY | nonblocking | getattr(os, "O_NOCTTY", 0)
    try:
        return os.open(path, flags)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise PinReadError("PIN_READ_FAILED") from None
    except OSError:
        raise PinReadError("PIN_READ_FAILED") from None


def _regular_bytes(
    path: Path, per_file_limit: int, remaining: int, *, missing_ok: bool = False
) -> tuple[bytes | None, int]:
    """Read one opened regular target, following links, within both byte budgets."""
    allowance = min(per_file_limit, max(1, remaining))
    fd = _open_read_fd(path, missing_ok)
    if fd is None:
        return None, remaining
    try:
        try:
            data = read_descriptor(fd, allowance)
        except TransportError as exc:
            if str(exc) == "SOURCE_NOT_REGULAR_FILE" and missing_ok:
                return None, remaining
            raise PinReadError("PIN_READ_FAILED") from None
    except OSError:
        raise PinReadError("PIN_READ_FAILED") from None
    finally:
        try:
            os.close(fd)
        except OSError:
            raise PinReadError("PIN_READ_FAILED") from None
    if len(data) > allowance:
        if remaining < per_file_limit:
            raise _read_limit("captured_bytes", PIN_TOTAL_MAX_BYTES)
        raise _read_limit("bytes", per_file_limit)
    if len(data) > remaining:
        raise _read_limit("captured_bytes", PIN_TOTAL_MAX_BYTES)
    return data, remaining - len(data)


def _visited_entry(count: int) -> int:
    count += 1
    if count > VISITED_ENTRIES_MAX:
        raise _read_limit("directory_entries", VISITED_ENTRIES_MAX)
    return count


def _root_branches(pins_root: Path) -> tuple[dict[str, Path], int]:
    visited = 0
    branches: dict[str, Path] = {}
    try:
        entries = os.scandir(pins_root)
    except FileNotFoundError:
        return {}, visited
    except OSError:
        raise PinReadError("PIN_READ_FAILED") from None
    try:
        with entries:
            for entry in entries:
                visited = _visited_entry(visited)
                if entry.name in {"active", "retired"}:
                    if not entry.is_dir(follow_symlinks=True):
                        raise PinReadError("PIN_READ_FAILED")
                    branches[entry.name] = Path(entry.path)
    except OSError:
        raise PinReadError("PIN_READ_FAILED") from None
    return branches, visited


def _repo_marker(repository: str, visited: int) -> tuple[Path | None, int]:
    marker: Path | None = None
    with os.scandir(repository) as members:
        for member in members:
            visited = _visited_entry(visited)
            if member.name.casefold() == "pins.json":
                if marker is not None or member.name != "PINS.json":
                    raise PinReadError("INVALID_PIN_MANIFEST")
                marker = Path(member.path)
    return marker, visited


def _branch_candidates(
    branch: Path, retired: bool, visited: int, admitted: int
) -> tuple[list[tuple[Path, str, bool]], int]:
    found: list[tuple[Path, str, bool]] = []
    try:
        with os.scandir(branch) as repositories:
            for repository in repositories:
                visited = _visited_entry(visited)
                if not repository.is_dir(follow_symlinks=True):
                    continue
                marker, visited = _repo_marker(repository.path, visited)
                if marker is None:
                    continue
                try:
                    _repo_component(repository.name)
                except PinError:
                    raise PinReadError("INVALID_PIN_PATH") from None
                found.append((marker, repository.name, retired))
                if admitted + len(found) > MANIFESTS_MAX:
                    raise _read_limit("manifests", MANIFESTS_MAX)
    except OSError:
        raise PinReadError("PIN_READ_FAILED") from None
    return sorted(found, key=lambda selected: selected[0]), visited


def _candidate_paths(pins_root: Path) -> list[tuple[Path, str, bool]]:
    """Select only active/retired repo manifests while counting ignored entries."""
    branches, visited = _root_branches(pins_root)
    paths: list[tuple[Path, str, bool]] = []
    for branch_name, retired in (("active", False), ("retired", True)):
        branch = branches.get(branch_name)
        if branch is None:
            continue
        found, visited = _branch_candidates(branch, retired, visited, len(paths))
        paths.extend(found)
    return paths


def _capture_manifests(pins_root: Path, remaining: int) -> tuple[list[Manifest], int]:
    manifests: list[Manifest] = []
    declared = 0
    for selected, repo, retired in _candidate_paths(pins_root):
        raw, remaining = _regular_bytes(selected, MANIFEST_MAX_BYTES, remaining)
        assert raw is not None
        try:
            manifest = _manifest_from_bytes(raw, selected.parent, repo, retired)
        except PinError as exc:
            if exc.reason == "DECLARED_FILE_LIMIT_EXCEEDED":
                raise _read_limit("declared_files", DECLARED_FILES_MAX) from None
            reason = (
                "INVALID_PIN_PATH" if exc.reason == "INVALID_PIN_PATH" else "INVALID_PIN_MANIFEST"
            )
            raise PinReadError(reason) from None
        declared += len(manifest.files)
        if declared > DECLARED_FILES_MAX:
            raise _read_limit("declared_files", DECLARED_FILES_MAX)
        manifests.append(manifest)
    return manifests, remaining


def load_manifests(pins_root: Path) -> list[Manifest]:
    """Return strictly admitted active and retired manifests in deterministic order."""
    return _capture_manifests(pins_root, PIN_TOTAL_MAX_BYTES)[0]


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
    manifest: Manifest, relpath: str, file: ManifestFile, sibling_dir: Path, remaining: int
) -> tuple[PinResult, int]:
    source_path = sibling_dir / file.source_path
    source, remaining = _regular_bytes(source_path, PIN_FILE_MAX_BYTES, remaining, missing_ok=True)
    if source is None:
        detail = f"sibling source file missing or nonregular: {source_path}"
        return PinResult(
            manifest.repo, relpath, PinState.DRIFT, file.sha256, None, detail
        ), remaining

    actual_sha256 = digest_bytes(source)
    pinned, remaining = _regular_bytes(
        manifest.manifest_dir / relpath, PIN_FILE_MAX_BYTES, remaining, missing_ok=True
    )
    if pinned is None:
        return (
            PinResult(
                manifest.repo,
                relpath,
                PinState.DRIFT,
                file.sha256,
                actual_sha256,
                "pin file missing or nonregular",
            ),
            remaining,
        )
    pinned_sha256 = digest_bytes(pinned)
    reasons: list[str] = []
    if actual_sha256 != file.sha256:
        reasons.append("sibling source diverged from pin")
    if pinned_sha256 != file.sha256:
        reasons.append("pin file tampered")
    if not reasons:
        return PinResult(
            manifest.repo, relpath, PinState.PASS, file.sha256, actual_sha256, ""
        ), remaining
    detail = "; ".join(reasons)
    return PinResult(
        manifest.repo, relpath, PinState.DRIFT, file.sha256, actual_sha256, detail
    ), remaining


def _checkout_present(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.stat().st_mode)
    except FileNotFoundError:
        return False
    except OSError:
        raise PinReadError("PIN_READ_FAILED") from None


def _inspect_pins(
    pins_root: Path, siblings_root: Path, remaining: int
) -> tuple[list[Manifest], PinReport]:
    """Capture manifests once and verify their selected bytes within one command budget."""
    if type(remaining) is not int or not 0 <= remaining <= PIN_TOTAL_MAX_BYTES:
        raise ValueError("INVALID_PIN_READ_BUDGET")
    manifests, remaining = _capture_manifests(pins_root, remaining)
    results: list[PinResult] = []
    active_population = 0
    retired_population = 0
    for manifest in manifests:
        if manifest.retired:
            retired_population += len(manifest.files)
            results.extend(_retired_results(manifest))
            continue
        active_population += len(manifest.files)
        sibling_dir = siblings_root / manifest.repo
        if not _checkout_present(sibling_dir):
            results.extend(_unconfigured_results(manifest, sibling_dir))
            continue
        for relpath, file in manifest.files.items():
            result, remaining = _file_result(manifest, relpath, file, sibling_dir, remaining)
            results.append(result)
    results.sort(key=lambda r: (r.repo, r.relpath))
    return manifests, PinReport(tuple(results), active_population, retired_population)


def verify_pins(pins_root: Path, siblings_root: Path) -> PinReport:
    """Verify every pin under `pins_root` against sibling checkouts under `siblings_root`."""
    return _inspect_pins(pins_root, siblings_root, PIN_TOTAL_MAX_BYTES)[1]


def _git_output(repo_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _manifest_bytes(manifest: Manifest) -> bytes:
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
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


_REPO_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_MAX_REPO_BYTES = 128
_MAX_PATH_BYTES = 4096
_MAX_COMPONENT_BYTES = 255
_MAX_COMPONENTS = 64


def _repo_component(repo: str) -> None:
    if (
        not _REPO_COMPONENT.fullmatch(repo)
        or len(repo.encode("utf-8")) > _MAX_REPO_BYTES
        or repo in {".", ".."}
    ):
        raise PinError("INVALID_REPO")


def _path_parts(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise PinError("INVALID_PIN_PATH")
    parts = tuple(value.split("/"))
    if (
        any(
            not part
            or part in {".", ".."}
            or ":" in part
            or len(part.encode("utf-8")) > _MAX_COMPONENT_BYTES
            or any(unicodedata.category(c) in {"Cc", "Cf"} for c in part)
            for part in parts
        )
        or len(parts) > _MAX_COMPONENTS
        or len(value.encode("utf-8")) > _MAX_PATH_BYTES
    ):
        raise PinError("INVALID_PIN_PATH")
    return parts


def _inventory(manifest: Manifest) -> tuple[tuple[str, ...], ...]:
    paths = tuple(_path_parts(path) for path in manifest.files)
    folded: set[tuple[str, ...]] = {("pins.json",)}
    for parts in paths:
        normalized = tuple(unicodedata.normalize("NFC", part).casefold() for part in parts)
        if normalized in folded or normalized[0] == "pins.json":
            raise PinError("INVALID_PIN_PATH")
        folded.add(normalized)
    for parts in folded:
        if any(parts[:index] in folded for index in range(1, len(parts))):
            raise PinError("INVALID_PIN_PATH")
    return paths


def _read_blob_stdout(process: subprocess.Popen[bytes], limit: int) -> bytes:
    assert process.stdout is not None
    return process.stdout.read(limit + 1)


def _git_blob(repo_dir: Path, commit: str, source_path: str, limit: int) -> bytes:
    """Capture one committed blob with a bounded pipe and reap on every exit."""
    command = ["git", "-C", str(repo_dir), "cat-file", "blob", f"{commit}:{source_path}"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert process.stdout is not None
    try:
        data = _read_blob_stdout(process, limit)
        if len(data) > limit:
            raise PinError("INPUT_LIMIT_EXCEEDED")
        returncode = process.wait()
        if returncode:
            raise subprocess.CalledProcessError(returncode, command)
        return data
    except BaseException:
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                process.wait()
        raise
    finally:
        process.stdout.close()


def refresh_pins(pins_root: Path, siblings_root: Path, repo: str) -> Manifest:
    """Copy blobs from one resolved HEAD commit, never uncommitted working-tree bytes.

    Resolve every source before writing, so a missing committed source leaves the
    previous pins intact. Subsequent drift checks still compare the live checkout.
    """
    _repo_component(repo)
    manifest_dir = pins_root / "active" / repo
    with PinRegistry(pins_root, repo) as registry:
        manifest_raw = registry.read_manifest()
        manifest = _manifest_from_bytes(manifest_raw, manifest_dir, repo, False)
        paths = _inventory(manifest)
        registry.admit(paths)
        sibling_dir = siblings_root / repo
        try:
            commit = _git_output(sibling_dir, "rev-parse", "HEAD")
            captured_at = _git_output(sibling_dir, "log", "-1", "--format=%cI", commit)
            remaining = PIN_TOTAL_MAX_BYTES - len(manifest_raw)
            blobs: dict[str, bytes] = {}
            for relpath, file in manifest.files.items():
                allowance = min(PIN_FILE_MAX_BYTES, max(1, remaining))
                data = _git_blob(sibling_dir, commit, file.source_path, allowance)
                if len(data) > remaining:
                    raise PinError("INPUT_LIMIT_EXCEEDED")
                remaining -= len(data)
                blobs[relpath] = data
        except OSError:
            raise PinError("GIT_EXECUTION_FAILED") from None
        files = {
            relpath: ManifestFile(file.source_path, digest_bytes(blobs[relpath]))
            for relpath, file in manifest.files.items()
        }
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
        serialized = _manifest_bytes(refreshed)
        if len(serialized) > MANIFEST_MAX_BYTES:
            raise PinError("MANIFEST_LIMIT_EXCEEDED")
        for (relpath, _file), parts in zip(manifest.files.items(), paths, strict=True):
            registry.replace(parts, blobs[relpath])
        registry.replace(("PINS.json",), serialized)
        return refreshed

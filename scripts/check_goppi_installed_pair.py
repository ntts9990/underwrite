#!/usr/bin/env python3
"""Accept installed Goppi and Underwrite artifacts against a pinned trajectory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import zlib
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import IO, Any, Literal, NamedTuple, cast

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/conformance/goppi-trajectory"
FIXTURE_V2 = ROOT / "fixtures/conformance/goppi-execution-v2"
DEADLINE_SECONDS = 180.0
STDOUT_MAX_BYTES = 1024 * 1024
STDERR_MAX_BYTES = 256 * 1024
POPULATIONS = {
    "paired_rows": 144,
    "case_stage_repeat_pairs": 72,
    "primary_repeat_pairs": 36,
    "unique_source_case_ids": 24,
}
ARTIFACT_ROLES = (
    "goppi_tarball",
    "underwrite_wheel",
    "underwrite_core_wheel",
    "node_executable",
)
CONTRACTS = {
    "fixture_pins": "underwrite.goppi_trajectory_fixture_pins.v1",
    "fixture_v2_pins": "underwrite.goppi_execution_v2_fixture_pins.v1",
    "native_verification": "goppi.paired_run_verification_result.v2",
    "underwrite_review": "goppi_trajectory_review.v3",
    "underwrite_review_v2": "goppi_trajectory_review.v4",
}
TRUST_LIMITS = {
    "goppi_native_verification": "separately_performed",
    "goppi_native_integrity_scope": "self_consistent_content_closure",
    "goppi_native_authenticity": "content_digest_only",
    "underwrite_independent_native_verification_claimed": False,
    "underwrite_verifier_report_state": "report_accepted",
    "underwrite_verifier_identity_verified": False,
    "underwrite_content_closure": "verifier_reported_valid",
    "underwrite_authenticity": "verifier_reported_content_digest_only",
    "independent_provenance_established": False,
    "producer_projection_independently_verified": False,
    "source_authentication_independently_verified": False,
}
REVIEW_TRUST = {
    "verifier_report_state": "report_accepted",
    "verifier_source": "operator_configured_executable",
    "verifier_identity_verified": False,
    "response_snapshot_binding": "selected_fields_matched",
    "snapshot_postcheck": "unchanged",
    "content_closure": "verifier_reported_valid",
    "authenticity": "verifier_reported_content_digest_only",
    "outcome_harness_binding": "v2_verified_against_patch_manifest",
    "source_authentication_independently_verified": False,
    "producer_projection_independently_verified": False,
}
# The pinned export records a stopped cycle whose three supported stages are all present.
REVIEW_CYCLE: dict[str, object] = {"status": "stopped", "stages_absent_from_export": []}
REVIEW_AUTHORITY = {
    "human_review": "required",
    "execution_authorized": False,
    "merge_authorized": False,
    "revert_authorized": False,
}
EXPECTED_NATIVE_VERIFICATION = {
    "schema": "goppi.paired_run_verification_result.v2",
    "status": "valid",
    "row_count": POPULATIONS["paired_rows"],
    "integrity_scope": "self_consistent_content_closure",
    "authenticity": "content_digest_only",
    "portable_checks": {"content_closure": "valid"},
    "source_local_authentication_independently_reverified": False,
    "producer_projection_independently_verified": False,
    "independent_provenance_established": False,
}


SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
# npm packs every member under this directory; the manifest names the package and its CLI.
PACKAGE_ROOT = "package"
PACKAGE_MANIFEST_MAX_BYTES = 1024 * 1024
PACKAGE_MEMBER_MAX_BYTES = 64 * 1024 * 1024
PACKAGE_TOTAL_MAX_BYTES = 256 * 1024 * 1024
NPM_PACKAGE_NAME = re.compile(r"(?:@[a-z0-9~-][a-z0-9._~-]*/)?[a-z0-9~-][a-z0-9._~-]*\Z")
NPM_PACKAGE_NAME_MAX = 214
PACKAGE_DIGEST_FIELDS = ("tarball_sha256_expected", "manifest_sha256", "cli_sha256")
PACKAGE_IDENTITY_FIELDS = (*PACKAGE_DIGEST_FIELDS, "cli_target", "installed_bytes_match_tarball")


class Failure(RuntimeError):
    def __init__(
        self,
        reason: str,
        location: str = "",
        phase: str = "verification",
        action: str = "INSPECT_ACCEPTANCE",
    ) -> None:
        self.phase, self.reason, self.location, self.next_action = phase, reason, location, action
        super().__init__(reason)

    def payload(self) -> dict[str, str]:
        return {
            "phase": self.phase,
            "reason": self.reason,
            "location": self.location,
            "next_action": self.next_action,
        }


def require(
    condition: bool,
    reason: str,
    location: str = "",
    phase: str = "verification",
    action: str = "INSPECT_ACCEPTANCE",
) -> None:
    if not condition:
        raise Failure(reason, location, phase, action)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return "sha256:" + value.hexdigest()


def _terminate(process: subprocess.Popen[bytes]) -> None:
    with suppress(OSError):
        os.killpg(process.pid, signal.SIGKILL)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=1)


def _receive(process: subprocess.Popen[bytes]) -> tuple[bytes, bytes, int]:
    assert process.stdout is not None and process.stderr is not None
    stdout_fd, stderr_fd = process.stdout.fileno(), process.stderr.fileno()
    buffers = {stdout_fd: bytearray(), stderr_fd: bytearray()}
    caps = {stdout_fd: STDOUT_MAX_BYTES, stderr_fd: STDERR_MAX_BYTES}
    deadline = time.monotonic() + DEADLINE_SECONDS
    with selectors.DefaultSelector() as selector:
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Failure("PROCESS_TIMEOUT", "/process", "execution", "INSPECT_PROCESS")
            for key, _ in selector.select(remaining):
                fd = key.fd
                chunk = os.read(fd, min(65536, caps[fd] - len(buffers[fd]) + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffers[fd].extend(chunk)
                if len(buffers[fd]) > caps[fd]:
                    raise Failure(
                        "PROCESS_OUTPUT_LIMIT",
                        "/process/output",
                        "execution",
                        "REDUCE_PROCESS_OUTPUT",
                    )
        try:
            code = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise Failure("PROCESS_TIMEOUT", "/process", "execution", "INSPECT_PROCESS") from None
    return bytes(buffers[stdout_fd]), bytes(buffers[stderr_fd]), code


def run(
    argv: list[str], cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    require(os.name == "posix", "PLATFORM_UNSUPPORTED", "/platform", "setup", "USE_POSIX_PLATFORM")
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except (OSError, ValueError):
        raise Failure(
            "PROCESS_START_FAILED", "/process/executable", "execution", "CORRECT_ARGUMENTS"
        ) from None
    try:
        stdout, stderr, code = _receive(process)
    except BaseException:
        _terminate(process)
        raise
    finally:
        assert process.stdout is not None and process.stderr is not None
        process.stdout.close()
        process.stderr.close()
    return subprocess.CompletedProcess(argv, code, stdout, stderr)


def success(result: subprocess.CompletedProcess[bytes], reason: str, phase: str) -> dict[str, Any]:
    require(result.returncode == 0, reason, "/process", phase, "INSPECT_PROCESS_OUTPUT")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise Failure(
            "PROCESS_JSON_INVALID", "/process/stdout", phase, "INSPECT_PROCESS_OUTPUT"
        ) from None
    require(isinstance(value, dict), "PROCESS_JSON_INVALID", "/process/stdout", phase)
    return cast(dict[str, Any], value)


def error_payload(result: subprocess.CompletedProcess[bytes], phase: str) -> dict[str, Any]:
    require(result.returncode != 0, "EXPECTED_REFUSAL_MISSING", "/process", phase)
    for raw in (result.stderr, result.stdout):
        try:
            value: Any = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            error = cast(dict[str, Any], value)
            data = error.get("data")
            storage = (
                cast(dict[str, Any], data).get("storage_code") if isinstance(data, dict) else None
            )
            selected = error.get("reason") or storage or error.get("code")
            if isinstance(selected, str):
                return error
    raise Failure("TYPED_ERROR_MISSING", "/process/output", phase, "INSPECT_PROCESS_OUTPUT")


def error_reason(result: subprocess.CompletedProcess[bytes], phase: str) -> str:
    error = error_payload(result, phase)
    data = error.get("data")
    storage = cast(dict[str, Any], data).get("storage_code") if isinstance(data, dict) else None
    selected = error.get("reason") or storage or error.get("code")
    require(isinstance(selected, str), "TYPED_ERROR_MISSING", "/process/output", phase)
    return cast(str, selected)


def validate_v2_refusal_case_names(case_names: list[str]) -> None:
    require(
        len(case_names) == V2_REFUSAL_CASES
        and len(set(case_names)) == V2_REFUSAL_CASES
        and set(case_names) == set(V2_REFUSAL_EXPECTATIONS),
        "FIXTURE_V2_REFUSAL_SET_DRIFT",
        "/refusals_v2",
    )


def validate_v2_native_refusal(
    result: subprocess.CompletedProcess[bytes], case_name: str
) -> None:
    payload = error_payload(result, "native_v2_refusal")
    data = payload.get("data")
    typed_data = cast(dict[str, Any], data) if isinstance(data, dict) else {}
    require(
        result.returncode == NATIVE_V2_REFUSAL_EXIT_CODE
        and payload.get("schema") == "goppi.command_result.v1"
        and payload.get("status") == "refused"
        and payload.get("code") == "COMMAND_FAILED"
        and payload.get("exit_code") == NATIVE_V2_REFUSAL_EXIT_CODE
        and typed_data.get("storage_code") == "PAIRED_VERIFY_EXCHANGE_INVALID",
        "NATIVE_V2_REFUSAL_DRIFT",
        "/refusals_v2/" + case_name,
    )


def validate_v2_underwrite_refusal(
    result: subprocess.CompletedProcess[bytes], case_name: str
) -> Literal["pre_verifier", "after_verifier"]:
    expectation = V2_REFUSAL_EXPECTATIONS.get(case_name)
    if expectation is None:
        raise Failure("FIXTURE_V2_REFUSAL_SET_DRIFT", "/refusals_v2/" + case_name)
    payload = error_payload(result, "underwrite_v2_refusal")
    require(
        result.returncode == expectation.exit_code
        and payload.get("schema") == "evidence_error.v1"
        and payload.get("code") == expectation.code
        and payload.get("reason") == expectation.reason
        and payload.get("location") == expectation.location
        and payload.get("exit_code") == expectation.exit_code
        and payload.get("next_action") == expectation.next_action
        and payload.get("retryable") is expectation.retryable,
        "UNDERWRITE_V2_REFUSAL_DRIFT",
        "/refusals_v2/" + case_name,
    )
    return expectation.phase


def v2_refusal_check_record(
    case_name: str, expected_phase: Literal["pre_verifier", "after_verifier"]
) -> dict[str, str]:
    return {
        "name": "refusal_v2:" + case_name,
        "expected_phase": expected_phase,
        "phase_basis": V2_REFUSAL_PHASE_BASIS,
        "status": "passed",
    }


def snapshot_artifacts(
    inputs: dict[str, Path], work: Path
) -> tuple[dict[str, Path], dict[str, dict[str, object]]]:
    root = work / "artifacts"
    root.mkdir(mode=0o700, parents=True)
    snapshots: dict[str, Path] = {}
    identities: dict[str, dict[str, object]] = {}
    for role in ARTIFACT_ROLES:
        name = "node" if role == "node_executable" else inputs[role].name
        target = root / name
        try:
            with inputs[role].open("rb") as reader:
                require(
                    stat.S_ISREG(os.fstat(reader.fileno()).st_mode),
                    "INPUT_ARTIFACT_NOT_REGULAR",
                    f"/{role}",
                    "snapshot",
                    "CORRECT_ARGUMENTS",
                )
                with target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, 1024 * 1024)
        except Failure:
            raise
        except OSError:
            raise Failure(
                "INPUT_ARTIFACT_UNAVAILABLE", f"/{role}", "snapshot", "CORRECT_ARGUMENTS"
            ) from None
        target.chmod(0o500 if role == "node_executable" else 0o400)
        snapshots[role] = target
        identities[role] = {"bytes": target.stat().st_size, "sha256": digest(target)}
    return snapshots, identities


class PackageCli(NamedTuple):
    """The CLI that the verified tarball's own manifest declares, with every file digest."""

    name: str
    target: str
    manifest_sha256: str
    cli_sha256: str
    files: tuple[tuple[str, str], ...]


class InstalledPair(NamedTuple):
    work: Path
    node: Path
    goppi: Path
    underwrite: Path
    env: dict[str, str]
    package: PackageCli
    npm_root: Path


def _package_require(condition: bool, reason: str) -> None:
    require(condition, reason, "/goppi_tarball", "installation")


def _archive_shape(members: list[tarfile.TarInfo]) -> None:
    """Refuse other roots and member types, and duplicate names as this reader sees them.

    npm can normalize names differently; the installed tree comparison is authoritative.
    """
    files: set[str] = set()
    for member in members:
        _package_require(
            member.name == PACKAGE_ROOT or member.name.startswith(PACKAGE_ROOT + "/"),
            "GOPPI_PACKAGE_MEMBER_OUTSIDE_ROOT",
        )
        _package_require(
            member.isfile() or member.isdir(), "GOPPI_PACKAGE_MEMBER_TYPE_UNSUPPORTED"
        )
        if member.isfile():
            # npm strips the root; a case-insensitive filesystem merges case variants.
            installed = member.name.removeprefix(PACKAGE_ROOT + "/").casefold()
            _package_require(installed not in files, "GOPPI_PACKAGE_MEMBER_AMBIGUOUS")
            files.add(installed)


def _single_member(
    members: list[tarfile.TarInfo], name: str, missing: str
) -> tarfile.TarInfo:
    matches = [member for member in members if member.name == name]
    _package_require(bool(matches), missing)
    _package_require(len(matches) == 1, "GOPPI_PACKAGE_MEMBER_AMBIGUOUS")
    _package_require(matches[0].isfile(), missing)
    return matches[0]


def _member_digest(
    archive: tarfile.TarFile, member: tarfile.TarInfo, limit: int
) -> tuple[str, bytes]:
    _package_require(member.size <= limit, "GOPPI_PACKAGE_MEMBER_LIMIT")
    reader = cast(IO[bytes], archive.extractfile(member))
    data = reader.read(limit + 1)
    _package_require(len(data) == member.size, "GOPPI_PACKAGE_UNREADABLE")
    return "sha256:" + hashlib.sha256(data).hexdigest(), data


def _cli_target(declared: object, name: str) -> str:
    """Select one declared CLI entry, refusing any ambiguity or escape."""
    if isinstance(declared, dict):
        entries = cast(dict[str, object], declared)
        if len(entries) > 1:
            unscoped = name.rsplit("/", 1)[-1]
            _package_require(unscoped in entries, "GOPPI_CLI_AMBIGUOUS")
            declared = entries[unscoped]
        else:
            declared = next(iter(entries.values()), None)
    _package_require(isinstance(declared, str) and declared != "", "GOPPI_CLI_UNDECLARED")
    target = cast(str, declared)
    path = PurePosixPath(target)
    escapes = path.is_absolute() or ".." in path.parts or path.parts == ()
    _package_require("\\" not in target and not escapes, "GOPPI_CLI_PATH_INVALID")
    return path.as_posix()


def package_cli(tarball: Path) -> PackageCli:
    """Read the package name and CLI from the tarball manifest instead of a fixed layout."""
    try:
        with tarfile.open(tarball, "r:gz") as archive:
            members = archive.getmembers()
            _archive_shape(members)
            manifest_member = _single_member(
                members, f"{PACKAGE_ROOT}/package.json", "GOPPI_PACKAGE_MANIFEST_MISSING"
            )
            manifest_sha256, raw = _member_digest(
                archive, manifest_member, PACKAGE_MANIFEST_MAX_BYTES
            )
            try:
                manifest: object = json.loads(raw)
            except (ValueError, RecursionError):
                manifest = None
            _package_require(isinstance(manifest, dict), "GOPPI_PACKAGE_MANIFEST_INVALID")
            fields = cast(dict[str, object], manifest)
            name = fields.get("name")
            _package_require(
                isinstance(name, str)
                and len(name) <= NPM_PACKAGE_NAME_MAX
                and NPM_PACKAGE_NAME.fullmatch(name) is not None,
                "GOPPI_PACKAGE_NAME_INVALID",
            )
            target = _cli_target(fields.get("bin"), cast(str, name))
            cli_member = _single_member(
                members, f"{PACKAGE_ROOT}/{target}", "GOPPI_CLI_MISSING"
            )
            regular = [member for member in members if member.isfile()]
            _package_require(
                sum(member.size for member in regular) <= PACKAGE_TOTAL_MAX_BYTES,
                "GOPPI_PACKAGE_MEMBER_LIMIT",
            )
            files = {
                member.name.removeprefix(PACKAGE_ROOT + "/"): _member_digest(
                    archive, member, PACKAGE_MEMBER_MAX_BYTES
                )[0]
                for member in regular
            }
    except (tarfile.TarError, OSError, EOFError, zlib.error):
        raise Failure("GOPPI_PACKAGE_UNREADABLE", "/goppi_tarball", "installation") from None
    cli_sha256 = files[cli_member.name.removeprefix(PACKAGE_ROOT + "/")]
    return PackageCli(
        cast(str, name), target, manifest_sha256, cli_sha256, tuple(sorted(files.items()))
    )


def _unreadable(_: OSError) -> None:
    # A directory that cannot be listed must not silently leave the comparison.
    raise Failure("GOPPI_CLI_INSTALL_UNREADABLE", "/goppi_tarball", "installation")


def _installed_files(directory: Path) -> dict[str, str]:
    """Digest every file npm placed under the package, nested directories included.

    Nothing is skipped: readers can disagree about where an archive ends, so a file
    only npm extracted would otherwise escape the comparison.
    """
    files: dict[str, str] = {}
    for current, folders, names in os.walk(directory, onerror=_unreadable, followlinks=False):
        base = Path(current)
        for entry in (*folders, *names):
            _package_require(not (base / entry).is_symlink(), "GOPPI_CLI_INSTALL_REDIRECTED")
        for entry in names:
            path = base / entry
            _package_require(path.is_file(), "GOPPI_CLI_INSTALL_MISMATCH")
            files[path.relative_to(directory).as_posix()] = digest(path)
    return files


def installed_cli(npm_root: Path, package: PackageCli) -> Path:
    """Use the CLI only if npm installed exactly the tarball's files, byte for byte."""
    modules = (npm_root / "node_modules").resolve()
    directory = modules.joinpath(*package.name.split("/"))
    cli = directory.joinpath(*package.target.split("/"))
    manifest = directory / "package.json"
    for path in (cli, manifest):
        _package_require(path.exists() or path.is_symlink(), "GOPPI_CLI_INSTALL_MISSING")
        # Resolution must not leave the package directory through any link.
        _package_require(path.resolve() == path, "GOPPI_CLI_INSTALL_REDIRECTED")
        _package_require(path.is_file(), "GOPPI_CLI_INSTALL_MISSING")
    # Name normalization can differ between readers; compare what was actually installed.
    _package_require(
        _installed_files(directory) == dict(package.files), "GOPPI_CLI_INSTALL_MISMATCH"
    )
    return cli


def checked_exercise(
    work: Path, exchange: Path, node: Path, pair: InstalledPair, env: dict[str, str]
) -> list[dict[str, str]]:
    checks = exercise(work, exchange, node, pair.goppi, pair.underwrite, env)
    # The checks executed installed code; the verified files must still be in place.
    try:
        installed_cli(pair.npm_root, pair.package)
    except Failure:
        raise Failure(
            "GOPPI_CLI_CHANGED_DURING_CHECKS", "/goppi_tarball", "verification"
        ) from None
    return checks



MAX_PACKAGE_FILES = 10_000
MAX_PACKAGE_ENTRIES = 50_000
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = MAX_ARCHIVE_BYTES
V2_PAIRED_ROWS = 48
V2_REPEAT_PAIRS = 24
V2_REFUSAL_CASES = 13
NATIVE_V2_REFUSAL_EXIT_CODE = 3


class V2RefusalExpectation(NamedTuple):
    phase: Literal["pre_verifier", "after_verifier"]
    code: str
    reason: str
    location: str
    exit_code: int
    next_action: str
    retryable: bool


V2_PRE_VERIFIER_REFUSAL = V2RefusalExpectation(
    "pre_verifier",
    "INPUT_BINDING_MISMATCH",
    "GOPPI_VERIFIER_BINDING_MISMATCH",
    "/trajectory/manifest.json",
    2,
    "CORRECT_INPUT_BINDING",
    False,
)
V2_AFTER_VERIFIER_REFUSAL = V2RefusalExpectation(
    "after_verifier",
    "INVALID_INPUT",
    "GOPPI_VERIFIER_REFUSED",
    "/exchange",
    2,
    "CORRECT_INPUT",
    False,
)
V2_REFUSAL_EXPECTATIONS = {
    "malformed-assignment-commitment": V2_PRE_VERIFIER_REFUSAL,
    "malformed-export-build": V2_PRE_VERIFIER_REFUSAL,
    "missing-execution-binding": V2_PRE_VERIFIER_REFUSAL,
    "missing-export-build": V2_PRE_VERIFIER_REFUSAL,
    "missing-trajectory-binding": V2_AFTER_VERIFIER_REFUSAL,
    "not-run-stage-build": V2_PRE_VERIFIER_REFUSAL,
    "not-run-stage-rows": V2_PRE_VERIFIER_REFUSAL,
    "runner-default-level-mismatch": V2_PRE_VERIFIER_REFUSAL,
    "runner-model-mismatch": V2_AFTER_VERIFIER_REFUSAL,
    "split-salt-limit-mismatch": V2_PRE_VERIFIER_REFUSAL,
    "trajectory-assignment-mismatch": V2_AFTER_VERIFIER_REFUSAL,
    "unknown-classification": V2_PRE_VERIFIER_REFUSAL,
    "unknown-not-run-reason": V2_PRE_VERIFIER_REFUSAL,
}
V2_REFUSAL_PHASE_BASIS = "inferred_from_exact_public_error_contract"
DIR_HASH_SEGMENT = re.compile(r"[A-Za-z0-9._-]+\Z")
DIR_HASH_HEX = re.compile(r"[0-9a-f]{64}\Z")


def _valid_tree_name(name: str, *, allow_root_node_modules: bool) -> bool:
    if not name or name.startswith("/") or "\\" in name:
        return False
    parts = name.split("/")
    first = parts[0]
    if first.startswith("-") and not (allow_root_node_modules and first == "node_modules"):
        return False
    if first == "node_modules" and not allow_root_node_modules:
        return False
    return all(
        DIR_HASH_SEGMENT.fullmatch(part) is not None and part not in {".", ".."} for part in parts
    )

def _claim_tree_path(claims: dict[str, tuple[str, bool]], name: str, *, is_file: bool) -> None:
    parts = name.split("/")
    for end in range(1, len(parts) + 1):
        current = "/".join(parts[:end])
        current_is_file = is_file and end == len(parts)
        folded = current.lower()
        prior = claims.get(folded)
        if prior is None:
            claims[folded] = (current, current_is_file)
        elif prior[0] != current:
            raise Failure("PACKAGE_TREE_PATH_COLLISION", "/package_tree")
        elif prior[1] or current_is_file:
            reason = (
                "PACKAGE_TREE_NAME_DUPLICATE"
                if prior[1] and current_is_file
                else "PACKAGE_TREE_PATH_COLLISION"
            )
            raise Failure(reason, "/package_tree")

def _dir_hash(files: dict[str, tuple[int, str]]) -> str:
    claims: dict[str, tuple[str, bool]] = {}
    for name, (_, file_digest) in files.items():
        require(
            _valid_tree_name(name, allow_root_node_modules=False),
            "PACKAGE_TREE_NAME_INVALID",
            "/package_tree",
        )
        require(
            DIR_HASH_HEX.fullmatch(file_digest) is not None,
            "PACKAGE_TREE_DIGEST_INVALID",
            "/package_tree",
        )
        _claim_tree_path(claims, name, is_file=True)
    summary = "".join(f"{files[name][1]}  {name}\n" for name in sorted(files)).encode("ascii")
    return hashlib.sha256(summary).hexdigest()

def _stream_digest(stream: Any, max_bytes: int) -> tuple[int, str]:
    value = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        size += len(chunk)
        require(size <= max_bytes, "PACKAGE_TREE_MEMBER_LIMIT", "/package_tree")
        value.update(chunk)
    return size, value.hexdigest()

def _archive_relative_path(member: tarfile.TarInfo) -> tuple[str, bool, bool]:
    if member.name in {"package", "package/"}:
        relative = ""
    elif member.name.startswith("package/"):
        relative = member.name[len("package/") :]
    else:
        raise Failure("PACKAGE_TARBALL_PATH_INVALID", "/goppi_tarball")
    is_directory, is_file = member.isdir(), member.isfile()
    require(is_directory or is_file, "PACKAGE_TARBALL_MEMBER_TYPE_INVALID", "/goppi_tarball")
    if hasattr(member, "issparse"):
        require(not member.issparse(), "PACKAGE_TARBALL_MEMBER_TYPE_INVALID", "/goppi_tarball")
    if is_directory and relative.endswith("/"):
        relative = relative[:-1]
    require(bool(relative) or is_directory, "PACKAGE_TARBALL_PATH_INVALID", "/goppi_tarball")
    return relative, is_directory, is_file

def _validated_archive_members(
    archive: tarfile.TarFile,
) -> list[tuple[tarfile.TarInfo, str, bool]]:
    members = archive.getmembers()
    require(len(members) <= MAX_PACKAGE_ENTRIES, "PACKAGE_TREE_ENTRY_LIMIT", "/goppi_tarball")
    prepared: list[tuple[tarfile.TarInfo, str, bool]] = []
    explicit: set[str] = set()
    claims: dict[str, tuple[str, bool]] = {}
    total_bytes = 0
    file_count = 0
    for member in members:
        relative, is_directory, is_file = _archive_relative_path(member)
        if relative:
            require(
                _valid_tree_name(relative, allow_root_node_modules=True),
                "PACKAGE_TARBALL_PATH_INVALID",
                "/goppi_tarball",
            )
            require(relative not in explicit, "PACKAGE_TARBALL_MEMBER_DUPLICATE", "/goppi_tarball")
            explicit.add(relative)
            _claim_tree_path(claims, relative, is_file=is_file)
        else:
            require("" not in explicit, "PACKAGE_TARBALL_MEMBER_DUPLICATE", "/goppi_tarball")
            explicit.add("")
        require(
            type(member.size) is int and 0 <= member.size <= MAX_MEMBER_BYTES,
            "PACKAGE_TARBALL_MEMBER_SIZE_INVALID",
            "/goppi_tarball",
        )
        if is_directory:
            require(member.size == 0, "PACKAGE_TARBALL_MEMBER_SIZE_INVALID", "/goppi_tarball")
        else:
            file_count += 1
            total_bytes += member.size
            require(
                total_bytes <= MAX_ARCHIVE_BYTES,
                "PACKAGE_TARBALL_TOTAL_SIZE_INVALID",
                "/goppi_tarball",
            )
        prepared.append((member, relative, is_file))
    require(file_count <= MAX_PACKAGE_FILES, "PACKAGE_TREE_FILE_LIMIT", "/goppi_tarball")
    return prepared

def _archive_file_hashes(
    archive: tarfile.TarFile, members: list[tuple[tarfile.TarInfo, str, bool]]
) -> dict[str, tuple[int, str]]:
    files: dict[str, tuple[int, str]] = {}
    total_bytes = 0
    for member, relative, is_file in members:
        if not is_file or PurePosixPath(relative).parts[0] == "node_modules":
            continue
        stream = archive.extractfile(member)
        if stream is None:
            raise Failure("PACKAGE_TARBALL_MEMBER_READ_FAILED", "/goppi_tarball")
        with stream:
            size, file_digest = _stream_digest(stream, MAX_PACKAGE_BYTES)
        require(size == member.size, "PACKAGE_TARBALL_MEMBER_SIZE_INVALID", "/goppi_tarball")
        total_bytes += size
        require(
            total_bytes <= MAX_PACKAGE_BYTES,
            "PACKAGE_TREE_TOTAL_SIZE_INVALID",
            "/goppi_tarball",
        )
        files[relative] = (size, file_digest)
    return files

def _tree_result(files: dict[str, tuple[int, str]]) -> dict[str, Any]:
    return {
        "dir_hash": _dir_hash(files),
        "files": [
            {"path": name, "bytes": size, "sha256": "sha256:" + file_digest}
            for name, (size, file_digest) in sorted(files.items())
        ],
    }

def archive_package_tree(tarball: Path, package: PackageCli) -> dict[str, Any]:
    """Independently hash ordinary files beneath exactly one package/ archive prefix."""
    try:
        archive = tarfile.open(tarball, mode="r:*")
    except (OSError, tarfile.TarError):
        raise Failure("PACKAGE_TARBALL_INVALID", "/goppi_tarball", "snapshot") from None
    with archive:
        members = _validated_archive_members(archive)
        files = _archive_file_hashes(archive, members)
    require(
        "package.json" in files and package.target in files,
        "GOPPI_PACKAGE_LAYOUT_INVALID",
        "/goppi_tarball",
    )
    return _tree_result(files)

def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )

class _InstalledPackageScanner:
    def __init__(self, root: Path, package: PackageCli) -> None:
        self.root = root
        self.package = package
        self.files: dict[str, tuple[int, str]] = {}
        self.claims: dict[str, tuple[str, bool]] = {}
        self.entry_count = 0
        self.total_bytes = 0
        self.directory_snapshots: list[tuple[Path, os.stat_result]] = []

    def scan(self) -> dict[str, Any]:
        try:
            self._visit(self.root, "")
            for directory, expected in self.directory_snapshots:
                require(
                    _stat_identity(expected) == _stat_identity(directory.lstat()),
                    "PACKAGE_TREE_CHANGED",
                    "/installed_package",
                )
        except OSError:
            raise Failure("PACKAGE_TREE_READ_FAILED", "/installed_package") from None
        require(
            "package.json" in self.files and self.package.target in self.files,
            "GOPPI_PACKAGE_LAYOUT_INVALID",
            "/installed_package",
        )
        return _tree_result(self.files)

    def _visit(self, directory: Path, relative: str) -> None:
        before = directory.lstat()
        require(
            stat.S_ISDIR(before.st_mode), "PACKAGE_TREE_DIRECTORY_INVALID", "/installed_package"
        )
        try:
            with os.scandir(directory) as iterator:
                names = sorted(entry.name for entry in iterator)
        except OSError:
            raise Failure("PACKAGE_TREE_READ_FAILED", "/installed_package") from None
        require(
            _stat_identity(before) == _stat_identity(directory.lstat()),
            "PACKAGE_TREE_CHANGED",
            "/installed_package",
        )
        self.directory_snapshots.append((directory, before))
        for name in names:
            self._visit_member(directory, relative, name)

    def _visit_member(self, directory: Path, relative: str, name: str) -> None:
        member_name = f"{relative}/{name}" if relative else name
        require(
            _valid_tree_name(member_name, allow_root_node_modules=True),
            "PACKAGE_TREE_NAME_INVALID",
            "/installed_package",
        )
        path = directory / name
        try:
            before = path.lstat()
        except OSError:
            raise Failure("PACKAGE_TREE_CHANGED", "/installed_package") from None
        if not relative and name == "node_modules":
            require(
                stat.S_ISDIR(before.st_mode),
                "PACKAGE_TREE_MEMBER_TYPE_INVALID",
                "/installed_package",
            )
            return
        self.entry_count += 1
        require(
            self.entry_count <= MAX_PACKAGE_ENTRIES,
            "PACKAGE_TREE_ENTRY_LIMIT",
            "/installed_package",
        )
        is_directory, is_file = stat.S_ISDIR(before.st_mode), stat.S_ISREG(before.st_mode)
        require(is_directory or is_file, "PACKAGE_TREE_MEMBER_TYPE_INVALID", "/installed_package")
        _claim_tree_path(self.claims, member_name, is_file=is_file)
        if is_directory:
            self._visit(path, member_name)
        else:
            self._hash_file(path, member_name, before)

    def _hash_file(self, path: Path, name: str, before: os.stat_result) -> None:
        require(
            before.st_size <= MAX_PACKAGE_BYTES,
            "PACKAGE_TREE_MEMBER_LIMIT",
            "/installed_package",
        )
        try:
            with path.open("rb") as stream:
                opened = os.fstat(stream.fileno())
                require(
                    stat.S_ISREG(opened.st_mode)
                    and _stat_identity(opened) == _stat_identity(before),
                    "PACKAGE_TREE_CHANGED",
                    "/installed_package",
                )
                size, file_digest = _stream_digest(stream, MAX_PACKAGE_BYTES)
            after = path.lstat()
        except Failure:
            raise
        except OSError:
            raise Failure("PACKAGE_TREE_READ_FAILED", "/installed_package") from None
        require(
            _stat_identity(before) == _stat_identity(after),
            "PACKAGE_TREE_CHANGED",
            "/installed_package",
        )
        require(size == before.st_size, "PACKAGE_TREE_CHANGED", "/installed_package")
        self.total_bytes += size
        require(
            self.total_bytes <= MAX_PACKAGE_BYTES,
            "PACKAGE_TREE_TOTAL_SIZE_INVALID",
            "/installed_package",
        )
        self.files[name] = (size, file_digest)
        require(
            len(self.files) <= MAX_PACKAGE_FILES,
            "PACKAGE_TREE_FILE_LIMIT",
            "/installed_package",
        )

def installed_package_tree(root: Path, package: PackageCli) -> dict[str, Any]:
    """Hash an installed package using lstat, excluding only its direct node_modules."""
    return _InstalledPackageScanner(root, package).scan()

def compare_package_trees(
    tarball: Path, installed: Path, package: PackageCli, export_build_dir_hash: str
) -> dict[str, Any]:
    tar_tree = archive_package_tree(tarball, package)
    installed_tree = installed_package_tree(installed, package)
    require(
        tar_tree["files"] == installed_tree["files"],
        "INSTALLED_PACKAGE_TREE_MISMATCH",
        "/package_tree",
        "installation",
        "INSPECT_INSTALLATION",
    )
    expected = "sha256:" + export_build_dir_hash
    return {
        "algorithm": "goppi_dirHash_sha256_summary_v1",
        "export_build_dir_hash": expected,
        "tarball": {
            "dir_hash": tar_tree["dir_hash"],
            "matches_export_build_digest": "sha256:" + tar_tree["dir_hash"] == expected,
            "files": tar_tree["files"],
        },
        "installed": {
            "dir_hash": installed_tree["dir_hash"],
            "matches_export_build_digest": "sha256:" + installed_tree["dir_hash"] == expected,
            "files": installed_tree["files"],
        },
        "tarball_matches_installed_tree": True,
        "interpretation_limit": (
            "The golden build digest is synthetic; this comparison does not establish "
            "producer provenance."
        ),
    }


def snapshot_fixture(work: Path) -> tuple[Path, dict[str, Any]]:
    root = shutil.copytree(FIXTURE, work / "fixture")
    pins = json.loads((root / "pins.json").read_bytes())
    exchange, members = root / "exchange", pins["members"]
    actual = {p.relative_to(exchange).as_posix() for p in exchange.rglob("*") if p.is_file()}
    require(actual == set(members), "FIXTURE_MEMBERS_DRIFT", "/fixture")
    for relative, expected in members.items():
        member = exchange / relative
        require(
            member.stat().st_size == expected["bytes"] and digest(member) == expected["sha256"],
            "FIXTURE_BYTES_DRIFT",
            f"/fixture/{relative}",
        )
    return exchange, pins


def snapshot_fixture_v2(work: Path) -> tuple[Path, Path, dict[str, Any], str]:
    root = shutil.copytree(FIXTURE_V2, work / "fixture-v2")
    pins = json.loads((root / "pins.json").read_bytes())
    require(
        pins.get("schema") == "underwrite.goppi_execution_v2_fixture_pins.v1",
        "FIXTURE_V2_SCHEMA_DRIFT",
        "/fixture_v2",
    )
    fixture_roots = ("paired-exchange-v2", "paired-exchange-v2-rejections", "build-dir-hash")
    actual = {
        path.relative_to(root).as_posix()
        for directory in fixture_roots
        for path in (root / directory).rglob("*")
        if path.is_file()
    }
    require(actual == set(pins["members"]), "FIXTURE_V2_MEMBERS_DRIFT", "/fixture_v2")
    for relative, expected in pins["members"].items():
        member = root / relative
        require(
            member.stat().st_size == expected["bytes"] and digest(member) == expected["sha256"],
            "FIXTURE_V2_BYTES_DRIFT",
            "/fixture_v2",
        )
    exchange = work / "execution-v2-exchange"
    exchange.mkdir(mode=0o700)
    for family in ("paired", "trajectory"):
        shutil.copytree(root / "paired-exchange-v2" / family, exchange / family)
    execution = json.loads((exchange / "paired/execution.json").read_bytes())
    build = execution.get("export_build")
    require(isinstance(build, dict), "FIXTURE_V2_BUILD_DIGEST_MISSING", "/fixture_v2")
    build_record = cast(dict[str, Any], build)
    digest_set = build_record.get("build_digest")
    require(isinstance(digest_set, dict), "FIXTURE_V2_BUILD_DIGEST_MISSING", "/fixture_v2")
    export_dir_hash = cast(dict[str, Any], digest_set).get("dirHash")
    if not isinstance(export_dir_hash, str) or DIR_HASH_HEX.fullmatch(export_dir_hash) is None:
        raise Failure("FIXTURE_V2_BUILD_DIGEST_INVALID", "/fixture_v2")
    return exchange, root / "paired-exchange-v2-rejections", pins, export_dir_hash


def isolated_env(work: Path, node: Path) -> dict[str, str]:
    paths = {name: work / name for name in ("home", "tmp", "cache", "config")}
    for path in paths.values():
        path.mkdir(mode=0o700)
    npm_user, npm_global = paths["config"] / "npmrc", paths["config"] / "npm-globalrc"
    npm_user.write_text("registry=https://registry.npmjs.org/\n", encoding="utf-8")
    npm_global.write_text("", encoding="utf-8")
    env = {
        "PATH": str(node.parent) + os.pathsep + os.defpath,
        "HOME": str(paths["home"]),
        "TMPDIR": str(paths["tmp"]),
        "XDG_CACHE_HOME": str(paths["cache"]),
        "XDG_CONFIG_HOME": str(paths["config"]),
        "NPM_CONFIG_CACHE": str(paths["cache"] / "npm"),
        "NPM_CONFIG_USERCONFIG": str(npm_user),
        "NPM_CONFIG_GLOBALCONFIG": str(npm_global),
        "NPM_CONFIG_AUDIT": "false",
        "NPM_CONFIG_FUND": "false",
        "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        "UV_CACHE_DIR": str(paths["cache"] / "uv"),
        "UV_NO_CONFIG": "1",
        "UV_NO_ENV_FILE": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_DEFAULT_INDEX": "https://pypi.org/simple",
        "PIP_CONFIG_FILE": os.devnull,
    }
    for name in ("LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def fingerprint(items: list[Any]) -> dict[str, object]:
    raw = json.dumps(items, separators=(",", ":")).encode()
    return {"package_count": len(items), "sha256": "sha256:" + hashlib.sha256(raw).hexdigest()}


def npm_resolution(lock_path: Path) -> dict[str, object]:
    packages = json.loads(lock_path.read_bytes())["packages"]
    observed = [
        (location, metadata.get("version"), metadata.get("integrity"))
        for location, metadata in sorted(packages.items())
        if location
    ]
    return {
        **fingerprint(observed),
        "scope": "observed_installed_package_tree",
        "reproducible_transitive_resolution_claimed": False,
    }


def python_resolution(python: Path, work: Path, env: dict[str, str]) -> dict[str, object]:
    code = (
        "import importlib.metadata as m,json;"
        "print(json.dumps(sorted((d.metadata['Name'],d.version) for d in m.distributions())))"
    )
    process = run([str(python), "-I", "-c", code], work, env)
    require(process.returncode == 0, "PYTHON_RESOLUTION_READ_FAILED", "/python_resolution")
    try:
        packages = json.loads(process.stdout)
    except json.JSONDecodeError:
        raise Failure("PYTHON_RESOLUTION_INVALID", "/python_resolution", "installation") from None
    require(isinstance(packages, list), "PYTHON_RESOLUTION_INVALID", "/python_resolution")
    return {
        **fingerprint(packages),
        "packages": [{"name": item[0], "version": item[1]} for item in packages],
    }


def native(
    node: Path, cli: Path, exchange: Path, work: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[bytes]:
    return run(
        [str(node), str(cli), "run", "verify-paired", "--input", str(exchange), "--json"],
        work,
        env,
    )


def audit(
    cli: Path, exchange: Path, goppi: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[bytes]:
    return run(
        [
            str(cli),
            "audit-trajectory",
            "--exchange",
            str(exchange),
            "--goppi-executable",
            str(goppi),
            "--json",
        ],
        exchange.parent,
        env,
    )


def has_tool_status(result: dict[str, Any], status: str) -> bool:
    return any(
        item.get("tool_name") == "read" and item.get("status") == status
        for item in result.get("tool_status_counts", [])
    )


def stable_change(review: dict[str, Any]) -> bool:
    for comparison in review.get("comparisons", []):
        baseline, candidate = comparison.get("baseline", {}), comparison.get("candidate", {})
        if (
            comparison.get("difference", {}).get("outcome_changed") is True
            and baseline.get("outcome", {}).get("status") == "observed"
            and baseline.get("outcome", {}).get("scored_value") is False
            and candidate.get("outcome", {}).get("status") == "observed"
            and candidate.get("outcome", {}).get("scored_value") is True
            and has_tool_status(baseline, "error")
            and has_tool_status(candidate, "ok")
        ):
            return True
    return False


def validate_review(review: dict[str, Any]) -> None:
    populations = review.get("populations", {})
    require(
        review.get("schema") == CONTRACTS["underwrite_review"]
        and review.get("state") == "analyzed"
        and review.get("trust_scope") == REVIEW_TRUST
        and review.get("cycle") == REVIEW_CYCLE
        and review.get("authority") == REVIEW_AUTHORITY
        and all(populations.get(key) == value for key, value in POPULATIONS.items())
        and len(review.get("comparisons", [])) == POPULATIONS["case_stage_repeat_pairs"]
        and stable_change(review),
        "UNDERWRITE_POSITIVE_CONTRACT_DRIFT",
        "/audit",
    )


def validate_v2_review(review: dict[str, Any], export_dir_hash: str) -> None:
    populations = cast(dict[str, Any], review.get("populations", {}))
    declarations = cast(dict[str, Any], review.get("producer_declarations", {}))
    builds = cast(dict[str, Any], declarations.get("builds", {}))
    export = cast(dict[str, Any], builds.get("export", {}))
    stages = cast(list[dict[str, Any]], review.get("stages", []))
    missing_stage: dict[str, Any] = {}
    held_in = next((stage for stage in stages if stage.get("stage") == "held_in"), missing_stage)
    held_out = next((stage for stage in stages if stage.get("stage") == "held_out"), missing_stage)
    trust = cast(dict[str, Any], review.get("trust_scope", {}))
    split = cast(dict[str, Any], declarations.get("split", {}))
    require(
        review.get("schema") == CONTRACTS["underwrite_review_v2"]
        and review.get("state") == "analyzed"
        and populations.get("paired_rows") == V2_PAIRED_ROWS
        and populations.get("case_stage_repeat_pairs") == V2_REPEAT_PAIRS
        and trust.get("verifier_identity_verified") is False
        and builds.get("identity_assurance") == "self_reported"
        and builds.get("independently_verified") is False
        and builds.get("cross_stage_comparability") == "not_inferred_from_digest_equality"
        and export.get("state") == "self_reported"
        and export.get("dir_hash") == export_dir_hash
        and split.get("commitment_recomputed") is False
        and held_in.get("execution_status") == "closed"
        and held_in.get("classification") == "indeterminate"
        and held_out.get("execution_status") == "not_run"
        and held_out.get("classification") == "not_evaluated"
        and held_out.get("not_run_reason") == "earlier_stage_partial_stop"
        and review.get("cycle") == {"status": "stopped", "stages_absent_from_export": []},
        "UNDERWRITE_V2_POSITIVE_CONTRACT_DRIFT",
        "/audit_v2",
    )


def validate_missing(
    native_result: subprocess.CompletedProcess[bytes],
    audit_result: subprocess.CompletedProcess[bytes],
) -> None:
    require(
        error_reason(native_result, "negative_missing") == "PAIRED_VERIFY_INPUT_INVALID",
        "NATIVE_MISSING_TRAJECTORY_REASON_DRIFT",
        "/negative/missing/native",
    )
    require(
        error_reason(audit_result, "negative_missing")
        == "TRAJECTORY_COMPLETE_HANDOFF_REQUIRED",
        "UNDERWRITE_MISSING_TRAJECTORY_REASON_DRIFT",
        "/negative/missing/underwrite",
    )


def validate_success_summary(summary: dict[str, Any]) -> None:
    require(summary.get("contracts") == CONTRACTS, "SUCCESS_CONTRACT_IDS_MISSING", "/contracts")
    package = cast(dict[str, object], summary.get("goppi_package") or {})
    artifacts = cast(dict[str, dict[str, object]], summary.get("artifacts") or {})
    digests = [package.get(field) for field in PACKAGE_DIGEST_FIELDS]
    require(
        sorted(package) == sorted(PACKAGE_IDENTITY_FIELDS)
        and package.get("installed_bytes_match_tarball") is True
        and isinstance(package.get("cli_target"), str)
        and all(isinstance(value, str) and SHA256.fullmatch(value) for value in digests)
        and artifacts.get("goppi_tarball", {}).get("sha256") == digests[0],
        "SUCCESS_PACKAGE_IDENTITY_MISSING",
        "/goppi_package",
    )
    require(
        summary.get("trust_limits") == TRUST_LIMITS,
        "SUCCESS_TRUST_LIMITS_MISSING",
        "/trust_limits",
    )
    for name in ("npm_resolution", "python_resolution"):
        resolution = summary.get(name)
        require(isinstance(resolution, dict), "SUCCESS_RESOLUTION_MISSING", f"/{name}")
        typed_resolution = cast(dict[str, Any], resolution)
        require(
            isinstance(typed_resolution.get("package_count"), int)
            and isinstance(typed_resolution.get("sha256"), str),
            "SUCCESS_RESOLUTION_MISSING",
            f"/{name}",
        )
    tree = summary.get("package_tree")
    require(isinstance(tree, dict), "PACKAGE_TREE_SUMMARY_MISSING", "/package_tree")
    typed_tree = cast(dict[str, Any], tree)
    expected = typed_tree.get("export_build_dir_hash")
    require(
        typed_tree.get("algorithm") == "goppi_dirHash_sha256_summary_v1"
        and isinstance(expected, str)
        and SHA256.fullmatch(expected) is not None,
        "PACKAGE_TREE_SUMMARY_INVALID",
        "/package_tree",
    )
    tarball_tree, installed_tree = typed_tree.get("tarball"), typed_tree.get("installed")
    require(
        isinstance(tarball_tree, dict) and isinstance(installed_tree, dict),
        "PACKAGE_TREE_SUMMARY_INVALID",
        "/package_tree",
    )
    tarball_record = cast(dict[str, Any], tarball_tree)
    installed_record = cast(dict[str, Any], installed_tree)
    tarball_files, installed_files = tarball_record.get("files"), installed_record.get("files")
    require(
        isinstance(tarball_files, list)
        and tarball_files == installed_files
        and tarball_record.get("dir_hash") == installed_record.get("dir_hash")
        and typed_tree.get("tarball_matches_installed_tree") is True,
        "INSTALLED_PACKAGE_TREE_MISMATCH",
        "/package_tree",
    )
    for record in (tarball_record, installed_record):
        files = cast(list[dict[str, Any]], record["files"])
        computed = _dir_hash(
            {
                item["path"]: (
                    item["bytes"],
                    cast(str, item["sha256"]).removeprefix("sha256:"),
                )
                for item in files
            }
        )
        require(
            record.get("dir_hash") == computed
            and isinstance(record.get("matches_export_build_digest"), bool)
            and record.get("matches_export_build_digest") == ("sha256:" + computed == expected),
            "PACKAGE_TREE_SUMMARY_INVALID",
            "/package_tree",
        )



def exercise_v2(
    pair: InstalledPair,
    exchange: Path,
    rejections: Path,
    export_dir_hash: str,
) -> list[dict[str, str]]:
    work, node, goppi, underwrite, env = (
        pair.work,
        pair.node,
        pair.goppi,
        pair.underwrite,
        pair.env,
    )
    complete = shutil.copytree(exchange, work / "complete-v2")
    producer = success(
        native(node, goppi, complete, work, env),
        "NATIVE_V2_VERIFICATION_FAILED",
        "native_v2",
    )["data"]
    require(
        producer.get("format") == "v2"
        and producer.get("trajectory", {}).get("format") == "v2"
        and producer.get("row_count") == V2_PAIRED_ROWS
        and producer.get("cycle_status") == "stopped",
        "NATIVE_V2_CONTRACT_DRIFT",
        "/native_v2",
    )
    review = success(
        audit(underwrite, complete, goppi, env),
        "AUDIT_V2_FAILED",
        "audit_v2",
    )
    validate_v2_review(review, export_dir_hash)

    checked = [{"name": "complete_execution_v2", "status": "passed"}]
    cases = sorted(path for path in rejections.iterdir() if path.is_dir())
    validate_v2_refusal_case_names([case.name for case in cases])
    for case in cases:
        invalid = shutil.copytree(complete, work / ("refusal-v2-" + case.name))
        for source in case.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(case)
            target = invalid / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o600)
        native_result = native(node, goppi, invalid, work, env)
        validate_v2_native_refusal(native_result, case.name)
        underwrite_result = audit(underwrite, invalid, goppi, env)
        refusal_phase = validate_v2_underwrite_refusal(underwrite_result, case.name)
        checked.append(v2_refusal_check_record(case.name, refusal_phase))
    require(
        len(checked) == V2_REFUSAL_CASES + 1,
        "FIXTURE_V2_REFUSAL_COUNT_DRIFT",
        "/refusals_v2",
    )
    return checked


def exercise(
    work: Path,
    exchange: Path,
    node: Path,
    goppi: Path,
    underwrite: Path,
    env: dict[str, str],
) -> list[dict[str, str]]:
    complete = shutil.copytree(exchange, work / "complete")
    producer = success(
        native(node, goppi, complete, work, env), "NATIVE_VERIFICATION_FAILED", "native"
    )["data"]
    require(
        all(producer.get(key) == value for key, value in EXPECTED_NATIVE_VERIFICATION.items()),
        "NATIVE_CONTRACT_DRIFT",
        "/native",
    )
    review = success(audit(underwrite, complete, goppi, env), "AUDIT_FAILED", "audit")
    validate_review(review)

    missing = shutil.copytree(complete, work / "missing-trajectory")
    shutil.rmtree(missing / "trajectory")
    validate_missing(
        native(node, goppi, missing, work, env),
        audit(underwrite, missing, goppi, env),
    )

    tampered = shutil.copytree(complete, work / "tampered-row")
    rows = tampered / "paired/rows.jsonl"
    lines = rows.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    require(first.get("arm") == "baseline", "FIXTURE_SEMANTIC_DRIFT", "/fixture/rows/0/arm")
    first["arm"] = "baselinx"
    lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
    rows.write_text("\n".join(lines) + "\n", encoding="utf-8")
    require(
        error_reason(native(node, goppi, tampered, work, env), "negative_tamper")
        == "PAIRED_VERIFY_EXCHANGE_INVALID",
        "NATIVE_TAMPER_REASON_DRIFT",
        "/negative/tamper/native",
    )
    require(
        error_reason(audit(underwrite, tampered, goppi, env), "negative_tamper")
        == "GOPPI_VERIFIER_REFUSED",
        "UNDERWRITE_TAMPER_REASON_DRIFT",
        "/negative/tamper/underwrite",
    )
    require(
        error_reason(
            audit(underwrite, complete, work / "missing-goppi", env), "negative_unavailable"
        )
        == "GOPPI_VERIFIER_UNAVAILABLE",
        "UNAVAILABLE_VERIFIER_REASON_DRIFT",
        "/negative/unavailable",
    )
    return [
        {"name": name, "status": "passed"}
        for name in (
            "complete_exchange",
            "missing_trajectory",
            "tampered_paired_row",
            "unavailable_verifier",
        )
    ]


def install(
    work: Path,
    snapshots: dict[str, Path],
    node: Path,
    env: dict[str, str],
    summary: dict[str, Any],
) -> InstalledPair:
    npm, uv = shutil.which("npm"), shutil.which("uv")
    if npm is None:
        raise Failure("NPM_UNAVAILABLE", "/npm", "setup", "INSTALL_NPM")
    if uv is None:
        raise Failure("UV_UNAVAILABLE", "/uv", "setup", "INSTALL_UV")
    package = package_cli(snapshots["goppi_tarball"])
    npm_root = work / "npm"
    npm_argv = [
        str(node),
        str(Path(npm).resolve()),
        "install",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
        "--prefix",
        str(npm_root),
        str(snapshots["goppi_tarball"]),
    ]
    require(run(npm_argv, work, env).returncode == 0, "NPM_INSTALL_FAILED", "/goppi_tarball")
    summary["npm_resolution"] = npm_resolution(npm_root / "package-lock.json")
    goppi = installed_cli(npm_root, package)
    summary["goppi_package"].update(
        manifest_sha256=package.manifest_sha256,
        cli_target=package.target,
        cli_sha256=package.cli_sha256,
        installed_bytes_match_tarball=True,
    )

    venv, uv_path = work / "venv", str(Path(uv).resolve())
    require(
        run([uv_path, "venv", "--python", sys.executable, str(venv)], work, env).returncode == 0,
        "UV_VENV_FAILED",
        "/python_environment",
    )
    python = venv / "bin/python"
    wheels = [str(snapshots["underwrite_core_wheel"]), str(snapshots["underwrite_wheel"])]
    require(
        run([uv_path, "pip", "install", "--python", str(python), *wheels], work, env).returncode
        == 0,
        "WHEEL_INSTALL_FAILED",
        "/underwrite_wheel",
    )
    require(
        run([uv_path, "pip", "check", "--python", str(python)], work, env).returncode == 0,
        "PYTHON_RESOLUTION_INVALID",
        "/python_resolution",
    )
    summary["python_resolution"] = python_resolution(python, work, env)
    underwrite = venv / "bin/underwrite"
    require(underwrite.is_file(), "UNDERWRITE_CLI_MISSING", "/underwrite_wheel")
    return InstalledPair(work, node, goppi, underwrite, env, package, npm_root)


def accept(args: argparse.Namespace, summary: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix="underwrite-goppi-installed-pair-") as raw_work:
        work = Path(raw_work).resolve()
        snapshots, summary["artifacts"] = snapshot_artifacts(
            {
                "goppi_tarball": args.goppi_tarball,
                "underwrite_wheel": args.underwrite_wheel,
                "underwrite_core_wheel": args.underwrite_core_wheel,
                "node_executable": args.node,
            },
            work,
        )
        expected = cast(str, args.goppi_tarball_sha256)
        summary["goppi_package"] = {"tarball_sha256_expected": expected}
        require(
            summary["artifacts"]["goppi_tarball"]["sha256"] == expected,
            "GOPPI_TARBALL_DIGEST_MISMATCH",
            "/goppi_tarball",
            "snapshot",
            "CORRECT_ARGUMENTS",
        )
        exchange, pins = snapshot_fixture(work)
        exchange_v2, rejections_v2, pins_v2, export_dir_hash = snapshot_fixture_v2(work)
        require(
            summary["artifacts"]["goppi_tarball"]["sha256"]
            == "sha256:" + pins_v2["goppi_tarball_sha256"],
            "GOPPI_TARBALL_SHA256_MISMATCH",
            "/goppi_tarball",
            "snapshot",
            "CORRECT_ARGUMENTS",
        )
        summary["fixture"] = {
            "schema": pins["schema"],
            "member_count": len(pins["members"]),
            "source_commit": pins["source_commit"],
        }
        summary["fixture_v2"] = {
            "schema": pins_v2["schema"],
            "member_count": len(pins_v2["members"]),
            "producer": pins_v2["producer"],
            "export_build_dir_hash": export_dir_hash,
        }
        node = snapshots["node_executable"]
        env = isolated_env(work, node)
        version = run([str(node), "--version"], work, env)
        node_version = version.stdout.decode().strip()
        require(
            version.returncode == 0 and node_version.startswith("v24."),
            "NODE_VERSION_UNSUPPORTED",
            "/node_executable",
        )
        summary["node_version"] = node_version
        pair = install(work, snapshots, node, env, summary)
        package_root = pair.npm_root / "node_modules" / Path(*pair.package.name.split("/"))
        summary["package_tree"] = compare_package_trees(
            snapshots["goppi_tarball"], package_root, pair.package, export_dir_hash
        )
        summary["checks"] = checked_exercise(work, exchange, node, pair, env)
        summary["checks"].extend(
            exercise_v2(pair, exchange_v2, rejections_v2, export_dir_hash)
        )
        require(pins["schema"] == CONTRACTS["fixture_pins"], "FIXTURE_SCHEMA_DRIFT", "/fixture")
        installed_cli(pair.npm_root, pair.package)
        summary["contracts"] = dict(CONTRACTS)
        summary["trust_limits"] = dict(TRUST_LIMITS)



def expected_sha256(value: str) -> str:
    if SHA256.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("expected sha256:<64 lowercase hex digits>")
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--goppi-tarball", required=True, type=Path)
    result.add_argument(
        "--goppi-tarball-sha256",
        required=True,
        type=expected_sha256,
        help="digest of the tarball built from the accepted source commit",
    )
    result.add_argument("--underwrite-wheel", required=True, type=Path)
    result.add_argument("--underwrite-core-wheel", required=True, type=Path)
    result.add_argument("--node", required=True, type=Path, help="explicit Node 24 executable")
    result.add_argument("--report", type=Path, help="optional JSON summary path")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    summary: dict[str, Any] = {
        "schema": "underwrite.goppi_installed_pair_acceptance.v2",
        "status": "failed",
        "artifacts": {},
        "checks": [],
        "registry_network": {
            "policy": "allowed_for_dependency_resolution",
            "registries": ["registry.npmjs.org", "pypi.org"],
            "offline_claimed": False,
        },
        "runtime_isolation": {
            "provider_credentials_forwarded": False,
            "npm_config_isolated": True,
            "uv_config_isolated": True,
            "python_downloads_allowed": False,
            "network_sandboxed": False,
        },
    }
    try:
        accept(args, summary)
        validate_success_summary(summary)
    except Failure as exc:
        summary["failure"], code = exc.payload(), 1
    except (OSError, KeyError, TypeError, ValueError):
        summary["failure"], code = Failure("ACCEPTANCE_INTERNAL_ERROR").payload(), 1
    else:
        summary["status"], code = "passed", 0
    encoded = json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n"
    if args.report:
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(encoded, encoding="utf-8")
        except OSError:
            summary["status"] = "failed"
            summary["failure"] = Failure(
                "REPORT_WRITE_FAILED", "/report", "report", "CORRECT_ARGUMENTS"
            ).payload()
            encoded, code = json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n", 1
    (sys.stdout if code == 0 else sys.stderr).write(encoded)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

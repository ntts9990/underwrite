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
from typing import IO, Any, NamedTuple, cast

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/conformance/goppi-trajectory"
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
    "native_verification": "goppi.paired_run_verification_result.v2",
    "underwrite_review": "goppi_trajectory_review.v3",
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


def error_reason(result: subprocess.CompletedProcess[bytes], phase: str) -> str:
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
                return selected
    raise Failure("TYPED_ERROR_MISSING", "/process/output", phase, "INSPECT_PROCESS_OUTPUT")


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
    goppi: Path
    underwrite: Path
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
        and package["installed_bytes_match_tarball"] is True
        and isinstance(package["cli_target"], str)
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
    return InstalledPair(goppi, underwrite, package, npm_root)


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
        summary["fixture"] = {
            "schema": pins["schema"],
            "member_count": len(pins["members"]),
            "source_commit": pins["source_commit"],
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
        summary["checks"] = checked_exercise(work, exchange, node, pair, env)
        require(pins["schema"] == CONTRACTS["fixture_pins"], "FIXTURE_SCHEMA_DRIFT", "/fixture")
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
        "schema": "underwrite.goppi_installed_pair_acceptance.v1",
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

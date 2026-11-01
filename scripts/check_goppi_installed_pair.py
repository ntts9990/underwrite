#!/usr/bin/env python3
"""Accept installed Goppi and Underwrite artifacts against a pinned trajectory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

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
    "underwrite_review": "goppi_trajectory_review.v2",
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
) -> tuple[Path, Path]:
    npm, uv = shutil.which("npm"), shutil.which("uv")
    if npm is None:
        raise Failure("NPM_UNAVAILABLE", "/npm", "setup", "INSTALL_NPM")
    if uv is None:
        raise Failure("UV_UNAVAILABLE", "/uv", "setup", "INSTALL_UV")
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
    goppi = npm_root / "node_modules/@ntts9990/goppi/dist/cli.mjs"
    require(goppi.is_file(), "GOPPI_CLI_MISSING", "/goppi_tarball")

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
    return goppi, underwrite


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
        goppi, underwrite = install(work, snapshots, node, env, summary)
        summary["checks"] = exercise(work, exchange, node, goppi, underwrite, env)
        require(pins["schema"] == CONTRACTS["fixture_pins"], "FIXTURE_SCHEMA_DRIFT", "/fixture")
        summary["contracts"] = dict(CONTRACTS)
        summary["trust_limits"] = dict(TRUST_LIMITS)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--goppi-tarball", required=True, type=Path)
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
    except Failure as exc:
        summary["failure"], code = exc.payload(), 1
    except (OSError, KeyError, TypeError, ValueError):
        summary["failure"], code = Failure("ACCEPTANCE_INTERNAL_ERROR").payload(), 1
    else:
        validate_success_summary(summary)
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

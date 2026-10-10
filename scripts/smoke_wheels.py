"""Exercise the documented CLI flow using only locally built, installed wheels."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
COMMAND_TIMEOUT = 120
EVIDENCE_ERROR_EXIT = 2


class SmokeFailure(Exception):
    """A wheel or installed CLI did not satisfy the release smoke contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def _wheels(dist: Path) -> tuple[Path, Path]:
    _require(dist.is_dir(), f"distribution directory is missing: {dist}")
    pairs: list[Path] = []
    for pattern in ("underwrite_core-*.whl", "underwrite-*.whl"):
        found = sorted(dist.glob(pattern))
        _require(len(found) == 1, f"expected one {pattern} in {dist}; found {len(found)}")
        pairs.append(found[0])
    return pairs[0], pairs[1]


def _command(
    args: list[str], cwd: Path, *, project_environment: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(name, None)
    if project_environment is not None:
        env["UV_PROJECT_ENVIRONMENT"] = str(project_environment)
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise SmokeFailure(f"command timed out after {COMMAND_TIMEOUT}s: {args[0]}") from exc


def _success(
    args: list[str], cwd: Path, *, project_environment: Path | None = None
) -> subprocess.CompletedProcess[str]:
    result = _command(args, cwd, project_environment=project_environment)
    _require(
        result.returncode == 0,
        f"command failed ({result.returncode}): {args[0]}: {result.stderr[:500]}",
    )
    return result


def _cli_json(cli: Path, args: list[str], output: Path, cwd: Path) -> dict[str, object]:
    result = _success([str(cli), *args, "--json"], cwd)
    _require(result.stderr == "", f"unexpected CLI stderr: {result.stderr[:500]}")
    try:
        document: object = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"invalid CLI JSON for {args[0]}") from exc
    _require(isinstance(document, dict), f"CLI JSON is not an object for {args[0]}")
    output.write_text(result.stdout, encoding="utf-8")
    return cast(dict[str, object], document)


def _install_wheels(work: Path, venv: Path, wheels: tuple[Path, Path]) -> None:
    python = venv / "bin/python"
    _success(
        [
            "uv",
            "sync",
            "--frozen",
            "--no-default-groups",
            "--no-install-workspace",
            "--project",
            str(ROOT),
            "--python",
            sys.executable,
        ],
        work,
        project_environment=venv,
    )
    _success(
        [
            "uv",
            "pip",
            "install",
            "--offline",
            "--no-deps",
            "--python",
            str(python),
            *(str(wheel) for wheel in wheels),
        ],
        work,
        project_environment=venv,
    )
    _success(["uv", "pip", "check", "--python", str(python)], work, project_environment=venv)


def _check_wheel_origins(venv: Path, wheels: tuple[Path, Path]) -> None:
    site = (
        venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    )
    for distribution, wheel in zip(("underwrite_core", "underwrite"), wheels, strict=True):
        records = list(site.glob(f"{distribution}-*.dist-info/direct_url.json"))
        _require(len(records) == 1, f"expected one installed {distribution} origin record")
        origin: object = json.loads(records[0].read_text(encoding="utf-8"))
        _require(isinstance(origin, dict), f"invalid {distribution} origin record")
        record = cast(dict[str, object], origin)
        _require(
            record.get("url") == wheel.as_uri() and "dir_info" not in record,
            f"{distribution} was not installed from its built wheel",
        )


def _evidence_pilots(cli: Path, fixtures: Path, work: Path) -> None:
    evidence = fixtures / "evidence"
    audit = _cli_json(
        cli,
        ["audit-counts", str(evidence / "synthetic-promptfoo-category-mismatch.case.json")],
        work / "audit.json",
        work,
    )
    _require(audit.get("declared_relation") == "inconsistent", "count mismatch was hidden")
    comparison = _cli_json(
        cli,
        [
            "compare-declarations",
            "--baseline",
            str(evidence / "baseline-accounting.json"),
            "--candidate",
            str(evidence / "candidate-accounting.json"),
            "--manifest",
            str(evidence / "comparison-declarations.json"),
        ],
        work / "comparison.json",
        work,
    )
    _require(comparison.get("declared_relation") == "different", "declaration mismatch hidden")
    _require(comparison.get("actual_conditions_verified") is False, "unverified conditions")
    for fixture, expected in (("complete", "measured"), ("incomplete", "not_measured")):
        paired = _cli_json(
            cli,
            [
                "pair-binary",
                "--input",
                str(evidence / f"synthetic-paired-{fixture}.json"),
                "--policy",
                str(evidence / "paired-binary-policy.json"),
            ],
            work / f"paired-{fixture}.json",
            work,
        )
        _require(paired.get("schema") == "paired_binary_evidence.v1", "paired schema mismatch")
        _require(paired.get("status") == expected, "paired missingness was hidden")
    error = _command([str(cli), "pair-binary", "--json"], work)
    _require(
        error.returncode == EVIDENCE_ERROR_EXIT and error.stdout == "",
        "pilot error channel mismatch",
    )
    diagnostic = json.loads(error.stderr)
    _require(diagnostic.get("schema") == "evidence_error.v1", "pilot error schema mismatch")


def _smoke(dist: Path) -> None:
    core_wheel, app_wheel = _wheels(dist)
    fixtures = ROOT / "fixtures/examples"
    with tempfile.TemporaryDirectory(prefix="underwrite-wheel-smoke-") as directory:
        work = Path(directory).resolve()
        _require(not work.is_relative_to(ROOT), "temporary directory must be outside checkout")
        venv = work / "venv"
        cli = venv / "bin/underwrite"
        wheels = (core_wheel, app_wheel)
        _install_wheels(work, venv, wheels)
        _check_wheel_origins(venv, wheels)
        _evidence_pilots(cli, fixtures, work)

        observation = work / "observation.json"
        observed = _cli_json(
            cli,
            [
                "ingest",
                "--format",
                "underwrite.evidence-bundle",
                "--version",
                "v1",
                "--file",
                str(fixtures / "measurement/bundle.json"),
            ],
            observation,
            work,
        )
        _require(observed.get("schema") == "observation.v1", "ingest schema mismatch")
        _require(
            observed.get("source")
            == {"format": "underwrite.evidence-bundle", "format_version": "v1"},
            "ingest source mismatch",
        )

        reads: list[Path] = []
        for subject in ("quality", "latency"):
            output = work / f"read-{subject}.json"
            read = _cli_json(
                cli,
                [
                    "measure",
                    "--observation",
                    str(observation),
                    "--policy",
                    str(fixtures / f"acceptance/policy-{subject}.json"),
                ],
                output,
                work,
            )
            _require(read.get("schema") == "read.v1", f"{subject} read schema mismatch")
            _require(
                read.get("subject") == {"subject_id": f"suite.{subject}", "conditions": {}},
                f"{subject} read subject mismatch",
            )
            _require(read.get("verdict") == "pass", f"{subject} read did not pass")
            reads.append(output)

        decision = _cli_json(
            cli,
            [
                "accept",
                "--candidate",
                str(fixtures / "acceptance/candidate.json"),
                "--claims",
                str(fixtures / "acceptance/claims.json"),
                "--read",
                str(reads[0]),
                "--read",
                str(reads[1]),
            ],
            work / "accept.json",
            work,
        )
        _require(decision.get("schema") == "acceptance_decision.v1", "decision schema mismatch")
        _require(decision.get("classification") == "screened", "classification mismatch")
        _require(decision.get("human_review") == "required", "human review mismatch")
        _require(decision.get("merge_authorized") is False, "merge authorization mismatch")

        malformed = work / "malformed-bundle.json"
        malformed.write_text(
            '{"schema_version":"underwrite.evidence-bundle.v1","run_id":"",'
            '"sources":[],"readings":[],"availability":[]}\n',
            encoding="utf-8",
        )
        error = _command(
            [
                str(cli),
                "ingest",
                "--format",
                "underwrite.evidence-bundle",
                "--version",
                "v1",
                "--file",
                str(malformed),
                "--json",
            ],
            work,
        )
        _require(error.returncode == 1, f"malformed ingest exit was {error.returncode}")
        _require(error.stdout == "", "malformed ingest wrote stdout")
        try:
            diagnostic: object = json.loads(error.stderr)
        except json.JSONDecodeError as exc:
            raise SmokeFailure("malformed ingest did not write JSON stderr") from exc
        _require(
            diagnostic
            == {
                "schema": "instrument_error.v1",
                "command": "ingest",
                "code": "INVALID_INPUT",
                "reason": "MALFORMED_EVIDENCE_BUNDLE_PAYLOAD",
                "exit_code": 1,
            },
            "malformed ingest diagnostic mismatch",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", required=True, type=Path, help="Directory with both built wheels")
    args = parser.parse_args(argv)
    try:
        _smoke(args.dist.resolve())
    except (SmokeFailure, OSError, json.JSONDecodeError) as exc:
        print(f"wheel smoke failed: {exc}", file=sys.stderr)
        return 1
    print("installed wheel native/evidence flows and malformed-input smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

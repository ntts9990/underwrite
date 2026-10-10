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


def _command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(name, None)
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


def _success(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    result = _command(args, cwd)
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


def _smoke(dist: Path) -> None:
    core_wheel, app_wheel = _wheels(dist)
    fixtures = ROOT / "fixtures/examples"
    with tempfile.TemporaryDirectory(prefix="underwrite-wheel-smoke-") as directory:
        work = Path(directory).resolve()
        _require(not work.is_relative_to(ROOT), "temporary directory must be outside checkout")
        venv = work / "venv"
        python = venv / "bin/python"
        cli = venv / "bin/underwrite"
        _success(["uv", "venv", "--python", sys.executable, str(venv)], work)
        _success(
            [
                "uv",
                "pip",
                "install",
                "--offline",
                "--python",
                str(python),
                str(core_wheel),
                str(app_wheel),
            ],
            work,
        )
        _success(["uv", "pip", "check", "--python", str(python)], work)

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
    except (SmokeFailure, OSError) as exc:
        print(f"wheel smoke failed: {exc}", file=sys.stderr)
        return 1
    print("installed wheel quickstart and malformed-input smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

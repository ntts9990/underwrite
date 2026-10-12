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
STORAGE_ERROR_EXIT = 3


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
    for fixture, expected in (
        ("paired-complete", "measured"),
        ("paired-incomplete", "not_measured"),
        ("observed-and-missing", "not_measured"),
    ):
        paired = _cli_json(
            cli,
            [
                "pair-binary",
                "--input",
                str(evidence / f"synthetic-{fixture}.json"),
                "--policy",
                str(evidence / "paired-binary-policy.json"),
            ],
            work / f"paired-{fixture}.json",
            work,
        )
        _require(paired.get("schema") == "paired_binary_evidence.v1", "paired schema mismatch")
        _require(paired.get("status") == expected, "paired missingness was hidden")
        if fixture == "observed-and-missing":
            accounting = cast(dict[str, object], paired["accounting"])
            examples = cast(list[dict[str, object]], accounting["observed_reason_examples"])
            _require(len(examples) == 1, "observed reason was lost")
            _require(examples[0]["outcome"] == 0, "observed zero was recoded")
            _require(examples[0]["source_claim_unverified"] is True, "source reason promoted")
    review = _cli_json(
        cli,
        [
            "review-pair",
            "--input",
            str(evidence / "synthetic-paired-complete.json"),
            "--policy",
            str(evidence / "paired-binary-policy.json"),
            "--context",
            str(evidence / "synthetic-review-partition.json"),
        ],
        work / "paired-review.json",
        work,
    )
    _require(review.get("schema") == "paired_review.v1", "review schema mismatch")
    _require(review.get("actual_conditions_verified") is False, "review verified source claims")
    reference = cast(dict[str, object], review["paired_evidence_ref"])
    _require(reference.get("status") == "measured", "review lost measured status")
    history = cast(dict[str, object], review["evaluation_history"])
    statuses = cast(dict[str, object], history["selected_statuses"])
    _require(statuses.get("baseline") == "invalidated", "history invalidation was lost")
    partition = cast(dict[str, object], review["case_partition"])
    groups = cast(list[dict[str, object]], partition["groups"])
    _require(groups[-1]["category"] is None, "unknown category was lost")
    _require(groups[0]["case_count"] == 0, "empty category was lost")
    error = _command([str(cli), "pair-binary", "--json"], work)
    _require(
        error.returncode == EVIDENCE_ERROR_EXIT and error.stdout == "",
        "pilot error channel mismatch",
    )
    diagnostic = json.loads(error.stderr)
    _require(diagnostic.get("schema") == "evidence_error.v1", "pilot error schema mismatch")


def _record_pilots(cli: Path, fixtures: Path, work: Path) -> None:
    installed_capabilities = _cli_json(cli, ["capabilities"], work / "capabilities.json", work)
    checkout_capabilities = _success(
        [sys.executable, "-m", "underwrite.cli.main", "capabilities", "--json"], ROOT
    )
    _require(
        installed_capabilities == json.loads(checkout_capabilities.stdout),
        "checkout and installed capabilities differ",
    )
    audit = _cli_json(
        cli,
        ["audit-run", "--input", str(fixtures / "run-audit/synthetic-inventory.json")],
        work / "run-audit.json",
        work,
    )
    _require(audit.get("schema") == "binary_run_audit.v1", "run audit schema mismatch")
    store = work / "records-store"
    saved = _cli_json(
        cli,
        [
            "records",
            "save",
            "--root",
            str(store),
            "--spec",
            str(fixtures / "records/record-spec.json"),
        ],
        work / "saved.json",
        work,
    )
    _require(saved.get("schema") == "record_view.v1", "record view schema mismatch")
    shown = _cli_json(
        cli,
        ["records", "show", "--root", str(store), "--id", "synthetic.capture-001"],
        work / "shown.json",
        work,
    )
    _require(saved == shown, "installed record save/show changed manifest")
    checked = _cli_json(
        cli,
        ["records", "verify", "--root", str(store), "--id", "synthetic.capture-001"],
        work / "verified.json",
        work,
    )
    _require(checked.get("valid") is True, "installed record verification failed")
    listing = _cli_json(cli, ["records", "list", "--root", str(store)], work / "catalog.json", work)
    _require(listing.get("record_count") == 1, "installed record catalog mismatch")


def _handoff_pilot(cli: Path, work: Path) -> None:
    source = work / "handoff-source"
    _success(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; "
            "from tests.drivers.goppi_p7_fixture import build_goppi_p7_fixture; "
            "build_goppi_p7_fixture().write(Path(sys.argv[1])); "
            "build_goppi_p7_fixture(((True, True),) * 22).write(Path(sys.argv[2]))",
            str(source),
            str(work / "larger-handoff-source"),
        ],
        ROOT,
    )
    store = work / "handoff-store"
    report = _cli_json(
        cli,
        [
            "review-handoff",
            "--handoff",
            str(source / "study/paired-analysis-input"),
            "--source-root",
            str(source),
            "--record-root",
            str(store),
            "--record-id",
            "synthetic.handoff",
        ],
        work / "handoff-result.json",
        work,
    )
    analysis = cast(dict[str, object], report["analysis"])
    summary = cast(dict[str, object], analysis["summary"])
    _require(summary["measurement_status"] == "not_measured", "handoff fabricated measurement")
    _require(summary["conditions_coverage"] == "unknown", "handoff fabricated conditions")
    saved = cast(dict[str, object], report["storage"])
    _require(saved["state"] == "saved", "installed handoff capture failed")
    verification = _cli_json(
        cli,
        ["records", "verify", "--root", str(store), "--id", "synthetic.handoff"],
        work / "handoff-verified.json",
        work,
    )
    _require(verification["valid"] is True, "installed handoff record is damaged")

    larger_source = work / "larger-handoff-source"
    larger_args = [
        "review-handoff",
        "--handoff",
        str(larger_source / "study/paired-analysis-input"),
        "--source-root",
        str(larger_source),
    ]
    larger = _cli_json(cli, larger_args, work / "larger-handoff.json", work)
    _require(
        cast(dict[str, object], larger["analysis"])["state"] == "result",
        "installed analysis inherited storage capacity",
    )
    refused_store = work / "refused-handoff-store"
    refused = _command(
        [
            str(cli),
            *larger_args,
            "--record-root",
            str(refused_store),
            "--record-id",
            "too-large",
            "--json",
        ],
        work,
    )
    _require(
        refused.returncode == STORAGE_ERROR_EXIT and refused.stderr == "",
        "wrong storage refusal exit",
    )
    refusal = json.loads(refused.stdout)
    _require(refusal["analysis"] == larger["analysis"], "storage refusal lost analysis")
    _require(
        refusal["storage"]["error"]["code"] == "INPUT_LIMIT_EXCEEDED",
        "wrong storage refusal diagnostic",
    )
    _require(not refused_store.exists(), "oversized capture mutated store")


def _normalization_pilot(cli: Path, work: Path) -> None:
    source = work / "normalization-source.json"
    source.write_text(
        json.dumps(
            {
                "testCases": [
                    {
                        "name": "synthetic",
                        "input": "local",
                        "success": False,
                        "metricsData": [
                            {"name": "example", "threshold": 0.5, "success": False, "score": -0.0}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    observed = _cli_json(
        cli,
        ["ingest", "--format", "deepeval.test-run", "--version", "4.1.1", "--file", str(source)],
        work / "normalization-cli.json",
        work,
    )
    library = _success(
        [
            str(cli.parent / "python"),
            "-c",
            "import json, sys; from pathlib import Path; "
            "from underwrite.instrument.ingest.application import packaged_ingestor; "
            "from underwrite.instrument.ingest.project import Source; "
            "result = packaged_ingestor(max_bytes=4096, max_depth=16).file("
            "Path(sys.argv[1]), Source('deepeval.test-run', '4.1.1', None)); "
            "print(json.dumps(result.observation, sort_keys=True))",
            str(source),
        ],
        work,
    )
    _require(library.stderr == "", "installed library emitted unexpected diagnostics")
    _require(
        library.stdout.strip() == json.dumps(observed, sort_keys=True),
        "installed API and CLI disagree on normalized JSON value representation",
    )
    payload = cast(dict[str, object], observed["payload"])
    cases = cast(list[dict[str, object]], payload["testCases"])
    metric = cast(list[dict[str, object]], cases[0]["metricsData"])[0]
    _require(
        type(metric["score"]) is int and metric["score"] == 0,
        "canonical negative zero was not normalized",
    )
    _require(
        metric["success"] is False and "error" not in metric,
        "normalization merged boolean, score, or field absence",
    )


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
        _record_pilots(cli, fixtures, work)
        _handoff_pilot(cli, work)
        _normalization_pilot(cli, work)

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

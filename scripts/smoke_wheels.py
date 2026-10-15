"""Exercise the documented CLI flow using only locally built, installed wheels."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
COMMAND_TIMEOUT = 120
EVIDENCE_ERROR_EXIT = 2
STORAGE_ERROR_EXIT = 3
BASELINE_RECORD_FILE_COUNT = 7


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


def _promptfoo_numeric_pilot(
    cli: Path, evidence: Path, work: Path, baseline: dict[str, object]
) -> None:
    body = json.loads((evidence / "synthetic-promptfoo-category-mismatch.json").read_bytes())
    for name in ("successes", "failures", "errors"):
        body["results"]["stats"][name] = float(body["results"]["stats"][name])
    for row in body["results"]["results"]:
        for name in ("promptIdx", "testIdx"):
            row[name] = float(row[name])
    source = work / "promptfoo-integral-counts.json"
    source.write_text(json.dumps(body), encoding="utf-8")
    manifest = work / "promptfoo-integral-counts.case.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "artifact_case.v1",
                "artifact": {"path": source.name},
                "source": {"format": "promptfoo.eval-output", "format_version": "0.123.0"},
            }
        ),
        encoding="utf-8",
    )
    observed = _cli_json(
        cli,
        [
            "ingest",
            "--format",
            "promptfoo.eval-output",
            "--version",
            "0.123.0",
            "--file",
            str(source),
        ],
        work / "promptfoo-integral-observation.json",
        work,
    )
    audited = _cli_json(
        cli,
        ["audit-counts", str(manifest)],
        work / "promptfoo-integral-audit.json",
        work,
    )
    _cli_json(
        cli,
        ["audit-promptfoo", str(source)],
        work / "promptfoo-integral-direct.json",
        work,
    )
    _require(
        (work / "promptfoo-integral-direct.json").read_bytes()
        == (work / "promptfoo-integral-audit.json").read_bytes(),
        "raw and manifest canonical output differs",
    )
    for key in baseline:
        if key in {"raw_hash", "observation_digest"}:
            _require(audited[key] != baseline[key], "different raw sources lost their identity")
        else:
            _require(audited[key] == baseline[key], "integral count spelling changed accounting")
    raw = cast(dict[str, object], observed["raw"])
    _require(raw["hash"] == audited["raw_hash"], "ingest/audit consumed different source bytes")


def _promptfoo_record_pilot(
    cli: Path, original: Path, work: Path, expected: dict[str, object], *, version: str = "0.123.0"
) -> None:
    raw = original.read_bytes()
    source = work / "selected-promptfoo.json"
    source.write_bytes(raw)
    selection = [] if version == "0.123.0" else ["--version", version]
    store = work / "promptfoo-records"
    report = _cli_json(
        cli,
        [
            "records",
            "audit-promptfoo",
            str(source),
            "--root",
            str(store),
            "--id",
            "captured",
            *selection,
        ],
        work / "promptfoo-record.json",
        work,
    )
    analysis = cast(dict[str, object], report["analysis"])
    storage = cast(dict[str, object], report["storage"])
    _require(analysis["report"] == expected, "record capture changed accounting")
    _require(storage["state"] == "saved", "record capture did not save")
    _require(
        cast(dict[str, object], storage["verification"])["valid"] is True,
        "record capture did not verify",
    )
    _require(
        (store / "records/captured/files/source/promptfoo.bin").read_bytes() == raw,
        "captured source differs from the selected bytes",
    )
    source.unlink()
    recovered = _cli_json(
        cli,
        [
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "captured",
            "--name",
            "analysis/accounting.json",
        ],
        work / "recovered-accounting.json",
        work,
    )
    _require(recovered["content"] == expected, "selected read changed the saved result")
    _require(
        recovered["manifest_digest"] == storage["manifest_digest"]
        and recovered["verification_scope"] == "selected_file_against_manifest",
        "selected read misrepresented its identity or scope",
    )
    malformed = work / "malformed-promptfoo.json"
    malformed.write_bytes(b"{")
    failed = _command(
        [
            str(cli),
            "records",
            "audit-promptfoo",
            str(malformed),
            "--root",
            str(store),
            "--id",
            "diagnostic",
            *selection,
            "--json",
        ],
        work,
    )
    _require(
        failed.returncode == EVIDENCE_ERROR_EXIT and failed.stderr == "",
        "wrong saved-diagnostic exit",
    )
    diagnostic = json.loads(failed.stdout)
    _require(
        diagnostic["analysis"]["state"] == "error" and diagnostic["storage"]["state"] == "saved",
        "saved analysis failure lost independent outcomes",
    )
    _require(
        (store / "records/diagnostic/files/source/promptfoo.bin").read_bytes() == b"{",
        "malformed complete source was not retained",
    )
    recovered_error = _cli_json(
        cli,
        [
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "diagnostic",
            "--name",
            "analysis/error.json",
        ],
        work / "recovered-error.json",
        work,
    )
    _require(
        recovered_error["content"] == diagnostic["analysis"]["error"],
        "selected read replaced the saved diagnostic",
    )
    if selection:
        invocation = _cli_json(
            cli,
            [
                "records",
                "read-json",
                "--root",
                str(store),
                "--id",
                "diagnostic",
                "--name",
                "invocation.json",
            ],
            work / "recovered-invocation.json",
            work,
        )
        content = cast(dict[str, object], invocation["content"])
        _require(
            cast(dict[str, object], content["arguments"])["format_version"] == version,
            "saved Promptfoo diagnostic lost its selected version",
        )


def _promptfoo_manifest_pilot(cli: Path, directory: Path, raw: bytes) -> None:
    source = directory / "manifest-source.json"
    source.write_bytes(raw)
    expected_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
    artifact = {"path": source.name, "expected_hash": expected_hash}
    manifest = {
        "schema": "artifact_case.v1",
        "artifact": artifact,
        "source": {
            "format": "promptfoo.eval-output",
            "format_version": "0.124.1",
            "ref": "synthetic:manifest-export",
            "locator": "synthetic:manifest-locator",
        },
    }
    case = directory / "case.json"
    case.write_text(json.dumps(manifest), encoding="utf-8")
    inspected = _cli_json(cli, ["inspect", str(case)], directory / "inspection.json", directory)
    counted = _cli_json(
        cli, ["audit-counts", str(case)], directory / "manifest-audit.json", directory
    )
    _require(
        counted["raw_hash"] == expected_hash
        and counted["observation_digest"]
        == cast(dict[str, object], inspected["observation"])["digest"]
        and counted["row_tallies"] == {"successes": 3, "failures": 1, "errors": 3, "total": 7},
        "manifest audit lost hash binding, explicit source identity or counts",
    )
    artifact["expected_hash"] = "sha256:" + "0" * 64
    case.write_text(json.dumps(manifest), encoding="utf-8")
    refused = _command([str(cli), "audit-counts", str(case), "--json"], directory)
    _require(
        refused.returncode == EVIDENCE_ERROR_EXIT
        and refused.stdout == ""
        and json.loads(refused.stderr)["code"] == "EXPECTED_HASH_MISMATCH",
        "manifest count audit accepted mismatching artifact bytes",
    )


def _promptfoo_current_pilot(cli: Path, work: Path) -> None:
    directory = work / "promptfoo-current"
    directory.mkdir()
    source = ROOT / "fixtures/golden/external/promptfoo-0.124.1/results.json"
    raw = source.read_bytes()
    observation = directory / "observation.json"
    projected = _cli_json(
        cli,
        [
            "ingest",
            "--format",
            "promptfoo.eval-output",
            "--version",
            "0.124.1",
            "--file",
            str(source),
        ],
        observation,
        directory,
    )
    _require(projected["payload"] == json.loads(raw), "new profile changed source")
    report = _cli_json(
        cli,
        ["audit-promptfoo", str(source), "--version", "0.124.1"],
        directory / "accounting.json",
        directory,
    )
    expected = {"successes": 3, "failures": 1, "errors": 3, "total": 7}
    _require(
        report["row_tallies"] == report["producer_stats"] == expected
        and report["declared_relation"] == "consistent",
        "current Promptfoo capture changed declared category meaning",
    )
    row_view = _cli_json(
        cli,
        ["audit-promptfoo", str(source), "--version", "0.124.1", "--view", "row-index"],
        directory / "row-index.json",
        directory,
    )
    selected_rows = row_view.get("rows")
    _require(isinstance(selected_rows, list), "installed Promptfoo row index did not return rows")
    source_rows = cast(list[object], selected_rows)
    _require(
        all(isinstance(row, dict) for row in source_rows),
        "installed Promptfoo row index contains non-object rows",
    )
    rows = cast(list[dict[str, object]], source_rows)
    _require(
        row_view.get("schema") == "promptfoo_row_index.v1"
        and row_view.get("raw_hash") == "sha256:" + hashlib.sha256(raw).hexdigest()
        and [row.get("row_index") for row in rows] == list(range(expected["total"]))
        and [row.get("category") for row in rows]
        == ["successes", "failures", "errors", "errors", "successes", "errors", "successes"]
        and all(
            set(row)
            == {"row_index", "category", "source_success", "source_failure_reason", "grading_pass"}
            for row in rows
        )
        and row_view.get("verification") == [],
        "installed Promptfoo row index changed selection, identity or content boundary",
    )
    _promptfoo_record_pilot(cli, source, directory, report, version="0.124.1")
    _promptfoo_manifest_pilot(cli, directory, raw)
    refused = _command(
        [
            str(cli),
            "measure",
            "--observation",
            str(observation),
            "--policy",
            str(ROOT / "fixtures/examples/event-acceptance/policy.json"),
            "--json",
        ],
        directory,
    )
    _require(
        refused.returncode == EVIDENCE_ERROR_EXIT
        and refused.stdout == ""
        and json.loads(refused.stderr)["code"] == "UNSUPPORTED_PROFILE",
        "Promptfoo version admission bypassed native measurement boundary",
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
    _promptfoo_numeric_pilot(cli, evidence, work, audit)
    _promptfoo_record_pilot(
        cli, evidence / "synthetic-promptfoo-category-mismatch.json", work, audit
    )
    _promptfoo_current_pilot(cli, work)
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
    commands = cast(list[dict[str, object]], installed_capabilities["commands"])
    for command, name, fixture, version, schema in (
        (
            "audit-deepeval",
            "deepeval.test-run|4.2.8",
            "deepeval-4.2.8/ordinary.json",
            "4.2.8",
            "deepeval_accounting.v2",
        ),
        (
            "audit-promptfoo",
            "promptfoo.eval-output|0.124.1",
            "promptfoo-0.124.1/results.json",
            "0.124.1",
            "promptfoo_accounting.v1",
        ),
    ):
        descriptor = next(item for item in commands if item["command"] == command)
        profiles = cast(list[dict[str, object]], descriptor["profiles"])
        selected = next(item for item in profiles if item["name"] == name)
        _require(
            selected["required_inputs"] == ["file", "version"]
            and selected["output_schemas"] == [schema],
            f"installed {name} capability selector changed",
        )
        required = cast(list[str], selected["required_inputs"])
        args = [command, str(ROOT / "fixtures/golden/external" / fixture)]
        if "version" in required:
            args.extend(("--version", version))
        observed = _cli_json(cli, args, work / f"{command}-advertised.json", work)
        source = cast(dict[str, object], observed["source"])
        _require(
            observed["schema"] == schema and source["format_version"] == version,
            f"installed {name} capability selected the wrong source profile",
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


def _deepeval_record_pilot(
    cli: Path, work: Path, source: Path, expected: dict[str, object], *, version: str = "4.1.1"
) -> None:
    selected = work / "deepeval-selected.json"
    raw = source.read_bytes()
    selected.write_bytes(raw)
    store = work / "deepeval-records"
    arguments = ["records", "audit-deepeval", str(selected), "--root", str(store)]
    if version != "4.1.1":
        arguments.extend(["--version", version])
    report = _cli_json(cli, [*arguments, "--id", "captured"], work / "deepeval-record.json", work)
    analysis = cast(dict[str, object], report["analysis"])
    storage = cast(dict[str, object], report["storage"])
    _require(analysis["report"] == expected, "DeepEval capture changed accounting")
    _require(storage["state"] == "saved", "DeepEval capture failed to save")
    _require(
        (store / "records/captured/files/source/deepeval.bin").read_bytes() == raw,
        "DeepEval source bytes changed",
    )
    selected.unlink()
    recovered = _cli_json(
        cli,
        [
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "captured",
            "--name",
            "analysis/accounting.json",
        ],
        work / "deepeval-recovered.json",
        work,
    )
    _require(recovered["content"] == expected, "DeepEval retained analysis could not be reused")
    _require(
        recovered["manifest_digest"] == storage["manifest_digest"],
        "DeepEval recovered manifest differs",
    )
    failed = _command([str(cli), *arguments, "--id", "unavailable", "--json"], work)
    _require(
        failed.returncode == EVIDENCE_ERROR_EXIT and failed.stderr == "",
        "DeepEval diagnostic capture used the wrong channel or exit",
    )
    diagnostic = json.loads(failed.stdout)
    if version != "4.1.1":
        _require(
            diagnostic["source_format_version"] == version,
            "DeepEval diagnostic lost selected producer version",
        )
    _require(
        diagnostic["source"]["state"] == "unavailable"
        and diagnostic["analysis"]["phase"] == "acquisition"
        and diagnostic["storage"]["state"] == "saved",
        "DeepEval acquisition failure lost independent outcomes",
    )


def _deepeval_pilot(cli: Path, work: Path) -> None:
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
                            {"name": "example", "threshold": 0.5, "success": False, "score": -0.0},
                            {
                                "name": "evaluator",
                                "threshold": 0.5,
                                "success": False,
                                "error": "synthetic error",
                            },
                            {"name": "integer", "threshold": 1, "success": True, "score": 1},
                        ],
                    }
                ],
                "traceMetricsScores": {
                    "agent": {},
                    "tool": {},
                    "retriever": {},
                    "llm": {},
                    "base": {},
                },
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

    accounting = _cli_json(
        cli,
        ["audit-deepeval", str(source)],
        work / "deepeval-accounting.json",
        work,
    )
    counts = cast(dict[str, dict[str, int]], accounting["counts"])
    _deepeval_record_pilot(cli, work, source, accounting)
    expected_scored = 2
    _require(
        counts["metrics"]["scored"] == expected_scored and counts["metrics"]["error"] == 1,
        "installed accounting conflated score and evaluator error",
    )
    rows = cast(list[dict[str, object]], accounting["cases"])
    error_metric = cast(list[dict[str, object]], rows[0]["metrics"])[1]
    _require(
        error_metric["state"] == "error" and "score" not in error_metric,
        "installed accounting invented a score for an error",
    )


def _deepeval_v2_pilot(cli: Path, work: Path) -> None:
    golden = ROOT / "fixtures/golden/external/deepeval-4.2.8"
    for scenario in ("ordinary", "flaky_metric", "flaky_case", "score_without_verdict"):
        directory = work / f"deepeval-v2-{scenario}"
        directory.mkdir()
        source = directory / "source.json"
        source.write_bytes((golden / f"{scenario}.json").read_bytes())
        observation = directory / "observation.json"
        _cli_json(
            cli,
            [
                "ingest",
                "--format",
                "deepeval.test-run",
                "--version",
                "4.2.8",
                "--file",
                str(source),
            ],
            observation,
            directory,
        )
        report = _cli_json(
            cli,
            ["audit-deepeval", str(source), "--version", "4.2.8"],
            directory / "accounting.json",
            directory,
        )
        _require(report["schema"] == "deepeval_accounting.v2", "wrong selected DeepEval schema")
        case = cast(list[dict[str, object]], report["cases"])[0]
        metrics = cast(list[dict[str, object]], case["metrics"])
        if scenario == "ordinary":
            _require(
                metrics[1]["score"] == 0
                and metrics[2]["state"] == "error"
                and "score" not in metrics[2],
                "DeepEval v2 collapsed zero and evaluator error",
            )
        elif scenario == "flaky_metric":
            _require(
                case["source_success"] is True
                and metrics[1]["source_flaky"] is True
                and metrics[1]["source_success"] is False,
                "DeepEval flaky metric changed independent case verdict",
            )
        elif scenario == "flaky_case":
            _require(
                case["source_flaky"] is True and case["source_success"] is False,
                "DeepEval flaky case was promoted to pass",
            )
        else:
            expected_score = 0.3
            _require(
                metrics[1]["state"] == "scored"
                and metrics[1]["score"] == expected_score
                and metrics[1]["source_success"] is None
                and metrics[1]["threshold"] is None,
                "DeepEval score-only metric acquired a verdict or lost its score",
            )
            refused = _command(
                [
                    str(cli),
                    "measure",
                    "--observation",
                    str(observation),
                    "--policy",
                    str(ROOT / "fixtures/examples/event-acceptance/policy.json"),
                    "--json",
                ],
                directory,
            )
            _require(
                refused.returncode == EVIDENCE_ERROR_EXIT
                and refused.stdout == ""
                and json.loads(refused.stderr)["code"] == "UNSUPPORTED_PROFILE",
                "DeepEval v2 ingestion bypassed native measurement boundary",
            )
        if scenario in {"flaky_case", "score_without_verdict"}:
            _deepeval_record_pilot(cli, directory, source, report, version="4.2.8")


def _langfuse_record_pilot(
    cli: Path, work: Path, observed: Path, scores: Path, expected: dict[str, object]
) -> None:
    inputs = (work / "observations.json", work / "scores.json")
    for source, copied in zip((observed, scores), inputs, strict=True):
        copied.write_bytes(source.read_bytes())
    store = work / "langfuse-records"
    arguments = [
        "records",
        "audit-langfuse",
        "--observations",
        str(inputs[0]),
        "--scores",
        str(inputs[1]),
        "--root",
        str(store),
    ]
    report = _cli_json(cli, [*arguments, "--id", "captured"], work / "langfuse-record.json", work)
    analysis = cast(dict[str, object], report["analysis"])
    storage = cast(dict[str, object], report["storage"])
    _require(analysis["report"] == expected, "Langfuse capture changed accounting")
    _require(storage["state"] == "saved", "Langfuse capture failed to save")
    for source, role in zip(inputs, ("observations", "scores"), strict=True):
        _require(
            (store / f"records/captured/files/source/{role}.bin").read_bytes()
            == source.read_bytes(),
            "Langfuse retained source differs",
        )
        source.unlink()
    recovered = _cli_json(
        cli,
        [
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "captured",
            "--name",
            "analysis/accounting.json",
        ],
        work / "langfuse-recovered.json",
        work,
    )
    _require(recovered["content"] == expected, "Langfuse stored analysis could not be reused")
    _require(
        recovered["manifest_digest"] == storage["manifest_digest"],
        "Langfuse recovered manifest differs",
    )
    failed = _command([str(cli), *arguments, "--id", "unavailable", "--json"], work)
    _require(
        failed.returncode == EVIDENCE_ERROR_EXIT and failed.stderr == "",
        "Langfuse diagnostic capture used the wrong channel or exit",
    )
    diagnostic = json.loads(failed.stdout)
    _require(
        diagnostic["analysis"]["phase"] == "acquisition"
        and diagnostic["storage"]["state"] == "saved",
        "Langfuse acquisition failure lost independent storage",
    )
    for source in diagnostic["sources"].values():
        _require(
            source["state"] == "unavailable" and "error" in source,
            "Langfuse input failure was not retained",
        )


def _langfuse_pilot(cli: Path, work: Path) -> None:
    golden = ROOT / "fixtures/golden/external/langfuse"
    observed = golden / "observations_v2-2026-09-14T06-40-00.json"
    scores = golden / "scores-2026-09-14T06-40-00.json"
    report = _cli_json(
        cli,
        ["audit-langfuse", "--observations", str(observed), "--scores", str(scores)],
        work / "langfuse-accounting.json",
        work,
    )
    counts = cast(dict[str, object], report["counts"])
    _langfuse_record_pilot(cli, work, observed, scores, report)
    _require(
        counts["by_data_type"] == {"NUMERIC": 2, "BOOLEAN": 1, "CATEGORICAL": 1, "TEXT": 1},
        "installed Langfuse score kinds changed",
    )
    _require(
        counts["associations"]
        == {
            "observation_local": 3,
            "trace_level": 2,
            "unresolved_observation_reference": 0,
        },
        "installed Langfuse associations changed",
    )
    _require(
        counts["observations_without_local_scores"] == 1,
        "trace-level scores were incorrectly attached to an observation",
    )
    _require(
        report["planned"]
        == {
            "status": "unknown",
            "count": None,
            "missing_execution_count": None,
        },
        "installed accounting fabricated planned population",
    )
    rows = cast(list[dict[str, object]], report["scores"])
    for row in rows:
        if row["data_type"] in ("TEXT", "CATEGORICAL"):
            _require(
                row["value"] == 0 and isinstance(row["string_value"], str),
                "non-numeric score fields were not retained",
            )


def _native_refusal(cli: Path, args: list[str], work: Path, code: str, reason: str) -> None:
    result = _command([str(cli), *args, "--json"], work)
    _require(
        result.returncode == EVIDENCE_ERROR_EXIT and result.stdout == "",
        "native refusal wrote success",
    )
    try:
        error: object = json.loads(result.stderr)
    except json.JSONDecodeError as exc:
        raise SmokeFailure("native refusal did not write JSON stderr") from exc
    _require(isinstance(error, dict), "native refusal was not an object")
    payload = cast(dict[str, object], error)
    _require(
        payload.get("code") == code and payload.get("reason") == reason, "wrong native refusal"
    )


def _native_measurement_pilot(cli: Path, fixtures: Path, work: Path) -> None:
    source = fixtures / "event-measurement"
    reads: list[Path] = []
    for variant, verdict in (("", "pass"), ("failure-", "fail")):
        observation = work / f"{variant}observation.json"
        observed = _cli_json(
            cli,
            [
                "ingest",
                "--format",
                "underwrite.evidence-bundle",
                "--version",
                "v1",
                "--file",
                str(source / f"{variant}bundle.json"),
            ],
            observation,
            work,
        )
        _require(observed.get("schema") == "observation.v1", "ingest schema mismatch")
        _require(
            observed.get("source")
            == {
                "format": "underwrite.evidence-bundle",
                "format_version": "v1",
            },
            "ingest source mismatch",
        )
        output = work / f"{variant}read.json"
        read = _cli_json(
            cli,
            [
                "measure",
                "--observation",
                str(observation),
                "--policy",
                str(fixtures / "event-acceptance/policy.json"),
            ],
            output,
            work,
        )
        _require(read.get("schema") == "read.v2", "event read schema mismatch")
        _require(read.get("verdict") == verdict, "event read direction mismatch")
        _require("score" not in read and "confidence" not in read, "generic confidence returned")
        reads.append(output)
    candidate = fixtures / "event-acceptance/candidate-single.json"
    claims = fixtures / "event-acceptance/claims.json"
    for index, classification in enumerate(("screened", "rejected")):
        decision = _cli_json(
            cli,
            [
                "accept",
                "--candidate",
                str(candidate),
                "--claims",
                str(claims),
                "--read",
                str(reads[index]),
            ],
            work / f"accept-{classification}.json",
            work,
        )
        _require(decision.get("schema") == "acceptance_decision.v2", "decision schema mismatch")
        _require(decision.get("classification") == classification, "classification mismatch")
        _require(decision.get("human_review") == "required", "human review mismatch")
        _require(decision.get("merge_authorized") is False, "merge authorization mismatch")
    _native_refusal(
        cli,
        [
            "measure",
            "--observation",
            str(work / "observation.json"),
            "--policy",
            str(fixtures / "measurement/policy.json"),
        ],
        work,
        "INVALID_POLICY",
        "LEGACY_POLICY_REQUIRES_MIGRATION",
    )
    _native_refusal(
        cli,
        [
            "accept",
            "--candidate",
            str(candidate),
            "--claims",
            str(fixtures / "acceptance/claims.json"),
            "--read",
            str(reads[0]),
        ],
        work,
        "INVALID_CLAIMS",
        "LEGACY_CLAIMS_REQUIRES_MIGRATION",
    )
    _native_refusal(
        cli,
        [
            "accept",
            "--candidate",
            str(candidate),
            "--claims",
            str(claims),
            "--read",
            str(fixtures / "acceptance/read.json"),
        ],
        work,
        "INVALID_READ",
        "LEGACY_READ_REQUIRES_MIGRATION",
    )
    substituted = json.loads(reads[0].read_bytes())
    substituted["semantics"]["construct"]["definition"] = "A substituted meaning"
    altered = work / "substituted-read.json"
    altered.write_text(json.dumps(substituted), encoding="utf-8")
    _native_refusal(
        cli,
        ["accept", "--candidate", str(candidate), "--claims", str(claims), "--read", str(altered)],
        work,
        "INVALID_READ",
        "SEMANTIC_DIGEST_MISMATCH",
    )


def _baseline_evidence_pilot(cli: Path, exchange: Path, work: Path) -> None:
    """Check the installed descriptive CLI and directory API on one exchange."""
    evidence = _cli_json(
        cli,
        ["audit-baseline", "--exchange", str(exchange)],
        work / "baseline-evidence.json",
        work,
    )
    _require(evidence.get("schema") == "evidence_result.v1", "baseline evidence schema")
    _require(evidence.get("verification") == [], "baseline invented portable verification")
    evidence_source = cast(dict[str, object], evidence["source"])
    identity = cast(dict[str, object], evidence_source["identity"])
    members = cast(dict[str, str], identity["members"])
    _require(
        identity["manifest_raw_hash"]
        == "sha256:" + hashlib.sha256((exchange / "manifest.json").read_bytes()).hexdigest()
        and members
        == {
            name: "sha256:" + hashlib.sha256((exchange / name).read_bytes()).hexdigest()
            for name in ("manifest.json", "export.json", "rows.jsonl", "schema.json", "scope.json")
        },
        "baseline evidence did not bind each raw source file",
    )
    evidence_population = cast(dict[str, object], evidence["population"])
    _require(
        evidence_population == {"unit": "case", "observed_count": 4, "planned_count": 4},
        "baseline evidence source-record population changed",
    )
    evidence_items = cast(list[dict[str, object]], evidence["items"])
    _require(
        [(item["availability"], item["value"]) for item in evidence_items]
        == [("observed", True), ("observed", False), ("observed", False), ("missing", None)],
        "baseline evidence source states changed",
    )
    api_code = (
        "import json,sys; from pathlib import Path; "
        "from underwrite.instrument.ingest.goppi_baseline import audit_baseline_exchange; "
        "print(json.dumps(audit_baseline_exchange(Path(sys.argv[1])),sort_keys=True))"
    )
    api = _success([str(cli.parent / "python"), "-c", api_code, str(exchange)], work)
    _require(json.loads(api.stdout) == evidence, "installed baseline Python API differs from CLI")
    _require(
        "SYNTHETIC_ANSWER_NOT_FOR_PROJECTION" not in json.dumps(evidence)
        and "indeterminate:agent_budget" not in json.dumps(evidence),
        "baseline descriptive result leaked private source content",
    )


def _common_result_inspection_pilot(cli: Path, fixtures: Path, work: Path) -> None:
    selected = fixtures / "evidence/common-result-c55.json"
    inspection = _cli_json(
        cli,
        ["inspect-result", str(selected)],
        work / "common-result-inspection.json",
        work,
    )
    _require(inspection.get("schema") == "common_result_inspection.v1", "result inspection schema")
    _require(
        inspection.get("selected_result") == {
            "format": "evidence_result.v1",
            "raw_hash": "sha256:" + hashlib.sha256(selected.read_bytes()).hexdigest(),
        },
        "result inspection raw-byte identity",
    )
    _require(
        inspection.get("availability_counts")
        == {"observed": 2, "missing": 2, "error": 1, "abstained": 2, "total": 7},
        "result inspection availability",
    )
    api_code = (
        "import json,sys; from pathlib import Path; "
        "from underwrite.cli.inspect_result import inspect_result_file; "
        "print(json.dumps(inspect_result_file(Path(sys.argv[1])),sort_keys=True))"
    )
    api = _success([str(cli.parent / "python"), "-c", api_code, str(selected)], work)
    _require(json.loads(api.stdout) == inspection, "installed result inspection API differs")
    _require(
        str(selected) not in json.dumps(inspection)
        and "goppi.operator_answer_assessment" not in json.dumps(inspection),
        "result inspection leaked input content",
    )
    invalid = work / "private-invalid-result.json"
    invalid.write_bytes(b'{"schema":"private","schema":"private"}')
    refused = _command([str(cli), "inspect-result", str(invalid), "--json"], work)
    _require(
        refused.returncode == EVIDENCE_ERROR_EXIT
        and refused.stdout == ""
        and str(invalid) not in refused.stderr
        and json.loads(refused.stderr)["reason"] == "INVALID_JSON_INPUT",
        "installed result inspection refusal",
    )


def _baseline_event_pilot(cli: Path, fixtures: Path, work: Path) -> None:
    source = fixtures / "baseline-event"
    exchange = source / "exchange"
    _baseline_evidence_pilot(cli, exchange, work)
    observation_path = work / "baseline-observation.json"
    observation = _cli_json(
        cli,
        ["project-baseline", "--exchange", str(exchange), "--direction", "higher"],
        observation_path,
        work,
    )
    encoded = observation_path.read_text(encoding="utf-8")
    _require(observation.get("schema") == "observation.v1", "baseline projection schema")
    _require("SYNTHETIC_ANSWER_NOT_FOR_PROJECTION" not in encoded, "baseline answer leaked")
    _require("indeterminate:agent_budget" not in encoded, "baseline private reason leaked")
    choices = json.loads((source / "policy.json").read_bytes())
    policy_options = [
        "--subject-id",
        choices["subject"]["subject_id"],
        "--p0",
        str(choices["eprocess"]["p0"]),
        "--ece-bins",
        str(choices["diagnostics"]["ece_bins"]),
        "--min-bin-n",
        str(choices["diagnostics"]["min_bin_n"]),
        "--all-k",
        str(choices["diagnostics"]["all_k"]),
    ]
    policy_path = work / "baseline-prepared-policy.json"
    policy = _cli_json(
        cli,
        ["prepare-event-policy", "--observation", str(observation_path), *policy_options],
        policy_path,
        work,
    )
    _require(policy == choices, "prepared policy changed explicit choices or declared bindings")
    missing_inventory = json.loads(observation_path.read_bytes())
    del missing_inventory["payload"]["sources"][0]["expected_case_ids"]
    missing_path = work / "baseline-without-inventory.json"
    missing_path.write_text(json.dumps(missing_inventory), encoding="utf-8")
    _native_refusal(
        cli,
        ["prepare-event-policy", "--observation", str(missing_path), *policy_options],
        work,
        "INVALID_INPUT",
        "BASELINE_EXPECTED_CASES_REQUIRED",
    )
    read = _cli_json(
        cli,
        [
            "measure",
            "--observation",
            str(observation_path),
            "--policy",
            str(policy_path),
        ],
        work / "baseline-read.json",
        work,
    )
    _require(read.get("verdict") == "not_measured", "baseline inference was fabricated")
    population = cast(dict[str, object], read["population"])
    predictions = cast(dict[str, object], population["predictions"])
    _require(
        predictions["status"] == "unavailable" and predictions["observed_count"] is None,
        "baseline predictions were fabricated",
    )
    diagnostics = cast(dict[str, dict[str, object]], read["diagnostics"])
    source_counts = json.loads((source / "exchange/export.json").read_bytes())["counts"]
    event = diagnostics["event_rate"]
    _require(
        event["positive_count"] == source_counts["observed_true"]
        and event["n"] == source_counts["observed_true"] + source_counts["observed_false"],
        "baseline primary labels changed",
    )
    _require(diagnostics["brier"]["value"] is None, "baseline forecast loss was fabricated")
    altered = work / "altered-baseline-exchange"
    altered.mkdir()
    for path in (source / "exchange").iterdir():
        (altered / path.name).write_bytes(path.read_bytes())
    rows = altered / "rows.jsonl"
    rows.write_bytes(rows.read_bytes().replace(b"observed_true", b"observed_false", 1))
    _native_refusal(
        cli,
        ["project-baseline", "--exchange", str(altered), "--direction", "higher"],
        work,
        "INVALID_INPUT",
        "BASELINE_FILE_IDENTITY_MISMATCH",
    )
    _native_refusal(
        cli,
        ["audit-baseline", "--exchange", str(altered)],
        work,
        "INVALID_INPUT",
        "BASELINE_FILE_IDENTITY_MISMATCH",
    )
    api_refusal_code = (
        "import json,sys\n"
        "from pathlib import Path\n"
        "from underwrite.instrument.ingest.goppi_baseline import audit_baseline_exchange\n"
        "from underwrite.instrument.ingest.local_capture import CaptureError\n"
        "from underwrite.instrument.evidence.goppi_baseline import BaselineProjectionError\n"
        "try:\n"
        "    audit_baseline_exchange(Path(sys.argv[1]))\n"
        "except (CaptureError, BaselineProjectionError) as exc:\n"
        "    print(json.dumps({'code': exc.code, 'reason': exc.reason, "
        "'location': exc.location}))\n"
        "else:\n"
        "    raise SystemExit(3)\n"
    )
    api_refusal = _success(
        [str(cli.parent / "python"), "-c", api_refusal_code, str(altered)], work
    )
    api_error = json.loads(api_refusal.stdout)
    _require(
        api_refusal.stderr == ""
        and api_error["code"] == "INVALID_INPUT"
        and api_error["reason"] == "BASELINE_FILE_IDENTITY_MISMATCH"
        and str(altered) not in api_refusal.stdout,
        "installed baseline Python API did not refuse tampered bytes finitely",
    )


def _baseline_record_pilot(cli: Path, fixtures: Path, work: Path) -> None:
    """Retain exact captured baseline bytes and read the result after source removal."""
    exchange = work / "record-baseline-exchange"
    exchange.mkdir()
    source = fixtures / "baseline-event/exchange"
    for path in source.iterdir():
        (exchange / path.name).write_bytes(path.read_bytes())
    record_root = work / "baseline-record-store"
    identifier = "baseline.smoke-001"
    report = _cli_json(
        cli,
        [
            "records", "audit-baseline", "--exchange", str(exchange),
            "--root", str(record_root), "--id", identifier,
        ],
        work / "baseline-record.json",
        work,
    )
    _require(report.get("schema") == "baseline_record.v1", "baseline record schema")
    analysis = cast(dict[str, object], report["analysis"])
    storage = cast(dict[str, object], report["storage"])
    _require(analysis["state"] == "result", "baseline record did not save a result")
    _require(
        analysis["report"] == json.loads((work / "baseline-evidence.json").read_bytes()),
        "baseline record changed descriptive result",
    )
    verification = cast(dict[str, object], storage["verification"])
    _require(
        storage["state"] == "saved"
        and verification["files_checked"] == BASELINE_RECORD_FILE_COUNT,
        "baseline record did not verify seven retained files",
    )
    encoded = json.dumps(report)
    _require(
        str(exchange) not in encoded and "SYNTHETIC_ANSWER_NOT_FOR_PROJECTION" not in encoded,
        "baseline record aggregate leaked private source",
    )
    raw_rows_hash = hashlib.sha256((exchange / "rows.jsonl").read_bytes()).hexdigest()
    shutil.rmtree(exchange)
    verified = _cli_json(
        cli,
        ["records", "verify", "--root", str(record_root), "--id", identifier],
        work / "baseline-record-verification.json",
        work,
    )
    _require(
        verified["valid"] is True
        and verified["files_checked"] == BASELINE_RECORD_FILE_COUNT,
        "baseline record verification failed after source removal",
    )
    selected = _cli_json(
        cli,
        [
            "records", "read-json", "--root", str(record_root), "--id", identifier,
            "--name", "analysis/evidence_result.json",
        ],
        work / "baseline-record-selected.json",
        work,
    )
    _require(selected["content"] == analysis["report"], "baseline saved result cannot be read back")
    raw_code = (
        "import hashlib,sys; from pathlib import Path; "
        "from underwrite.service.records import read_record_file; "
        "from underwrite.instrument.ingest.goppi_baseline import ROWS_MAX_BYTES; "
        "print(hashlib.sha256(read_record_file(Path(sys.argv[1]),sys.argv[2],"
        "'source/rows.jsonl',max_bytes=ROWS_MAX_BYTES).data).hexdigest())"
    )
    raw = _success([str(cli.parent / "python"), "-c", raw_code, str(record_root), identifier], work)
    _require(raw.stdout.strip() == raw_rows_hash, "baseline raw rows changed in record")


def _measurement_record_pilot(cli: Path, work: Path) -> None:
    observation = work / "measurement-input.json"
    raw = (work / "baseline-observation.json").read_bytes() + b"\n"
    observation.write_bytes(raw)
    policy = work / "baseline-prepared-policy.json"
    store = work / "measurement-records"
    args = [
        "records",
        "measure",
        "--observation",
        str(observation),
        "--policy",
        str(policy),
        "--root",
        str(store),
        "--id",
        "measurement-001",
    ]
    result = _cli_json(cli, args, work / "measurement-record.json", work)
    analysis = cast(dict[str, object], result["analysis"])
    storage = cast(dict[str, object], result["storage"])
    expected = json.loads((work / "baseline-read.json").read_bytes())
    _require(result.get("schema") == "measurement_record.v1", "measurement capture schema")
    _require(analysis.get("report") == expected, "capture changed measurement")
    _require(storage.get("state") == "saved", "measurement was not retained")
    source = _cli_json(
        cli,
        [
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "measurement-001",
            "--name",
            "source/observation.bin",
        ],
        work / "measurement-source.json",
        work,
    )
    _require(
        source.get("sha256") == "sha256:" + hashlib.sha256(raw).hexdigest()
        and source.get("bytes") == len(raw),
        "capture did not preserve exact source bytes",
    )
    recovered = _cli_json(
        cli,
        [
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "measurement-001",
            "--name",
            "analysis/read.json",
        ],
        work / "measurement-recovered.json",
        work,
    )
    _require(recovered.get("content") == expected, "stored read changed")
    conflict = _command([str(cli), *args, "--json"], work)
    conflict_report = json.loads(conflict.stdout)
    _require(
        conflict.returncode == STORAGE_ERROR_EXIT
        and conflict.stderr == ""
        and conflict_report["storage"]["error"]["code"] == "RECORD_EXISTS"
        and conflict_report["analysis"]["report"] == expected,
        "record conflict lost analysis or overwrote the record",
    )
    malformed = work / "malformed-measurement.json"
    malformed.write_bytes(b"{broken")
    failure = _command(
        [
            str(cli),
            "records",
            "measure",
            "--observation",
            str(malformed),
            "--policy",
            str(work / "absent-policy.json"),
            "--root",
            str(store),
            "--id",
            "measurement-diagnostic",
            "--json",
        ],
        work,
    )
    report = json.loads(failure.stdout)
    _require(
        failure.returncode == EVIDENCE_ERROR_EXIT
        and failure.stderr == ""
        and report["storage"]["state"] == "saved"
        and report["analysis"]["phase"] == "analysis"
        and report["analysis"]["error"]["code"] == "INVALID_OBSERVATION",
        "diagnostic capture changed measurement error precedence",
    )


def _measurement_replay_pilot(cli: Path, work: Path) -> None:
    store = work / "measurement-records"
    observation = work / "measurement-input.json"
    policy = work / "baseline-prepared-policy.json"
    source_bytes, policy_bytes = observation.read_bytes(), policy.read_bytes()
    observation.unlink()
    policy.unlink()
    replay = _cli_json(
        cli,
        ["records", "replay-measure", "--root", str(store), "--id", "measurement-001"],
        work / "measurement-replay.json",
        work,
    )
    _require(replay.get("schema") == "measurement_replay.v1", "replay schema mismatch")
    _require(
        replay["comparison"]
        == {"state": "match", "scope": "canonical_outcome_kind_and_full_document"},
        "saved measurement did not recalculate after originals disappeared",
    )
    _native_refusal(
        cli,
        ["records", "replay-measure", "--root", str(store), "--id", "measurement-diagnostic"],
        work,
        "INVALID_INPUT",
        "INCOMPLETE_MEASUREMENT_RECORD",
    )
    policy.write_bytes(policy_bytes)
    malformed = work / "malformed-measurement.json"
    diagnostic = _command(
        [
            str(cli),
            "records",
            "measure",
            "--observation",
            str(malformed),
            "--policy",
            str(policy),
            "--root",
            str(store),
            "--id",
            "complete-diagnostic",
            "--json",
        ],
        work,
    )
    _require(diagnostic.returncode == EVIDENCE_ERROR_EXIT, "diagnostic was not captured")
    error_replay = _cli_json(
        cli,
        ["records", "replay-measure", "--root", str(store), "--id", "complete-diagnostic"],
        work / "diagnostic-replay.json",
        work,
    )
    recomputed = cast(dict[str, object], error_replay["recomputed"])
    outcome = cast(dict[str, object], recomputed["outcome"])
    _require(
        error_replay["comparison"] == replay["comparison"] and outcome["state"] == "error",
        "matching refusal became a successful measurement",
    )
    large = work / "large-measurement-input.json"
    large.write_bytes(source_bytes + b" " * (1024 * 1024))
    _cli_json(
        cli,
        [
            "records",
            "measure",
            "--observation",
            str(large),
            "--policy",
            str(policy),
            "--root",
            str(store),
            "--id",
            "large-measurement",
        ],
        work / "large-measurement-record.json",
        work,
    )
    large.unlink()
    policy.unlink()
    large_replay = _cli_json(
        cli,
        ["records", "replay-measure", "--root", str(store), "--id", "large-measurement"],
        work / "large-measurement-replay.json",
        work,
    )
    _require(large_replay["comparison"] == replay["comparison"], "large input replay failed")
    refused = _command(
        [
            str(cli),
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "large-measurement",
            "--name",
            "source/observation.bin",
            "--json",
        ],
        work,
    )
    failure = json.loads(refused.stderr)
    _require(
        refused.returncode == EVIDENCE_ERROR_EXIT
        and refused.stdout == ""
        and failure["schema"] == "record_error.v1"
        and failure["code"] == "INPUT_LIMIT_EXCEEDED",
        "replay widened the JSON viewer limit",
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
        _deepeval_pilot(cli, work)
        _deepeval_v2_pilot(cli, work)
        _langfuse_pilot(cli, work)

        _native_measurement_pilot(cli, fixtures, work)
        _baseline_event_pilot(cli, fixtures, work)
        _common_result_inspection_pilot(cli, fixtures, work)
        _baseline_record_pilot(cli, fixtures, work)
        _measurement_record_pilot(cli, work)
        _measurement_replay_pilot(cli, work)

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

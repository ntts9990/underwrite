"""Declared comparison binds two audits without promoting source claims to proof."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from _repo_paths import repo_root
from jsonschema import Draft202012Validator
from underwrite_core.canonical import canonical_bytes, content_digest

from underwrite.cli import compare_declarations
from underwrite.cli.evidence import run_cli
from underwrite.instrument.ingest.accounting import audit_case

ROOT = repo_root(Path(__file__).resolve())
CASE = ROOT / "fixtures/examples/evidence/synthetic-promptfoo-complete.case.json"
ERROR_EXIT = 2


def _conditions() -> dict[str, object]:
    return {
        "suite": "suite-a",
        "prompt_version": "prompt-v1",
        "model": "model-a",
        "gen_params": {
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 128,
            "think": "off",
            "seed": 7,
            "num_ctx": 2048,
            "timeout_s": 30.0,
        },
    }


def _metric() -> dict[str, object]:
    return {
        "name": "accuracy",
        "unit": "fraction",
        "higher_is_better": True,
        "scope": "case",
        "denominator": "valid-evaluations",
        "aggregation": "mean",
        "evaluator": "exact-match",
        "evaluator_version": "v1",
        "policy": "policy-a",
        "threshold": 0.0,
    }


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, Any]]:
    report = audit_case(CASE)
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    for path in (baseline, candidate):
        path.write_bytes(canonical_bytes(report) + b"\n")
    digest = content_digest(report)
    manifest: dict[str, Any] = {
        "schema": "declared_comparison_case.v1",
        "baseline": {
            "audit_digest": digest,
            "run_ref": "baseline-run",
            "conditions": _conditions(),
            "metric": _metric(),
        },
        "candidate": {
            "audit_digest": digest,
            "run_ref": "candidate-run",
            "conditions": _conditions(),
            "metric": _metric(),
        },
    }
    path = tmp_path / "declarations.json"
    path.write_bytes(canonical_bytes(manifest) + b"\n")
    return baseline, candidate, path, manifest


def _run(
    baseline: Path, candidate: Path, manifest: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any], str, str]:
    code = compare_declarations.main(
        [
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
            "--manifest",
            str(manifest),
            "--json",
        ]
    )
    streams = capsys.readouterr()
    return code, json.loads(streams.out if code == 0 else streams.err), streams.out, streams.err


def test_complete_same_declarations_are_canonical_but_not_verified(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline, candidate, manifest_path, manifest = _inputs(tmp_path)
    code, report, stdout, stderr = _run(baseline, candidate, manifest_path, capsys)
    assert code == 0 and stderr == ""
    schema = json.loads((ROOT / "contracts/declared_comparison.v1.schema.json").read_bytes())
    Draft202012Validator(schema).validate(report)  # pyright: ignore[reportUnknownMemberType]
    assert stdout.encode() == canonical_bytes(report) + b"\n"
    assert report["declared_relation"] == "same"
    assert report["differences"] == report["unknowns"] == []
    assert report["actual_conditions_verified"] is False
    assert report["semantic_equivalence_verified"] is False
    assert report["audit_relations"] == {"baseline": "consistent", "candidate": "consistent"}
    assert report["input_digests"] == {
        "baseline": manifest["baseline"]["audit_digest"],
        "candidate": manifest["candidate"]["audit_digest"],
    }
    assert report["manifest_digest"] == content_digest(manifest)
    assert "NO_MEASUREMENT_OR_APPROVAL" in report["limitations"]


def test_public_cli_dispatches_the_exact_comparison_command(tmp_path: Path) -> None:
    baseline, candidate, manifest_path, _ = _inputs(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "underwrite.cli.main",
            "compare-declarations",
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
            "--manifest",
            str(manifest_path),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and result.stderr == ""
    assert json.loads(result.stdout)["declared_relation"] == "same"


def test_known_difference_survives_unknown_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline, candidate, manifest_path, manifest = _inputs(tmp_path)
    manifest["candidate"]["conditions"]["model"] = "model-b"
    manifest["candidate"]["conditions"]["gen_params"]["seed"] = None
    manifest["baseline"]["metric"]["threshold"] = None
    manifest_path.write_bytes(canonical_bytes(manifest) + b"\n")
    code, report, _, stderr = _run(baseline, candidate, manifest_path, capsys)
    assert code == 0 and stderr == ""
    assert report["declared_relation"] == "different"
    assert report["differences"] == [
        {"field": "conditions.model", "baseline": "model-a", "candidate": "model-b"}
    ]
    assert {item["field"] for item in report["unknowns"]} == {
        "conditions.gen_params.seed",
        "metric.threshold",
    }


@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_audit_digest_mismatch_is_typed_and_emits_no_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], side: str
) -> None:
    baseline, candidate, manifest_path, manifest = _inputs(tmp_path)
    manifest[side]["audit_digest"] = "sha256:" + "0" * 64
    manifest_path.write_bytes(canonical_bytes(manifest) + b"\n")
    code, error, stdout, stderr = _run(baseline, candidate, manifest_path, capsys)
    assert code == ERROR_EXIT and stdout == "" and stderr
    assert error["schema"] == "evidence_error.v1"
    assert error["code"] == "INPUT_BINDING_MISMATCH"
    assert error["reason"] == "AUDIT_DIGEST_MISMATCH"
    assert error["location"] == f"/manifest/{side}/audit_digest"


@pytest.mark.parametrize("fault", ["metric-bool", "extra-field", "blank-ref", "bad-audit"])
def test_invalid_supplied_fields_fail_without_echoing_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fault: str
) -> None:
    baseline, candidate, manifest_path, manifest = _inputs(tmp_path)
    if fault == "metric-bool":
        manifest["baseline"]["metric"]["threshold"] = True
    elif fault == "extra-field":
        manifest["baseline"]["conditions"]["untrusted-secret"] = "private-payload"
    elif fault == "blank-ref":
        manifest["candidate"]["run_ref"] = "   "
    else:
        baseline.write_text('{"schema":"wrong","secret":"private-payload"}')
    manifest_path.write_bytes(canonical_bytes(manifest) + b"\n")
    code, error, stdout, stderr = _run(baseline, candidate, manifest_path, capsys)
    assert code == ERROR_EXIT and stdout == "" and stderr
    assert error["schema"] == "evidence_error.v1"
    assert error["code"] == "INVALID_INPUT"
    assert "private-payload" not in stderr


def test_report_output_limit_is_typed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    baseline, candidate, manifest_path, _ = _inputs(tmp_path)
    code = run_cli(
        "compare-declarations",
        [
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
            "--manifest",
            str(manifest_path),
            "--json",
        ],
        compare_declarations.configure,
        compare_declarations.produce,
        "declared_comparison.v1",
        output_max_bytes=1,
    )
    streams = capsys.readouterr()
    assert code == ERROR_EXIT and streams.out == ""
    assert json.loads(streams.err)["code"] == "OUTPUT_LIMIT_EXCEEDED"


@pytest.mark.parametrize("kind", ["audit", "manifest"])
def test_input_size_limits_are_typed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    baseline, candidate, manifest_path, _ = _inputs(tmp_path)
    path = baseline if kind == "audit" else manifest_path
    limit = (
        compare_declarations.AUDIT_MAX_BYTES
        if kind == "audit"
        else compare_declarations.MANIFEST_MAX_BYTES
    )
    path.write_bytes(b" " * (limit + 1))
    code, error, stdout, _ = _run(baseline, candidate, manifest_path, capsys)
    assert code == ERROR_EXIT and stdout == ""
    assert error["code"] == "INPUT_LIMIT_EXCEEDED"

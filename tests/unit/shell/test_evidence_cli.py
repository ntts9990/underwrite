"""Shared evidence commands fail atomically without reflecting untrusted errors."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from _repo_paths import repo_root
from jsonschema import Draft202012Validator

from underwrite.cli.evidence import ERROR_ACTIONS, EvidenceError, run_cli
from underwrite.instrument.ingest.inspection import inspect_artifact

ROOT = repo_root(Path(__file__).resolve())
ERROR_EXIT = 2


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true")


def report(args: argparse.Namespace) -> dict[str, object]:
    return inspect_artifact(
        ROOT / "fixtures/examples/evidence/synthetic-promptfoo-complete.case.json"
    )


def test_error_codes_match_the_installed_contract() -> None:
    schema = json.loads((ROOT / "contracts/evidence_error.v1.schema.json").read_bytes())
    assert set(ERROR_ACTIONS) == set(schema["properties"]["code"]["enum"])
    validator = Draft202012Validator(schema)
    for code in ERROR_ACTIONS:
        validator.validate(EvidenceError(code, "FIXED_REASON", "/input").payload)  # pyright: ignore[reportUnknownMemberType]


def test_successful_output_is_one_json_document(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cli("example", ["--json"], configure, report, "artifact_inspection.v1") == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == report(argparse.Namespace())


@pytest.mark.parametrize("failure", ["arguments", "internal", "output_limit", "output_schema"])
def test_failure_has_empty_stdout_and_no_untrusted_error_text(
    failure: str, capsys: pytest.CaptureFixture[str]
) -> None:
    def produce(args: argparse.Namespace) -> dict[str, object]:
        if failure == "internal":
            raise RuntimeError("secret-source-content")
        return {} if failure == "output_schema" else report(args)

    argv = ["--json"]
    if failure == "arguments":
        argv += ["--unknown", "secret-source-content"]
    assert (
        run_cli(
            "example",
            argv,
            configure,
            produce,
            "artifact_inspection.v1",
            output_max_bytes=1 if failure == "output_limit" else 1024,
        )
        == ERROR_EXIT
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "secret-source-content" not in captured.err
    error = json.loads(captured.err)
    assert error["schema"] == "evidence_error.v1" and error["exit_code"] == ERROR_EXIT
    assert (
        error["code"]
        == {
            "arguments": "USAGE_ERROR",
            "internal": "INTERNAL_ERROR",
            "output_limit": "OUTPUT_LIMIT_EXCEEDED",
            "output_schema": "INTERNAL_ERROR",
        }[failure]
    )

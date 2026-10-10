"""An external export can be ingested without becoming native measurement evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from _repo_paths import repo_root
from jsonschema import Draft202012Validator
from underwrite_core.canonical import digest_bytes

ROOT = repo_root(Path(__file__).resolve())
EXPORT = ROOT / "fixtures/golden/external/promptfoo/results.json"
POLICY = ROOT / "fixtures/examples/measurement/policy.json"
MEASURE_ERROR_EXIT = 2


def test_promptfoo_ingest_does_not_imply_measurable_profile(tmp_path: Path) -> None:
    ingest = subprocess.run(
        [
            sys.executable,
            "-m",
            "underwrite.cli.main",
            "ingest",
            "--format",
            "promptfoo.eval-output",
            "--version",
            "0.123.0",
            "--file",
            str(EXPORT),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ingest.returncode == 0, ingest.stderr
    assert ingest.stderr == ""
    observation: dict[str, Any] = json.loads(ingest.stdout)
    assert observation["schema"] == "observation.v1"
    assert observation["source"] == {"format": "promptfoo.eval-output", "format_version": "0.123.0"}
    assert observation["raw"]["hash"] == digest_bytes(EXPORT.read_bytes())
    source = json.loads(EXPORT.read_bytes())
    assert observation["payload"]["results"]["stats"] == source["results"]["stats"]

    path = tmp_path / "observation.json"
    path.write_text(ingest.stdout, encoding="utf-8")
    measure = subprocess.run(
        [
            sys.executable,
            "-m",
            "underwrite.cli.main",
            "measure",
            "--observation",
            str(path),
            "--policy",
            str(POLICY),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert measure.returncode == MEASURE_ERROR_EXIT
    assert measure.stdout == ""
    error: dict[str, Any] = json.loads(measure.stderr)
    schema = json.loads((ROOT / "contracts/measurement_error.v1.schema.json").read_bytes())
    Draft202012Validator(schema).validate(error)  # pyright: ignore[reportUnknownMemberType]
    assert error == {
        "schema": "measurement_error.v1",
        "code": "UNSUPPORTED_PROFILE",
        "reason": "UNSUPPORTED_OBSERVATION_PROFILE",
        "location": "/observation",
        "next_action": "USE_SUPPORTED_PROFILE",
        "retryable": False,
        "exit_code": 2,
    }

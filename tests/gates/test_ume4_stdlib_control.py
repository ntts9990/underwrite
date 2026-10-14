"""The independent stdlib control preserves the pinned source-claim task boundary."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from drivers.ume4_stdlib_control import project

from underwrite.instrument.ingest.goppi_baseline import audit_baseline_exchange

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures/conformance/ume4"
CONTROL = ROOT / "tests/drivers/ume4_stdlib_control.py"
ERROR_EXIT = 2


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("exchange_name", "neutral_name"),
    [
        ("goppi-v2-exchange", "neutral-result.json"),
        ("goppi-v2-false-missing", "neutral-result-false-missing.json"),
    ],
)
def test_stdlib_control_matches_common_descriptive_fields(
    exchange_name: str, neutral_name: str
) -> None:
    exchange = FIXTURE / exchange_name
    control = project(exchange, FIXTURE / neutral_name)
    underwrite = cast(dict[str, Any], audit_baseline_exchange(exchange))
    assert control["population"] == underwrite["population"]
    assert control["source_identity"] == {
        key: value for key, value in underwrite["source"]["identity"].items() if key != "kind"
    }
    assert control["verification"] == underwrite["verification"] == []
    control_items = cast(list[dict[str, Any]], control["items"])
    underwrite_items = cast(list[dict[str, Any]], underwrite["items"])
    for left, right in zip(control_items, underwrite_items, strict=True):
        for key in ("coordinate", "availability", "value", "reason", "origin", "permitted_effect"):
            assert left[key] == right[key]
    visible = json.dumps(control)
    scope = json.loads((exchange / "scope.json").read_bytes())
    for case_id in scope["selection"]["case_ids"]:
        assert case_id not in visible


def test_stdlib_control_refuses_changed_member_without_partial_stdout(tmp_path: Path) -> None:
    exchange = tmp_path / "exchange"
    shutil.copytree(FIXTURE / "goppi-v2-exchange", exchange)
    rows = exchange / "rows.jsonl"
    raw = rows.read_bytes()
    rows.write_bytes(raw.replace(b'"case_id":"aa-case-001"', b'"case_id":"ba-case-001"', 1))
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(CONTROL),
            "--exchange",
            str(exchange),
            "--neutral",
            str(FIXTURE / "neutral-result.json"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == ERROR_EXIT
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "schema": "ume4.control_error.v1",
        "reason": "INPUT_REJECTED",
    }


def test_stdlib_control_refuses_stale_neutral_result(tmp_path: Path) -> None:
    neutral: dict[str, Any] = json.loads((FIXTURE / "neutral-result.json").read_bytes())
    neutral["source_manifest_sha256"] = "sha256:" + "0" * 64
    path = tmp_path / "neutral.json"
    path.write_text(json.dumps(neutral))
    with pytest.raises(ValueError, match="INPUT_REJECTED"):
        project(FIXTURE / "goppi-v2-exchange", path)


def test_stdlib_control_refuses_symlink_exchange_root(tmp_path: Path) -> None:
    linked = tmp_path / "linked"
    linked.symlink_to(FIXTURE / "goppi-v2-exchange", target_is_directory=True)
    with pytest.raises(ValueError, match="INPUT_REJECTED"):
        project(linked, FIXTURE / "neutral-result.json")


def test_value_matrix_is_bound_to_sources_and_states_its_limits() -> None:
    matrix: dict[str, Any] = json.loads((FIXTURE / "value-comparison.json").read_bytes())
    pins: dict[str, Any] = json.loads((FIXTURE / "pins.json").read_bytes())
    negatives: dict[str, Any] = json.loads((FIXTURE / "negative-results.json").read_bytes())
    assert matrix["input_pins_sha256"] == _sha256(FIXTURE / "pins.json")
    assert matrix["conditions"]["C"]["control_source_sha256"] == _sha256(CONTROL)
    assert matrix["conditions"]["C"]["control_source_lines"] == len(
        CONTROL.read_text().splitlines()
    )
    assert {item["capture_key"] for item in matrix["positive_cases"]} == set(pins["captures"])
    assert {item["probe_id"] for item in matrix["negative_cases"]} == {
        item["id"] for item in negatives["probes"]
    }
    for item in matrix["negative_cases"]:
        prefix = "negative-results.json#/probes/"
        assert item["source_identity_ref"].startswith(prefix)
        index = int(item["source_identity_ref"].removeprefix(prefix))
        assert negatives["probes"][index]["id"] == item["probe_id"]
    assert matrix["observed"]["C_standalone_negative_runs_in_this_trial"] == 0
    assert matrix["interpretation"]["B0_C_full_trust_or_schema_equivalence_established"] is False
    assert all(matrix["unmeasured"].values())

"""Generated trace-index contract binds only selected, content-minimized facts."""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import digest_bytes

from underwrite.instrument.evidence.deepeval_trace import (
    SCHEMA,
    SOURCE_FORMAT,
    SOURCE_VERSION,
    index_deepeval_trace,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures/golden/external/deepeval-4.2.8/traces"


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _validator() -> Validator:
    schema = json.loads((ROOT / "contracts" / f"{SCHEMA}.schema.json").read_bytes())
    Draft202012Validator.check_schema(schema)
    return cast("Validator", Draft202012Validator(schema))


def _report(name: str) -> dict[str, Any]:
    raw = (FIXTURES / f"{name}.json").read_bytes()
    return cast(
        dict[str, Any],
        {
            "schema": SCHEMA,
            "source": {
                "format": SOURCE_FORMAT,
                "format_version": SOURCE_VERSION,
                "claims_verified": False,
            },
            "raw_hash": digest_bytes(raw),
            **index_deepeval_trace(json.loads(raw)),
        },
    )


def test_generated_contract_is_current_and_valid() -> None:
    check = subprocess.run(
        [sys.executable, "scripts/build_deepeval_trace_index.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    _validator()


@pytest.mark.parametrize("name", ["success", "recovery"])
def test_native_source_index_validates_and_remains_content_minimized(name: str) -> None:
    report = _report(name)
    _validator().validate(report)
    assert report["cases"][0]["pointer"] == "/testCases/0"
    assert report["counts"]["spans"]["total"] == len(report["cases"][0]["spans"])
    serialized = json.dumps(report)
    for private in (
        "synthetic expected miss",
        "primary_lookup",
        "lookup_rule",
        "2+2?",
        "trace_fixed",
    ):
        assert private not in serialized
    for row in report["cases"][0]["spans"]:
        assert (
            not {"uuid", "parentUuid", "name", "error", "input", "output", "metricsData"}
            & row.keys()
        )


def _assign(report: dict[str, Any], path: tuple[str | int, ...], value: object) -> None:
    selected: Any = report
    for step in path[:-1]:
        selected = selected[step]
    selected[path[-1]] = value


MUTATIONS: list[tuple[tuple[str | int, ...], object]] = [
    (("source", "format"), "unsupported"),
    (("raw_hash",), "sha256:private"),
    (("cases", 0, "trace_availability"), "absent"),
    (("cases", 0, "groups", "unknownSpans"), "present"),
    (("cases", 0, "spans", 1, "parent_state"), "unresolved"),
    (("cases", 0, "spans", 1, "uuid"), "PRIVATE_UUID"),
]


@pytest.mark.parametrize("path,value", MUTATIONS)
def test_contract_refuses_false_identity_parent_and_private_fields(
    path: tuple[str | int, ...], value: object
) -> None:
    invalid = deepcopy(_report("success"))
    _assign(invalid, path, value)
    with pytest.raises(ValidationError):
        _validator().validate(invalid)


def test_contract_refuses_cycle_claim_without_resolved_parent() -> None:
    invalid = deepcopy(_report("recovery"))
    row = invalid["cases"][0]["spans"][0]
    row["parent_state"] = "unresolved"
    row["cycle_member"] = True
    with pytest.raises(ValidationError):
        _validator().validate(invalid)

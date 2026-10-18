"""Generated CLI contracts keep typed values, retrieval states and privacy closed."""

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

from underwrite.instrument.evidence.langfuse_compat import (
    ACCOUNTING_SCHEMA,
    ROW_INDEX_SCHEMA,
    account_langfuse_compatible,
    index_langfuse_compatible,
)

ROOT = Path(__file__).resolve().parents[2]
HASH = "sha256:" + "a" * 64


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _validator(name: str) -> Validator:
    schema = json.loads((ROOT / "contracts" / f"{name}.schema.json").read_bytes())
    Draft202012Validator.check_schema(schema)
    return cast("Validator", Draft202012Validator(schema))


def _assign(report: dict[str, Any], path: tuple[str | int, ...], value: object) -> None:
    selected: Any = report
    for step in path[:-1]:
        selected = selected[step]
    selected[path[-1]] = value


def _report() -> dict[str, Any]:
    observations = {
        "status": 200,
        "headers": {"x-private": "SECRET_HEADER"},
        "body": {
            "data": [{"id": "o", "projectId": "p", "traceId": "t", "type": "SPAN"}],
            "meta": {"cursor": "SECRET_CURSOR"},
        },
    }
    scores = {
        "data": [
            {
                "id": "s",
                "projectId": "p",
                "name": "private-name",
                "dataType": "BOOLEAN",
                "value": False,
                "subject": {"kind": "observation", "id": "o", "traceId": "t"},
            }
        ],
        "meta": {"pages": 1, "fetchedItems": 1, "truncated": False},
    }
    report: dict[str, Any] = {
        "schema": ACCOUNTING_SCHEMA,
        **account_langfuse_compatible(observations, scores),
    }
    for source in report["sources"].values():
        source["raw_hash"] = HASH
    return report


def _index(report: dict[str, Any]) -> dict[str, Any]:
    return {"schema": ROW_INDEX_SCHEMA, **index_langfuse_compatible(report)}


def test_generated_contracts_are_current_and_admit_both_owned_views() -> None:
    check = subprocess.run(
        [sys.executable, "scripts/build_langfuse_compat.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    report = _report()
    _validator("langfuse_accounting.v2").validate(report)
    index = _index(report)
    _validator("langfuse_row_index.v2").validate(index)
    assert index["counts"] == report["counts"]
    assert index["retrieval"] == report["retrieval"]
    serialized = json.dumps(index)
    for withheld in ("SECRET_HEADER", "SECRET_CURSOR", "private-name", '"value":', '"id":'):
        assert withheld not in serialized


@pytest.mark.parametrize(
    "path,value",
    [
        (("scores", 0, "value"), 0),
        (("scores", 0, "association"), "subject_unknown"),
        (("scores", 0, "target_observation_index"), None),
        (("retrieval", "observations", "http_status"), None),
        (("retrieval", "scores", "pagination", "cursor_state"), "present"),
        (("sources", "scores", "format"), "langfuse.scores"),
    ],
)
def test_full_contract_refuses_cross_kind_target_retrieval_and_source_claims(
    path: tuple[str | int, ...],
    value: object,
) -> None:
    invalid = deepcopy(_report())
    _assign(invalid, path, value)
    with pytest.raises(ValidationError):
        _validator("langfuse_accounting.v2").validate(invalid)


@pytest.mark.parametrize(
    "path,value",
    [
        (("score_rows", 0, "value"), False),
        (("score_rows", 0, "id"), "private"),
        (("score_rows", 0, "association"), "subject_unknown"),
        (("score_rows", 0, "target_observation_index"), None),
        (("counts", "by_data_type", "UNKNOWN"), 1),
    ],
)
def test_minimized_contract_refuses_content_and_false_structural_claims(
    path: tuple[str | int, ...],
    value: object,
) -> None:
    invalid = deepcopy(_index(_report()))
    _assign(invalid, path, value)
    with pytest.raises(ValidationError):
        _validator("langfuse_row_index.v2").validate(invalid)

"""Generated score rules keep kind, association and unknown states distinct."""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from underwrite.cli.local_json import Validator
from underwrite.instrument.evidence.langfuse_accounting import account_langfuse
from underwrite.instrument.evidence.langfuse_row_index import index_langfuse_accounting
from underwrite.instrument.ingest.langfuse_accounting import SOURCES

ROOT = Path(__file__).resolve().parents[2]


def test_generated_contract_is_current_and_valid() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/build_langfuse_accounting.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    Draft202012Validator.check_schema(
        json.loads((ROOT / "contracts/langfuse_accounting.v1.schema.json").read_bytes())
    )
    Draft202012Validator.check_schema(
        json.loads((ROOT / "contracts/langfuse_row_index.v1.schema.json").read_bytes())
    )


def test_rule_bindings_reject_cross_kind_and_false_associations() -> None:
    validator = cast(
        Validator,
        Draft202012Validator(
            json.loads((ROOT / "contracts/langfuse_accounting.v1.schema.json").read_bytes())
        ),
    )
    observation = {"id": "o", "project_id": "p", "trace_id": "t", "type": "SPAN"}
    base = {
        "id": "s",
        "project_id": "p",
        "trace_id": "t",
        "name": "score",
        "data_type": "TEXT",
        "value": 0,
        "string_value": "text",
    }
    report: dict[str, Any] = {
        "schema": "langfuse_accounting.v1",
        "sources": {
            role: {
                "format": selector[0],
                "format_version": selector[1],
                "raw_hash": "sha256:" + "a" * 64,
                "observation_digest": "sha256:" + "b" * 64,
                "claims_verified": False,
            }
            for role, selector in SOURCES.items()
        },
        **account_langfuse(
            [observation],
            [{**base, "observation_id": reference} for reference in ("o", None, "unknown")],
        ),
    }
    validator.validate(report)
    for index, patch in (
        (0, {"target_observation_index": None}),
        (1, {"target_observation_index": 0}),
        (2, {"observation_id": None}),
        (0, {"kind_rule": "NUMERIC_SCORE"}),
        (0, {"value": False}),
        (0, {"association_rule": "TRACE_LEVEL_SCORE"}),
    ):
        invalid = deepcopy(report)
        invalid["scores"][index].update(patch)
        with pytest.raises(ValidationError):
            validator.validate(invalid)


def test_row_index_contract_refuses_private_fields_and_false_target() -> None:
    schema = json.loads((ROOT / "contracts/langfuse_row_index.v1.schema.json").read_bytes())
    validator = Draft202012Validator(schema)
    accounting: dict[str, Any] = {
        "sources": {
            role: {
                "format": selector[0],
                "format_version": selector[1],
                "raw_hash": "sha256:" + "a" * 64,
                "observation_digest": "sha256:" + "b" * 64,
                "claims_verified": False,
            }
            for role, selector in SOURCES.items()
        },
        **account_langfuse(
            [{"id": "o", "project_id": "p", "trace_id": "t", "type": "SPAN"}],
            [
                {
                    "id": "s",
                    "project_id": "p",
                    "trace_id": "t",
                    "observation_id": None,
                    "name": "private",
                    "data_type": "TEXT",
                    "value": 0,
                    "string_value": "private",
                }
            ],
        ),
    }
    result = index_langfuse_accounting(accounting)
    validator.validate(result)  # pyright: ignore[reportUnknownMemberType]
    for section, field, value in (
        ("score_rows", "name", "private"),
        ("score_rows", "value", 0),
        ("score_rows", "target_observation_index", 0),
        ("sources", "observation_digest", "sha256:" + "b" * 64),
    ):
        invalid = cast(dict[str, Any], deepcopy(result))
        if section == "score_rows":
            invalid[section][0][field] = value
        else:
            invalid[section]["scores"][field] = value
        with pytest.raises(ValidationError):
            validator.validate(invalid)  # pyright: ignore[reportUnknownMemberType]

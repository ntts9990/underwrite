"""Standalone behavior and boundary checks."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from underwrite_core.receipt import Artifact, Ladder, Limitations, build_statement

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "contracts" / "receipt.v1.schema.json"


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validator() -> Draft202012Validator:
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate(instance: dict[str, Any]) -> None:
    """Standalone behavior and boundary checks."""
    _validator().validate(instance)  # pyright: ignore[reportUnknownMemberType]


def _good_statement() -> dict[str, Any]:
    return build_statement(
        subjects=[Artifact(role="evaluation_receipt", digest="sha256:" + "ab" * 32)],
        artifacts=[
            Artifact(role="evaluation_receipt", digest="sha256:" + "ab" * 32),
            Artifact(role="reference_receipt", digest="sha256:" + "cd" * 32),
        ],
        limitations=Limitations(
            signature_verification="not_measured",
            raw_included="yes",
            ledger_anchored="not_measured",
            ladder=Ladder(
                selfcheck="pass", mock_replay="pass", aa_vacuous_rate=0.0, mutation_kill_rate=0.95
            ),
        ),
        run_id="run-0001",
        produced_at="2026-09-04T00:00:00Z",
    )


def test_schema_itself_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(_schema())


def test_good_statement_validates() -> None:
    _validate(_good_statement())


def test_statement_missing_a_limitation_field_is_rejected() -> None:
    statement = _good_statement()
    del statement["predicate"]["limitations"]["ledger_anchored"]
    with pytest.raises(ValidationError):
        _validate(statement)


def test_statement_with_extra_unknown_key_is_rejected() -> None:
    statement = _good_statement()
    statement["predicate"]["unexpected_field"] = "nope"
    with pytest.raises(ValidationError):
        _validate(statement)

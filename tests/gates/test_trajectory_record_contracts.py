"""Generated retained trajectory envelopes compose installed contract owners."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import ValidationError

from underwrite.cli.local_json import Validator
from underwrite.instrument.ingest.goppi_trajectory import HISTORY_SCHEMA
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).parents[2]


def test_generated_trajectory_record_contracts_match_authored_owners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "path", [str(ROOT / "scripts"), *sys.path])
    name = "build_trajectory_record_contracts"
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    expected = {HISTORY_SCHEMA: module.build_history(), **module.build_envelopes(ROOT)}
    for schema_name, schema in expected.items():
        authored = ROOT / "contracts" / (schema_name + ".schema.json")
        packaged = ROOT / "src/underwrite/_contracts" / authored.name
        assert json.loads(authored.read_bytes()) == schema
        assert packaged.resolve() == authored.resolve()
        assert packaged.read_bytes() == authored.read_bytes()
        cast(Validator, packaged_validator(schema_name))
        # Nested large results dispatch by a discriminator, not exhaustive oneOf validation.
        definitions = cast(dict[str, Any], schema["$defs"])
        if "analysis" in definitions:
            assert "oneOf" not in definitions["analysis"]
            assert "anyOf" not in definitions["analysis"]
            assert definitions["analysis"]["properties"]["state"]["enum"] == ["result", "error"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "refused"),
        ("source_authentication_independently_verified", 0),
        ("authenticity", "independent_provenance"),
    ],
)
def test_history_contract_refuses_unadopted_states_and_authenticity(
    field: str, value: object
) -> None:
    from underwrite.instrument.ingest.goppi_trajectory import capture_goppi_trajectory

    captured = capture_goppi_trajectory(ROOT / "fixtures/conformance/goppi-trajectory/exchange")
    history: dict[str, Any] = {
        "schema": HISTORY_SCHEMA,
        "state": "not_performed",
        "source_members": [
            {"name": name, "raw_hash": item.sha256, "bytes": item.size}
            for name, item in captured.items()
        ],
        "integrity_scope": None,
        "authenticity": None,
        "source_authentication_independently_verified": False,
        "producer_projection_independently_verified": False,
    }
    validator = cast(Validator, packaged_validator(HISTORY_SCHEMA))
    validator.validate(history)
    history[field] = value
    with pytest.raises(ValidationError):
        validator.validate(history)

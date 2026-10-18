"""Current inspection contracts derive only consumed meaning from exact input owners."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, ValidationError

from scripts import build_common_result_inspection as builder
from underwrite.cli.inspect_result import (
    ACTIONS_SCHEMA,
    INPUT_SCHEMAS,
    LIMITATIONS,
    SCHEMA,
    inspect_result_file,
    inspection_vocabulary,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError

ROOT = Path(__file__).resolve().parents[2]


def _schema(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((ROOT / f"contracts/{name}.schema.json").read_bytes()))


def _owners(tmp_path: Path) -> Path:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    for name in INPUT_SCHEMAS:
        filename = f"{name}.schema.json"
        shutil.copyfile(ROOT / "contracts" / filename, contracts / filename)
    return contracts


def test_cold_generation_has_only_authored_input_owners(tmp_path: Path) -> None:
    contracts = _owners(tmp_path)
    before = {path.name: path.read_bytes() for path in contracts.iterdir()}
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("build_common_result_inspection.py", "_schema_compose.py"):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    command = [sys.executable, str(scripts / "build_common_result_inspection.py")]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = {
        f"{name}.schema.json": (ROOT / f"contracts/{name}.schema.json").read_bytes()
        for name in (SCHEMA, ACTIONS_SCHEMA)
    }
    assert {path.name: path.read_bytes() for path in contracts.iterdir()} == {**before, **expected}
    checked = subprocess.run(
        [*command, "--check"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    for name, schema in builder.build_schemas().items():
        assert (
            expected[f"{name}.schema.json"]
            == (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode()
        )
        Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("source_name", INPUT_SCHEMAS)
def test_summary_processing_population_and_availability_follow_the_input_owner(
    source_name: str,
) -> None:
    source = _schema(source_name)
    output = _schema(SCHEMA)
    fields = output["properties"]
    assert fields["selected_result"]["properties"]["format"] == {"enum": list(INPUT_SCHEMAS)}
    assert fields["declared_processing"] == source["properties"]["processing"]
    assert fields["declared_population"]["oneOf"][1] == source["$defs"]["population"]
    histogram = fields["availability_counts"]["oneOf"][1]
    assert histogram["required"] == [
        *source["$defs"]["item"]["properties"]["availability"]["enum"],
        "total",
    ]
    assert fields["limitations"]["const"] == LIMITATIONS


@pytest.mark.parametrize("source_name", INPUT_SCHEMAS)
def test_actions_use_matching_input_vocabulary_without_copying_source_items(
    source_name: str,
) -> None:
    base, actions = _schema(SCHEMA), _schema(ACTIONS_SCHEMA)
    extra = ("item_next_action_counts", "declared_semantic_binding_counts")
    assert actions["additionalProperties"] is False
    assert actions["required"] == [*base["required"], *extra]
    for name in base["properties"]:
        if name != "schema":
            assert actions["properties"][name] == base["properties"][name]
    for field, names in zip(extra, inspection_vocabulary(_schema(source_name)), strict=True):
        histogram = actions["properties"][field]["oneOf"][1]
        assert histogram["additionalProperties"] is False
        assert histogram["required"] == list(names) == list(histogram["properties"])
        assert all(
            value == {"type": "integer", "minimum": 0} for value in histogram["properties"].values()
        )
        assert actions["allOf"][0]["then"]["properties"][field] == {"type": "object"}
        assert actions["allOf"][0]["else"]["properties"][field] == {"type": "null"}


@pytest.mark.parametrize("mutation", ["processing", "population-bool", "availability"])
def test_consumed_owner_mismatch_is_type_strict_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    contracts = _owners(tmp_path)
    owner = _schema(INPUT_SCHEMAS[1])
    if mutation == "population-bool":
        owner["$defs"]["population"]["properties"]["observed_count"]["minimum"] = False
        # Plain Python equality would overlook this JSON type difference.
        assert owner["$defs"]["population"] == _schema(INPUT_SCHEMAS[0])["$defs"]["population"]
    elif mutation == "availability":
        owner["$defs"]["item"]["properties"]["availability"]["enum"].append("new-state")
    else:
        owner["properties"]["processing"]["properties"]["next_action"]["enum"].append("new-action")
    (contracts / f"{INPUT_SCHEMAS[1]}.schema.json").write_text(json.dumps(owner))
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    with pytest.raises(SchemaConfigurationError, match="INCOMPATIBLE_INSPECTION_INPUT_CONTRACTS"):
        builder.build_schemas()
    assert set(path.name for path in contracts.iterdir()) == {
        name + ".schema.json" for name in INPUT_SCHEMAS
    }


@pytest.mark.parametrize(
    "mutation", ["duplicate-action", "empty-bindings", "non-string", "reference"]
)
def test_malformed_or_referenced_consumed_owner_is_not_silently_broadened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    contracts = _owners(tmp_path)
    owner = _schema(INPUT_SCHEMAS[1])
    if mutation == "duplicate-action":
        names = owner["$defs"]["item"]["properties"]["next_action"]["enum"]
        names.append(names[0])
    elif mutation == "empty-bindings":
        owner["$defs"]["metric_binding"]["oneOf"] = []
    elif mutation == "non-string":
        owner["$defs"]["item"]["properties"]["availability"]["enum"][0] = 1
    else:
        owner["$defs"]["population"]["properties"]["observed_count"] = {"$ref": "#/$defs/elsewhere"}
    (contracts / f"{INPUT_SCHEMAS[1]}.schema.json").write_text(json.dumps(owner))
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    with pytest.raises(SchemaConfigurationError):
        builder.build_schemas()


@pytest.mark.parametrize("mutation", ["missing-field", "extra-key", "negative", "null-analyzed"])
def test_action_contract_closes_histograms(mutation: str) -> None:
    report = inspect_result_file(
        ROOT / "fixtures/examples/evidence/common-result-c55.json", view="actions"
    )
    counts = cast(dict[str, int], report["item_next_action_counts"])
    if mutation == "missing-field":
        del counts["rerun_source"]
    elif mutation == "extra-key":
        counts["private_metric_id"] = 1
    elif mutation == "negative":
        counts["none"] = -1
    else:
        report["item_next_action_counts"] = None
    with pytest.raises(ValidationError):
        Draft202012Validator(_schema(ACTIONS_SCHEMA)).validate(report)  # pyright: ignore[reportUnknownMemberType]


def test_action_contract_requires_null_histograms_without_analyzed_population() -> None:
    report = inspect_result_file(
        ROOT / "fixtures/examples/evidence/common-result-c55.json", view="actions"
    )
    report["declared_processing"] = {
        "state": "insufficient",
        "reason": "MISSING_INPUT",
        "next_action": "supply_input",
    }
    report["declared_population"] = None
    report["availability_counts"] = None
    report["checks"] = {
        "contract_shape": "valid",
        "item_invariants": "not_applicable",
        "source_bytes": "not_checked",
        "producer_authentication": "not_checked",
        "verification_refs": "not_reperformed",
    }
    validator = Draft202012Validator(_schema(ACTIONS_SCHEMA))
    with pytest.raises(ValidationError):
        validator.validate(report)  # pyright: ignore[reportUnknownMemberType]
    report["item_next_action_counts"] = report["declared_semantic_binding_counts"] = None
    validator.validate(report)  # pyright: ignore[reportUnknownMemberType]

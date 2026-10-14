"""The installed DeepEval pack contract embeds the current definition owner."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from jsonschema import Draft202012Validator, ValidationError
from underwrite_core.canonical import content_digest

ROOT = Path(__file__).resolve().parents[2]
NAME = "deepeval_metric_bindings.v1.schema.json"


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _schema() -> dict[str, Any]:
    return json.loads((ROOT / "contracts" / NAME).read_bytes())


def _pack() -> dict[str, Any]:
    definition = json.loads(
        (ROOT / "fixtures/conformance/ume1/metric-definition-example.v1.json").read_bytes()
    )
    semantic = definition["semantic"]
    definition["namespace"] = "synthetic"
    definition["metric_id"] = "fixed_score"
    semantic["construct"] = {
        "id": "synthetic_fixed_score",
        "definition": "Numeric score emitted by the synthetic fixed DeepEval fixture metric.",
    }
    semantic["target"] = {
        "kind": "quantity",
        "id": "fixture_metric_output",
        "definition": "The fixture metric's output, without a truth or quality claim.",
    }
    semantic["value"] = {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
        "unit": "score",
        "desirable_direction": "neither",
    }
    semantic["sampling"]["unit"] = "metric_occurrence"
    semantic["sampling"]["expected_population_basis"] = {
        "id": "reported_occurrences",
        "definition": "Metric occurrences present in the captured fixture TestRun.",
    }
    semantic["applicability"] = {
        "id": "reported_metric",
        "definition": "A metric occurrence recorded in the captured fixture TestRun.",
    }
    semantic["missingness"] = {
        "missing": {"id": "unscored", "definition": "The occurrence contains no score or error."},
        "error": {"id": "evaluator_error", "definition": "The occurrence reports an error."},
        "abstained": {"id": "not_used", "definition": "The fixture has no abstention state."},
    }
    semantic["required_coordinates"] = ["case_index", "metric_index"]
    definition["semantic_digest"] = content_digest(semantic)
    return {
        "schema": "deepeval_metric_bindings.v1",
        "source": {"format": "deepeval.test-run", "format_version": "4.2.8"},
        "name_binding_authority": "owner_asserted_stable_name_not_verified",
        "bindings": [{"source_metric_name": "zero", "definition": definition}],
    }


def test_generated_pack_and_packaged_asset_are_current() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/build_deepeval_metric_bindings.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    packaged = ROOT / "src/underwrite/_contracts" / NAME
    assert packaged.is_symlink()
    assert packaged.readlink() == Path("../../../contracts") / NAME
    assert packaged.read_bytes() == (ROOT / "contracts" / NAME).read_bytes()
    Draft202012Validator.check_schema(_schema())


def test_owner_embedded_pack_accepts_complete_numeric_definition() -> None:
    pack = _pack()
    assert pack["bindings"][0]["definition"]["semantic"]["value"]["unit"] == "score"
    cast(Validator, Draft202012Validator(_schema())).validate(pack)


@pytest.mark.parametrize(
    "mutation",
    [
        "source_version",
        "authority",
        "missing_definition_field",
        "extra_definition_field",
        "text_value",
        "blank_selector",
        "too_many_bindings",
    ],
)
def test_pack_schema_refuses_wrong_profile_or_definition_shape(mutation: str) -> None:
    pack = copy.deepcopy(_pack())
    binding = pack["bindings"][0]
    definition = binding["definition"]
    if mutation == "source_version":
        pack["source"]["format_version"] = "4.1.1"
    elif mutation == "authority":
        pack["name_binding_authority"] = "name_verified"
    elif mutation == "missing_definition_field":
        del definition["semantic"]["missingness"]
    elif mutation == "extra_definition_field":
        definition["unowned_prose"] = "extra"
    elif mutation == "text_value":
        definition["semantic"]["value"]["type"] = "text"
    elif mutation == "blank_selector":
        binding["source_metric_name"] = "  "
    else:
        pack["bindings"] = [copy.deepcopy(binding) for _ in range(65)]
    with pytest.raises(ValidationError):
        cast(Validator, Draft202012Validator(_schema())).validate(pack)

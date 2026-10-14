"""Public UME-1 vectors bind contract examples to real producer fixture facts."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import content_digest, digest_bytes

from underwrite.cli.local_json import Validator
from underwrite.instrument.evidence.goppi_baseline import project_baseline_bundle
from underwrite.instrument.ingest.goppi_baseline import capture_baseline_documents

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "fixtures/conformance/ume1"
EXTERNAL = ROOT / "fixtures/golden/external"
GOPPI = ROOT / "fixtures/examples/baseline-event/exchange"


def _json(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_bytes())
    return value


def _validator(name: str) -> Validator:
    schema = _json(ROOT / f"contracts/{name}.schema.json")
    Draft202012Validator.check_schema(schema)
    return cast(Validator, Draft202012Validator(schema))


def _vector(name: str) -> dict[str, Any]:
    return _json(VECTORS / name)


def _reject(result: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _validator("evidence_result.v1").validate(result)


def _assert_record_coordinates(result: dict[str, Any]) -> None:
    """Cross-field rule JSON Schema cannot express: one item per parsed source record."""
    population = result["population"]
    if population is None:
        assert result["items"] == []
        return
    coordinates = [json.dumps(item["coordinate"], sort_keys=True) for item in result["items"]]
    assert len(coordinates) == population["observed_count"]
    assert len(coordinates) == len(set(coordinates))


def test_deepeval_vector_keeps_observed_zero_and_metric_error_separate() -> None:
    raw = EXTERNAL / "deepeval-4.2.8/ordinary.json"
    source = _json(raw)
    result = _vector("deepeval-4.2.8.evidence_result.v1.json")
    _validator("evidence_result.v1").validate(result)
    _assert_record_coordinates(result)
    assert result["source"]["identity"]["raw_hash"] == digest_bytes(raw.read_bytes())
    metrics = source["testCases"][0]["metricsData"]
    assert len(metrics) == len(result["items"]) == result["population"]["observed_count"]
    assert result["population"]["unit"] == "metric_occurrence"
    assert result["population"]["planned_count"] is None
    for index, item in enumerate(result["items"]):
        assert item["coordinate"] == {
            "kind": "metric_occurrence", "case_index": 0, "metric_index": index
        }
        assert item["metric"] == {"semantic_binding": "unbound", "source_metric_index": index}
        assert item["origin"] == "source_claim"
        assert item["permitted_effect"] == "descriptive"
        if "error" in metrics[index]:
            assert (item["availability"], item["value"], item["reason"]) == (
                "error", None, "SOURCE_ERROR"
            )
        else:
            assert (item["availability"], item["value"]) == (
                "observed", metrics[index]["score"]
            )
    assert result["items"][1]["value"] == 0
    assert all(check["target"] != "label" for check in result["verification"])


def test_promptfoo_vector_preserves_row_unit_and_unverified_source_scores() -> None:
    raw = EXTERNAL / "promptfoo-0.124.1/results.json"
    source = _json(raw)
    rows = source["results"]["results"]
    result = _vector("promptfoo-0.124.1.evidence_result.v1.json")
    _validator("evidence_result.v1").validate(result)
    _assert_record_coordinates(result)
    assert result["source"]["identity"]["raw_hash"] == digest_bytes(raw.read_bytes())
    assert result["population"] == {"unit": "row", "observed_count": 7, "planned_count": None}
    stats = source["results"]["stats"]
    declared_total = sum(stats[key] for key in ("successes", "failures", "errors"))
    assert len(rows) == len(result["items"]) == declared_total
    for index, item in enumerate(result["items"]):
        assert item["coordinate"] == {"kind": "row", "index": index}
        grade = rows[index].get("gradingResult")
        if isinstance(grade, dict) and "score" in grade:
            assert (item["availability"], item["value"]) == ("observed", grade["score"])
        else:
            assert (item["availability"], item["value"]) == ("error", None)
        assert item["metric"]["semantic_binding"] == "unbound"
        assert item["permitted_effect"] == "descriptive"
    assert result["items"][1]["value"] == 0
    assert result["items"][6]["value"] == rows[6]["gradingResult"]["score"]
    assert rows[6]["success"] is True  # A source verdict, not independent truth.
    assert rows[5].get("response", {}).get("cost") is None and rows[5]["cost"] == 0


def test_goppi_vector_binds_each_file_and_keeps_false_distinct_from_missing() -> None:
    result = _vector("goppi-synthetic-v2.evidence_result.v1.json")
    _validator("evidence_result.v1").validate(result)
    _assert_record_coordinates(result)
    files = ("manifest.json", "export.json", "rows.jsonl", "schema.json", "scope.json")
    identity = result["source"]["identity"]
    assert identity["kind"] == "bound_bundle"
    assert identity["manifest_raw_hash"] == digest_bytes((GOPPI / files[0]).read_bytes())
    assert identity["members"] == {
        name: digest_bytes((GOPPI / name).read_bytes())
        for name in files
    }
    projected = project_baseline_bundle(capture_baseline_documents(GOPPI), direction="higher")
    readings = cast(list[dict[str, Any]], projected["readings"])
    projected_rows = cast(list[dict[str, Any]], readings[0]["rows"])
    assert [row["outcome"] for row in projected_rows] == [1, 0, 0, None]
    export = _json(GOPPI / "export.json")
    scope = _json(GOPPI / "scope.json")
    assert result["population"] == {"unit": "case", "observed_count": 4, "planned_count": 4}
    assert export["counts"] == {"planned": 4, "observed_true": 1, "observed_false": 2, "missing": 1}
    rows = [json.loads(line) for line in (GOPPI / "rows.jsonl").read_text().splitlines()]
    statuses = [row["outcome"]["primary_status"] for row in rows]
    assert [row["case_id"] for row in rows] == scope["selection"]["case_ids"]
    assert statuses == ["observed_true", "observed_false", "observed_false", "missing"]
    assert rows[2]["outcome"]["reason"] == "indeterminate:agent_budget"
    assert [(item["availability"], item["value"]) for item in result["items"]] == [
        ("observed", True), ("observed", False), ("observed", False), ("missing", None)
    ]
    assert {check["target"] for check in result["verification"]} == {
        "source_bytes", "bundle_binding", "planned_population"
    }
    assert all(check["target"] != "label" for check in result["verification"])


def test_metric_definition_digest_binds_meaning_not_method_or_policy() -> None:
    definition = _vector("metric-definition-example.v1.json")
    _validator("metric_definition.v1").validate(definition)
    assert definition["semantic_digest"] == content_digest(definition["semantic"])
    changed_unit = deepcopy(definition)
    changed_unit["semantic"]["value"]["unit"] = "seconds"
    assert content_digest(changed_unit["semantic"]) != definition["semantic_digest"]
    changed_method = deepcopy(definition)
    changed_method["method"] = {"id": "timer", "version": "v2", "digest": "sha256:" + "a" * 64}
    assert content_digest(changed_method["semantic"]) == definition["semantic_digest"]
    assert changed_method["method"] != definition["method"]


def test_result_schema_rejects_false_promotion_and_state_conflation() -> None:
    base = _vector("deepeval-4.2.8.evidence_result.v1.json")
    for mutation in (
        "zero_as_missing", "error_as_zero", "gate_field", "unbound_advisory",
        "hash_as_label_check", "computed_unbound", "ambiguous_coordinate",
    ):
        bad = deepcopy(base)
        if mutation == "zero_as_missing":
            bad["items"][1]["availability"] = "missing"
        elif mutation == "error_as_zero":
            bad["items"][2]["value"] = 0
        elif mutation == "gate_field":
            bad["verdict"] = "pass"
        elif mutation == "unbound_advisory":
            bad["items"][1]["permitted_effect"] = "advisory"
        elif mutation == "hash_as_label_check":
            bad["verification"].append({
                "target": "label", "method": "sha256", "scope": "artifact",
                "evidence_ref": "fixture_sha256",
            })
        elif mutation == "computed_unbound":
            bad["items"][1]["origin"] = "computed"
        else:
            bad["items"][1]["coordinate"]["raw_path"] = "/private/answer"
        _reject(bad)


def test_record_coordinates_are_unique_even_when_schema_cannot_compare_array_members() -> None:
    result = _vector("deepeval-4.2.8.evidence_result.v1.json")
    duplicate = deepcopy(result)
    duplicate["items"][1]["coordinate"] = duplicate["items"][0]["coordinate"]
    _validator("evidence_result.v1").validate(duplicate)
    with pytest.raises(AssertionError):
        _assert_record_coordinates(duplicate)
    omitted = deepcopy(result)
    omitted["items"].pop()
    with pytest.raises(AssertionError):
        _assert_record_coordinates(omitted)


def test_state_reason_action_and_population_coordinate_pairs_are_closed() -> None:
    base = _vector("promptfoo-0.124.1.evidence_result.v1.json")
    bad_unit = deepcopy(base)
    bad_unit["population"]["unit"] = "case"
    _reject(bad_unit)
    bad_reason = deepcopy(base)
    bad_reason["items"][2]["reason"] = "SOURCE_MISSING"
    _reject(bad_reason)
    bad_action = deepcopy(base)
    bad_action["items"][2]["next_action"] = "supply_label"
    _reject(bad_action)
    bad_processing = deepcopy(base)
    bad_processing["processing"] = {
        "state": "unsupported", "reason": "MISSING_INPUT", "next_action": "supply_input"
    }
    bad_processing["population"] = None
    bad_processing["items"] = []
    _reject(bad_processing)


def test_run_level_definition_has_representable_coordinate() -> None:
    definition = _vector("metric-definition-example.v1.json")
    definition["semantic"]["sampling"]["unit"] = "run"
    definition["semantic"]["required_coordinates"] = ["run_index"]
    definition["semantic_digest"] = content_digest(definition["semantic"])
    _validator("metric_definition.v1").validate(definition)
    result = _vector("deepeval-4.2.8.evidence_result.v1.json")
    result["population"] = {"unit": "run", "observed_count": 1, "planned_count": None}
    result["items"] = [result["items"][0]]
    result["items"][0]["coordinate"] = {"kind": "run", "index": 0}
    _validator("evidence_result.v1").validate(result)
    impossible = deepcopy(definition)
    impossible["semantic"]["required_coordinates"] = ["run_index", "case_index"]
    impossible["semantic_digest"] = content_digest(impossible["semantic"])
    with pytest.raises(ValidationError):
        _validator("metric_definition.v1").validate(impossible)
    mismatched_unit = deepcopy(definition)
    mismatched_unit["semantic"]["sampling"]["unit"] = "case"
    mismatched_unit["semantic_digest"] = content_digest(mismatched_unit["semantic"])
    with pytest.raises(ValidationError):
        _validator("metric_definition.v1").validate(mismatched_unit)


def test_unsupported_and_all_not_applicable_are_explicitly_non_successful() -> None:
    base = _vector("deepeval-4.2.8.evidence_result.v1.json")
    no_input = deepcopy(base)
    no_input["source"] = None
    no_input["processing"] = {
        "state": "insufficient", "reason": "MISSING_INPUT", "next_action": "supply_input"
    }
    no_input["population"] = None
    no_input["items"] = []
    no_input["verification"] = []
    _validator("evidence_result.v1").validate(no_input)
    no_input["verification"] = base["verification"]
    _reject(no_input)
    no_input["verification"] = []
    no_input["processing"] = {"state": "analyzed", "reason": None, "next_action": "none"}
    _reject(no_input)

    unsupported = deepcopy(base)
    unsupported["source"]["format_version"] = "9999.0"
    unsupported["processing"] = {
        "state": "unsupported", "reason": "UNSUPPORTED_FORMAT",
        "next_action": "provide_supported_export",
    }
    unsupported["population"] = None
    unsupported["items"] = []
    _validator("evidence_result.v1").validate(unsupported)
    unsupported["items"] = [base["items"][0]]
    _reject(unsupported)

    all_na = deepcopy(base)
    all_na["items"] = [base["items"][0]]
    all_na["items"][0]["value"] = 1  # JevEval-style source aggregate, not a measured success.
    all_na["items"][0]["applicability"] = "not_applicable"
    all_na["population"]["observed_count"] = 1
    _validator("evidence_result.v1").validate(all_na)
    all_na["items"][0]["permitted_effect"] = "advisory"
    _reject(all_na)

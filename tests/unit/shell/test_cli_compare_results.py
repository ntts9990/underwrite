"""Two-file comparison preserves marginal deltas and unestablished relations."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from underwrite.cli import compare_results
from underwrite.cli.main import main
from underwrite.instrument.ingest.deepeval_binding import audit_deepeval_bound_file
from underwrite.instrument.ingest.schema import packaged_validator

SOURCE = Path("fixtures/examples/evidence/common-result-c55.json")
V2_SOURCE = Path("fixtures/examples/deepeval-auto-4.3.1/ordinary.json")
V2_PACK = Path("fixtures/examples/evidence/synthetic-deepeval.bindings.v2.json")
ERROR_EXIT = 2


def _write(path: Path, value: dict[str, Any]) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_declared_marginals_are_not_item_transitions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = cast(dict[str, Any], json.loads(SOURCE.read_text()))
    candidate = deepcopy(baseline)
    item = candidate["items"][4]
    item.update(availability="observed", next_action="none", reason=None, value=True)
    before = _write(tmp_path / "PRIVATE_BEFORE.json", baseline)
    after = _write(tmp_path / "PRIVATE_AFTER.json", candidate)

    report = cast(dict[str, Any], compare_results.compare_result_files(before, after))
    assert report["schema"] == "common_result_marginal_comparison.v1"
    calculation = report["marginal_calculation"]
    assert calculation["state"] == "computed" and calculation["reason"] is None
    assert calculation["direction"] == "candidate_minus_baseline"
    assert calculation["observed_population_count_delta"] == 0
    assert calculation["planned_population_count_delta"] == 0
    assert calculation["availability_counts"]["delta"] == {
        "observed": 1,
        "missing": -1,
        "error": 0,
        "abstained": 0,
        "total": 0,
    }
    assert calculation["item_next_action_counts"]["delta"]["inspect_source"] == -1
    assert calculation["item_next_action_counts"]["delta"]["none"] == 1
    assert report["item_correspondence"] == {
        "state": "not_established",
        "reason": "CROSS_RESULT_ITEM_BINDING_NOT_SUPPLIED",
    }
    assert report["semantic_comparability"] == "unestablished"
    assert report["next_verification_tasks"][0]["code"] == "ESTABLISH_CROSS_RESULT_ITEM_BINDING"
    encoded = json.dumps(report)
    assert len(encoded.encode()) < 16 * 1024
    assert all(
        token not in encoded
        for token in ("PRIVATE_BEFORE", "PRIVATE_AFTER", "coordinate", '"value"')
    )

    assert (
        main(["compare-results", "--baseline", str(before), "--candidate", str(after), "--json"])
        == 0
    )
    captured = capsys.readouterr()
    assert captured.err == "" and json.loads(captured.out) == report
    packaged_validator(report["schema"]).validate(report)  # pyright: ignore[reportUnknownMemberType]


def test_incompatible_schema_or_unanalyzed_input_has_no_partial_delta(tmp_path: Path) -> None:
    baseline = cast(dict[str, Any], json.loads(SOURCE.read_text()))
    before = _write(tmp_path / "before.json", baseline)
    v2 = cast(dict[str, Any], audit_deepeval_bound_file(V2_SOURCE, V2_PACK))
    after = _write(tmp_path / "after.json", v2)
    mismatch = cast(dict[str, Any], compare_results.compare_result_files(before, after))
    calculation = mismatch["marginal_calculation"]
    assert calculation["state"] == "not_computed"
    assert calculation["reason"] == "RESULT_FORMAT_MISMATCH"
    assert calculation["availability_counts"] is None
    assert calculation["item_next_action_counts"] is None
    assert calculation["declared_semantic_binding_counts"] is None
    assert calculation["observed_population_count_delta"] is None
    assert mismatch["item_correspondence"]["state"] == "not_established"

    insufficient = deepcopy(baseline)
    insufficient["processing"] = {
        "state": "insufficient",
        "reason": "MISSING_INPUT",
        "next_action": "supply_input",
    }
    insufficient["population"] = None
    insufficient["source"] = None
    insufficient["items"] = []
    not_ready = cast(
        dict[str, Any], compare_results.compare_result_files(before, _write(after, insufficient))
    )
    assert not_ready["marginal_calculation"]["state"] == "not_computed"
    assert not_ready["marginal_calculation"]["reason"] == "RESULT_NOT_ANALYZED"
    assert not_ready["marginal_calculation"]["availability_counts"] is None


def test_invalid_input_is_typed_and_never_echoes_private_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "PRIVATE_CANARY.json"
    bad.write_text("not-json", encoding="utf-8")
    assert (
        main(["compare-results", "--baseline", str(bad), "--candidate", str(SOURCE), "--json"])
        == ERROR_EXIT
    )
    captured = capsys.readouterr()
    assert captured.out == "" and "PRIVATE_CANARY" not in captured.err
    error = json.loads(captured.err)
    assert error["schema"] == "evidence_error.v1" and error["code"] == "INVALID_INPUT"

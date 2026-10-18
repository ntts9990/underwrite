"""Selected Promptfoo V3 fields preserve existing count and row decisions."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from underwrite.instrument.evidence.accounting import audit_promptfoo_with_rows
from underwrite.instrument.evidence.promptfoo_compat import (
    READER_PROFILE,
    PromptfooCompatibilityError,
    account_promptfoo_compatible,
)

FIXTURES = Path("fixtures/golden/external")


def _source(version: str = "0.124.1") -> dict[str, Any]:
    folder = "promptfoo-0.124.1" if version == "0.124.1" else "promptfoo"
    return cast(dict[str, Any], json.loads((FIXTURES / folder / "results.json").read_bytes()))


@pytest.mark.parametrize("version", ["0.123.0", "0.124.1"])
def test_public_regressions_reuse_the_pure_classifier(version: str) -> None:
    source = _source(version)
    full = cast(dict[str, Any], account_promptfoo_compatible(source))
    indexed = cast(dict[str, Any], account_promptfoo_compatible(source, row_index=True))
    expected, rows = audit_promptfoo_with_rows(
        source["results"]["stats"], source["results"]["results"]
    )
    for key, value in expected.items():
        assert full[key] == indexed[key] == value
    assert indexed["rows"] == rows
    assert indexed["verification"] == []
    assert (
        full["source"]
        == indexed["source"]
        == {
            "format": "promptfoo.eval-output",
            "summary_version": 3,
            "producer_version": version,
            "producer_version_state": "declared",
            "claims_verified": False,
        }
    )
    assert full["reader_profile"] == indexed["reader_profile"] == READER_PROFILE
    assert full["scope"]["selected_fields_only"] is True
    assert indexed["scope"]["values"] == "withheld"
    assert "rows" not in full and "observation_digest" not in full


@pytest.mark.parametrize("metadata", [None, {}, {"other": "PRIVATE_CANARY"}])
def test_missing_declaration_stays_unknown(metadata: object) -> None:
    source = _source()
    source["metadata"] = metadata
    report = cast(dict[str, Any], account_promptfoo_compatible(source))
    assert report["source"]["producer_version"] is None
    assert report["source"]["producer_version_state"] == "unknown"


def test_absent_metadata_stays_unknown() -> None:
    source = _source()
    del source["metadata"]
    report = cast(dict[str, Any], account_promptfoo_compatible(source))
    assert report["source"]["producer_version"] is None
    assert report["source"]["producer_version_state"] == "unknown"


def test_future_declaration_and_additive_fields_do_not_change_selected_counts() -> None:
    source = _source()
    expected = account_promptfoo_compatible(source)
    source["metadata"]["promptfooVersion"] = "99.100.101-alpha.1+build.007"
    source["metadata"]["newField"] = "PRIVATE_CANARY"
    source["config"]["newField"] = "PRIVATE_CANARY"
    source["results"]["newField"] = "PRIVATE_CANARY"
    source["results"]["results"][0]["newField"] = "PRIVATE_CANARY"
    source["results"]["prompts"] = ["PRIVATE_CANARY"]
    report = cast(dict[str, Any], account_promptfoo_compatible(source, row_index=True))
    assert report["source"]["producer_version"] == "99.100.101-alpha.1+build.007"
    assert report["source"]["producer_version_state"] == "declared"
    for key in ("producer_stats", "row_tallies", "deltas", "declared_relation", "findings"):
        assert report[key] == expected[key]
    assert "PRIVATE_CANARY" not in json.dumps(report)
    assert "UNCONSUMED_FIELDS_AND_POPULATIONS_NOT_ANALYZED" in report["limitations"]


@pytest.mark.parametrize(
    "value",
    [
        None,
        False,
        1,
        "",
        "PRIVATE_CANARY",
        "/private/PRIVATE_CANARY",
        "0.124.1\nPRIVATE_CANARY",
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "1.2.3-alpha.01",
        "1.2.3-alpha..1",
        "1.2.3+build..1",
        "1.2.3/extra",
        "1.2.3\u202e",
        "1.2.3" + "A" * 129,
    ],
)
def test_malformed_present_release_never_reaches_a_result(value: object) -> None:
    source = _source()
    source["metadata"]["promptfooVersion"] = value
    with pytest.raises(PromptfooCompatibilityError) as caught:
        account_promptfoo_compatible(source)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "INVALID_INPUT",
        "PROMPTFOO_PRODUCER_VERSION_INVALID",
        "/metadata/promptfooVersion",
    )
    assert "PRIVATE_CANARY" not in str(caught.value)


@pytest.mark.parametrize("value", [False, [], "PRIVATE_CANARY", 1])
def test_malformed_metadata_refuses_at_metadata(value: object) -> None:
    source = _source()
    source["metadata"] = value
    with pytest.raises(PromptfooCompatibilityError) as caught:
        account_promptfoo_compatible(source)
    assert (caught.value.code, caught.value.location) == ("INVALID_INPUT", "/metadata")
    assert "PRIVATE_CANARY" not in str(caught.value)


@pytest.mark.parametrize("version", [None, False, "3", 2, 4])
def test_unknown_or_malformed_summary_generation_never_falls_back(version: object) -> None:
    source = _source()
    source["results"]["version"] = version
    with pytest.raises(PromptfooCompatibilityError) as caught:
        account_promptfoo_compatible(source)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        "UNSUPPORTED_PROFILE",
        "PROMPTFOO_SUMMARY_VERSION_UNSUPPORTED",
        "/results/version",
    )


@pytest.mark.parametrize(
    ("mutation", "code", "location"),
    [
        ("bad_reason", "INVALID_INPUT", "/results/results"),
        ("bad_success", "INVALID_INPUT", "/results/results"),
        ("bad_grading", "INVALID_INPUT", "/results/results"),
        ("bad_count", "INVALID_INPUT", "/results/stats"),
        ("row_cap", "COMPUTATION_LIMIT_EXCEEDED", "/results/results"),
    ],
)
def test_consumed_semantics_and_budget_refuse(mutation: str, code: str, location: str) -> None:
    source = _source()
    summary = source["results"]
    if mutation == "bad_reason":
        summary["results"][0]["failureReason"] = 3
    elif mutation == "bad_success":
        summary["results"][0]["success"] = "PRIVATE_CANARY"
    elif mutation == "bad_grading":
        summary["results"][0]["gradingResult"] = {"pass": "PRIVATE_CANARY"}
    elif mutation == "bad_count":
        summary["stats"]["successes"] = -1
    else:
        summary["results"] = [deepcopy(summary["results"][0]) for _ in range(2001)]
    with pytest.raises(PromptfooCompatibilityError) as caught:
        account_promptfoo_compatible(source, row_index=True)
    assert (caught.value.code, caught.value.location) == (code, location)
    assert "PRIVATE_CANARY" not in str(caught.value)


@pytest.mark.parametrize("payload", [None, [], "PRIVATE_CANARY", {}])
def test_selected_input_requires_actual_output_object_and_summary(payload: object) -> None:
    with pytest.raises(PromptfooCompatibilityError) as caught:
        account_promptfoo_compatible(payload)
    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.location == ("/results" if payload == {} else "/")

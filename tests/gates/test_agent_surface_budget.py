"""The model-visible agent surface is measured exactly and may only shrink."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from scripts import agent_surface_metrics
from underwrite.cli import main as cli_main

# The retained-trajectory summary adds its 6,176-byte schema; the A3 discovery
# wording repair reduces that catalog by 9 bytes. The paired-review summary
# adds 5,988 canonical bytes, including its installed schema and three inputs.
# Its source-derived conditional limitation rules add 552 bytes to reject
# contradictory present/absent and declared/unknown summaries. The ninth
# cross-artifact summary adds 10,363 bytes for its installed output contract,
# source-bound inputs, and closed owner/omission/count invariants. The tenth
# readiness summary adds 12,046 net bytes for its installed output contract,
# metadata counts and profile description. The eleventh paged record list adds
# 8,529 bytes for its installed output contract, continuation inputs, and
# exact offset-pair/count/closed page-state constraints. The
# twelfth manifest summary adds 7,876 bytes for the installed output contract,
# exact file names and input/output continuation constraints. The original CLI
# thirteenth stored-byte verification summary adds 3,167 bytes for its installed
# population-safe check-state arrays and exact record input. The original CLI
# views remain intact. The fourteenth bounded JSON read adds 2,573 bytes for
# the existing document schema, exact file selector and untrusted-content warning.
# The original full CLI view remains intact.
# The fifteenth inspect tool adds 3,456 bytes for the existing finite artifact
# inspection contract and its case selector; the original CLI output remains intact.
# The sixteenth Promptfoo summary adds 5,083 bytes for source-derived count,
# action and omission rules; full accounting and row-index views remain intact.
# The seventeenth manifest count audit adds 6,990 bytes for its shared summary
# schema, supported profile pair, and explicit expected-hash binding semantics.
# The eighteenth DeepEval accounting summary adds 10,347 bytes for both
# reader-profile count contracts and exact source/missingness limitations.
# The nineteenth trace summary adds 5,921 bytes for source status, parent-link
# populations and explicit omission without carrying individual pointers.
# The twentieth owner-bound DeepEval summary adds 7,882 bytes for paired source
# hashes, observed item states and semantic-binding vocabularies without raw items.
# The twenty-first Langfuse summary adds 13,241 bytes for both reader-family
# count, retrieval and missingness contracts while omitting observation/score rows.
# The twenty-second score-source tool adds 7,533 bytes for its existing closed
# known/unknown source count contract, without another CLI schema or view.
MAX_TOOLS_LIST_CANONICAL_JSON_BYTES = 153102
MAX_TOOL_DESCRIPTION_UTF8_BYTES = {
    "underwrite_capabilities": 221,
    "underwrite_inspect_result_v1": 243,
    "underwrite_inspect_result_v2": 243,
    "underwrite_audit_baseline": 271,
    "underwrite_audit_promptfoo": 347,
    "underwrite_audit_counts": 377,
    "underwrite_audit_deepeval": 402,
    "underwrite_audit_deepeval_trace": 316,
    "underwrite_audit_deepeval_bound": 364,
    "underwrite_audit_langfuse": 356,
    "underwrite_audit_langfuse_score_source": 316,
    "underwrite_inspect": 341,
    "underwrite_inspect_result_v1_actions": 247,
    "underwrite_inspect_result_v2_actions": 247,
    "underwrite_records_read_trajectory": 290,
    "underwrite_review_pair": 242,
    "underwrite_review_artifacts": 356,
    "underwrite_review_artifacts_readiness": 380,
    "underwrite_records_list": 353,
    "underwrite_records_show": 432,
    "underwrite_records_verify": 300,
    "underwrite_records_read_json": 389,
}


def _assert_tools_list_budget(metrics: agent_surface_metrics.Metrics) -> None:
    assert metrics["tools_list_canonical_json_bytes"] <= MAX_TOOLS_LIST_CANONICAL_JSON_BYTES


def _assert_tool_description_budget(metrics: agent_surface_metrics.Metrics) -> None:
    measured = metrics["tool_description_utf8_bytes"]
    assert measured.keys() == MAX_TOOL_DESCRIPTION_UTF8_BYTES.keys()
    assert all(
        measured[name] <= ceiling for name, ceiling in MAX_TOOL_DESCRIPTION_UTF8_BYTES.items()
    )


def test_measurement_is_versioned_and_matches_registered_surface(
    capsys: pytest.CaptureFixture[str],
) -> None:
    measured = agent_surface_metrics.measure()

    assert measured == agent_surface_metrics.measure()
    assert measured["schema"] == "underwrite.agent_surface_metrics.v1"
    assert measured["descriptor_commands"] == len(cli_main.COMMAND_DESCRIPTORS)
    assert measured["descriptor_profiles"] == sum(
        len(descriptor.profiles) for descriptor in cli_main.COMMAND_DESCRIPTORS
    )
    assert set(measured["tool_description_utf8_bytes"]) == {
        "underwrite_capabilities",
        "underwrite_inspect_result_v1",
        "underwrite_inspect_result_v2",
        "underwrite_audit_baseline",
        "underwrite_audit_promptfoo",
        "underwrite_audit_counts",
        "underwrite_audit_deepeval",
        "underwrite_audit_deepeval_trace",
        "underwrite_audit_deepeval_bound",
        "underwrite_audit_langfuse",
        "underwrite_audit_langfuse_score_source",
        "underwrite_inspect",
        "underwrite_inspect_result_v1_actions",
        "underwrite_inspect_result_v2_actions",
        "underwrite_records_read_trajectory",
        "underwrite_review_pair",
        "underwrite_review_artifacts",
        "underwrite_review_artifacts_readiness",
        "underwrite_records_list",
        "underwrite_records_show",
        "underwrite_records_verify",
        "underwrite_records_read_json",
    }
    assert measured["tool_canonical_json_bytes"].keys() == (
        measured["tool_description_utf8_bytes"].keys()
    )
    assert measured["output_schema_canonical_json_bytes"].keys() == (
        measured["tool_description_utf8_bytes"].keys()
    )
    assert measured["output_schema_total_bytes"] == sum(
        measured["output_schema_canonical_json_bytes"].values()
    )
    assert measured["output_schema_total_bytes"] == (
        measured["output_schema_unique_bytes"] + measured["output_schema_duplicate_bytes"]
    )
    assert measured["output_schema_duplicate_bytes"] == sum(
        group["duplicate_bytes"] for group in measured["output_schema_duplicate_groups"]
    )
    assert all(
        len(group["tools"]) > 1
        and group["duplicate_bytes"]
        == group["schema_canonical_json_bytes"] * (len(group["tools"]) - 1)
        for group in measured["output_schema_duplicate_groups"]
    )
    assert sum(measured["tool_canonical_json_bytes"].values()) < measured[
        "tools_list_canonical_json_bytes"
    ]
    assert cli_main.main(["capabilities", "--json"]) == 0
    assert measured["capabilities_json_stdout_bytes"] == len(
        capsys.readouterr().out.encode("utf-8")
    )
    _assert_tool_description_budget(measured)
    _assert_tools_list_budget(measured)


def test_description_shrink_passes_and_growth_fails_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = agent_surface_metrics.measure()
    shorter_descriptors = tuple(
        replace(descriptor, help="Capabilities.")
        if descriptor.command == "capabilities"
        else descriptor
        for descriptor in cli_main.COMMAND_DESCRIPTORS
    )
    monkeypatch.setattr(cli_main, "COMMAND_DESCRIPTORS", shorter_descriptors)

    shorter = agent_surface_metrics.measure()

    assert (
        shorter["tool_description_utf8_bytes"]["underwrite_capabilities"]
        < baseline["tool_description_utf8_bytes"]["underwrite_capabilities"]
    )
    assert shorter["tools_list_canonical_json_bytes"] < baseline["tools_list_canonical_json_bytes"]
    assert shorter["tool_canonical_json_bytes"]["underwrite_capabilities"] < (
        baseline["tool_canonical_json_bytes"]["underwrite_capabilities"]
    )
    _assert_tool_description_budget(shorter)
    _assert_tools_list_budget(shorter)

    grown_descriptors = tuple(
        replace(
            descriptor,
            help=descriptor.help + "x" * (MAX_TOOLS_LIST_CANONICAL_JSON_BYTES + 1),
        )
        if descriptor.command == "capabilities"
        else descriptor
        for descriptor in cli_main.COMMAND_DESCRIPTORS
    )
    monkeypatch.setattr(cli_main, "COMMAND_DESCRIPTORS", grown_descriptors)

    grown = agent_surface_metrics.measure()

    assert grown["descriptor_commands"] == baseline["descriptor_commands"]
    assert grown["descriptor_profiles"] == baseline["descriptor_profiles"]
    assert (
        grown["tool_description_utf8_bytes"]["underwrite_capabilities"]
        > baseline["tool_description_utf8_bytes"]["underwrite_capabilities"]
    )
    assert grown["tools_list_canonical_json_bytes"] > baseline["tools_list_canonical_json_bytes"]
    with pytest.raises(AssertionError):
        _assert_tool_description_budget(grown)
    with pytest.raises(AssertionError):
        _assert_tools_list_budget(grown)


def test_cli_prints_versioned_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert agent_surface_metrics.main([]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == ("underwrite.agent_surface_metrics.v1")

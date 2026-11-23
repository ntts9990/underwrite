"""Read-only tools derive from CLI registrations and installed contracts."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from underwrite.cli import main as cli_main
from underwrite.cli import records as record_cli
from underwrite.instrument.ingest.schema import packaged_validator


def test_capabilities_tool_uses_registered_descriptor_and_installed_schema() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(item for item in catalog() if item["name"] == "underwrite_capabilities")
    descriptor = next(row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "capabilities")
    assert tool["name"] == "underwrite_capabilities"
    description = tool["description"]
    assert isinstance(description, str)
    assert description.startswith(descriptor.help)
    assert "Use when:" in description
    assert "No input file is required." in description
    assert "Do not use when:" in description
    assert tool["inputSchema"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert tool["outputSchema"] == packaged_validator("capabilities.v1").schema
    assert tool["annotations"] == {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }


def test_inspect_result_summary_tools_follow_the_registered_input_profiles() -> None:
    from underwrite.agent_surface.tools import catalog

    tools = catalog()
    assert [tool["name"] for tool in tools] == [
        "underwrite_capabilities",
        "underwrite_inspect_result_v1",
        "underwrite_inspect_result_v2",
        "underwrite_audit_baseline",
        "underwrite_inspect",
        "underwrite_audit_promptfoo",
        "underwrite_audit_counts",
        "underwrite_audit_deepeval",
        "underwrite_audit_deepeval_trace",
        "underwrite_audit_deepeval_bound",
        "underwrite_audit_langfuse",
        "underwrite_audit_langfuse_score_source",
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
    ]
    descriptor = next(
        row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "inspect-result"
    )
    for version, tool in zip(("v1", "v2"), tools[1:3], strict=True):
        profile = next(
            row for row in descriptor.profiles if row.name == f"evidence_result.{version}"
        )
        assert profile.required_inputs == ("file",)
        assert profile.output_schemas == ("common_result_inspection.v3",)
        assert tool["outputSchema"] == packaged_validator(profile.output_schemas[0]).schema
        assert tool["inputSchema"] == {
            "type": "object",
            "properties": {"file": {"type": "string", "minLength": 1}},
            "required": ["file"],
            "additionalProperties": False,
        }
        assert f"evidence_result.{version}" in str(tool["description"])


def test_baseline_tool_follows_registered_directory_profile() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(item for item in catalog() if item["name"] == "underwrite_audit_baseline")
    descriptor = next(
        row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "audit-baseline"
    )
    profile = descriptor.profiles[0]
    assert profile.name == "goppi-baseline-exchange-v2"
    assert profile.required_inputs == ("exchange",)
    assert profile.output_schemas == ("evidence_result.v2",)
    assert tool["outputSchema"] == packaged_validator("evidence_result.v2").schema
    assert tool["inputSchema"] == {
        "type": "object",
        "properties": {"exchange": {"type": "string", "minLength": 1}},
        "required": ["exchange"],
        "additionalProperties": False,
    }
    assert str(tool["description"]).startswith(descriptor.help)
    assert "Use when:" in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_inspect_tool_follows_registered_artifact_case_profile() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(item for item in catalog() if item["name"] == "underwrite_inspect")
    descriptor = next(row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "inspect")
    profile = descriptor.profiles[0]
    assert profile.name == "artifact-case"
    assert profile.required_inputs == ("case",)
    assert profile.input_schemas == ("artifact_case.v1",)
    assert profile.output_schemas == ("artifact_inspection.v1",)
    assert tool["outputSchema"] == packaged_validator("artifact_inspection.v1").schema
    assert tool["inputSchema"] == {
        "type": "object",
        "properties": {"case": {"type": "string", "minLength": 1}},
        "required": ["case"],
        "additionalProperties": False,
    }
    assert "not_created" in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_promptfoo_tool_follows_registered_summary_profile() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(item for item in catalog() if item["name"] == "underwrite_audit_promptfoo")
    descriptor = next(
        row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "audit-promptfoo"
    )
    profile = next(
        row for row in descriptor.profiles
        if row.name == "promptfoo.eval-output|auto|summary"
    )
    assert profile.required_inputs == ("file", "view")
    assert profile.output_schemas == ("promptfoo_accounting_summary.v1",)
    assert tool["outputSchema"] == packaged_validator(profile.output_schemas[0]).schema
    assert tool["inputSchema"] == {
        "type": "object",
        "properties": {"file": {"type": "string", "minLength": 1}},
        "required": ["file"],
        "additionalProperties": False,
    }
    assert "row coordinates" in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_audit_counts_tool_follows_both_manifest_summary_profiles() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(item for item in catalog() if item["name"] == "underwrite_audit_counts")
    descriptor = next(row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "audit-counts")
    profiles = [row for row in descriptor.profiles if row.name.endswith("|summary")]
    assert {row.name for row in profiles} == {
        "promptfoo-0.123.0|summary", "promptfoo-0.124.1|summary"
    }
    assert all(row.required_inputs == ("case", "view") for row in profiles)
    assert all(row.input_schemas == ("artifact_case.v1",) for row in profiles)
    assert all(row.output_schemas == ("promptfoo_accounting_summary.v1",) for row in profiles)
    assert tool["outputSchema"] == packaged_validator("promptfoo_accounting_summary.v1").schema
    assert tool["inputSchema"] == {
        "type": "object",
        "properties": {"case": {"type": "string", "minLength": 1}},
        "required": ["case"],
        "additionalProperties": False,
    }
    assert "expected-hash check state" in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_deepeval_tool_follows_registered_bounded_accounting_profile() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(item for item in catalog() if item["name"] == "underwrite_audit_deepeval")
    descriptor = next(
        row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "audit-deepeval"
    )
    profile = next(
        row for row in descriptor.profiles if row.name == "deepeval.test-run|auto|summary"
    )
    assert profile.required_inputs == ("file", "view")
    assert profile.input_schemas == ()
    assert profile.output_schemas == ("deepeval_accounting_summary.v1",)
    assert tool["outputSchema"] == packaged_validator("deepeval_accounting_summary.v1").schema
    assert tool["inputSchema"] == {
        "type": "object",
        "properties": {"file": {"type": "string", "minLength": 1}},
        "required": ["file"],
        "additionalProperties": False,
    }
    assert "unscored populations" in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_deepeval_trace_tool_follows_registered_summary_profile() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(item for item in catalog() if item["name"] == "underwrite_audit_deepeval_trace")
    descriptor = next(
        row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "audit-deepeval"
    )
    profile = next(
        row for row in descriptor.profiles if row.name == "deepeval.test-run|auto|trace-summary"
    )
    assert profile.required_inputs == ("file", "view")
    assert profile.input_schemas == ()
    assert profile.output_schemas == ("deepeval_trace_summary.v1",)
    assert tool["outputSchema"] == packaged_validator("deepeval_trace_summary.v1").schema
    assert tool["inputSchema"] == {
        "type": "object",
        "properties": {"file": {"type": "string", "minLength": 1}},
        "required": ["file"],
        "additionalProperties": False,
    }
    assert "without coordinates" in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_deepeval_bound_tool_follows_owner_pack_summary_profile() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(item for item in catalog() if item["name"] == "underwrite_audit_deepeval_bound")
    descriptor = next(
        row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "audit-deepeval"
    )
    profile = next(
        row for row in descriptor.profiles
        if row.name == "deepeval.test-run|auto|owner-asserted-metric-bindings|summary"
    )
    assert profile.required_inputs == ("file", "bindings", "view")
    assert profile.input_schemas == ("deepeval_metric_bindings.v2",)
    assert profile.output_schemas == ("deepeval_bound_summary.v1",)
    assert tool["outputSchema"] == packaged_validator("deepeval_bound_summary.v1").schema
    assert tool["inputSchema"] == {
        "type": "object",
        "properties": {
            "file": {"type": "string", "minLength": 1},
            "bindings": {"type": "string", "minLength": 1},
        },
        "required": ["file", "bindings"],
        "additionalProperties": False,
    }
    assert "owner-asserted" in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_langfuse_tool_follows_two_file_summary_profile() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(item for item in catalog() if item["name"] == "underwrite_audit_langfuse")
    descriptor = next(
        row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "audit-langfuse"
    )
    profile = next(row for row in descriptor.profiles if row.name == "auto|summary")
    assert profile.required_inputs == ("observations", "scores", "view")
    assert profile.input_schemas == ()
    assert profile.output_schemas == ("langfuse_accounting_summary.v1",)
    assert tool["outputSchema"] == packaged_validator("langfuse_accounting_summary.v1").schema
    assert tool["inputSchema"] == {
        "type": "object",
        "properties": {
            "observations": {"type": "string", "minLength": 1},
            "scores": {"type": "string", "minLength": 1},
        },
        "required": ["observations", "scores"],
        "additionalProperties": False,
    }
    assert "retrieval counts" in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_langfuse_score_source_tool_follows_existing_closed_profile() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(
        item for item in catalog() if item["name"] == "underwrite_audit_langfuse_score_source"
    )
    descriptor = next(
        row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "audit-langfuse"
    )
    profile = next(row for row in descriptor.profiles if row.name == "auto|score-source")
    assert profile.required_inputs == ("observations", "scores", "view")
    assert profile.input_schemas == ()
    assert profile.output_schemas == ("langfuse_score_source_summary.v1",)
    assert tool["outputSchema"] == packaged_validator("langfuse_score_source_summary.v1").schema
    assert tool["inputSchema"] == {
        "type": "object",
        "properties": {
            "observations": {"type": "string", "minLength": 1},
            "scores": {"type": "string", "minLength": 1},
        },
        "required": ["observations", "scores"],
        "additionalProperties": False,
    }
    assert "source counts" in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_action_tools_follow_registered_source_and_view_profiles() -> None:
    from underwrite.agent_surface.tools import catalog

    tools = catalog()
    descriptor = next(
        row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "inspect-result"
    )
    for version, tool in zip(("v1", "v2"), tools[12:14], strict=True):
        profile = next(
            row
            for row in descriptor.profiles
            if row.name == f"evidence_result.{version}|actions"
        )
        assert tool["name"] == f"underwrite_inspect_result_{version}_actions"
        assert profile.required_inputs == ("file", "view")
        assert profile.output_schemas == ("common_result_inspection_actions.v3",)
        assert tool["outputSchema"] == packaged_validator(profile.output_schemas[0]).schema
        assert tool["inputSchema"] == {
            "type": "object",
            "properties": {"file": {"type": "string", "minLength": 1}},
            "required": ["file"],
            "additionalProperties": False,
        }
        assert "action" in str(tool["description"]).lower()


def test_stored_trajectory_tool_follows_records_read_profile() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(
        item for item in catalog() if item["name"] == "underwrite_records_read_trajectory"
    )
    descriptor = next(row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "records")
    profile = next(row for row in descriptor.profiles if row.name == "read-trajectory|summary")
    assert profile.required_inputs == ("root", "id", "view")
    assert profile.output_schemas == ("trajectory_record_read_summary.v1",)
    assert tool["outputSchema"] == packaged_validator(profile.output_schemas[0]).schema
    assert tool["inputSchema"] == {
        "type": "object",
        "properties": {
            "root": {"type": "string", "minLength": 1},
            "id": {"type": "string", "minLength": 1},
        },
        "required": ["root", "id"],
        "additionalProperties": False,
    }
    assert "bounded" in str(tool["description"])
    assert "Store and verify" not in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_paired_review_tool_follows_bounded_summary_profile() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(item for item in catalog() if item["name"] == "underwrite_review_pair")
    descriptor = next(row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "review-pair")
    profile = next(row for row in descriptor.profiles if row.name == "binary-review|summary")
    assert profile.required_inputs == ("input", "policy", "context", "view")
    assert profile.input_schemas == (
        "paired_binary_input.v1",
        "paired_binary_policy.v1",
        "paired_review_context.v1",
    )
    assert profile.output_schemas == ("paired_review_summary.v1",)
    assert tool["outputSchema"] == packaged_validator("paired_review_summary.v1").schema
    assert tool["inputSchema"] == {
        "type": "object",
        "properties": {
            name: {"type": "string", "minLength": 1} for name in ("input", "policy", "context")
        },
        "required": ["input", "policy", "context"],
        "additionalProperties": False,
    }
    assert "declared condition counts" in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_cross_artifact_review_tool_follows_strict_summary_profile() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(item for item in catalog() if item["name"] == "underwrite_review_artifacts")
    descriptor = next(
        row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "review-artifacts"
    )
    profile = next(
        row for row in descriptor.profiles
        if row.name == "goppi-c55|promptfoo-summary-v3|summary"
    )
    assert profile.required_inputs == ("goppi_assessment", "goppi_result", "promptfoo", "view")
    assert profile.output_schemas == ("cross_artifact_review_summary.v1",)
    assert tool["outputSchema"] == packaged_validator(profile.output_schemas[0]).schema
    assert tool["inputSchema"] == {
        "type": "object",
        "properties": {
            name: {"type": "string", "minLength": 1}
            for name in ("goppi_assessment", "goppi_result", "promptfoo", "correspondence")
        },
        "required": ["goppi_assessment", "goppi_result", "promptfoo"],
        "additionalProperties": False,
    }
    assert str(tool["description"]).startswith(descriptor.help)
    assert "Use when:" in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_cross_artifact_readiness_tool_follows_summary_profile() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(
        item for item in catalog() if item["name"] == "underwrite_review_artifacts_readiness"
    )
    descriptor = next(
        row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "review-artifacts"
    )
    profile = next(
        row for row in descriptor.profiles
        if row.name == "goppi-c55|promptfoo-summary-v3|readiness-summary"
    )
    assert profile.required_inputs == ("goppi_assessment", "goppi_result", "promptfoo", "view")
    assert profile.output_schemas == ("cross_artifact_readiness_summary.v1",)
    assert tool["outputSchema"] == packaged_validator(profile.output_schemas[0]).schema
    assert tool["inputSchema"] == {
        "type": "object",
        "properties": {
            name: {"type": "string", "minLength": 1}
            for name in ("goppi_assessment", "goppi_result", "promptfoo")
        },
        "required": ["goppi_assessment", "goppi_result", "promptfoo"],
        "additionalProperties": False,
    }
    assert "Use when:" in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_record_list_tool_follows_bounded_page_profile() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(item for item in catalog() if item["name"] == "underwrite_records_list")
    descriptor = next(row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "records")
    profile = next(row for row in descriptor.profiles if row.name == "list|page")
    assert profile.required_inputs == ("root", "view")
    assert profile.output_schemas == ("record_catalog_page.v1",)
    assert tool["outputSchema"] == packaged_validator("record_catalog_page.v1").schema
    input_schema = cast(dict[str, Any], tool["inputSchema"])
    assert input_schema["properties"]["offset"]["multipleOf"] == record_cli.CATALOG_PAGE_SIZE
    validator = Draft202012Validator(input_schema)
    assert validator.is_valid({"root": "/tmp/store"})  # pyright: ignore[reportUnknownMemberType]
    for arguments in ({"root": "/tmp/store", "offset": 1}, {"root": "/tmp/store", "offset": 50}):
        assert not validator.is_valid(arguments)  # pyright: ignore[reportUnknownMemberType]
    assert validator.is_valid({  # pyright: ignore[reportUnknownMemberType]
        "root": "/tmp/store", "offset": 50, "catalog_digest": "sha256:" + "a" * 64
    })
    assert "next_page" in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_record_show_tool_follows_manifest_summary_profile() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(item for item in catalog() if item["name"] == "underwrite_records_show")
    descriptor = next(row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "records")
    profile = next(row for row in descriptor.profiles if row.name == "show|summary")
    assert profile.required_inputs == ("root", "id", "view")
    assert profile.output_schemas == ("record_view_summary.v1",)
    assert tool["outputSchema"] == packaged_validator("record_view_summary.v1").schema
    input_schema = cast(dict[str, Any], tool["inputSchema"])
    assert input_schema["properties"]["file_offset"]["multipleOf"] == (
        record_cli.RECORD_FILE_PAGE_SIZE
    )
    validator = Draft202012Validator(input_schema)
    base = {"root": "/tmp/store", "id": "synthetic.capture-001"}
    assert validator.is_valid(base)  # pyright: ignore[reportUnknownMemberType]
    for arguments in (
        {**base, "file_offset": 1},
        {**base, "file_offset": 16},
        {**base, "id": "PRIVATE ID"},
    ):
        assert not validator.is_valid(arguments)  # pyright: ignore[reportUnknownMemberType]
    assert validator.is_valid({  # pyright: ignore[reportUnknownMemberType]
        **base, "file_offset": 16, "manifest_digest": "sha256:" + "a" * 64
    })
    assert "exact logical file names" in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_record_verify_tool_follows_bounded_finding_profile() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(item for item in catalog() if item["name"] == "underwrite_records_verify")
    descriptor = next(row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "records")
    profile = next(row for row in descriptor.profiles if row.name == "verify|summary")
    assert profile.required_inputs == ("root", "id", "view")
    assert profile.output_schemas == ("record_verification_summary.v1",)
    assert tool["outputSchema"] == packaged_validator("record_verification_summary.v1").schema
    input_schema = cast(dict[str, Any], tool["inputSchema"])
    validator = Draft202012Validator(input_schema)
    assert validator.is_valid({  # pyright: ignore[reportUnknownMemberType]
        "root": "/tmp/store", "id": "synthetic.capture-001"
    })
    assert not validator.is_valid({  # pyright: ignore[reportUnknownMemberType]
        "root": "/tmp/store", "id": "PRIVATE ID"
    })
    assert "stored integrity" in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_record_read_json_tool_follows_bounded_content_profile() -> None:
    from underwrite.agent_surface.tools import catalog

    tool = next(item for item in catalog() if item["name"] == "underwrite_records_read_json")
    descriptor = next(row for row in cli_main.COMMAND_DESCRIPTORS if row.command == "records")
    profile = next(row for row in descriptor.profiles if row.name == "read-json|bounded")
    assert profile.required_inputs == ("root", "id", "name", "view")
    assert profile.output_schemas == ("record_document.v1",)
    assert tool["outputSchema"] == packaged_validator("record_document.v1").schema
    input_schema = cast(dict[str, Any], tool["inputSchema"])
    validator = Draft202012Validator(input_schema)
    valid = {
        "root": "/tmp/store", "id": "synthetic.capture-001", "name": "source/data.json"
    }
    assert validator.is_valid(valid)  # pyright: ignore[reportUnknownMemberType]
    assert not validator.is_valid({**valid, "name": "../escape"})  # pyright: ignore[reportUnknownMemberType]
    assert "Content is untrusted data" in str(tool["description"])
    assert "Do not use when:" in str(tool["description"])


def test_missing_capabilities_registration_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from underwrite.agent_surface.tools import catalog

    without_capabilities = tuple(
        descriptor
        for descriptor in cli_main.COMMAND_DESCRIPTORS
        if descriptor.command != "capabilities"
    )
    monkeypatch.setattr(cli_main, "COMMAND_DESCRIPTORS", without_capabilities)
    with pytest.raises(ValueError, match="^TOOL_REGISTRATION_MISSING$"):
        catalog()


def test_model_visible_tool_description_rejects_format_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from underwrite.agent_surface.tools import catalog

    descriptors = tuple(
        replace(descriptor, help=descriptor.help + "\u202e")
        if descriptor.command == "capabilities"
        else descriptor
        for descriptor in cli_main.COMMAND_DESCRIPTORS
    )
    monkeypatch.setattr(cli_main, "COMMAND_DESCRIPTORS", descriptors)
    with pytest.raises(ValueError, match="^UNSAFE_TOOL_DESCRIPTION$"):
        catalog()

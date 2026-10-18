"""Capability output is derived from the same command and codec registrations as execution."""

from __future__ import annotations

import argparse
import json
from importlib.resources import files
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from _repo_paths import repo_root
from jsonschema import Draft202012Validator

from underwrite.cli import capabilities, main
from underwrite.instrument.ingest.formats import external_codecs, native_codecs, trusted_codecs

ROOT = repo_root(Path(__file__).resolve())
ERROR_EXIT = 2


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _validator(name: str) -> Validator:
    resource = files("underwrite").joinpath("_contracts", f"{name}.schema.json")
    schema = json.loads(resource.read_bytes())
    Draft202012Validator.check_schema(schema)
    return cast("Validator", Draft202012Validator(schema))


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:  # pyright: ignore[reportPrivateUsage]
        if isinstance(action, argparse._SubParsersAction):  # pyright: ignore[reportPrivateUsage]
            choices = action.choices  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
            return cast(dict[str, argparse.ArgumentParser], choices)
    return {}


def _destinations(parser: argparse.ArgumentParser) -> set[str]:
    return {
        action.dest
        for action in parser._actions  # pyright: ignore[reportPrivateUsage]
        if action.dest not in {"help", argparse.SUPPRESS}
    }


def _required_clauses(parser: argparse.ArgumentParser) -> list[set[str]]:
    groups = parser._mutually_exclusive_groups  # pyright: ignore[reportPrivateUsage]
    grouped_actions = {
        action
        for group in groups
        for action in group._group_actions  # pyright: ignore[reportPrivateUsage]
    }
    required = [
        {action.dest}
        for action in parser._actions  # pyright: ignore[reportPrivateUsage]
        if action not in grouped_actions
        and action.dest not in {"help", argparse.SUPPRESS}
        and (not action.option_strings or action.required)
    ]
    for group in groups:
        if group.required:
            actions = group._group_actions  # pyright: ignore[reportPrivateUsage]
            required.append({action.dest for action in actions})
    return required


def test_capability_command_validates_and_is_registration_derived(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main.main(["capabilities", "--json"]) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    report = json.loads(streams.out)
    _validator("capabilities.v1").validate(report)
    assert report == capabilities.report(main.COMMAND_DESCRIPTORS)
    assert [item["command"] for item in report["commands"]] == sorted(
        descriptor.command for descriptor in main.COMMAND_DESCRIPTORS
    )
    assert "package_version" not in report


@pytest.mark.parametrize(
    ("command", "name", "fixture", "version", "schema"),
    [
        (
            "audit-deepeval",
            "deepeval.test-run|4.1.1",
            "deepeval/test_run_20260914_132634.json",
            "4.1.1",
            "deepeval_accounting.v1",
        ),
        (
            "audit-deepeval",
            "deepeval.test-run|4.2.8",
            "deepeval-4.2.8/ordinary.json",
            "4.2.8",
            "deepeval_accounting.v2",
        ),
        (
            "audit-promptfoo",
            "promptfoo.eval-output|0.123.0",
            "promptfoo/results.json",
            "0.123.0",
            "promptfoo_accounting.v1",
        ),
        (
            "audit-promptfoo",
            "promptfoo.eval-output|0.124.1",
            "promptfoo-0.124.1/results.json",
            "0.124.1",
            "promptfoo_accounting.v1",
        ),
    ],
)
def test_advertised_audit_profile_runs_its_registered_fixture(
    command: str,
    name: str,
    fixture: str,
    version: str,
    schema: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main.main(["capabilities", "--json"]) == 0
    capabilities_report = json.loads(capsys.readouterr().out)
    descriptor = next(row for row in capabilities_report["commands"] if row["command"] == command)
    selected = next(row for row in descriptor["profiles"] if row["name"] == name)
    required = ["file"] if name == "deepeval.test-run|4.1.1" else ["file", "version"]
    assert selected["required_inputs"] == required
    assert selected["output_schemas"] == [schema]

    argv = [command, str(ROOT / "fixtures/golden/external" / fixture)]
    if "version" in selected["required_inputs"]:
        argv.extend(("--version", version))
    assert main.main([*argv, "--json"]) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    result = json.loads(streams.out)
    assert result["schema"] == schema
    assert result["source"]["format_version"] == version
    _validator(schema).validate(result)


@pytest.mark.parametrize(
    "route",
    [
        (
            "audit-promptfoo",
            "promptfoo-0.124.1/results.json",
            "0.124.1",
            "promptfoo_record.v2",
            "promptfoo_accounting.v2",
        ),
        (
            "audit-promptfoo|0.123.0",
            "promptfoo/results.json",
            "0.123.0",
            "promptfoo_record.v1",
            "promptfoo_accounting.v1",
        ),
        (
            "audit-promptfoo|0.124.1",
            "promptfoo-0.124.1/results.json",
            "0.124.1",
            "promptfoo_record.v1",
            "promptfoo_accounting.v1",
        ),
        (
            "audit-deepeval",
            "deepeval/test_run_20260914_132634.json",
            "4.1.1",
            "deepeval_record.v1",
            "deepeval_accounting.v1",
        ),
        (
            "audit-deepeval|4.2.8",
            "deepeval-4.2.8/ordinary.json",
            "4.2.8",
            "deepeval_record.v2",
            "deepeval_accounting.v2",
        ),
    ],
)
def test_advertised_record_profile_captures_and_verifies_its_selected_fixture(
    route: tuple[str, str, str, str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    name, fixture, version, schema, analysis_schema = route
    assert main.main(["capabilities", "--json"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    records = next(row for row in catalog["commands"] if row["command"] == "records")
    selected = next(row for row in records["profiles"] if row["name"] == name)
    required = ["file", "root", "id"] if "|" not in name else ["file", "version", "root", "id"]
    assert selected["required_inputs"] == required
    assert selected["output_schemas"] == [schema]

    root = tmp_path / "private-records"
    values = {
        "file": str(ROOT / "fixtures/golden/external" / fixture),
        "version": version,
        "root": str(root),
        "id": "selected",
    }
    argv = ["records", name.split("|", 1)[0]]
    for field in selected["required_inputs"]:
        if field == "file":
            argv.append(values[field])
        else:
            argv.extend((f"--{field}", values[field]))
    assert main.main([*argv, "--json"]) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    report = json.loads(streams.out)
    assert report["schema"] == schema
    assert report["analysis"]["state"] == "result"
    assert report["analysis"]["report"]["schema"] == analysis_schema
    identity = report["analysis"]["report"]["source"]
    if analysis_schema == "promptfoo_accounting.v2":
        assert identity["producer_version"] == version
        assert identity["producer_version_state"] == "declared"
        original = json.loads(Path(values["file"]).read_bytes())
        assert identity["summary_version"] == original["results"]["version"]
        assert identity["claims_verified"] is False
    else:
        assert identity["format_version"] == version
    assert report["storage"]["state"] == "saved"
    assert main.main(["records", "verify", "--root", str(root), "--id", "selected", "--json"]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["valid"] is True


@pytest.mark.parametrize(
    ("command", "fixture", "older_version"),
    [
        ("audit-promptfoo", "promptfoo-0.124.1/results.json", "0.123.0"),
        ("audit-deepeval", "deepeval-4.2.8/ordinary.json", "4.1.1"),
    ],
)
def test_explicit_older_record_profile_does_not_fallback(
    command: str,
    fixture: str,
    older_version: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = ROOT / "fixtures/golden/external" / fixture
    root = tmp_path / "private-records"
    assert (
        main.main(
            [
                "records",
                command,
                str(source),
                "--version",
                older_version,
                "--root",
                str(root),
                "--id",
                "selected",
                "--json",
            ]
        )
        == ERROR_EXIT
    )
    streams = capsys.readouterr()
    assert streams.err == ""
    report = json.loads(streams.out)
    assert report["analysis"]["state"] == "error"
    assert report["analysis"]["error"]["code"] == "INVALID_INPUT"
    assert report["storage"]["state"] == "saved"


@pytest.mark.parametrize("family", ["blob", "cli"])
def test_langfuse_auto_capabilities_need_no_profile_and_run_both_families(
    family: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main.main(["capabilities", "--json"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    commands = {row["command"]: row for row in catalog["commands"]}
    audit = next(row for row in commands["audit-langfuse"]["profiles"] if row["name"] == "auto")
    record = next(row for row in commands["records"]["profiles"] if row["name"] == "audit-langfuse")
    assert audit["required_inputs"] == ["observations", "scores"]
    assert audit["output_schemas"] == ["langfuse_accounting.v2"]
    assert record["required_inputs"] == ["observations", "scores", "root", "id"]
    assert record["output_schemas"] == ["langfuse_record.v2"]
    if family == "blob":
        directory = ROOT / "fixtures/golden/external/langfuse"
        observations = directory / "observations_v2-2026-09-14T06-40-00.json"
        scores = directory / "scores-2026-09-14T06-40-00.json"
    else:
        directory = ROOT / "fixtures/examples/langfuse-cli"
        observations, scores = directory / "observations.json", directory / "scores.json"
    args = ["audit-langfuse", "--observations", str(observations), "--scores", str(scores)]
    assert main.main([*args, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    _validator(audit["output_schemas"][0]).validate(report)
    assert main.main(["records", *args, "--root", str(tmp_path), "--id", "auto", "--json"]) == 0
    saved = json.loads(capsys.readouterr().out)
    _validator(record["output_schemas"][0]).validate(saved)
    assert saved["analysis"]["report"] == report
    assert saved["storage"]["state"] == "saved"


def test_help_and_dispatch_names_come_from_each_descriptor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser_names = set(_subcommands(main.build_parser()))
    registered_names = {
        name
        for descriptor in main.COMMAND_DESCRIPTORS
        for name in (descriptor.command, *descriptor.aliases)
    }
    assert parser_names == registered_names == set(main.COMMANDS)
    for name in sorted(registered_names):
        with pytest.raises(SystemExit) as exc:
            main.main([name, "--help"])
        assert exc.value.code == 0
        capsys.readouterr()


def test_required_inputs_exist_on_the_registered_parser_profile() -> None:
    for descriptor in main.COMMAND_DESCRIPTORS:
        parser = argparse.ArgumentParser(add_help=False)
        descriptor.configure(parser)
        children = _subcommands(parser)
        for profile in descriptor.profiles:
            owner_name = (
                profile.name.split("|", 1)[0] if descriptor.command == "records" else profile.name
            )
            owner = children.get(owner_name, parser)
            destinations = _destinations(owner)
            required_clauses = _required_clauses(owner)
            for requirement in profile.required_inputs:
                alternatives = set(requirement.split("|"))
                assert alternatives & destinations, (
                    descriptor.command,
                    profile.name,
                    requirement,
                    destinations,
                )
            covered = {
                destination
                for requirement in profile.required_inputs
                for destination in requirement.split("|")
            }
            for required_clause in required_clauses:
                assert required_clause & covered, (
                    descriptor.command,
                    profile.name,
                    required_clause,
                    profile.required_inputs,
                )


def test_every_declared_input_and_output_schema_has_an_authored_contract() -> None:
    declared = {
        schema
        for descriptor in main.COMMAND_DESCRIPTORS
        for profile in descriptor.profiles
        for schema in (*profile.input_schemas, *profile.output_schemas)
    }
    packaged = {
        resource.name.removesuffix(".schema.json")
        for resource in files("underwrite").joinpath("_contracts").iterdir()
        if resource.name.endswith(".schema.json")
    }
    authored = {
        path.name.removesuffix(".schema.json")
        for path in (ROOT / "contracts").glob("*.schema.json")
    }
    assert declared <= authored
    assert declared <= packaged


def test_codec_capabilities_exactly_match_trusted_runtime_registries() -> None:
    report = capabilities.report(main.COMMAND_DESCRIPTORS)
    rows = cast(list[dict[str, Any]], report["codecs"])
    observed = {(row["format"], row["format_version"]) for row in rows}
    assert observed == set(trusted_codecs())
    native = set(native_codecs())
    external = set(external_codecs())
    assert native.isdisjoint(external)
    assert {
        (row["format"], row["format_version"]) for row in rows if row["classification"] == "native"
    } == native
    assert all(row["commands"] == ["ingest", "project"] for row in rows)


def test_capabilities_usage_error_is_typed_and_atomic(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main.main(["capabilities", "--unknown", "--json"]) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == ""
    error = json.loads(streams.err)
    _validator("evidence_error.v1").validate(error)
    assert error["code"] == "USAGE_ERROR"


def test_native_profiles_advertise_only_current_semantics() -> None:
    descriptors = {item.command: item for item in main.COMMAND_DESCRIPTORS}
    measurement = descriptors["measure"].profiles[0]
    assert measurement.name == "binary-event-directional.v1"
    assert measurement.input_schemas == ("observation.v1", "measurement_policy.v3")
    assert measurement.output_schemas == ("read.v2",)
    acceptance = descriptors["accept"].profiles[0]
    assert acceptance.input_schemas == ("change_candidate.v1", "declared_claims.v2", "read.v2")
    assert acceptance.output_schemas == ("acceptance_decision.v2",)

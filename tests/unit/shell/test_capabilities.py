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


def _required_destinations(parser: argparse.ArgumentParser) -> set[str]:
    required = {
        action.dest
        for action in parser._actions  # pyright: ignore[reportPrivateUsage]
        if action.dest not in {"help", argparse.SUPPRESS}
        and (not action.option_strings or action.required)
    }
    groups = parser._mutually_exclusive_groups  # pyright: ignore[reportPrivateUsage]
    for group in groups:
        if group.required:
            actions = group._group_actions  # pyright: ignore[reportPrivateUsage]
            required.update(action.dest for action in actions)
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
            owner = children.get(profile.name, parser)
            destinations = _destinations(owner)
            required_destinations = _required_destinations(owner)
            for requirement in profile.required_inputs:
                alternatives = set(requirement.split("|"))
                assert alternatives & destinations, (
                    descriptor.command,
                    profile.name,
                    requirement,
                    destinations,
                )
                assert alternatives & required_destinations, (
                    descriptor.command,
                    profile.name,
                    requirement,
                    required_destinations,
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

"""Derive the installed agent-facing capability report from CLI and codec registrations."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from underwrite.cli.evidence import run_cli
from underwrite.instrument.ingest.formats import external_codecs, native_codecs, trusted_codecs

Configure = Callable[[argparse.ArgumentParser], None]
Handler = Callable[[str, list[str]], int]


@dataclass(frozen=True)
class CommandProfile:
    """One implemented command mode and the local artifacts it requires and emits."""

    name: str
    required_inputs: tuple[str, ...]
    input_schemas: tuple[str, ...]
    output_schemas: tuple[str, ...]

    def render(self) -> dict[str, object]:
        return {
            "name": self.name,
            "required_inputs": list(self.required_inputs),
            "input_schemas": list(self.input_schemas),
            "output_schemas": list(self.output_schemas),
        }


@dataclass(frozen=True)
class CommandDescriptor:
    """The single registration used for help, dispatch, and capability discovery."""

    command: str
    help: str
    configure: Configure
    profiles: tuple[CommandProfile, ...]
    handler: Handler | None = None
    aliases: tuple[str, ...] = ()

    def render(self) -> dict[str, object]:
        return {
            "command": self.command,
            "aliases": list(self.aliases),
            "profiles": [profile.render() for profile in self.profiles],
        }


def profile(
    name: str,
    required_inputs: tuple[str, ...],
    input_schemas: tuple[str, ...],
    output_schemas: tuple[str, ...],
) -> CommandProfile:
    """Keep descriptor declarations compact while retaining immutable tuple fields."""
    return CommandProfile(name, required_inputs, input_schemas, output_schemas)


def report(descriptors: Sequence[CommandDescriptor]) -> dict[str, object]:
    """Render command registrations and exact installed codec selectors deterministically."""
    native = set(native_codecs())
    external = set(external_codecs())
    trusted = trusted_codecs()
    if native & external or set(trusted) != native | external:
        raise ValueError("INCONSISTENT_CODEC_REGISTRY")
    return {
        "schema": "capabilities.v1",
        "commands": [
            descriptor.render() for descriptor in sorted(descriptors, key=lambda item: item.command)
        ],
        "codecs": [
            {
                "format": format_name,
                "format_version": version,
                "classification": "native" if selector in native else "external",
                "commands": ["ingest", "project"],
                "output_schema": "observation.v1",
                "required_inputs": ["format", "version", "file|http_body_file"],
            }
            for selector in sorted(trusted)
            for format_name, version in (selector,)
        ],
    }


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "List command and exact codec capabilities derived from this installed program. "
        "The package version alone does not establish capability equivalence."
    )
    parser.add_argument("--json", action="store_true", help="Emit capabilities.v1 JSON.")


def _produce(args: argparse.Namespace) -> dict[str, object]:
    del args
    from underwrite.cli.main import COMMAND_DESCRIPTORS

    return report(COMMAND_DESCRIPTORS)


def main(argv: list[str]) -> int:
    return run_cli("capabilities", argv, configure, _produce, "capabilities.v1")

"""Installed artifact projection, with one explicit alias and bounded diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn, cast

from underwrite_core.canonical import canonical_bytes, content_digest

from underwrite.instrument.ingest.application import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_DEPTH,
    DIAGNOSTICS,
    UsageError,
    instrument_diagnostic,
    packaged_ingestor,
)
from underwrite.instrument.ingest.formats import trusted_codecs
from underwrite.instrument.ingest.project import Source

COMMANDS = ("ingest", "project")
DISPLAY_LIMIT = 160


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError("INVALID_ARGUMENTS_SEE_HELP")


def _positive_integer(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("positive integer required") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("positive integer required")
    return value


def configure(parser: argparse.ArgumentParser) -> None:
    """Shared options for canonical ingest and its project alias."""
    parser.add_argument("--format", required=True, help="Exact supported source format.")
    parser.add_argument("--version", required=True, help="Exact source profile version.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--file", type=Path, help="Local original artifact.")
    mode.add_argument(
        "--http-body-file", type=Path, help="Local exact HTTP body capture; no fetch."
    )
    parser.add_argument(
        "--source-ref", help="Stable source reference; omitted means raw content hash."
    )
    parser.add_argument("--source-locator", help="Optional stable source retrieval hint.")
    parser.add_argument(
        "--transport-locator", help="Required reception label for body captures only."
    )
    parser.add_argument(
        "--max-bytes",
        type=_positive_integer,
        default=DEFAULT_MAX_BYTES,
        help=f"Input byte budget (default {DEFAULT_MAX_BYTES}).",
    )
    parser.add_argument(
        "--max-depth",
        type=_positive_integer,
        default=DEFAULT_MAX_DEPTH,
        help=f"JSON container-depth budget (default {DEFAULT_MAX_DEPTH}).",
    )
    parser.add_argument(
        "--json", action="store_true", help="Canonical observation stdout; typed errors stderr."
    )


def _observation(args: argparse.Namespace) -> dict[str, object]:
    locator: str | None = args.transport_locator
    if args.http_body_file is not None:
        if locator is None or not locator.strip():
            raise UsageError("BODY_TRANSPORT_LOCATOR_REQUIRED")
    elif args.transport_locator is not None:
        raise UsageError("TRANSPORT_LOCATOR_REQUIRES_BODY")
    source = Source(args.format, args.version, args.source_ref, args.source_locator)
    ingestor = packaged_ingestor(max_bytes=args.max_bytes, max_depth=args.max_depth)
    if args.http_body_file is not None:
        assert locator is not None  # Validated above; not a substituted reception label.
        result = ingestor.http_body_file(args.http_body_file, locator, source)
    else:
        result = ingestor.file(args.file, source)
    return result.observation


def _display(value: object) -> str:
    escaped = json.dumps(value, ensure_ascii=True)
    return escaped if len(escaped) <= DISPLAY_LIMIT else escaped[:DISPLAY_LIMIT] + "… [truncated]"


def _human(observation: dict[str, object]) -> str:
    # Observation shape has already passed the one deployed schema validator.
    source = cast(dict[str, object], observation["source"])
    raw = cast(dict[str, object], observation["raw"])
    return "\n".join(
        [
            f"Artifact projection: {_display(source['format'])} / "
            f"{_display(source['format_version'])}",
            f"  kind: {_display(observation['kind'])}",
            f"  reference: {_display(raw['ref'])}",
            f"  raw hash: {_display(raw['hash'])}",
            f"  observation digest: {content_digest(observation)}",
            "  Projection only; source claims not verified. "
            "Content hashes are not resolvable provenance.",
        ]
    )


def main(command: str, argv: list[str]) -> int:
    """Parse the instrument family separately; legacy argparse is unchanged."""
    parser = _Parser(
        prog=f"underwrite {command}",
        allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.description = "Artifact projection only. project is an explicit alias of ingest."
    try:
        profiles = "\n".join(
            f"  {format_name} / {version}" for format_name, version in sorted(trusted_codecs())
        )
        parser.epilog = (
            f"Supported format/version pairs (exact matches):\n{profiles}\n\n"
            "Local input only; source claims are preserved, not verified. "
            "With --json, observations go to stdout and typed errors to stderr."
        )
        configure(parser)
        args = parser.parse_args(argv)
        observation = _observation(args)
        output = canonical_bytes(observation).decode("utf-8") if args.json else _human(observation)
    except tuple(DIAGNOSTICS) as exc:
        diagnostic = instrument_diagnostic(exc, command)
        output = (
            json.dumps(diagnostic)
            if "--json" in argv
            else f"{command}: {diagnostic['code']} ({diagnostic['reason']}); see --help"
        )
        print(output, file=sys.stderr)
        return cast(int, diagnostic["exit_code"])
    print(output)
    return 0

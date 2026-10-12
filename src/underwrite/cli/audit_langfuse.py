"""Explain supplied Langfuse score kinds and associations without task verdicts."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
from underwrite.instrument.ingest.langfuse_accounting import (
    LangfuseFileError,
    audit_langfuse_files,
)


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--observations",
        type=Path,
        required=True,
        metavar="FILE",
        help="Langfuse server 4.35.0 observations_v2 JSON export.",
    )
    parser.add_argument(
        "--scores",
        type=Path,
        required=True,
        metavar="FILE",
        help="Langfuse server 4.35.0 scores JSON export.",
    )
    parser.add_argument("--json", action="store_true", help="Emit langfuse_accounting.v1 JSON.")
    parser.description = (
        "Account for score kinds and exact project/trace/observation associations in two "
        "supplied Langfuse 4.35.0 exports. Unresolved references remain descriptive results. "
        "No producer execution, export-completeness claim, task success, or approval. "
        "The source profile is explicitly selected, not detected."
    )


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        return audit_langfuse_files(cast(Path, args.observations), cast(Path, args.scores))
    except LangfuseFileError as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location) from None


def render_human(report: dict[str, object]) -> str:
    counts = cast(dict[str, Any], report["counts"])
    kinds, links = counts["by_data_type"], counts["associations"]
    return "\n".join(
        [
            f"Supplied rows: {counts['observations']} observations; {counts['scores']} scores.",
            f"Score kinds: numeric {kinds['NUMERIC']}; boolean {kinds['BOOLEAN']}; "
            f"categorical {kinds['CATEGORICAL']}; text {kinds['TEXT']}.",
            f"Associations: observation-local {links['observation_local']}; "
            f"trace-level {links['trace_level']}; "
            f"unresolved {links['unresolved_observation_reference']}.",
            f"Observations without local scores: {counts['observations_without_local_scores']}.",
            "Planned evaluations and export completeness unknown; source claims unverified.",
            "Stored score values are not task verdicts; no statistical or approval authority.",
            "Next: " + "; ".join(cast(list[str], report["next_actions"])),
        ]
    )


def main(argv: list[str]) -> int:
    return run_cli(
        "audit-langfuse",
        argv,
        configure,
        produce,
        ReportOutput("langfuse_accounting.v1", human=render_human),
    )

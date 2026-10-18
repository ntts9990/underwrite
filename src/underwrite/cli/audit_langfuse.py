"""Explain supplied Langfuse score kinds and associations without task verdicts."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
from underwrite.instrument.evidence.langfuse_compat import (
    ACCOUNTING_SCHEMA as AUTO_ACCOUNTING_SCHEMA,
)
from underwrite.instrument.evidence.langfuse_compat import BLOB_READER, CLI_READER
from underwrite.instrument.evidence.langfuse_compat import (
    ROW_INDEX_SCHEMA as AUTO_ROW_INDEX_SCHEMA,
)
from underwrite.instrument.ingest.langfuse_accounting import (
    BLOB_PROFILE,
    CLI_PROFILE,
    LangfuseFileError,
    audit_langfuse_files,
    audit_langfuse_row_index_files,
    output_schema,
)

_INVENTORY_GUIDANCE = (
    "SUPPLY_PLANNED_INVENTORY_FOR_MISSINGNESS applies only when missing-execution "
    "accounting is needed: obtain explicit planned evidence from the evaluation owner. "
    "This command cannot ingest it; planned and missing-execution counts remain unknown. "
    "Descriptive accounting needs no additional collection."
)


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--observations",
        type=Path,
        required=True,
        metavar="FILE",
        help="Selected Langfuse observations_v2 blob export or CLI JSON capture.",
    )
    parser.add_argument(
        "--scores",
        type=Path,
        required=True,
        metavar="FILE",
        help="Selected Langfuse scores blob export or CLI JSON capture.",
    )
    parser.add_argument(
        "--profile",
        choices=(BLOB_PROFILE, CLI_PROFILE),
        default=None,
        help=(
            "Optional strict historical blob or CLI profile; omitted selects "
            "a reader from both files."
        ),
    )
    parser.add_argument(
        "--view",
        choices=("accounting", "row-index"),
        default="accounting",
        help="Select full accounting or a content-minimized per-score row index.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the selected view as JSON.")
    parser.description = (
        "Account for score kinds and local associations in two supplied Langfuse files. "
        "Without --profile, choose one supported array or CLI-list reader family from both "
        "files. Their shapes do not establish server or CLI release identity. "
        "Explicit --profile retains historical strict results. "
        "CLI collection status does not establish matching windows or complete source data. "
        "Unresolved references remain descriptive results. "
        "No producer execution, export-completeness claim, task success, or approval. "
        "If missing-execution accounting is required, the inventory next action asks for "
        "external planned evidence from the evaluation owner. This command has no inventory "
        "input; planned and missing-execution counts remain unknown."
    )


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        audit = audit_langfuse_row_index_files if args.view == "row-index" else audit_langfuse_files
        return audit(
            cast(Path, args.observations),
            cast(Path, args.scores),
            profile=cast(str | None, args.profile),
        )
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
            _INVENTORY_GUIDANCE,
        ]
    )


def render_selected_human(report: dict[str, object]) -> str:
    if report["schema"] == "langfuse_accounting.v1" or (
        report["schema"] == AUTO_ACCOUNTING_SCHEMA and report.get("reader_profile") == BLOB_READER
    ):
        return render_human(report)
    if report["schema"] in {
        output_schema(CLI_PROFILE, "accounting"),
        output_schema(CLI_PROFILE, "row-index"),
    } or (
        report["schema"] in {AUTO_ACCOUNTING_SCHEMA, AUTO_ROW_INDEX_SCHEMA}
        and report.get("reader_profile") == CLI_READER
    ):
        return render_cli_human(report)
    counts = cast(dict[str, Any], report["counts"])
    return (
        f"Supplied rows: {counts['observations']} observations; {counts['scores']} scores.\n"
        "Per-score kinds and association ordinals available in --json; score content withheld.\n"
        "Selected file hashes and row patterns remain linkable; no task verdict or approval."
    )


def render_cli_human(report: dict[str, object]) -> str:
    counts = cast(dict[str, Any], report["counts"])
    retrieval = cast(dict[str, dict[str, Any]], report["retrieval"])
    kinds = cast(dict[str, int], counts["by_data_type"])
    associations = cast(dict[str, int], counts["associations"])
    collected = "; ".join(
        f"{role} {retrieval[role]['pagination']['mode']}, "
        f"cursor {retrieval[role]['pagination']['cursor_state']}, "
        f"truncated {retrieval[role]['pagination']['truncated']}"
        for role in ("observations", "scores")
    )
    return "\n".join(
        [
            f"Supplied rows: {counts['observations']} observations; {counts['scores']} scores.",
            "Score kinds: "
            + "; ".join(f"{kind.lower()} {count}" for kind, count in kinds.items())
            + ".",
            "Local associations: "
            + "; ".join(f"{state} {count}" for state, count in associations.items())
            + ".",
            f"Collection reported: {collected}.",
            "Requested windows, export completeness and planned executions are unknown. "
            "Stored values are source claims, not task verdicts or approval.",
            "Next: " + "; ".join(cast(list[str], report["next_actions"])),
            _INVENTORY_GUIDANCE,
        ]
    )


def main(argv: list[str]) -> int:
    return run_cli(
        "audit-langfuse",
        argv,
        configure,
        produce,
        ReportOutput(
            lambda args: output_schema(args.profile, args.view),
            human=render_selected_human,
        ),
    )

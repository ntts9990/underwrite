"""Inspect one external common-result file without projecting its private items."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from underwrite_core.canonical import digest_bytes

from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
from underwrite.cli.local_json import decode_document, read_source, validate
from underwrite.instrument.evidence.common_result import (
    ResultInvariantError,
    availability_counts,
    check_result_invariants,
)
from underwrite.instrument.ingest.schema import packaged_validator

INPUT_MAX_BYTES = 4 * 1024 * 1024
INPUT_MAX_DEPTH = 64
SCHEMA = "common_result_inspection.v1"
LIMITATIONS = [
    "SOURCE_CLAIMS_UNVERIFIED",
    "SOURCE_BYTES_NOT_CHECKED",
    "BUNDLE_BINDING_NOT_CHECKED",
    "VERIFICATION_REFS_NOT_REPERFORMED",
    "ITEM_INVARIANTS_LIMITED",
    "NO_MEASUREMENT_OR_ACCEPTANCE",
    "RAW_HASH_AND_COUNTS_LINKABLE",
]


def inspect_result_file(path: Path) -> dict[str, object]:
    """Screen selected raw bytes against the installed common-result contract.

    This checks structure and analyzed item invariants only. Embedded source and
    verification assertions remain unchecked claims.
    """
    raw = read_source(path, INPUT_MAX_BYTES, "/result", "INVALID_INPUT", EvidenceError)
    document, _ = decode_document(
        raw, INPUT_MAX_BYTES, INPUT_MAX_DEPTH, "/result", "INVALID_INPUT", EvidenceError
    )
    validate(
        packaged_validator("evidence_result.v1"),
        document,
        EvidenceError,
        "INVALID_INPUT",
        "/result",
    )
    try:
        check_result_invariants(document)
    except ResultInvariantError as exc:
        raise EvidenceError("INVALID_INPUT", exc.reason, exc.location) from None

    processing = cast(dict[str, Any], document["processing"])
    analyzed = processing["state"] == "analyzed"
    report: dict[str, object] = {
        "schema": SCHEMA,
        "selected_result": {"format": "evidence_result.v1", "raw_hash": digest_bytes(raw)},
        "declared_processing": {
            "state": processing["state"],
            "reason": processing["reason"],
            "next_action": processing["next_action"],
        },
        "declared_population": document["population"],
        "availability_counts": availability_counts(document),
        "checks": {
            "contract_shape": "valid",
            "item_invariants": "valid" if analyzed else "not_applicable",
            "source_bytes": "not_checked",
            "producer_authentication": "not_checked",
            "verification_refs": "not_reperformed",
        },
        "verification_entry_count": len(cast(list[object], document["verification"])),
        "limitations": LIMITATIONS.copy(),
    }
    validate(packaged_validator(SCHEMA), report, EvidenceError, "INTERNAL_ERROR")
    return report


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Screen one local evidence_result.v1 against the installed shape and analyzed-item "
        "invariants. Exit 0 means the selected document was inspected, even when its producer "
        "declares insufficient or unsupported processing. Source bytes, bundle binding, and "
        "verification references are not checked."
    )
    parser.add_argument("file", type=Path, metavar="FILE")
    parser.add_argument("--json", action="store_true", help=f"Emit {SCHEMA} JSON.")


def render_human(report: dict[str, object]) -> str:
    processing = cast(dict[str, object], report["declared_processing"])
    counts = report["availability_counts"]
    if counts is None:
        summary = "No analyzed item counts"
    else:
        available = cast(dict[str, int], counts)
        summary = "Availability: " + ", ".join(
            f"{name}={available[name]}"
            for name in ("observed", "missing", "error", "abstained", "total")
        )
    return (
        f"Common result shape valid; declared processing: {processing['state']}. "
        f"{summary}. Source claims and verification references remain unchecked."
    )


def produce(args: argparse.Namespace) -> dict[str, object]:
    return inspect_result_file(cast(Path, args.file))


def main(argv: list[str]) -> int:
    return run_cli(
        "inspect-result", argv, configure, produce, ReportOutput(SCHEMA, human=render_human)
    )

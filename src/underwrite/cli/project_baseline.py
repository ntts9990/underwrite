"""Card: baseline-event | Paper: ADR-020 | Stage: producer-bridge.

Project an exact baseline exchange through the installed native ingestion path."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from underwrite_core.canonical import canonical_bytes

from underwrite.cli.evidence import EvidenceError, run_cli
from underwrite.instrument.evidence.goppi_baseline import (
    BaselineProjectionError,
    project_baseline_bundle,
)
from underwrite.instrument.ingest.application import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_DEPTH,
    DIAGNOSTICS,
    instrument_diagnostic,
    packaged_ingestor,
)
from underwrite.instrument.ingest.goppi_baseline import capture_baseline_documents
from underwrite.instrument.ingest.local_capture import CaptureError
from underwrite.instrument.ingest.project import Source


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Project the exact goppi baseline exchange v2 into observation.v1. "
        "Producer primary-status claims are not independent correctness or approval. "
        "No statistical policy or assumptions are supplied."
    )
    parser.add_argument("--exchange", type=Path, required=True, metavar="DIR")
    parser.add_argument("--direction", choices=("higher", "lower"), required=True)
    parser.add_argument("--json", action="store_true", help="Emit observation.v1 JSON.")


def produce(args: argparse.Namespace) -> dict[str, object]:
    try:
        documents = capture_baseline_documents(cast(Path, args.exchange))
        bundle = project_baseline_bundle(documents, direction=cast(str, args.direction))
        raw = canonical_bytes(bundle)
        if len(raw) > DEFAULT_MAX_BYTES:
            raise EvidenceError("INPUT_LIMIT_EXCEEDED", "GENERATED_BUNDLE_LIMIT_EXCEEDED")
        ingestor = packaged_ingestor(max_bytes=DEFAULT_MAX_BYTES, max_depth=DEFAULT_MAX_DEPTH)
        result, _ = ingestor.buffered_projection(
            raw,
            "generated baseline native bundle buffer",
            Source("underwrite.evidence-bundle", "v1", None),
        )
        return result.observation
    except (CaptureError, BaselineProjectionError) as exc:
        raise EvidenceError(exc.code, exc.reason, exc.location or "/exchange") from None
    except tuple(DIAGNOSTICS) as exc:
        diagnostic = instrument_diagnostic(exc, "ingest")
        code = cast(str, diagnostic["code"])
        if code not in {"INPUT_LIMIT_EXCEEDED", "SCHEMA_CONFIGURATION_ERROR"}:
            code = "INVALID_INPUT"
        raise EvidenceError(code, cast(str, diagnostic["reason"]), "/projection") from None


def main(argv: list[str] | None = None) -> int:
    return run_cli(
        "project-baseline",
        argv if argv is not None else [],
        configure,
        produce,
        "observation.v1",
        output_max_bytes=DEFAULT_MAX_BYTES,
    )

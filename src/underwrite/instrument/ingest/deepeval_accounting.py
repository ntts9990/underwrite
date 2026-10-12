"""Account for one explicitly selected DeepEval file through trusted ingestion."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from underwrite_core.canonical import content_digest

from underwrite.instrument.evidence.deepeval_accounting import (
    DeepevalAccountingError,
    account_deepeval,
)
from underwrite.instrument.ingest.application import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_DEPTH,
    DIAGNOSTICS,
    instrument_diagnostic,
    packaged_ingestor,
)
from underwrite.instrument.ingest.project import Source

SOURCE = ("deepeval.test-run", "4.1.1")


class DeepevalFileError(ValueError):
    """A code-owned acquisition or accounting refusal without source path text."""

    def __init__(self, code: str, reason: str, location: str = "") -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def audit_deepeval_file(path: Path) -> dict[str, object]:
    """Read once, normalize, and account without running a producer or evaluator."""
    try:
        observation = (
            packaged_ingestor(max_bytes=DEFAULT_MAX_BYTES, max_depth=DEFAULT_MAX_DEPTH)
            .file(path, Source(*SOURCE, None))
            .observation
        )
    except tuple(DIAGNOSTICS) as exc:
        diagnostic = instrument_diagnostic(exc, "ingest")
        code = cast(str, diagnostic["code"])
        if code not in {
            "SOURCE_READ_FAILED",
            "INPUT_LIMIT_EXCEEDED",
            "UNSUPPORTED_FORMAT",
            "SCHEMA_CONFIGURATION_ERROR",
        }:
            code = "INVALID_INPUT"
        location = "" if code == "SCHEMA_CONFIGURATION_ERROR" else "/file"
        raise DeepevalFileError(code, cast(str, diagnostic["reason"]), location) from None
    try:
        counted = account_deepeval(observation["payload"])
    except DeepevalAccountingError as exc:
        raise DeepevalFileError(exc.code, exc.reason, exc.location) from None
    return {
        "schema": "deepeval_accounting.v1",
        "source": {"format": SOURCE[0], "format_version": SOURCE[1], "claims_verified": False},
        "raw_hash": cast(dict[str, object], observation["raw"])["hash"],
        "observation_digest": content_digest(observation),
        **counted,
    }

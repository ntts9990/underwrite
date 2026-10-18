"""Canonical DeepEval entry points for current content-minimized trace indexing."""

from __future__ import annotations

from pathlib import Path

from underwrite.instrument.ingest.deepeval_compat import (
    audit_deepeval_compatible_bytes,
    audit_deepeval_compatible_file,
)


def audit_deepeval_trace_bytes(raw: bytes) -> dict[str, object]:
    """Index supplied original bytes without materializing a normalized source copy."""
    return audit_deepeval_compatible_bytes(raw, view="trace-index")


def audit_deepeval_trace_file(path: Path) -> dict[str, object]:
    """Load the current contract before capturing the regular source file once."""
    return audit_deepeval_compatible_file(path, view="trace-index")

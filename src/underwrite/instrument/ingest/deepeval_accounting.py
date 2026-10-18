"""Canonical DeepEval accounting over one bounded original TestRun buffer."""

from __future__ import annotations

from pathlib import Path

from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES, direct_file_read_error
from underwrite.instrument.ingest.transport import TransportError, read_local_file


class DeepevalFileError(ValueError):
    """A code-owned acquisition or accounting refusal without source path text."""

    def __init__(self, code: str, reason: str, location: str = "") -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def audit_deepeval_file(path: Path) -> dict[str, object]:
    """Read once and apply current field-era rules without a producer release claim."""
    from underwrite.instrument.ingest.deepeval_compat import audit_deepeval_compatible_file

    return audit_deepeval_compatible_file(path)


def read_deepeval_file(path: Path) -> bytes:
    """Capture one complete bounded file without projecting or reopening it."""
    try:
        return read_local_file(path, DEFAULT_MAX_BYTES)
    except TransportError as exc:
        code, reason = direct_file_read_error(exc)
        raise DeepevalFileError(code, reason, "/file") from None


def audit_deepeval_bytes(raw: bytes) -> dict[str, object]:
    """Account for the supplied bounded original bytes with the current reader."""
    from underwrite.instrument.ingest.deepeval_compat import audit_deepeval_compatible_bytes

    return audit_deepeval_compatible_bytes(raw)

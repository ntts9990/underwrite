"""Canonical file/bytes entry points for current Langfuse selected-field accounting."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from underwrite.instrument.evidence.langfuse_compat import (
    ACCOUNTING_SCHEMA,
    ROLES,
    ROW_INDEX_SCHEMA,
)
from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES, direct_file_read_error
from underwrite.instrument.ingest.transport import TransportError, read_local_file


def output_schema(view: str) -> str:
    """Select a current result view; source family is determined from the input pair."""
    if view == "accounting":
        return ACCOUNTING_SCHEMA
    if view == "row-index":
        return ROW_INDEX_SCHEMA
    raise ValueError("UNSUPPORTED_LANGFUSE_VIEW")


class LangfuseFileError(ValueError):
    """A trusted diagnostic with structural locations rather than source paths."""

    def __init__(self, code: str, reason: str, location: str = "") -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def read_langfuse_file(path: Path, role: str) -> bytes:
    """Read one complete bounded export, with a role-only diagnostic location."""
    if role not in ROLES:
        raise ValueError("unknown Langfuse source role")
    try:
        return read_local_file(path, DEFAULT_MAX_BYTES)
    except TransportError as exc:
        code, reason = direct_file_read_error(exc)
        raise LangfuseFileError(code, reason, "/" + role) from None


def audit_langfuse_files(observations_file: Path, scores_file: Path) -> dict[str, object]:
    """Read each source once through the current coherent-family reader."""
    from underwrite.instrument.ingest.langfuse_compat import audit_langfuse_compatible_files

    return audit_langfuse_compatible_files(observations_file, scores_file)


def audit_langfuse_row_index_files(observations_file: Path, scores_file: Path) -> dict[str, object]:
    """Return the current content-minimized view of the same two selected files."""
    from underwrite.instrument.ingest.langfuse_compat import audit_langfuse_compatible_files

    return audit_langfuse_compatible_files(observations_file, scores_file, view="row-index")


def audit_langfuse_bytes(
    observations: bytes,
    scores: bytes,
    *,
    view: Literal["accounting", "row-index"] = "accounting",
) -> dict[str, object]:
    """Analyze the exact two supplied buffers without reopening their sources."""
    from underwrite.instrument.ingest.langfuse_compat import audit_langfuse_compatible_bytes

    return audit_langfuse_compatible_bytes(observations, scores, view=view)

"""Card: record-reuse | Paper: ADR-019 | Stage: storage-read.

Shared effectful descriptor reading; callers own descriptor acquisition and lifetime.
"""

from __future__ import annotations

import os
import stat

_FILE_READ_CHUNK_BYTES = 64 * 1024


class TransportError(ValueError):
    """Source bytes could not be read within the configured byte budget."""

    attempted_read: bool = False


def positive_limit(value: int) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError("INGEST_LIMIT_MUST_BE_POSITIVE_INTEGER")


def read_descriptor(fd: int, max_bytes: int) -> bytes:
    """Read a caller-owned regular descriptor, at most budget plus one byte."""
    positive_limit(max_bytes)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise TransportError("SOURCE_NOT_REGULAR_FILE")
    data = bytearray()
    remaining = max_bytes + 1
    # EOF is the loop condition itself, so no inverted EOF test can spin at end of file.
    while remaining and (chunk := os.read(fd, min(_FILE_READ_CHUNK_BYTES, remaining))):
        data.extend(chunk)
        remaining -= len(chunk)
    return bytes(data)

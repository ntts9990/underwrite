"""Bounded file/in-memory envelope ingestion through one decode/project/validate seam."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from underwrite._fileio import TransportError as TransportError
from underwrite._fileio import positive_limit as _positive_limit
from underwrite._fileio import read_descriptor as read_descriptor
from underwrite._json import DecodeError as DecodeError
from underwrite._json import decode_json as decode_json
from underwrite.instrument.ingest import project
from underwrite.instrument.ingest.project import Projection, Source
from underwrite.instrument.ingest.schema import ObservationSchema

type Codec = Callable[[object], Projection]


class UnsupportedFormat(ValueError):
    """No trusted codec was registered for this exact format/version pair."""


@dataclass(frozen=True)
class IngestResult:
    """Validated observation with transport metadata outside its content digest."""

    observation: dict[str, object]
    transport_locator: str


def _read_bounded_file(path: Path, max_bytes: int) -> bytes:
    """Read the opened regular Unix descriptor, at most budget plus one byte.

    The chunk quantum is not a total admission cap. Nonblocking open prevents
    an ordinary writerless FIFO wait, not arbitrary filesystem latency.
    """
    _positive_limit(max_bytes)
    nonblocking = getattr(os, "O_NONBLOCK", None)
    no_controlling_terminal = getattr(os, "O_NOCTTY", None)
    if nonblocking is None or no_controlling_terminal is None:
        raise TransportError("SOURCE_NONBLOCKING_UNAVAILABLE")
    try:
        fd = os.open(path, os.O_RDONLY | nonblocking | no_controlling_terminal)
        try:
            return read_descriptor(fd, max_bytes)
        finally:
            os.close(fd)
    except OSError as exc:
        raise TransportError("SOURCE_READ_FAILED") from exc


_LOCAL_FILE_REASONS = {
    "SOURCE_NOT_REGULAR_FILE": "SOURCE_NOT_REGULAR_FILE",
    "SOURCE_NONBLOCKING_UNAVAILABLE": "UNSUPPORTED_PLATFORM",
    "BYTE_LIMIT_EXCEEDED": "BYTE_LIMIT_EXCEEDED",
}


def read_local_file(path: Path, max_bytes: int) -> bytes:
    """Read one whole local file within budget, or fail with one local-file read reason.

    Reasons: SOURCE_READ_FAILED, SOURCE_NOT_REGULAR_FILE, UNSUPPORTED_PLATFORM, BYTE_LIMIT_EXCEEDED;
    any other private read reason is a SOURCE_READ_FAILED.
    """
    try:
        data = _read_bounded_file(path, max_bytes)
    except TransportError as exc:
        reason = _LOCAL_FILE_REASONS.get(str(exc), "SOURCE_READ_FAILED")
        raise TransportError(reason) from None
    if len(data) > max_bytes:
        raise TransportError("BYTE_LIMIT_EXCEEDED")
    return data


class Ingestor:
    """Trusted codecs/schema configured once; caller supplies positive limits.

    max_depth counts JSON containers, with a root object/array at depth one.
    file() reads at most max_bytes + 1; http_body() opens no network connection.
    """

    def __init__(
        self,
        schema_path: Path,
        codecs: Mapping[tuple[str, str], Codec],
        *,
        max_bytes: int,
        max_depth: int,
    ) -> None:
        _positive_limit(max_bytes)
        _positive_limit(max_depth)
        self._max_bytes = max_bytes
        self._max_depth = max_depth
        self._codecs = dict(codecs)
        self._schema = ObservationSchema(schema_path)

    def _codec(self, source: Source) -> Codec:
        try:
            return self._codecs[(source.format, source.format_version)]
        except KeyError as exc:
            raise UnsupportedFormat("UNSUPPORTED_FORMAT") from exc

    def _receive(self, data: bytes, locator: str, source: Source, codec: Codec) -> IngestResult:
        return self._receive_projection(data, locator, source, codec)[0]

    def _receive_projection(
        self, data: bytes, locator: str, source: Source, codec: Codec
    ) -> tuple[IngestResult, Projection]:
        if len(data) > self._max_bytes:
            raise TransportError("BYTE_LIMIT_EXCEEDED")
        decoded = decode_json(data, self._max_depth)
        projection = codec(decoded)
        observation = project.project(projection, source, data)
        self._schema.validate(observation)
        return IngestResult(observation, locator), projection

    def buffered_projection(
        self, body: bytes, locator: str, source: Source
    ) -> tuple[IngestResult, Projection]:
        """Internal inspection seam retaining original codec facts transiently."""
        return self._receive_projection(body, locator, source, self._codec(source))

    def file(self, path: Path, source: Source) -> IngestResult:
        """Read a bounded regular local artifact and retain its transport path."""
        codec = self._codec(source)
        return self._receive(_read_bounded_file(path, self._max_bytes), str(path), source, codec)

    def http_body_file(self, path: Path, locator: str, source: Source) -> IngestResult:
        """Read a bounded local body capture; locator is supplied, never fetched."""
        codec = self._codec(source)
        return self._receive(_read_bounded_file(path, self._max_bytes), locator, source, codec)

    def http_body(self, body: bytes, locator: str, source: Source) -> IngestResult:
        """Ingest an already-received HTTP envelope; never perform network I/O."""
        return self._receive(body, locator, source, self._codec(source))

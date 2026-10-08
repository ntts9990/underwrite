"""Bounded file/in-memory envelope ingestion through one decode/project/validate seam."""

from __future__ import annotations

import json
import math
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from underwrite.instrument.ingest import project
from underwrite.instrument.ingest.project import Projection, Source
from underwrite.instrument.ingest.schema import ObservationSchema

type Codec = Callable[[object], Projection]

_FILE_READ_CHUNK_BYTES = 64 * 1024


class UnsupportedFormat(ValueError):
    """No trusted codec was registered for this exact format/version pair."""


class TransportError(ValueError):
    """Source bytes could not be read within the configured byte budget."""

    attempted_read: bool = False


class DecodeError(ValueError):
    """Source bytes are not strict UTF-8 JSON within the configured depth."""


@dataclass(frozen=True)
class IngestResult:
    """Validated observation with transport metadata outside its content digest."""

    observation: dict[str, object]
    transport_locator: str


def _positive_limit(value: int) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError("INGEST_LIMIT_MUST_BE_POSITIVE_INTEGER")


def read_descriptor(fd: int, max_bytes: int) -> bytes:
    """Read a caller-owned regular descriptor, at most budget plus one byte."""
    _positive_limit(max_bytes)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise TransportError("SOURCE_NOT_REGULAR_FILE")
    data = bytearray()
    remaining = max_bytes + 1
    # EOF is the loop condition itself, so no inverted EOF test can spin at end of file.
    while remaining and (chunk := os.read(fd, min(_FILE_READ_CHUNK_BYTES, remaining))):
        data.extend(chunk)
        remaining -= len(chunk)
    return bytes(data)


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


def _check_depth(text: str, max_depth: int) -> None:
    """Check container nesting before json.loads can exhaust its call stack."""
    depth = 0
    quoted = False
    escaped = False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > max_depth:
                raise DecodeError("DEPTH_LIMIT_EXCEEDED")
        elif character in "]}":
            depth -= 1


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DecodeError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise DecodeError("NON_FINITE_JSON_NUMBER")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise DecodeError("NON_FINITE_JSON_NUMBER")
    return number


def decode_json(data: bytes, max_depth: int) -> object:
    try:
        text = data.decode("utf-8", errors="strict")
        _check_depth(text, max_depth)
        decoded: object = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
        # Escaped lone surrogates survive json.loads but are not Unicode scalars.
        json.dumps(decoded, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return decoded
    except DecodeError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DecodeError("INVALID_JSON_INPUT") from exc


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

"""Descriptor ownership and byte accounting, not an implementation-shaped one-read mock."""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from _repo_paths import repo_root

from underwrite.instrument.ingest import transport
from underwrite.instrument.ingest.project import Projection, Source

ROOT = repo_root(Path(__file__).resolve())
SCHEMA = ROOT / "contracts/observation.v1.schema.json"
SOURCE = Source("test.input", "v1", "same-reference")
_MIN_MULTICHUNK_REQUESTS = 2


def _read(path: Path, limit: int) -> bytes:
    return transport._read_bounded_file(path, limit)  # pyright: ignore[reportPrivateUsage]


def _ingestor(limit: int) -> transport.Ingestor:
    return transport.Ingestor(
        SCHEMA,
        {
            (SOURCE.format, SOURCE.format_version): lambda value: Projection(
                "eval_run", {"value": value}
            )
        },
        max_bytes=limit,
        max_depth=8,
    )


def _closed(fd: int, original_fstat: Any) -> None:
    with pytest.raises(OSError):
        original_fstat(fd)


def _fixed_fd(fd: int) -> Callable[..., int]:
    def opening(*args: object) -> int:
        return fd

    return opening


def _fixed_mode(mode: int) -> Callable[[int], os.stat_result]:
    def checking(fd: int) -> os.stat_result:
        return os.stat_result((mode, 0, 0, 0, 0, 0, 0, 0, 0, 0))

    return checking


@pytest.mark.parametrize("limit", [0, -1, True, False, 1.5, "5", None])
def test_private_reader_requires_exact_positive_limit_before_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: Any
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid budget reached file open")

    monkeypatch.setattr(os, "open", forbidden)
    with pytest.raises(ValueError, match="^INGEST_LIMIT_MUST_BE_POSITIVE_INTEGER$"):
        _read(tmp_path / "absent", limit)


@pytest.mark.parametrize("flag", ["O_NONBLOCK", "O_NOCTTY"])
def test_missing_safe_open_capability_is_typed_but_in_memory_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    ingest = _ingestor(1)
    monkeypatch.delattr(os, flag)
    with pytest.raises(transport.TransportError, match="^SOURCE_NONBLOCKING_UNAVAILABLE$"):
        _read(tmp_path / "absent", 1)
    assert ingest.http_body(b"0", "not-fetched", SOURCE).observation["payload"] == {"value": 0}


@pytest.mark.parametrize("body", [b"", b"0", b"0 ", b"0  "])
def test_descriptor_order_flags_exact_bytes_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    path = tmp_path / "regular"
    path.write_bytes(body)
    opened: list[int] = []
    events: list[str] = []
    real_open, real_fstat, real_read, real_close = os.open, os.fstat, os.read, os.close

    def opening(target: Path, flags: int) -> int:
        assert target == path
        assert flags & os.O_NONBLOCK
        assert flags & os.O_NOCTTY
        assert flags & os.O_ACCMODE == os.O_RDONLY
        assert not flags & (os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        fd = real_open(target, flags)
        assert not os.get_inheritable(fd)
        opened.append(fd)
        events.append("open")
        return fd

    def checking(fd: int) -> os.stat_result:
        assert fd == opened[0]
        events.append("fstat")
        return real_fstat(fd)

    def reading(fd: int, size: int) -> bytes:
        assert fd == opened[0] and "fstat" in events
        assert 0 < size <= 64 * 1024
        events.append("read")
        return real_read(fd, size)

    def closing(fd: int) -> None:
        assert fd == opened[0]
        events.append("close")
        real_close(fd)

    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(os, "fstat", checking)
    monkeypatch.setattr(os, "read", reading)
    monkeypatch.setattr(os, "close", closing)
    assert _read(path, 2) == body[:3]
    assert events[:2] == ["open", "fstat"] and events[-1] == "close"
    assert events.count("open") == events.count("fstat") == events.count("close") == 1
    _closed(opened[0], real_fstat)


@pytest.mark.parametrize(
    "mode", [stat.S_IFIFO, stat.S_IFDIR, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK]
)
def test_nonregular_descriptor_rejects_before_read_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: int
) -> None:
    path = tmp_path / "regular"
    path.write_bytes(b"0")
    fd = os.open(path, os.O_RDONLY)
    real_fstat = os.fstat
    monkeypatch.setattr(os, "open", _fixed_fd(fd))
    monkeypatch.setattr(os, "fstat", _fixed_mode(mode))

    def forbidden(*args: object) -> bytes:
        pytest.fail("nonregular descriptor reached read")

    monkeypatch.setattr(os, "read", forbidden)
    with pytest.raises(transport.TransportError, match="^SOURCE_NOT_REGULAR_FILE$"):
        _read(path, 1)
    _closed(fd, real_fstat)


@pytest.mark.parametrize("operation", ["fstat", "read", "close"])
def test_os_errors_are_typed_and_descriptor_is_not_leaked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    path = tmp_path / "regular"
    path.write_bytes(b"0")
    fd = os.open(path, os.O_RDONLY)
    real_fstat, real_close = os.fstat, os.close
    monkeypatch.setattr(os, "open", _fixed_fd(fd))
    closed: list[int] = []

    def close(current: int) -> None:
        closed.append(current)
        real_close(current)
        if operation == "close":
            raise OSError("owned-close-fault")

    def fail(*args: object) -> Any:
        raise OSError("owned-file-fault")

    monkeypatch.setattr(os, "close", close)
    if operation != "close":
        monkeypatch.setattr(os, operation, fail)
    with pytest.raises(transport.TransportError, match="^SOURCE_READ_FAILED$") as caught:
        _read(path, 1)
    assert isinstance(caught.value.__cause__, OSError)
    assert closed == [fd]
    _closed(fd, real_fstat)


def test_open_error_never_closes_unowned_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object) -> int:
        raise PermissionError("owned-open-fault")

    def forbidden(*args: object) -> None:
        pytest.fail("closed a descriptor without a successful open")

    monkeypatch.setattr(os, "open", fail)
    monkeypatch.setattr(os, "close", forbidden)
    with pytest.raises(transport.TransportError, match="^SOURCE_READ_FAILED$") as caught:
        _read(tmp_path / "absent", 1)
    assert isinstance(caught.value.__cause__, PermissionError)


@pytest.mark.parametrize("limit", [1, 23, 64 * 1024, 64 * 1024 + 11, sys.maxsize**2])
def test_short_nonempty_reads_continue_with_bounded_native_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    body = b"abcdefg" * 20000
    path = tmp_path / "regular"
    path.write_bytes(body)
    real_read = os.read
    consumed = 0
    requests: list[int] = []

    def reading(fd: int, size: int) -> bytes:
        nonlocal consumed
        assert 0 < size <= min(64 * 1024, limit + 1 - consumed)
        requests.append(size)
        piece = real_read(fd, min(size, 8191))
        consumed += len(piece)
        return piece

    monkeypatch.setattr(os, "read", reading)
    assert _read(path, limit) == body[: limit + 1]
    assert consumed == min(len(body), limit + 1)
    assert requests
    if limit > len(body):
        assert len(requests) > _MIN_MULTICHUNK_REQUESTS


def test_descriptor_zero_is_still_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Synthetic descriptor avoids altering the test runner's real stdin.
    closed: list[int] = []
    monkeypatch.setattr(os, "open", _fixed_fd(0))
    monkeypatch.setattr(os, "fstat", _fixed_mode(stat.S_IFREG))

    def eof(fd: int, count: int) -> bytes:
        return b""

    monkeypatch.setattr(os, "read", eof)
    monkeypatch.setattr(os, "close", closed.append)
    assert _read(tmp_path / "synthetic", 1) == b""
    assert closed == [0]


@pytest.mark.parametrize(
    "error", [MemoryError("owned allocation failure"), RuntimeError("owned defect")]
)
def test_unexpected_failures_propagate_after_descriptor_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    path = tmp_path / "regular"
    path.write_bytes(b"0")
    fd = os.open(path, os.O_RDONLY)
    real_fstat = os.fstat
    monkeypatch.setattr(os, "open", _fixed_fd(fd))

    def fail(*args: object) -> bytes:
        raise error

    monkeypatch.setattr(os, "read", fail)
    with pytest.raises(type(error)) as caught:
        _read(path, 1)
    assert caught.value is error
    _closed(fd, real_fstat)


def test_replacing_path_after_open_keeps_original_unlinked_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "regular"
    path.write_bytes(b"original")
    fifo = tmp_path / "owned-fifo"
    os.mkfifo(fifo)
    real_open = os.open
    opened = 0

    def opening(target: Path, flags: int) -> int:
        nonlocal opened
        opened += 1
        assert opened == 1
        fd = real_open(target, flags)
        os.replace(fifo, target)
        return fd

    monkeypatch.setattr(os, "open", opening)
    assert _read(path, 100) == b"original"
    assert path.is_fifo()


def test_regular_symlink_and_dangling_symlink_preserve_input_semantics(tmp_path: Path) -> None:
    path = tmp_path / "regular"
    path.write_bytes(b"0")
    link = tmp_path / "link"
    link.symlink_to(path)
    ingest = _ingestor(1)
    assert ingest.file(path, SOURCE).observation == ingest.file(link, SOURCE).observation
    path.unlink()
    with pytest.raises(transport.TransportError, match="^SOURCE_READ_FAILED$"):
        ingest.file(link, SOURCE)


def test_unlinked_symlink_after_open_does_not_trigger_path_revalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "regular"
    path.write_bytes(b"0")
    link = tmp_path / "link"
    link.symlink_to(path)
    real_open = os.open

    def opening(target: Path, flags: int) -> int:
        fd = real_open(target, flags)
        link.unlink()
        return fd

    monkeypatch.setattr(os, "open", opening)
    assert _read(link, 1) == b"0"
    assert path.read_bytes() == b"0" and not link.exists()


@pytest.mark.parametrize(
    "body,error",
    [
        (b"", transport.DecodeError),
        (b"{", transport.DecodeError),
        (b"0 ", transport.TransportError),
    ],
)
def test_descriptor_closed_before_decode_or_admission_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: bytes, error: type[ValueError]
) -> None:
    path = tmp_path / "regular"
    path.write_bytes(body)
    real_open, real_fstat = os.open, os.fstat
    opened: list[int] = []

    def opening(target: Path, flags: int) -> int:
        fd = real_open(target, flags)
        opened.append(fd)
        return fd

    ingest = _ingestor(1)
    monkeypatch.setattr(os, "open", opening)
    with pytest.raises(error):
        ingest.file(path, SOURCE)
    _closed(opened[0], real_fstat)


def test_chunk_size_is_not_an_admission_cap(tmp_path: Path) -> None:
    body = b"0" + b" " * (64 * 1024 + 10)
    path = tmp_path / "regular"
    path.write_bytes(body)
    assert (
        _ingestor(len(body)).file(path, SOURCE).observation
        == _ingestor(len(body)).http_body(body, "label", SOURCE).observation
    )
    with pytest.raises(transport.TransportError, match="^BYTE_LIMIT_EXCEEDED$"):
        _ingestor(len(body) - 1).file(path, SOURCE)


def test_local_file_admits_exactly_its_budget_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "regular"
    path.write_bytes(b"0123")
    assert transport.read_local_file(path, 4) == b"0123"


def _local_file_reason(path: Path, limit: int) -> tuple[object, ...]:
    with pytest.raises(transport.TransportError) as caught:
        transport.read_local_file(path, limit)
    # No chained private reason is shown: raised plainly, or re-raised ``from None``.
    error = caught.value
    assert error.__cause__ is None and (error.__context__ is None or error.__suppress_context__)
    return error.args


def test_local_file_over_budget_is_a_byte_limit(tmp_path: Path) -> None:
    path = tmp_path / "regular"
    path.write_bytes(b"01234")
    assert _local_file_reason(path, 4) == ("BYTE_LIMIT_EXCEEDED",)


def test_local_file_directory_fifo_and_missing_keep_their_reasons(tmp_path: Path) -> None:
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert _local_file_reason(tmp_path, 1) == ("SOURCE_NOT_REGULAR_FILE",)
    assert _local_file_reason(fifo, 1) == ("SOURCE_NOT_REGULAR_FILE",)
    assert _local_file_reason(tmp_path / "absent", 1) == ("SOURCE_READ_FAILED",)


@pytest.mark.parametrize("flag", ["O_NONBLOCK", "O_NOCTTY"])
def test_local_file_without_safe_open_is_an_unsupported_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    monkeypatch.delattr(os, flag)
    assert _local_file_reason(tmp_path / "absent", 1) == ("UNSUPPORTED_PLATFORM",)


@pytest.mark.parametrize(
    ("private", "public"),
    [
        ("INGEST_PRIVATE_REASON", "SOURCE_READ_FAILED"),
        ("", "SOURCE_READ_FAILED"),
        ("BYTE_LIMIT_EXCEEDED ", "SOURCE_READ_FAILED"),
        ("SOURCE_READ_FAILED", "SOURCE_READ_FAILED"),
        ("SOURCE_NOT_REGULAR_FILE", "SOURCE_NOT_REGULAR_FILE"),
        ("SOURCE_NONBLOCKING_UNAVAILABLE", "UNSUPPORTED_PLATFORM"),
        ("BYTE_LIMIT_EXCEEDED", "BYTE_LIMIT_EXCEEDED"),
    ],
)
def test_local_file_maps_any_other_read_reason_to_a_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, private: str, public: str
) -> None:
    """The four reasons are the whole vocabulary: any other private reason is a read failure."""

    def raising(path: Path, limit: int) -> bytes:
        raise transport.TransportError(private)

    monkeypatch.setattr(transport, "_read_bounded_file", raising)
    assert _local_file_reason(tmp_path / "absent", 1) == (public,)

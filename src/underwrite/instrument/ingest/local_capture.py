"""Bounded read-once capture of declared regular files beneath one trusted root."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final, cast

from underwrite.instrument.ingest.inspection import InspectionError, constrained_flags, read_at
from underwrite.instrument.ingest.transport import DecodeError, TransportError, decode_json

_REASON_CODES: Final = {
    "CAPTURE_PLATFORM_UNSUPPORTED": "UNSUPPORTED_PLATFORM",
    "CAPTURE_ROOT_SYMLINK_REJECTED": "SOURCE_READ_FAILED",
    "CAPTURE_ROOT_READ_FAILED": "SOURCE_READ_FAILED",
    "CAPTURE_ROOT_DIRECTORY_REQUIRED": "SOURCE_READ_FAILED",
    "CAPTURE_RELATIVE_PATH_INVALID": "SOURCE_READ_FAILED",
    "CAPTURE_PATH_DEPTH_EXCEEDED": "INPUT_LIMIT_EXCEEDED",
    "CAPTURE_LOGICAL_NAME_INVALID": "SOURCE_READ_FAILED",
    "CAPTURE_LOGICAL_NAME_CONFLICT": "SOURCE_READ_FAILED",
    "CAPTURE_READER_CLOSED": "SOURCE_READ_FAILED",
    "CAPTURE_CLOSE_FAILED": "SOURCE_READ_FAILED",
    "CAPTURE_FILE_LIMIT_INVALID": "INPUT_LIMIT_EXCEEDED",
    "CAPTURE_FILE_LIMIT_EXCEEDED": "INPUT_LIMIT_EXCEEDED",
    "CAPTURE_FILE_COUNT_EXCEEDED": "INPUT_LIMIT_EXCEEDED",
    "CAPTURE_TOTAL_LIMIT_EXCEEDED": "INPUT_LIMIT_EXCEEDED",
    "CAPTURE_REGULAR_FILE_REQUIRED": "SOURCE_READ_FAILED",
    "CAPTURE_READ_FAILED": "SOURCE_READ_FAILED",
    "CAPTURE_JSON_DEPTH_LIMIT_INVALID": "INPUT_LIMIT_EXCEEDED",
    "CAPTURE_INVALID_JSON": "INVALID_INPUT",
    "CAPTURE_JSON_OBJECT_REQUIRED": "INVALID_INPUT",
    "CAPTURE_TOPOLOGY_INVALID": "SOURCE_READ_FAILED",
    "CAPTURE_TOPOLOGY_CHANGED": "SOURCE_READ_FAILED",
}


@dataclass(frozen=True)
class CapturePolicy:
    """Caller-owned aggregate limits shared by every capture from one root."""

    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    max_path_components: int = 64

    def __post_init__(self) -> None:
        values = (
            self.max_files,
            self.max_file_bytes,
            self.max_total_bytes,
            self.max_path_components,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("CAPTURE_POLICY_REQUIRES_POSITIVE_INTEGER_LIMITS")


@dataclass(frozen=True)
class CapturedBytes:
    """One immutable logical artifact and its raw byte identity."""

    logical_name: str
    data: bytes
    sha256: str
    size: int


class CaptureError(ValueError):
    """A finite, code-owned local capture failure."""

    def __init__(self, code: str, reason: str, location: str = "") -> None:
        if _REASON_CODES.get(reason) != code:
            code, reason, location = "SOURCE_READ_FAILED", "CAPTURE_READ_FAILED", ""
        self.code = code
        self.reason = reason
        self.location = location
        super().__init__(reason)


def _error(code: str, reason: str, location: str = "") -> CaptureError:
    return CaptureError(code, reason, location)


def _flags() -> tuple[int, int]:
    try:
        return constrained_flags()
    except InspectionError:
        raise _error("UNSUPPORTED_PLATFORM", "CAPTURE_PLATFORM_UNSUPPORTED") from None


def _absolute_without_resolution(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def _open_root(path: Path, flags: int) -> int:
    """Anchor every root component to its opened parent, including relative roots."""
    if "\x00" in str(path):
        raise _error("SOURCE_READ_FAILED", "CAPTURE_ROOT_READ_FAILED")
    try:
        os.fsencode(path)
    except UnicodeError:
        raise _error("SOURCE_READ_FAILED", "CAPTURE_ROOT_READ_FAILED") from None
    descriptor: int | None = None
    try:
        absolute = _absolute_without_resolution(path)
        descriptor = os.open(absolute.anchor, flags)
        for part in absolute.parts[1:]:
            info = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise _error("SOURCE_READ_FAILED", "CAPTURE_ROOT_SYMLINK_REJECTED")
            if not stat.S_ISDIR(info.st_mode):
                raise _error("SOURCE_READ_FAILED", "CAPTURE_ROOT_DIRECTORY_REQUIRED")
            child = os.open(part, flags, dir_fd=descriptor)
            parent, descriptor = descriptor, child
            os.close(parent)
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise _error("SOURCE_READ_FAILED", "CAPTURE_ROOT_DIRECTORY_REQUIRED")
        result, descriptor = descriptor, None
        return result
    except OSError:
        raise _error("SOURCE_READ_FAILED", "CAPTURE_ROOT_READ_FAILED") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _relative_parts(value: str, maximum: int, location: str) -> tuple[str, ...]:
    if type(value) is not str or not value or value.startswith(("/", "\\")):
        raise _error("SOURCE_READ_FAILED", "CAPTURE_RELATIVE_PATH_INVALID", location)
    if "\x00" in value or "\\" in value:
        raise _error("SOURCE_READ_FAILED", "CAPTURE_RELATIVE_PATH_INVALID", location)
    try:
        os.fsencode(value)
    except UnicodeError:
        raise _error("SOURCE_READ_FAILED", "CAPTURE_RELATIVE_PATH_INVALID", location) from None
    parts = tuple(value.split("/"))
    if len(parts) > maximum:
        raise _error("INPUT_LIMIT_EXCEEDED", "CAPTURE_PATH_DEPTH_EXCEEDED", location)
    if any(part in {"", ".", ".."} for part in parts):
        raise _error("SOURCE_READ_FAILED", "CAPTURE_RELATIVE_PATH_INVALID", location)
    return parts


def _logical_name(value: str, location: str) -> str:
    if type(value) is not str or not value or not value.strip() or "\x00" in value:
        raise _error("SOURCE_READ_FAILED", "CAPTURE_LOGICAL_NAME_INVALID", location)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _error("SOURCE_READ_FAILED", "CAPTURE_LOGICAL_NAME_INVALID", location) from None
    return value


class RootReader:
    """Own one root descriptor and cache each successfully captured path once."""

    def __init__(self, root_fd: int, policy: CapturePolicy) -> None:
        self._root_fd: int | None = root_fd
        self._root_path: Path | None = None
        self._policy = policy
        self._by_path: dict[str, CapturedBytes] = {}
        self._path_by_logical: dict[str, str] = {}
        self._total_bytes = 0

    @classmethod
    def open(cls, root: Path, policy: CapturePolicy) -> RootReader:
        """Open an actual non-symlink directory as the capture boundary."""
        directory_flags, _ = _flags()
        reader = cls(_open_root(root, directory_flags), policy)
        reader._root_path = _absolute_without_resolution(root)
        return reader

    def __enter__(self) -> RootReader:
        if self._root_fd is None:
            raise _error("SOURCE_READ_FAILED", "CAPTURE_READER_CLOSED")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self.close()

    @property
    def file_count(self) -> int:
        return len(self._by_path)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def close(self) -> None:
        """Close the owned root descriptor; repeated close is harmless."""
        descriptor, self._root_fd = self._root_fd, None
        if descriptor is None:
            return
        try:
            os.close(descriptor)
        except OSError:
            raise _error("SOURCE_READ_FAILED", "CAPTURE_CLOSE_FAILED") from None

    def capture_exact_tree(self, members: Mapping[str, int]) -> dict[str, CapturedBytes]:
        """Capture a bounded declared tree, refusing links and topology changes.

        Membership traversal is descriptor-relative and stops on the first extra
        entry. Metadata includes inode and change times, not only entry names.
        This detects ordinary concurrent changes, not a hostile same-user writer
        capable of restoring every observation between checks.
        """
        if self._by_path:
            raise _error("SOURCE_READ_FAILED", "CAPTURE_TOPOLOGY_INVALID")
        before = self._tree_identity(members)
        captured = {
            name: self.capture_relative(
                name, logical_name=name, max_bytes=limit, location="/exchange/" + name
            )
            for name, limit in members.items()
        }
        if self._tree_identity(members) != before:
            raise _error("SOURCE_READ_FAILED", "CAPTURE_TOPOLOGY_CHANGED")
        return captured

    def _tree_identity(self, members: Mapping[str, int]) -> dict[str, tuple[int, ...]]:
        root = self._root_fd
        if root is None:
            raise _error("SOURCE_READ_FAILED", "CAPTURE_READER_CLOSED")
        if not members or len(members) > self._policy.max_files:
            raise _error("INPUT_LIMIT_EXCEEDED", "CAPTURE_FILE_COUNT_EXCEEDED")
        children = _tree_children(members, self._policy.max_path_components)
        identities: dict[str, tuple[int, ...]] = {}
        try:
            if self._root_path is not None and _stat_identity(
                os.stat(self._root_path, follow_symlinks=False)
            ) != _stat_identity(os.fstat(root)):
                raise _error("SOURCE_READ_FAILED", "CAPTURE_TOPOLOGY_CHANGED")
            _visit_tree(root, "", children, identities)
        except OSError:
            raise _error("SOURCE_READ_FAILED", "CAPTURE_TOPOLOGY_INVALID") from None
        return identities

    def capture_relative(
        self,
        relative_path: str,
        *,
        logical_name: str,
        max_bytes: int,
        location: str,
    ) -> CapturedBytes:
        """Capture a declared root-relative regular file at most once."""
        if self._root_fd is None:
            raise _error("SOURCE_READ_FAILED", "CAPTURE_READER_CLOSED", location)
        if type(max_bytes) is not int or max_bytes <= 0:
            raise _error("INPUT_LIMIT_EXCEEDED", "CAPTURE_FILE_LIMIT_INVALID", location)
        parts = _relative_parts(relative_path, self._policy.max_path_components, location)
        identity = "/".join(parts)
        logical = _logical_name(logical_name, location)
        maximum = min(max_bytes, self._policy.max_file_bytes)
        cached = self._cached(identity, logical, maximum, location)
        if cached is not None:
            return cached
        if len(self._by_path) >= self._policy.max_files:
            raise _error("INPUT_LIMIT_EXCEEDED", "CAPTURE_FILE_COUNT_EXCEEDED", location)
        remaining = self._policy.max_total_bytes - self._total_bytes
        data = self._read_bounded(parts, maximum, remaining, location)
        captured = CapturedBytes(
            logical,
            data,
            "sha256:" + hashlib.sha256(data).hexdigest(),
            len(data),
        )
        self._by_path[identity] = captured
        self._path_by_logical[logical] = identity
        self._total_bytes += captured.size
        return captured

    def _cached(
        self, identity: str, logical: str, maximum: int, location: str
    ) -> CapturedBytes | None:
        prior_path = self._path_by_logical.get(logical)
        if prior_path is not None and prior_path != identity:
            raise _error("SOURCE_READ_FAILED", "CAPTURE_LOGICAL_NAME_CONFLICT", location)
        cached = self._by_path.get(identity)
        if cached is not None:
            if cached.logical_name != logical:
                raise _error("SOURCE_READ_FAILED", "CAPTURE_LOGICAL_NAME_CONFLICT", location)
            if cached.size > maximum:
                raise _error("INPUT_LIMIT_EXCEEDED", "CAPTURE_FILE_LIMIT_EXCEEDED", location)
            return cached
        return None

    def _read_bounded(
        self, parts: tuple[str, ...], maximum: int, remaining: int, location: str
    ) -> bytes:
        allowance = min(maximum, remaining)
        root_fd = self._root_fd
        if root_fd is None:
            raise _error("SOURCE_READ_FAILED", "CAPTURE_READER_CLOSED", location)
        try:
            return read_at(root_fd, list(parts), allowance, _flags())
        except TransportError as exc:
            if exc.args == ("BYTE_LIMIT_EXCEEDED",):
                reason = (
                    "CAPTURE_TOTAL_LIMIT_EXCEEDED"
                    if remaining < maximum
                    else "CAPTURE_FILE_LIMIT_EXCEEDED"
                )
                raise _error("INPUT_LIMIT_EXCEEDED", reason, location) from None
            if exc.args == ("SOURCE_NOT_REGULAR_FILE",):
                raise _error(
                    "SOURCE_READ_FAILED", "CAPTURE_REGULAR_FILE_REQUIRED", location
                ) from None
            raise _error("SOURCE_READ_FAILED", "CAPTURE_READ_FAILED", location) from None
        except OSError:
            raise _error("SOURCE_READ_FAILED", "CAPTURE_READ_FAILED", location) from None


def decode_captured_object(
    captured: CapturedBytes, *, max_depth: int, location: str
) -> dict[str, object]:
    """Strictly decode one captured JSON object without another file read."""
    if type(max_depth) is not int or max_depth <= 0:
        raise _error("INPUT_LIMIT_EXCEEDED", "CAPTURE_JSON_DEPTH_LIMIT_INVALID", location)
    try:
        value = decode_json(captured.data, max_depth)
    except DecodeError:
        raise _error("INVALID_INPUT", "CAPTURE_INVALID_JSON", location) from None
    if type(value) is not dict:
        raise _error("INVALID_INPUT", "CAPTURE_JSON_OBJECT_REQUIRED", location)
    return cast(dict[str, object], value)


def _tree_children(members: Mapping[str, int], maximum: int) -> dict[str, set[str]]:
    children: dict[str, set[str]] = {"": set()}
    for name in members:
        parts = _relative_parts(name, maximum, "")
        for index, part in enumerate(parts):
            parent = "/".join(parts[:index])
            children.setdefault(parent, set()).add(part)
    return children


def _stat_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _visit_tree(
    fd: int,
    parent: str,
    children: Mapping[str, set[str]],
    identities: dict[str, tuple[int, ...]],
) -> None:
    identities[parent] = _stat_identity(os.fstat(fd))
    expected = children[parent]
    seen: set[str] = set()
    with os.scandir(fd) as entries:
        for entry in entries:
            if entry.name not in expected or entry.name in seen:
                raise _error("SOURCE_READ_FAILED", "CAPTURE_TOPOLOGY_INVALID")
            seen.add(entry.name)
            name = f"{parent}/{entry.name}" if parent else entry.name
            info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
            if name in children:
                _visit_child(fd, entry.name, name, info, children, identities)
            else:
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise _error("SOURCE_READ_FAILED", "CAPTURE_TOPOLOGY_INVALID")
                identities[name] = _stat_identity(info)
    if seen != expected:
        raise _error("SOURCE_READ_FAILED", "CAPTURE_TOPOLOGY_INVALID")


def _visit_child(
    fd: int,
    entry: str,
    name: str,
    info: os.stat_result,
    children: Mapping[str, set[str]],
    identities: dict[str, tuple[int, ...]],
) -> None:
    if not stat.S_ISDIR(info.st_mode):
        raise _error("SOURCE_READ_FAILED", "CAPTURE_TOPOLOGY_INVALID")
    child = os.open(entry, _flags()[0], dir_fd=fd)
    try:
        if _stat_identity(os.fstat(child)) != _stat_identity(info):
            raise _error("SOURCE_READ_FAILED", "CAPTURE_TOPOLOGY_CHANGED")
        _visit_tree(child, name, children, identities)
    finally:
        os.close(child)

"""Bounded descriptor reads for local agent-surface evaluation artifacts."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path, PurePosixPath

from underwrite._fileio import TransportError, read_descriptor

MAX_RELATIVE_PATH_BYTES = 4096
MAX_COMPONENT_BYTES = 255
MAX_COMPONENTS = 64


def _open_flags() -> tuple[int, int]:
    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK", "O_NOCTTY")
    if any(not hasattr(os, name) for name in required) or os.open not in os.supports_dir_fd:
        raise ValueError("UNSUPPORTED_PLATFORM")
    directory = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    leaf = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY
    return directory, leaf


def read_relative(  # noqa: C901, PLR0912 - one pinned descriptor transaction
    root: Path, relative: str, limit: int
) -> bytes:
    """Read one regular file below a pinned root without following symlinks."""
    if type(relative) is not str:
        raise ValueError("UNSAFE_PATH")
    try:
        encoded = relative.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("UNSAFE_PATH") from exc
    selected = PurePosixPath(relative)
    if (
        not relative
        or not selected.parts
        or len(encoded) > MAX_RELATIVE_PATH_BYTES
        or len(selected.parts) > MAX_COMPONENTS
        or any(len(part.encode("utf-8")) > MAX_COMPONENT_BYTES for part in selected.parts)
        or "\\" in relative
        or selected.is_absolute()
        or ".." in selected.parts
        or selected.as_posix() != relative
        or limit <= 0
    ):
        raise ValueError("UNSAFE_PATH")
    if root.is_symlink():
        raise ValueError("UNSAFE_PATH")
    directory_flags, leaf_flags = _open_flags()
    descriptors: list[int] = []
    try:
        directory = os.open(root, directory_flags)
        descriptors.append(directory)
        for part in selected.parts[:-1]:
            directory = os.open(part, directory_flags, dir_fd=directory)
            descriptors.append(directory)
        file_fd = os.open(selected.parts[-1], leaf_flags, dir_fd=directory)
        descriptors.append(file_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("FILE_NOT_REGULAR")
        if info.st_size > limit:
            raise ValueError("FILE_LIMIT_EXCEEDED")
        data = read_descriptor(file_fd, limit)
        if len(data) > limit:
            raise ValueError("FILE_LIMIT_EXCEEDED")
        return data
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise ValueError("FILE_MISSING") from exc
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError("UNSAFE_PATH") from exc
        raise ValueError("FILE_UNREADABLE") from exc
    except TransportError as exc:
        raise ValueError("FILE_NOT_REGULAR") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)

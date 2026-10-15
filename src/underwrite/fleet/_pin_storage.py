"""Descriptor-anchored admission and atomic leaf replacement for one pin registry."""

from __future__ import annotations

import os
import stat
import uuid
from contextlib import ExitStack
from pathlib import Path

from underwrite._fileio import TransportError, read_descriptor

MANIFEST_MAX_BYTES = 1024 * 1024


class PinError(ValueError):
    """Finite pin-refresh refusal; publication describes attempted mutation."""

    def __init__(self, reason: str, publication: str = "not_started") -> None:
        self.reason = reason
        self.publication = publication
        super().__init__(reason)


def _flags() -> int:
    directory = getattr(os, "O_DIRECTORY", None)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if directory is None or nofollow is None:
        raise PinError("UNSUPPORTED_PLATFORM")
    return os.O_RDONLY | directory | nofollow


def _open_dir(name: str | Path, *, parent: int | None = None) -> int:
    try:
        if parent is None:
            return os.open(name, _flags())
        return os.open(name, _flags(), dir_fd=parent)
    except (OSError, TypeError, NotImplementedError):
        raise PinError("PIN_TARGET_UNSAFE") from None


class PinRegistry:
    """Hold the selected repo anchor; inspect all leaves before mutation."""

    def __init__(self, root: Path, repo: str) -> None:
        self.root = root
        self.repo = repo
        self._stack = ExitStack()
        self.repo_fd = -1
        self.started = False

    def __enter__(self) -> PinRegistry:
        try:
            root_fd = self._stack.enter_context(_owned_fd(_open_dir(self.root)))
            active_fd = self._stack.enter_context(_owned_fd(_open_dir("active", parent=root_fd)))
            self.repo_fd = self._stack.enter_context(
                _owned_fd(_open_dir(self.repo, parent=active_fd))
            )
            return self
        except BaseException:
            try:
                self._stack.close()
            except OSError:
                pass
            raise

    def __exit__(self, *_exc: object) -> None:
        try:
            self._stack.close()
        except OSError:
            raise self._error("PIN_WRITE_FAILED") from None

    def _error(self, reason: str) -> PinError:
        return PinError(reason, "may_be_partial" if self.started else "not_started")

    def read_manifest(self) -> bytes:
        nonblock = getattr(os, "O_NONBLOCK", None)
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nonblock is None or nofollow is None:
            raise PinError("UNSUPPORTED_PLATFORM")
        try:
            fd = os.open("PINS.json", os.O_RDONLY | nonblock | nofollow, dir_fd=self.repo_fd)
            with _owned_fd(fd):
                raw = read_descriptor(fd, MANIFEST_MAX_BYTES)
        except (OSError, TransportError, TypeError, NotImplementedError):
            raise PinError("PIN_TARGET_UNSAFE") from None
        if len(raw) > MANIFEST_MAX_BYTES:
            raise PinError("MANIFEST_LIMIT_EXCEEDED")
        return raw

    def _existing_parent(self, parts: tuple[str, ...]) -> int | None:
        """Open existing parents without links; None means a safe missing suffix."""
        flags = _flags()
        try:
            fd = os.dup(self.repo_fd)
        except OSError:
            raise self._error("PIN_TARGET_UNSAFE") from None
        try:
            for part in parts:
                try:
                    next_fd = os.open(part, flags, dir_fd=fd)
                except FileNotFoundError:
                    os.close(fd)
                    return None
                os.close(fd)
                fd = next_fd
            return fd
        except (OSError, TypeError, NotImplementedError):
            os.close(fd)
            raise self._error("PIN_TARGET_UNSAFE") from None

    def _check_leaf(self, parent_fd: int, leaf: str) -> int | None:
        try:
            status = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except (OSError, TypeError, NotImplementedError):
            raise self._error("PIN_TARGET_UNSAFE") from None
        if not stat.S_ISREG(status.st_mode):
            raise self._error("PIN_TARGET_UNSAFE")
        return stat.S_IMODE(status.st_mode) & 0o777

    def admit(self, paths: tuple[tuple[str, ...], ...]) -> None:
        for parts in paths:
            parent_fd = self._existing_parent(parts[:-1])
            if parent_fd is None:
                continue
            try:
                self._check_leaf(parent_fd, parts[-1])
            finally:
                try:
                    os.close(parent_fd)
                except OSError:
                    raise self._error("PIN_TARGET_UNSAFE") from None

    def _ensure_parent(self, parts: tuple[str, ...]) -> int:
        flags = _flags()
        try:
            fd = os.dup(self.repo_fd)
        except OSError:
            raise self._error("PIN_TARGET_UNSAFE") from None
        try:
            for part in parts:
                try:
                    next_fd = os.open(part, flags, dir_fd=fd)
                except FileNotFoundError:
                    self.started = True
                    os.mkdir(part, 0o755, dir_fd=fd)
                    next_fd = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            return fd
        except (OSError, TypeError, NotImplementedError):
            os.close(fd)
            raise self._error("PIN_TARGET_UNSAFE") from None

    def replace(self, parts: tuple[str, ...], data: bytes) -> None:
        parent_fd = self._ensure_parent(parts[:-1])
        temp: str | None = None
        try:
            mode = self._check_leaf(parent_fd, parts[-1])
            candidate = f".pins-refresh-{uuid.uuid4().hex}"
            self.started = True
            fd = os.open(
                candidate,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o644,
                dir_fd=parent_fd,
            )
            temp = candidate
            with _owned_fd(fd):
                position = 0
                view = memoryview(data)
                while position < len(data):
                    written = os.write(fd, view[position : position + 64 * 1024])
                    if written <= 0:
                        raise OSError
                    position += written
                if mode is not None:
                    os.fchmod(fd, mode)
            os.replace(temp, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            temp = None
        except PinError:
            raise
        except (OSError, TypeError, NotImplementedError):
            raise self._error("PIN_WRITE_FAILED") from None
        finally:
            try:
                if temp is not None:
                    try:
                        os.unlink(temp, dir_fd=parent_fd)
                    except OSError:
                        pass
            finally:
                try:
                    os.close(parent_fd)
                except OSError:
                    raise self._error("PIN_WRITE_FAILED") from None


class _owned_fd:
    """Close a descriptor owned by one lexical scope."""

    def __init__(self, fd: int) -> None:
        self.fd = fd

    def __enter__(self) -> int:
        return self.fd

    def __exit__(self, *_exc: object) -> None:
        os.close(self.fd)

"""Bounded offline process port for the operator's trusted Goppi installation."""

from __future__ import annotations

import os
import selectors
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Protocol

DEADLINE_SECONDS = 45.0
STDOUT_MAX_BYTES = 256 * 1024
STDERR_MAX_BYTES = 64 * 1024


class VerifierError(ValueError):
    """Finite process diagnostic; child text and command paths are never exposed."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(reason)


class TrajectoryVerifier(Protocol):
    """Verify only the caller-owned private snapshot, returning bounded raw JSON."""

    def verify(self, snapshot: Path) -> bytes: ...


class GoppiCliVerifier:
    """Run the installed producer's offline verifier, without shell or providers.

    Executable selection is trusted operator configuration. This adapter is not
    an executable sandbox or a guarantee of network isolation.
    """

    def __init__(self, executable: Path | None = None) -> None:
        self._executable = executable

    def verify(self, snapshot: Path) -> bytes:
        if os.name != "posix":
            raise VerifierError("UNSUPPORTED_PLATFORM", "GOPPI_VERIFIER_PLATFORM_UNSUPPORTED")
        executable = (
            str(self._executable.absolute())
            if self._executable is not None
            else shutil.which("goppi")
        )
        if executable is None:
            raise VerifierError("SOURCE_READ_FAILED", "GOPPI_VERIFIER_UNAVAILABLE")
        deadline = time.monotonic() + DEADLINE_SECONDS
        try:
            process = subprocess.Popen(
                [executable, "run", "verify-paired", "--input", str(snapshot), "--json"],
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except (OSError, ValueError):
            raise VerifierError("SOURCE_READ_FAILED", "GOPPI_VERIFIER_UNAVAILABLE") from None
        try:
            return _receive(process, deadline)
        except BaseException as exc:
            _terminate(process)
            if isinstance(exc, VerifierError):
                raise
            if isinstance(exc, (OSError, ValueError)):
                raise VerifierError("SOURCE_READ_FAILED", "GOPPI_VERIFIER_PROCESS_FAILED") from None
            raise
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


def _terminate(process: subprocess.Popen[bytes]) -> None:
    # Even an exited leader may leave descendants holding its pipe descriptors.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        # Cleanup remains bounded even under an abnormal operating-system stall.
        pass


def _receive(process: subprocess.Popen[bytes], deadline: float) -> bytes:
    assert process.stdout is not None and process.stderr is not None
    output = bytearray()
    sizes = {process.stdout.fileno(): 0, process.stderr.fileno(): 0}
    caps = {process.stdout.fileno(): STDOUT_MAX_BYTES, process.stderr.fileno(): STDERR_MAX_BYTES}
    stdout_fd = process.stdout.fileno()
    with selectors.DefaultSelector() as selector:
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VerifierError("SOURCE_READ_FAILED", "GOPPI_VERIFIER_TIMEOUT")
            for key, _ in selector.select(remaining):
                fd = key.fd
                chunk = os.read(fd, min(65536, caps[fd] - sizes[fd] + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                sizes[fd] += len(chunk)
                if sizes[fd] > caps[fd]:
                    raise VerifierError("OUTPUT_LIMIT_EXCEEDED", "GOPPI_VERIFIER_OUTPUT_LIMIT")
                if fd == stdout_fd:
                    output.extend(chunk)
        try:
            code = process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise VerifierError("SOURCE_READ_FAILED", "GOPPI_VERIFIER_TIMEOUT") from None
    if code != 0:
        raise VerifierError("INVALID_INPUT", "GOPPI_VERIFIER_REFUSED")
    return bytes(output)

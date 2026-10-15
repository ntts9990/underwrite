"""Bounded committed-blob capture finishes before any pin publication."""

from __future__ import annotations

import json
import subprocess
import sys
from argparse import Namespace
from collections.abc import Callable
from pathlib import Path

import pytest

from underwrite.cli import commands
from underwrite.fleet import pins
from underwrite.fleet._pin_storage import PinError
from underwrite.instrument.ingest.schema import packaged_validator

SMALL_BLOB_LIMIT = 10
NONZERO_EXIT = 17
REFUSAL_EXIT = 2


def _spawn_python(monkeypatch: pytest.MonkeyPatch, source: str) -> list[subprocess.Popen[bytes]]:
    real_popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []

    def launch(*_args: object, **_kwargs: object) -> subprocess.Popen[bytes]:
        assert _kwargs["stderr"] == subprocess.DEVNULL
        child = real_popen(
            [sys.executable, "-u", "-c", source],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        children.append(child)
        return child

    monkeypatch.setattr(pins.subprocess, "Popen", launch)
    return children


def _reaped(children: list[subprocess.Popen[bytes]]) -> None:
    assert len(children) == 1
    child = children[0]
    assert child.poll() is not None
    assert child.stdout is not None and child.stdout.closed


def test_blob_overflow_terminates_reaps_and_closes_real_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = (
        "import sys,time; sys.stdout.buffer.write(b'x'*1024); sys.stdout.flush(); time.sleep(30)"
    )
    with monkeypatch.context() as patch:
        children = _spawn_python(patch, source)
        with pytest.raises(PinError, match="^INPUT_LIMIT_EXCEEDED$"):
            pins._git_blob(tmp_path, "commit", "source", SMALL_BLOB_LIMIT)  # pyright: ignore[reportPrivateUsage]
    _reaped(children)


def test_blob_read_exception_terminates_reaps_and_closes_real_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_read(_process: subprocess.Popen[bytes], _limit: int) -> bytes:
        raise OSError("injected pipe failure")

    with monkeypatch.context() as patch:
        children = _spawn_python(patch, "import time; time.sleep(30)")
        patch.setattr(pins, "_read_blob_stdout", broken_read)
        with pytest.raises(OSError, match="injected pipe failure"):
            pins._git_blob(tmp_path, "commit", "source", SMALL_BLOB_LIMIT)  # pyright: ignore[reportPrivateUsage]
    _reaped(children)


def test_nonzero_git_blob_preserves_called_process_error_returncode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as patch:
        children = _spawn_python(patch, f"import sys; sys.exit({NONZERO_EXIT})")
        with pytest.raises(subprocess.CalledProcessError) as caught:
            pins._git_blob(tmp_path, "commit", "source", SMALL_BLOB_LIMIT)  # pyright: ignore[reportPrivateUsage]
    assert caught.value.returncode == NONZERO_EXIT
    _reaped(children)


def test_blob_cap_refuses_before_any_registry_mutation(
    stale_sample_pins: tuple[Path, Path],
    hermetic_git_sibling: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pins_root, _ = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    before = {p.relative_to(pins_root): p.read_bytes() for p in pins_root.rglob("*") if p.is_file()}
    monkeypatch.setattr(pins, "PIN_FILE_MAX_BYTES", SMALL_BLOB_LIMIT)
    with pytest.raises(PinError, match="^INPUT_LIMIT_EXCEEDED$"):
        pins.refresh_pins(pins_root, sibling_dir.parent, "sample-tool")
    exit_code, payload = commands.cmd_pins_refresh(
        Namespace(pins_root=pins_root, siblings_root=sibling_dir.parent, repo="sample-tool")
    )
    assert exit_code == REFUSAL_EXIT
    assert payload["details"] == {"reason": "INPUT_LIMIT_EXCEEDED", "publication": "not_started"}
    packaged_validator("cli_result.v1").validate(payload)  # pyright: ignore[reportUnknownMemberType]
    after = {p.relative_to(pins_root): p.read_bytes() for p in pins_root.rglob("*") if p.is_file()}
    assert after == before
    assert not list(pins_root.rglob(".pins-refresh-*"))


def test_manifest_expansion_refuses_before_any_registry_mutation(
    stale_sample_pins: tuple[Path, Path],
    hermetic_git_sibling: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    original = json.loads(manifest_path.read_bytes())
    original["files"][next(iter(original["files"]))]["sha256"] = "stale"
    manifest_path.write_text(json.dumps(original, separators=(",", ":")))
    before = {p.relative_to(pins_root): p.read_bytes() for p in pins_root.rglob("*") if p.is_file()}
    monkeypatch.setattr(pins, "MANIFEST_MAX_BYTES", len(manifest_path.read_bytes()) + 1)
    with pytest.raises(PinError, match="^MANIFEST_LIMIT_EXCEEDED$"):
        pins.refresh_pins(pins_root, sibling_dir.parent, "sample-tool")
    exit_code, payload = commands.cmd_pins_refresh(
        Namespace(pins_root=pins_root, siblings_root=sibling_dir.parent, repo="sample-tool")
    )
    assert exit_code == REFUSAL_EXIT
    assert payload["details"] == {"reason": "MANIFEST_LIMIT_EXCEEDED", "publication": "not_started"}
    packaged_validator("cli_result.v1").validate(payload)  # pyright: ignore[reportUnknownMemberType]
    after = {p.relative_to(pins_root): p.read_bytes() for p in pins_root.rglob("*") if p.is_file()}
    assert after == before
    assert not list(pins_root.rglob(".pins-refresh-*"))

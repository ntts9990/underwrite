"""Read-once and bounded acquisition of the five baseline exchange artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from _baseline_exchange import build_baseline_exchange

from underwrite.instrument.evidence.goppi_baseline import BaselineProjectionError
from underwrite.instrument.ingest import goppi_baseline
from underwrite.instrument.ingest.local_capture import CapturedBytes, CaptureError, RootReader


def _write(root: Path) -> dict[str, bytes]:
    files = build_baseline_exchange().files()
    for name, raw in files.items():
        (root / name).write_bytes(raw)
    return files


def test_captures_only_fixed_files_once_and_preserves_byte_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _write(tmp_path)
    (tmp_path / "private-extra.json").write_text("not source evidence")
    calls: list[str] = []
    original = RootReader.capture_relative

    def capture(
        self: RootReader, relative_path: str, *, logical_name: str, max_bytes: int, location: str
    ) -> CapturedBytes:
        calls.append(relative_path)
        return original(
            self, relative_path, logical_name=logical_name, max_bytes=max_bytes, location=location
        )

    monkeypatch.setattr(RootReader, "capture_relative", capture)
    documents = goppi_baseline.capture_baseline_documents(tmp_path)
    assert len(calls) == len(set(calls)) == len(files)
    assert set(calls) == set(files)
    assert dict(documents.identities) == {
        name: ("sha256:" + hashlib.sha256(raw).hexdigest(), len(raw)) for name, raw in files.items()
    }


@pytest.mark.parametrize("raw", [b"\xff", b'{"a":1,"a":2}', b"[]", b'{"x":NaN}', b"[" * 33])
def test_strict_document_decode(tmp_path: Path, raw: bytes) -> None:
    _write(tmp_path)
    (tmp_path / "manifest.json").write_bytes(raw)
    with pytest.raises(CaptureError) as caught:
        goppi_baseline.capture_baseline_documents(tmp_path)
    assert caught.value.code == "INVALID_INPUT"
    assert str(tmp_path) not in str(caught.value)


@pytest.mark.parametrize("raw", [b"\xff\n", b'{"a":1,"a":2}\n', b"[]\n", b"{}\n\n{}\n"])
def test_strict_rows_decode(tmp_path: Path, raw: bytes) -> None:
    _write(tmp_path)
    (tmp_path / "rows.jsonl").write_bytes(raw)
    with pytest.raises(CaptureError) as caught:
        goppi_baseline.capture_baseline_documents(tmp_path)
    assert caught.value.code == "INVALID_INPUT"


def test_row_limit(tmp_path: Path) -> None:
    _write(tmp_path)
    (tmp_path / "rows.jsonl").write_bytes(b"{}\n" * 201)
    with pytest.raises((CaptureError, BaselineProjectionError)) as caught:
        goppi_baseline.capture_baseline_documents(tmp_path)
    assert caught.value.code == "INPUT_LIMIT_EXCEEDED"


def test_file_byte_limit(tmp_path: Path) -> None:
    _write(tmp_path)
    (tmp_path / "manifest.json").write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises((CaptureError, BaselineProjectionError)) as caught:
        goppi_baseline.capture_baseline_documents(tmp_path)
    assert caught.value.code == "INPUT_LIMIT_EXCEEDED"


def test_member_symlink_rejected(tmp_path: Path) -> None:
    _write(tmp_path)
    (tmp_path / "manifest.json").unlink()
    (tmp_path / "manifest.json").symlink_to(tmp_path / "schema.json")
    with pytest.raises(CaptureError) as caught:
        goppi_baseline.capture_baseline_documents(tmp_path)
    assert caught.value.code == "SOURCE_READ_FAILED"


def test_root_symlink_rejected(tmp_path: Path) -> None:
    root = tmp_path / "real"
    root.mkdir()
    _write(root)
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(CaptureError) as caught:
        goppi_baseline.capture_baseline_documents(link)
    assert caught.value.code == "SOURCE_READ_FAILED"


def test_schema_is_captured_as_inert_bytes(tmp_path: Path) -> None:
    _write(tmp_path)
    raw = b'not executable JSON\xff {"$ref":"https://PRIVATE_SENTINEL"}'
    (tmp_path / "schema.json").write_bytes(raw)
    documents = goppi_baseline.capture_baseline_documents(tmp_path)
    assert documents.identities["schema.json"] == (
        "sha256:" + hashlib.sha256(raw).hexdigest(),
        len(raw),
    )


def test_directory_member_rejected(tmp_path: Path) -> None:
    _write(tmp_path)
    (tmp_path / "rows.jsonl").unlink()
    (tmp_path / "rows.jsonl").mkdir()
    with pytest.raises(CaptureError) as caught:
        goppi_baseline.capture_baseline_documents(tmp_path)
    assert caught.value.code == "SOURCE_READ_FAILED"

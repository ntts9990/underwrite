"""Bytes-backed records share the path-backed immutable publication pipeline."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import cast

import pytest
from underwrite_core.canonical import digest_bytes

from underwrite.service import _record_format, records
from underwrite.service.records import (
    CapturedRecordFile,
    CapturedRecordSpec,
    RecordError,
    save_captured_record,
)

PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def captured_spec(
    identifier: str = "captured.001",
    *,
    files: tuple[CapturedRecordFile, ...] | None = None,
    links: tuple[dict[str, str], ...] = (),
    rerun: dict[str, object] | None = None,
) -> CapturedRecordSpec:
    return CapturedRecordSpec(
        record_id=identifier,
        kind="handoff.capture",
        label="Captured bytes",
        metadata={"scope": "test"},
        links=links,
        selection_excluded=("private material",),
        rerun_verification=(
            {"status": "not_performed", "reference": None} if rerun is None else rerun
        ),
        provenance={"verified": False, "attestation_ref": None},
        files=(
            (CapturedRecordFile("result/data.json", "result", b'{"ok":true}\n'),)
            if files is None
            else files
        ),
    )


def assert_error(code: str, call: object) -> RecordError:
    assert callable(call)
    with pytest.raises(RecordError) as caught:
        call()
    assert caught.value.code == code
    return caught.value


def test_captured_bytes_are_the_only_source_and_preserve_exact_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = tmp_path / "external.json"
    external.write_bytes(b"original")
    captured = external.read_bytes()
    external.write_bytes(b"changed after capture")
    real_open = os.open

    def opening(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if path == external:
            pytest.fail("save_captured_record reopened an external source path")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(records.os, "open", opening)
    root = tmp_path / "store"
    snapshot = save_captured_record(
        root,
        captured_spec(files=(CapturedRecordFile("source/data.json", "source", captured),)),
    )
    stored = root / "records/captured.001/files/source/data.json"
    assert stored.read_bytes() == b"original"
    item = cast(list[dict[str, object]], snapshot.manifest["files"])[0]
    assert item["sha256"] == digest_bytes(b"original")
    assert item["bytes"] == len(b"original") and item["source_locator"] is None
    assert stat.S_IMODE(stored.stat().st_mode) == PRIVATE_FILE_MODE
    assert stat.S_IMODE(stored.parent.stat().st_mode) == PRIVATE_DIR_MODE
    assert records.verify_record(root, "captured.001")["valid"] is True


def test_path_and_bytes_apis_produce_the_same_manifest_facts(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    raw = b"same exact bytes"
    (source / "data").write_bytes(raw)
    path_spec = {
        "schema": "record_spec.v1",
        "record_id": "same.001",
        "kind": "handoff.capture",
        "label": "Captured bytes",
        "metadata": {"scope": "test"},
        "selection": {"scope": "explicit_files", "excluded": ["private material"]},
        "rerun_verification": {"status": "not_performed", "reference": None},
        "provenance": {"verified": False, "attestation_ref": None},
        "links": [],
        "files": [{"name": "result/data", "role": "result", "path": "data"}],
    }
    path_snapshot = records.save_record(tmp_path / "path-store", path_spec, source)
    byte_snapshot = save_captured_record(
        tmp_path / "byte-store",
        captured_spec("same.001", files=(CapturedRecordFile("result/data", "result", raw),)),
    )
    path_manifest = {k: v for k, v in path_snapshot.manifest.items() if k != "captured_at"}
    byte_manifest = {k: v for k, v in byte_snapshot.manifest.items() if k != "captured_at"}
    assert path_manifest == byte_manifest


def test_public_file_count_limit_has_one_format_owner() -> None:
    assert records.RECORD_MAX_FILES == _record_format.MAX_FILES


def test_captured_file_count_limit_rejects_before_store_creation(tmp_path: Path) -> None:
    files = tuple(
        CapturedRecordFile(f"result/{index}.json", "result", b"")
        for index in range(records.RECORD_MAX_FILES + 1)
    )
    root = tmp_path / "store"
    assert_error("INVALID_SPEC", lambda: save_captured_record(root, captured_spec(files=files)))
    assert not root.exists()


@pytest.mark.parametrize("limit", ["file", "total"])
def test_captured_byte_limits_reject_before_store_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    if limit == "file":
        monkeypatch.setattr(records, "MAX_FILE_BYTES", 3)
        files = (CapturedRecordFile("result/data", "result", b"1234"),)
    else:
        monkeypatch.setattr(records, "MAX_TOTAL_BYTES", 3)
        files = (
            CapturedRecordFile("result/one", "result", b"12"),
            CapturedRecordFile("result/two", "result", b"34"),
        )
    root = tmp_path / "store"
    assert_error(
        "INPUT_LIMIT_EXCEEDED", lambda: save_captured_record(root, captured_spec(files=files))
    )
    assert not root.exists()


@pytest.mark.parametrize(
    "files",
    [
        (
            CapturedRecordFile("result/data", "result", b"1"),
            CapturedRecordFile("RESULT/DATA", "result", b"2"),
        ),
        (
            CapturedRecordFile("result", "result", b"1"),
            CapturedRecordFile("result/data", "result", b"2"),
        ),
    ],
)
def test_duplicate_and_prefix_conflicts_use_existing_name_rules(
    tmp_path: Path, files: tuple[CapturedRecordFile, ...]
) -> None:
    root = tmp_path / "store"
    assert_error("INVALID_SPEC", lambda: save_captured_record(root, captured_spec(files=files)))
    assert not root.exists()


def test_captured_links_and_rerun_roles_use_existing_reference_rules(tmp_path: Path) -> None:
    root = tmp_path / "store"
    parent = save_captured_record(
        root,
        captured_spec(
            "parent.001",
            files=(CapturedRecordFile("verification/check.json", "verification", b"{}"),),
        ),
    )
    reference = {
        "scope": "linked_record",
        "record_id": "parent.001",
        "manifest_digest": parent.digest,
        "name": "verification/check.json",
    }
    child = captured_spec(
        "child.001",
        links=(
            {
                "relation": "input",
                "record_id": "parent.001",
                "manifest_digest": parent.digest,
            },
        ),
        rerun={"status": "referenced", "reference": reference},
    )
    snapshot = save_captured_record(root, child)
    assert snapshot.manifest["rerun_verification"] == child.rerun_verification
    assert records.verify_record(root, "child.001")["valid"] is True

    bad = captured_spec(
        "bad-child.001",
        links=(
            {
                "relation": "input",
                "record_id": "parent.001",
                "manifest_digest": "sha256:" + "0" * 64,
            },
        ),
    )
    assert_error("INVALID_SPEC", lambda: save_captured_record(root, bad))
    assert not (root / "records/bad-child.001").exists()


def test_captured_collision_and_unpublished_reservation_match_existing_contract(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    spec = captured_spec()
    save_captured_record(root, spec)
    assert_error("RECORD_EXISTS", lambda: save_captured_record(root, spec))

    pending = root / "records/pending.001"
    pending.mkdir(mode=PRIVATE_DIR_MODE)
    assert_error(
        "RECORD_CAPTURE_INCOMPLETE",
        lambda: save_captured_record(root, captured_spec("pending.001")),
    )


def test_captured_partial_write_failure_cleans_only_owned_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "store"
    original = records._write_bytes  # pyright: ignore[reportPrivateUsage]

    def fail_after_write(data: bytes, output: Path, allowance: int) -> tuple[int, str]:
        original(data, output, allowance)
        raise OSError("injected")

    monkeypatch.setattr(records, "_write_bytes", fail_after_write)
    assert_error("STORE_IO_ERROR", lambda: save_captured_record(root, captured_spec()))
    assert not (root / "records/captured.001").exists()


def test_captured_postpublication_fsync_failure_keeps_complete_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "store"
    original = records._fsync_dir  # pyright: ignore[reportPrivateUsage]

    def fail_record_dir(path: Path) -> None:
        if path == root / "records/captured.001" and (path / "manifest.json").exists():
            raise OSError("injected")
        original(path)

    monkeypatch.setattr(records, "_fsync_dir", fail_record_dir)
    assert_error("STORE_IO_ERROR", lambda: save_captured_record(root, captured_spec()))
    assert (root / "records/captured.001/manifest.json").exists()
    assert records.read_record(root, "captured.001").manifest["record_id"] == "captured.001"


def test_captured_spec_rejects_nonbytes_without_mutation(tmp_path: Path) -> None:
    invalid_file = CapturedRecordFile("result/data", "result", b"valid")
    object.__setattr__(invalid_file, "data", bytearray(b"invalid"))
    root = tmp_path / "store"
    assert_error(
        "INVALID_SPEC",
        lambda: save_captured_record(root, captured_spec(files=(invalid_file,))),
    )
    assert not root.exists()


@pytest.mark.parametrize("population", ["files", "links", "excluded"])
def test_oversized_tuple_is_rejected_before_inspecting_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, population: str
) -> None:
    item = CapturedRecordFile("result/data", "result", b"")
    spec = captured_spec(files=(item,))
    if population == "files":
        object.__setattr__(spec, "files", (item,) * (records.RECORD_MAX_FILES + 1))
    elif population == "links":
        object.__setattr__(spec, "links", ({},) * (_record_format.MAX_LINKS + 1))
    else:
        object.__setattr__(
            spec, "selection_excluded", ("x",) * (_record_format.MAX_SELECTION_EXCLUDED + 1)
        )
    real_getattribute = CapturedRecordFile.__getattribute__

    def access(file: CapturedRecordFile, name: str) -> object:
        if name == "data":
            pytest.fail("file data was inspected before rejecting oversized population")
        return real_getattribute(file, name)

    monkeypatch.setattr(CapturedRecordFile, "__getattribute__", access)
    root = tmp_path / "store"
    assert_error("INVALID_SPEC", lambda: save_captured_record(root, spec))
    assert not root.exists()

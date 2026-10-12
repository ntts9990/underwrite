from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self, cast

import pytest
from jsonschema import Draft202012Validator
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite._fileio import TransportError
from underwrite.service import _record_format, records
from underwrite.service.records import RecordError

PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def test_manifest_decoder_value_error_is_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> object:
        raise ValueError("decoder resource limit")

    monkeypatch.setattr(_record_format.json, "loads", refuse)
    with pytest.raises(_record_format.FormatError, match="^INVALID_MANIFEST_JSON$"):
        _record_format.strict_manifest(b"0")


@pytest.mark.parametrize(
    ("raw", "reason"),
    [(b'{"x":1,"x":2}', "DUPLICATE_KEY"), (b"NaN", "NONFINITE_NUMBER")],
)
def test_manifest_decoder_preserves_owned_errors(raw: bytes, reason: str) -> None:
    with pytest.raises(_record_format.FormatError, match=f"^{reason}$"):
        _record_format.strict_manifest(raw)


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _spec(identifier: str = "capture.001") -> dict[str, object]:
    return {
        "schema": "record_spec.v1",
        "record_id": identifier,
        "kind": "source.capture",
        "label": "Local capture",
        "metadata": {"scope": "test"},
        "selection": {"scope": "explicit_files", "excluded": None},
        "rerun_verification": {"status": "not_performed", "reference": None},
        "provenance": {"verified": False, "attestation_ref": None},
        "links": [],
        "files": [
            {
                "name": "source/result.json",
                "role": "result",
                "path": "result.json",
                "media_type": "application/json",
            }
        ],
    }


def _source(tmp_path: Path, data: bytes = b'{"ok":true}\n') -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "result.json").write_bytes(data)
    return source


def _save(tmp_path: Path, data: bytes = b'{"ok":true}\n') -> tuple[Path, dict[str, object]]:
    source = _source(tmp_path, data)
    root = tmp_path / "store"
    snapshot = records.save_record(root, _spec(), source)
    return root, snapshot.manifest


def _assert_error(code: str, call: object) -> RecordError:
    assert callable(call)
    with pytest.raises(RecordError) as caught:
        call()
    assert caught.value.code == code
    return caught.value


def test_save_read_list_and_verify_are_consistent_and_private(tmp_path: Path) -> None:
    raw = b"exact raw bytes\x00\xff"
    root, manifest = _save(tmp_path, raw)
    snapshot = records.read_record(root, "capture.001")

    assert snapshot.manifest == manifest
    assert snapshot.digest == digest_bytes(canonical_bytes(manifest))
    assert snapshot.manifest["files"] == [
        {
            "name": "source/result.json",
            "role": "result",
            "sha256": digest_bytes(raw),
            "bytes": len(raw),
            "media_type": "application/json",
            "source_locator": None,
        }
    ]
    assert "path" not in json.dumps(snapshot.manifest)
    assert (root / "records/capture.001/files/source/result.json").read_bytes() == raw
    for directory in [
        root,
        root / "records",
        root / ".staging",
        root / "records/capture.001",
        root / "records/capture.001/files",
        root / "records/capture.001/files/source",
    ]:
        assert stat.S_IMODE(directory.stat().st_mode) == PRIVATE_DIR_MODE
    for path in [
        root / "records/capture.001/files/source/result.json",
        root / "records/capture.001/manifest.json",
    ]:
        assert stat.S_IMODE(path.stat().st_mode) == PRIVATE_FILE_MODE

    catalog = records.list_records(root)
    entry = cast(list[dict[str, object]], catalog["records"])[0]
    assert catalog["record_count"] == 1
    assert entry["state"] == "complete"
    assert entry["manifest_digest"] == snapshot.digest
    assert records.verify_record(root, "capture.001") == {
        "schema": "record_verification.v1",
        "record_id": "capture.001",
        "manifest_digest": snapshot.digest,
        "valid": True,
        "files_checked": 1,
        "links_checked": 0,
        "findings": [],
        "verification_scope": "stored_bytes_and_direct_links",
    }


@pytest.mark.parametrize(
    ("name", "other"),
    [
        ("../escape", "safe"),
        ("/absolute", "safe"),
        ("same", "SAME"),
        ("nested", "nested/file"),
        ("bad\\name", "safe"),
    ],
)
def test_invalid_or_conflicting_logical_names_are_rejected_before_store_creation(
    tmp_path: Path, name: str, other: str
) -> None:
    source = _source(tmp_path)
    spec = _spec()
    files = cast(list[dict[str, object]], spec["files"])
    files[0]["name"] = name
    files.append({**files[0], "name": other})
    _assert_error("INVALID_SPEC", lambda: records.save_record(tmp_path / "store", spec, source))
    assert not (tmp_path / "store").exists()


def test_source_is_opened_once_and_later_path_replacement_does_not_change_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path, b"first")
    original = records._store_for_save  # pyright: ignore[reportPrivateUsage]

    def replace_after_open(root: Path) -> tuple[Path, Path]:
        replacement = source / "replacement"
        replacement.write_bytes(b"second")
        replacement.replace(source / "result.json")
        return original(root)

    monkeypatch.setattr(records, "_store_for_save", replace_after_open)
    snapshot = records.save_record(tmp_path / "store", _spec(), source)
    stored = cast(list[dict[str, object]], snapshot.manifest["files"])[0]
    assert stored["sha256"] == digest_bytes(b"first")
    stored_path = tmp_path / "store/records/capture.001/files/source/result.json"
    assert stored_path.read_bytes() == b"first"


@pytest.mark.parametrize("logical_name", ["manifest.json", "manifest.json/child.json"])
def test_logical_manifest_names_do_not_collide_with_completion_marker(
    tmp_path: Path, logical_name: str
) -> None:
    source = _source(tmp_path, b"selected bytes")
    spec = _spec()
    cast(list[dict[str, object]], spec["files"])[0]["name"] = logical_name
    root = tmp_path / "store"
    records.save_record(root, spec, source)
    assert (root / "records/capture.001/manifest.json").is_file()
    selected = root / "records/capture.001/files" / logical_name
    assert selected.read_bytes() == b"selected bytes"
    assert records.verify_record(root, "capture.001")["valid"] is True


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_nonregular_source_is_rejected_without_creating_store(tmp_path: Path, kind: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = source / "result.json"
    if kind == "symlink":
        (source / "real").write_text("x")
        target.symlink_to("real")
    else:
        os.mkfifo(target)
    _assert_error(
        "SOURCE_READ_FAILED", lambda: records.save_record(tmp_path / "store", _spec(), source)
    )
    assert not (tmp_path / "store").exists()


def test_nfd_source_path_is_captured_without_normalizing_filesystem_identity(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    nfd_name = "e\u0301.json"
    (source / nfd_name).write_bytes(b"nfd bytes")
    spec = _spec()
    cast(list[dict[str, object]], spec["files"])[0]["path"] = nfd_name
    contract = json.loads(
        (Path(__file__).resolve().parents[3] / "contracts/record_spec.v1.schema.json").read_bytes()
    )
    cast(Validator, Draft202012Validator(contract)).validate(spec)
    root = tmp_path / "store"
    snapshot = records.save_record(root, spec, source)
    stored_path = root / "records/capture.001/files/source/result.json"
    assert stored_path.read_bytes() == b"nfd bytes"
    stored = cast(list[dict[str, object]], snapshot.manifest["files"])
    assert "path" not in stored[0]


def test_two_writers_for_one_id_have_one_winner_and_do_not_mix_bytes(tmp_path: Path) -> None:
    source = _source(tmp_path, b"winner bytes")
    root = tmp_path / "store"

    def capture() -> str:
        try:
            records.save_record(root, _spec(), source)
        except RecordError as exc:
            return exc.code
        return "OK"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(capture) for _ in range(2)]
        outcomes = sorted(future.result() for future in futures)
    assert outcomes in (["OK", "RECORD_EXISTS"], ["OK", "RECORD_CAPTURE_INCOMPLETE"])
    assert records.verify_record(root, "capture.001")["valid"] is True
    stored_path = root / "records/capture.001/files/source/result.json"
    assert stored_path.read_bytes() == b"winner bytes"


def test_unpublished_capture_is_listed_but_cannot_be_read_or_reused(tmp_path: Path) -> None:
    root = tmp_path / "store"
    (root / "records/capture.001").mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
    os.chmod(root / "records", 0o700)
    catalog = records.list_records(root)
    assert catalog["records"] == [{"record_id": "capture.001", "state": "unpublished"}]
    _assert_error("RECORD_NOT_PUBLISHED", lambda: records.read_record(root, "capture.001"))
    source = _source(tmp_path)
    _assert_error("RECORD_CAPTURE_INCOMPLETE", lambda: records.save_record(root, _spec(), source))


def test_corrupt_manifest_is_invocation_error_but_corrupt_data_is_finding(tmp_path: Path) -> None:
    root, _ = _save(tmp_path)
    data = root / "records/capture.001/files/source/result.json"
    data.write_bytes(b"same length??")
    result = records.verify_record(root, "capture.001")
    assert result["valid"] is False
    assert cast(list[dict[str, object]], result["findings"])[0]["code"] in {
        "FILE_SIZE_MISMATCH",
        "FILE_DIGEST_MISMATCH",
    }
    manifest = root / "records/capture.001/manifest.json"
    manifest.write_text('{"schema":"result_record.v1","schema":"result_record.v1"}')
    _assert_error("INVALID_RECORD", lambda: records.read_record(root, "capture.001"))


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("missing", "FILE_MISSING"),
        ("public", "FILE_NOT_PRIVATE"),
        ("symlink", "FILE_NOT_REGULAR"),
    ],
)
def test_verify_emits_one_precedence_finding_per_file(
    tmp_path: Path, mutation: str, code: str
) -> None:
    root, _ = _save(tmp_path)
    data = root / "records/capture.001/files/source/result.json"
    if mutation == "missing":
        data.unlink()
    elif mutation == "public":
        data.chmod(0o644)
    else:
        data.unlink()
        data.symlink_to("missing")
    findings = cast(list[dict[str, object]], records.verify_record(root, "capture.001")["findings"])
    assert findings == [{"code": code, "name": "source/result.json", "record_id": None}]


@pytest.mark.parametrize(
    ("mutation", "code"),
    [("symlink", "FILE_NOT_REGULAR"), ("public", "FILE_NOT_PRIVATE")],
)
def test_verify_checks_files_root_before_selected_leaf(
    tmp_path: Path, mutation: str, code: str
) -> None:
    root, _ = _save(tmp_path)
    files_root = root / "records/capture.001/files"
    if mutation == "symlink":
        moved = root / "records/capture.001/moved-files"
        files_root.rename(moved)
        files_root.symlink_to("moved-files", target_is_directory=True)
    else:
        files_root.chmod(0o755)
    findings = cast(list[dict[str, object]], records.verify_record(root, "capture.001")["findings"])
    assert findings == [{"code": code, "name": "source/result.json", "record_id": None}]


def test_links_are_bound_before_mutation_and_checked_directly(tmp_path: Path) -> None:
    source = _source(tmp_path)
    root = tmp_path / "store"
    first = records.save_record(root, _spec("input.001"), source)
    linked_spec = _spec("analysis.001")
    linked_spec["links"] = [
        {"relation": "input", "record_id": "input.001", "manifest_digest": first.digest}
    ]
    second = records.save_record(root, linked_spec, source)
    assert second.manifest["links"] == linked_spec["links"]
    assert records.verify_record(root, "analysis.001")["valid"] is True

    (root / "records/input.001/manifest.json").unlink()
    verification = records.verify_record(root, "analysis.001")
    assert verification["valid"] is False
    assert verification["findings"] == [
        {"code": "LINK_RECORD_NOT_PUBLISHED", "name": None, "record_id": "input.001"}
    ]

    bad = _spec("analysis.002")
    bad["links"] = [
        {
            "relation": "input",
            "record_id": "input.001",
            "manifest_digest": "sha256:" + "0" * 64,
        }
    ]
    _assert_error("INVALID_SPEC", lambda: records.save_record(root, bad, source))
    assert not (root / "records/analysis.002").exists()


def test_truth_axes_and_artifact_roles_are_validated_independently(tmp_path: Path) -> None:
    source = _source(tmp_path)
    spec = _spec()
    spec["provenance"] = {"verified": True, "attestation_ref": None}
    _assert_error("INVALID_SPEC", lambda: records.save_record(tmp_path / "store", spec, source))

    rerun = _spec()
    rerun["rerun_verification"] = {
        "status": "referenced",
        "reference": {"scope": "this_record", "name": "source/result.json"},
    }
    _assert_error("INVALID_SPEC", lambda: records.save_record(tmp_path / "other", rerun, source))


def test_linked_rerun_reference_requires_a_verification_artifact(tmp_path: Path) -> None:
    source = _source(tmp_path)
    root = tmp_path / "store"
    target = records.save_record(root, _spec("input.001"), source)
    spec = _spec("analysis.001")
    spec["links"] = [
        {"relation": "input", "record_id": "input.001", "manifest_digest": target.digest}
    ]
    spec["rerun_verification"] = {
        "status": "referenced",
        "reference": {
            "scope": "linked_record",
            "record_id": "input.001",
            "manifest_digest": target.digest,
            "name": "source/result.json",
        },
    }
    _assert_error("INVALID_SPEC", lambda: records.save_record(root, spec, source))
    assert not (root / "records/analysis.001").exists()


@pytest.mark.parametrize("fault", ["missing", "wrong_role"])
def test_verify_rechecks_linked_artifact_name_and_rerun_role(tmp_path: Path, fault: str) -> None:
    source = _source(tmp_path)
    root = tmp_path / "store"
    parent_spec = _spec("input.001")
    cast(list[dict[str, object]], parent_spec["files"])[0]["role"] = "verification"
    parent = records.save_record(root, parent_spec, source)
    child_spec = _spec("analysis.001")
    child_spec["links"] = [
        {"relation": "input", "record_id": "input.001", "manifest_digest": parent.digest}
    ]
    child_spec["rerun_verification"] = {
        "status": "referenced",
        "reference": {
            "scope": "linked_record",
            "record_id": "input.001",
            "manifest_digest": parent.digest,
            "name": "source/result.json",
        },
    }
    child = records.save_record(root, child_spec, source)
    child_manifest = deepcopy(child.manifest)
    reference = cast(
        dict[str, object],
        cast(dict[str, object], child_manifest["rerun_verification"])["reference"],
    )
    if fault == "missing":
        reference["name"] = "missing.json"
    else:
        parent_manifest = deepcopy(parent.manifest)
        cast(list[dict[str, object]], parent_manifest["files"])[0]["role"] = "result"
        parent_raw = canonical_bytes(parent_manifest)
        parent_path = root / "records/input.001/manifest.json"
        parent_path.write_bytes(parent_raw)
        new_digest = digest_bytes(parent_raw)
        cast(list[dict[str, object]], child_manifest["links"])[0]["manifest_digest"] = new_digest
        reference["manifest_digest"] = new_digest
    child_path = root / "records/analysis.001/manifest.json"
    child_path.write_bytes(canonical_bytes(child_manifest))
    result = records.verify_record(root, "analysis.001")
    assert result["valid"] is False
    assert result["findings"] == [
        {"code": "LINK_MANIFEST_INVALID", "name": None, "record_id": "input.001"}
    ]


def test_prepublication_failure_removes_only_owned_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    root = tmp_path / "store"
    original = records._copy_one  # pyright: ignore[reportPrivateUsage]

    def fail_after_copy(
        descriptor: int, expected: int, output: Path, allowance: int
    ) -> tuple[int, str]:
        original(descriptor, expected, output, allowance)
        raise OSError("injected")

    monkeypatch.setattr(records, "_copy_one", fail_after_copy)
    _assert_error("STORE_IO_ERROR", lambda: records.save_record(root, _spec(), source))
    assert not (root / "records/capture.001").exists()
    assert (source / "result.json").exists()


def test_postpublication_fsync_failure_preserves_complete_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    root = tmp_path / "store"
    original = records._fsync_dir  # pyright: ignore[reportPrivateUsage]

    def fail_record_dir(path: Path) -> None:
        if path == root / "records/capture.001" and (path / "manifest.json").exists():
            raise OSError("injected")
        original(path)

    monkeypatch.setattr(records, "_fsync_dir", fail_record_dir)
    _assert_error("STORE_IO_ERROR", lambda: records.save_record(root, _spec(), source))
    assert (root / "records/capture.001/manifest.json").exists()
    assert records.read_record(root, "capture.001").manifest["record_id"] == "capture.001"


def test_new_control_directories_are_fsynced_before_their_parent_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    root = tmp_path / "store"
    calls: list[Path] = []
    original = records._fsync_dir  # pyright: ignore[reportPrivateUsage]

    def observe(path: Path) -> None:
        calls.append(path)
        original(path)

    monkeypatch.setattr(records, "_fsync_dir", observe)
    records.save_record(root, _spec(), source)
    assert calls.index(root) < calls.index(tmp_path)
    first_root_parent_sync = calls.index(root, calls.index(tmp_path) + 1)
    assert calls.index(root / "records") < first_root_parent_sync
    assert calls.index(root / ".staging") < calls.index(root, first_root_parent_sync + 1)


def test_cleanup_fsync_failure_is_not_suppressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    root = tmp_path / "store"
    original_copy = records._copy_one  # pyright: ignore[reportPrivateUsage]
    original_fsync = records._fsync_dir  # pyright: ignore[reportPrivateUsage]
    capture_failed = False

    def fail_after_copy(
        descriptor: int, expected: int, output: Path, allowance: int
    ) -> tuple[int, str]:
        nonlocal capture_failed
        original_copy(descriptor, expected, output, allowance)
        capture_failed = True
        raise OSError("capture failure")

    def fail_cleanup(path: Path) -> None:
        if capture_failed and path == root / "records" and not (path / "capture.001").exists():
            raise OSError("cleanup durability failure")
        original_fsync(path)

    monkeypatch.setattr(records, "_copy_one", fail_after_copy)
    monkeypatch.setattr(records, "_fsync_dir", fail_cleanup)
    _assert_error("STORE_IO_ERROR", lambda: records.save_record(root, _spec(), source))
    assert (root / "records/capture.001").is_dir()
    _assert_error("RECORD_CAPTURE_INCOMPLETE", lambda: records.save_record(root, _spec(), source))


def test_old_source_bytes_are_unchanged(tmp_path: Path) -> None:
    source = _source(tmp_path, b"do not mutate")
    before = (source / "result.json").read_bytes()
    records.save_record(tmp_path / "store", deepcopy(_spec()), source)
    assert (source / "result.json").read_bytes() == before


def test_size_limits_are_enforced_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path, b"too large")
    monkeypatch.setattr(records, "MAX_FILE_BYTES", 4)
    _assert_error(
        "INPUT_LIMIT_EXCEEDED",
        lambda: records.save_record(tmp_path / "store", _spec(), source),
    )
    assert not (tmp_path / "store").exists()


def test_record_error_never_retains_an_unowned_code_or_location() -> None:
    error = RecordError("received secret text", "/received/secret")
    assert error.code == "INTERNAL_ERROR"
    assert error.location == ""
    assert str(error) == "INTERNAL_ERROR"


def test_noncanonical_manifest_and_record_id_mismatch_are_rejected(tmp_path: Path) -> None:
    root, manifest = _save(tmp_path)
    path = root / "records/capture.001/manifest.json"
    path.write_bytes(json.dumps(manifest, indent=2).encode())
    _assert_error("INVALID_RECORD", lambda: records.read_record(root, "capture.001"))

    path.write_bytes(canonical_bytes({**manifest, "record_id": "different.001"}))
    _assert_error("INVALID_RECORD", lambda: records.read_record(root, "capture.001"))


def test_read_only_operations_do_not_create_staging(tmp_path: Path) -> None:
    root, _ = _save(tmp_path)
    (root / ".staging").rmdir()
    records.read_record(root, "capture.001")
    records.list_records(root)
    records.verify_record(root, "capture.001")
    assert not (root / ".staging").exists()


@pytest.mark.parametrize("operation", ["read", "list", "verify"])
def test_manifest_read_accepts_short_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    root, _ = _save(tmp_path)
    operations: dict[str, Callable[[], object]] = {
        "read": lambda: records.read_record(root, "capture.001"),
        "list": lambda: records.list_records(root),
        "verify": lambda: records.verify_record(root, "capture.001"),
    }
    invoke = operations[operation]
    expected = invoke()
    manifest_path = root / "records/capture.001/manifest.json"
    before = manifest_path.read_bytes()
    monkeypatch.setattr(records, "MAX_MANIFEST_BYTES", len(before))
    original_read = os.read

    def short_read(descriptor: int, size: int) -> bytes:
        return original_read(descriptor, min(size, 7))

    monkeypatch.setattr(os, "read", short_read)
    assert invoke() == expected
    assert manifest_path.read_bytes() == before


@pytest.mark.parametrize("failure", ["truncated", "overflow", "io"])
def test_manifest_read_refuses_incomplete_or_over_budget_bytes_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    root, _ = _save(tmp_path)
    manifest_path = root / "records/capture.001/manifest.json"
    before = manifest_path.read_bytes()
    monkeypatch.setattr(records, "MAX_MANIFEST_BYTES", len(before))
    original_read = os.read
    descriptors: list[int] = []
    consumed = 0

    def interrupted_read(descriptor: int, size: int) -> bytes:
        nonlocal consumed
        if not descriptors:
            descriptors.append(descriptor)
            if failure == "overflow":
                with manifest_path.open("ab") as stream:
                    stream.write(b"xx")
        elif failure == "truncated":
            return b""
        elif failure == "io":
            raise OSError("read failure")
        chunk = original_read(descriptor, min(size, 7))
        consumed += len(chunk)
        return chunk

    monkeypatch.setattr(os, "read", interrupted_read)
    error = _assert_error("INVALID_RECORD", lambda: records.read_record(root, "capture.001"))
    assert error.location == "/record"
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    if failure == "overflow":
        assert consumed == len(before) + 1
    else:
        assert manifest_path.read_bytes() == before


@pytest.mark.parametrize("error_type", [TransportError, ValueError, TypeError])
def test_manifest_read_closes_on_reader_refusal_without_masking_unexpected_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    root, _ = _save(tmp_path)
    failure = error_type("reader failure")
    descriptors: list[int] = []

    def refuse(descriptor: int, limit: int) -> bytes:
        descriptors.append(descriptor)
        raise failure

    monkeypatch.setattr(records, "read_descriptor", refuse)
    if isinstance(failure, TransportError):
        error = _assert_error("INVALID_RECORD", lambda: records.read_record(root, "capture.001"))
        assert error.location == "/record"
    else:
        with pytest.raises(error_type) as caught:
            records.read_record(root, "capture.001")
        assert caught.value is failure
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


# Card: record-reuse | Paper: record_document.v1 | Stage: storage-read


def test_selected_read_returns_captured_bytes_without_mutation_or_source(
    tmp_path: Path,
) -> None:
    raw = b"binary\x00\xff"
    root, _ = _save(tmp_path, raw)
    (tmp_path / "source/result.json").unlink()
    before = {
        path: (path.read_bytes(), path.stat().st_mode) for path in root.rglob("*") if path.is_file()
    }
    result = records.read_record_file(root, "capture.001", "source/result.json")
    assert result.data == raw
    assert result.sha256 == digest_bytes(raw)
    assert result.manifest_digest == records.read_record(root, "capture.001").digest
    assert result.record_id == "capture.001" and result.name == "source/result.json"
    assert result.media_type == "application/json"
    assert {path: (path.read_bytes(), path.stat().st_mode) for path in before} == before


@pytest.mark.parametrize("name", ["../escape", "/absolute", "source/../result.json", "bad\\name"])
def test_selected_read_refuses_invalid_names(tmp_path: Path, name: str) -> None:
    root, _ = _save(tmp_path)
    _assert_error("INVALID_SPEC", lambda: records.read_record_file(root, "capture.001", name))


@pytest.mark.parametrize("limit", [True, 0, -1, records.RECORD_READ_MAX_BYTES + 1])
def test_selected_read_refuses_invalid_budget(tmp_path: Path, limit: int) -> None:
    root, _ = _save(tmp_path)
    _assert_error(
        "INVALID_SPEC",
        lambda: records.read_record_file(
            root, "capture.001", "source/result.json", max_bytes=limit
        ),
    )


def test_selected_read_unlisted_name_and_declared_limit_never_open_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _save(tmp_path)
    original = os.open

    def guarded(
        path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        assert path != "files", "Refused selection must not open selected-file subtree"
        return original(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", guarded)
    _assert_error(
        "RECORD_FILE_NOT_FOUND",
        lambda: records.read_record_file(root, "capture.001", "missing.json"),
    )
    _assert_error(
        "INPUT_LIMIT_EXCEEDED",
        lambda: records.read_record_file(root, "capture.001", "source/result.json", max_bytes=1),
    )


@pytest.mark.parametrize("replacement", [b"x", b'{"no":true}\n'])
def test_selected_read_checks_captured_length_and_hash(tmp_path: Path, replacement: bytes) -> None:
    root, _ = _save(tmp_path)
    (root / "records/capture.001/files/source/result.json").write_bytes(replacement)
    _assert_error(
        "RECORD_FILE_INTEGRITY_MISMATCH",
        lambda: records.read_record_file(root, "capture.001", "source/result.json"),
    )


def test_selected_read_limit_exact_and_file_growth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = b"1234"
    root, _ = _save(tmp_path, raw)
    assert (
        records.read_record_file(root, "capture.001", "source/result.json", max_bytes=4).data == raw
    )
    selected = root / "records/capture.001/files/source/result.json"
    original = records.read_descriptor

    def grow(descriptor: int, max_bytes: int) -> bytes:
        if os.fstat(descriptor).st_ino == selected.stat().st_ino:
            selected.write_bytes(b"12345")
        return original(descriptor, max_bytes)

    monkeypatch.setattr(records, "read_descriptor", grow)
    _assert_error(
        "INPUT_LIMIT_EXCEEDED",
        lambda: records.read_record_file(root, "capture.001", "source/result.json", max_bytes=4),
    )


@pytest.mark.parametrize(
    "relative",
    [
        ".",
        "records",
        ".staging",
        "records/capture.001",
        "records/capture.001/files",
        "records/capture.001/files/source",
        "records/capture.001/files/source/result.json",
        "records/capture.001/manifest.json",
    ],
)
def test_selected_read_checks_private_modes_on_every_opened_node(
    tmp_path: Path, relative: str
) -> None:
    root, _ = _save(tmp_path)
    path = root / relative
    path.chmod(0o755 if path.is_dir() else 0o644)
    _assert_error(
        "INSECURE_STORE",
        lambda: records.read_record_file(root, "capture.001", "source/result.json"),
    )


@pytest.mark.parametrize(
    "relative",
    [
        ".",
        "records",
        ".staging",
        "records/capture.001",
        "records/capture.001/files",
        "records/capture.001/files/source",
        "records/capture.001/files/source/result.json",
        "records/capture.001/manifest.json",
    ],
)
def test_selected_read_refuses_symlinks_at_every_control_node(
    tmp_path: Path, relative: str
) -> None:
    root, _ = _save(tmp_path)
    path = root if relative == "." else root / relative
    target = tmp_path / "moved"
    path.rename(target)
    path.symlink_to(target, target_is_directory=target.is_dir())
    _assert_error(
        "INVALID_STORE_LAYOUT",
        lambda: records.read_record_file(root, "capture.001", "source/result.json"),
    )


@pytest.mark.parametrize("kind", ["missing", "fifo", "directory"])
def test_selected_read_missing_and_wrong_type(tmp_path: Path, kind: str) -> None:
    root, _ = _save(tmp_path)
    path = root / "records/capture.001/files/source/result.json"
    path.unlink()
    if kind == "fifo":
        os.mkfifo(path, 0o600)
    elif kind == "directory":
        path.mkdir(mode=0o700)
    code = "SOURCE_READ_FAILED" if kind == "missing" else "INVALID_STORE_LAYOUT"
    _assert_error(code, lambda: records.read_record_file(root, "capture.001", "source/result.json"))


def test_selected_read_anchors_manifest_and_file_to_same_record_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _save(tmp_path, b"original")
    directory = root / "records/capture.001"
    original = records.read_descriptor
    captured = False

    def replace_record(descriptor: int, max_bytes: int) -> bytes:
        nonlocal captured
        raw = original(descriptor, max_bytes)
        if not captured:
            captured = True
            directory.rename(root / "records/moved")
            directory.mkdir(mode=0o700)
            (directory / "files").symlink_to(root / "records/moved/files", target_is_directory=True)
        return raw

    monkeypatch.setattr(records, "read_descriptor", replace_record)
    result = records.read_record_file(root, "capture.001", "source/result.json")
    assert result.data == b"original"


def test_selected_read_refuses_replaced_nested_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _save(tmp_path)
    nested = root / "records/capture.001/files/source"
    original = os.open

    def replace_nested(
        path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if path == "source":
            nested.rename(tmp_path / "moved")
            nested.symlink_to(tmp_path / "moved", target_is_directory=True)
        return original(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", replace_nested)
    _assert_error(
        "INVALID_STORE_LAYOUT",
        lambda: records.read_record_file(root, "capture.001", "source/result.json"),
    )


def test_selected_read_checks_opened_permissions_and_releases_all_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _save(tmp_path)
    original = os.open
    opened: list[int] = []

    def change_mode(
        path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        descriptor = original(path, flags, mode, dir_fd=dir_fd)
        opened.append(descriptor)
        if path == "result.json":
            os.fchmod(descriptor, 0o644)
        return descriptor

    monkeypatch.setattr(os, "open", change_mode)
    _assert_error(
        "INSECURE_STORE",
        lambda: records.read_record_file(root, "capture.001", "source/result.json"),
    )
    assert opened
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_selected_read_returns_verified_buffer_after_leaf_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _save(tmp_path, b"original")
    path = root / "records/capture.001/files/source/result.json"
    inode = path.stat().st_ino
    original = records.read_descriptor

    def replace_leaf(descriptor: int, max_bytes: int) -> bytes:
        raw = original(descriptor, max_bytes)
        if os.fstat(descriptor).st_ino == inode:
            path.unlink()
            path.write_bytes(b"different")
        return raw

    monkeypatch.setattr(records, "read_descriptor", replace_leaf)
    assert records.read_record_file(root, "capture.001", "source/result.json").data == b"original"


@pytest.mark.parametrize("flag", ["O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK", "O_NOCTTY"])
def test_selected_read_refuses_missing_platform_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    root, _ = _save(tmp_path)
    monkeypatch.delattr(os, flag)
    _assert_error(
        "UNSUPPORTED_PLATFORM",
        lambda: records.read_record_file(root, "capture.001", "source/result.json"),
    )


def test_selected_read_refuses_missing_dir_fd_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _save(tmp_path)
    original = os.open

    def unsupported(
        path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if dir_fd is not None:
            raise NotImplementedError
        return original(path, flags, mode)

    monkeypatch.setattr(os, "open", unsupported)
    _assert_error(
        "UNSUPPORTED_PLATFORM",
        lambda: records.read_record_file(root, "capture.001", "source/result.json"),
    )


def test_selected_read_does_not_verify_other_files_or_links(tmp_path: Path) -> None:
    source = _source(tmp_path)
    root = tmp_path / "store"
    linked = records.save_record(root, _spec("linked.001"), source)
    spec = _spec()
    spec["links"] = [
        {"relation": "input", "record_id": "linked.001", "manifest_digest": linked.digest}
    ]
    files = cast(list[dict[str, object]], spec["files"])
    files.append({**files[0], "name": "other.json"})
    records.save_record(root, spec, source)
    (root / "records/capture.001/files/other.json").write_bytes(b"corrupted")
    (root / "records/linked.001/manifest.json").write_bytes(b"corrupted")
    assert (
        records.read_record_file(root, "capture.001", "source/result.json").data == b'{"ok":true}\n'
    )
    assert records.verify_record(root, "capture.001")["valid"] is False


@pytest.mark.parametrize(
    ("relative", "code"),
    [
        ("records/capture.001/manifest.json", "RECORD_NOT_PUBLISHED"),
        ("records/capture.001", "RECORD_NOT_FOUND"),
        ("records", "STORE_NOT_FOUND"),
        (".", "STORE_NOT_FOUND"),
    ],
)
def test_selected_read_distinguishes_missing_control_nodes(
    tmp_path: Path, relative: str, code: str
) -> None:
    root, _ = _save(tmp_path)
    path = root if relative == "." else root / relative
    path.rename(tmp_path / "moved")
    _assert_error(code, lambda: records.read_record_file(root, "capture.001", "source/result.json"))


@pytest.mark.parametrize("mutation", ["syntax", "id", "noncanonical", "oversize", "huge_integer"])
def test_selected_read_refuses_invalid_manifest(tmp_path: Path, mutation: str) -> None:
    root, manifest = _save(tmp_path)
    if mutation == "syntax":
        raw = b"invalid"
    elif mutation == "huge_integer":
        raw = b"9" * 5000
    elif mutation == "id":
        raw = canonical_bytes({**manifest, "record_id": "different"})
    elif mutation == "noncanonical":
        raw = canonical_bytes(manifest) + b"\n"
    else:
        raw = b" " * (records.MAX_MANIFEST_BYTES + 1)
    (root / "records/capture.001/manifest.json").write_bytes(raw)
    _assert_error(
        "INVALID_RECORD",
        lambda: records.read_record_file(root, "capture.001", "source/result.json"),
    )


def test_selected_read_allows_ancestor_symlink_but_anchors_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _save(tmp_path)
    ancestor = tmp_path / "ancestor"
    ancestor.symlink_to(tmp_path, target_is_directory=True)
    assert (
        records.read_record_file(ancestor / root.name, "capture.001", "source/result.json").data
        == b'{"ok":true}\n'
    )
    monkeypatch.chdir(tmp_path)
    assert (
        records.read_record_file(Path(root.name), "capture.001", "source/result.json").data
        == b'{"ok":true}\n'
    )


def test_selected_read_checks_opened_size_and_source_io_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _save(tmp_path, b"1234")
    selected = root / "records/capture.001/files/source/result.json"
    selected.write_bytes(b"12345")
    _assert_error(
        "INPUT_LIMIT_EXCEEDED",
        lambda: records.read_record_file(root, "capture.001", "source/result.json", max_bytes=4),
    )
    selected.write_bytes(b"1234")
    original = records.read_descriptor

    def unreadable(descriptor: int, max_bytes: int) -> bytes:
        if os.fstat(descriptor).st_ino == selected.stat().st_ino:
            raise PermissionError("private-path")
        return original(descriptor, max_bytes)

    monkeypatch.setattr(records, "read_descriptor", unreadable)
    error = _assert_error(
        "SOURCE_READ_FAILED",
        lambda: records.read_record_file(root, "capture.001", "source/result.json"),
    )
    assert "private-path" not in str(error)


class _CatalogScan:
    """An observable scandir iterator with normal, early-stop and I/O paths."""

    def __init__(self, entries: list[os.DirEntry[str]], *, fail: bool = False) -> None:
        self.entries = iter(entries)
        self.fail = fail
        self.consumed = 0
        self.closed = False

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> os.DirEntry[str]:
        if self.fail and self.consumed:
            raise OSError("controlled iteration failure")
        try:
            entry = next(self.entries)
        except StopIteration:
            self.closed = True
            raise
        self.consumed += 1
        return entry

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.closed = True


@pytest.mark.parametrize("io_error", [False, True])
def test_catalog_bounds_entry_consumption_and_closes_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    io_error: bool,
) -> None:
    root, _ = _save(tmp_path)
    limit = 2
    monkeypatch.setattr(records, "MAX_RECORDS", limit)
    # Uninspectable sentinels ensure no metadata is accessed on either refusal.
    entry = cast(os.DirEntry[str], object())
    scan = _CatalogScan([entry] * (limit + 5), fail=io_error)

    def scandir(path: Path) -> _CatalogScan:
        return scan

    monkeypatch.setattr(records.os, "scandir", scandir)
    expected = "STORE_IO_ERROR" if io_error else "RECORD_LIMIT_EXCEEDED"
    error = _assert_error(expected, lambda: records.list_records(root))
    assert error.location == "/catalog"
    assert scan.consumed == (1 if io_error else limit + 1)
    assert scan.closed


def test_catalog_at_entry_limit_stays_sorted_and_closes_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _save(tmp_path)
    records.save_record(root, _spec("a.earlier"), tmp_path / "source")
    with os.scandir(root / "records") as iterator:
        entries = sorted(iterator, key=lambda entry: entry.name, reverse=True)
    monkeypatch.setattr(records, "MAX_RECORDS", len(entries))
    scan = _CatalogScan(entries)

    def scandir(path: Path) -> _CatalogScan:
        return scan

    monkeypatch.setattr(records.os, "scandir", scandir)
    result = records.list_records(root)
    rows = cast(list[dict[str, object]], result["records"])
    assert [row["record_id"] for row in rows] == sorted(entry.name for entry in entries)
    assert result["record_count"] == len(entries)
    assert scan.consumed == len(entries)
    assert scan.closed

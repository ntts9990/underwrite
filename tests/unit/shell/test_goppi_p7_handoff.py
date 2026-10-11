"""Exact-profile acquisition boundaries, independent of the pure mapping checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from underwrite.instrument.ingest.goppi_p7_handoff import (
    HandoffInputError,
    capture_goppi_p7_handoff,
)
from underwrite.instrument.ingest.local_capture import CapturePolicy

POLICY = CapturePolicy(58, 8 * 1024 * 1024, 128 * 1024 * 1024)


def _write_mapping(root: Path, *, completion: str = "regrade/completion-manifest.json") -> Path:
    handoff = root / "study" / "paired-analysis-input"
    handoff.mkdir(parents=True)
    entries: dict[str, object] = {}
    files: list[dict[str, object]] = []
    for key, name in (
        ("paired_binary_input", "paired-input.json"),
        ("analysis_policy", "analysis-policy.json"),
        ("derivation_provenance", "derivation-provenance.json"),
    ):
        raw = b"{}"
        sha = "sha256:" + hashlib.sha256(raw).hexdigest()
        (handoff / name).write_bytes(raw)
        entries[key] = {"path": name, "sha256": sha}
        files.append({"path": name, "sha256": sha, "bytes": len(raw)})
    entries["source_regrade_completion"] = {"path": completion, "sha256": "sha256:" + "0" * 64}
    entries["source_original_run_result"] = {
        "path": "original/result.json",
        "sha256": "sha256:" + "0" * 64,
    }
    raw = json.dumps(
        {
            "schema": "goppi.p7_regrade_consumer_handoff.v1",
            "status": "ready_for_descriptive_analysis",
            "entry_files": entries,
        }
    ).encode()
    (handoff / "consumer-handoff.json").write_bytes(raw)
    files.append(
        {
            "path": "consumer-handoff.json",
            "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }
    )
    (handoff / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "goppi.p7_regrade_mapping_bundle_manifest.v1",
                "status": "complete",
                "files": files,
            }
        )
    )
    return handoff


def _capture(handoff: Path, root: Path | None, policy: CapturePolicy = POLICY) -> None:
    capture_goppi_p7_handoff(handoff, source_root=root, policy=policy)


@pytest.mark.parametrize("pointer", ["../secret", "foo//bar", "./secret", "foo\\bar", "foo\x00bar"])
def test_rejects_lexically_invalid_external_pointer(tmp_path: Path, pointer: str) -> None:
    handoff = _write_mapping(tmp_path, completion=pointer)
    with pytest.raises(HandoffInputError, match="HANDOFF_PATH_INVALID"):
        _capture(handoff, tmp_path)


def test_rejects_absolute_external_pointer_without_leaking_path(tmp_path: Path) -> None:
    secret = tmp_path.parent / "sensitive-private-file"
    handoff = _write_mapping(tmp_path, completion=str(secret))
    with pytest.raises(HandoffInputError) as caught:
        _capture(handoff, tmp_path)
    assert caught.value.reason == "HANDOFF_PATH_OUTSIDE_ROOT"
    assert str(secret) not in str(caught.value)
    assert caught.value.location == "/regrade/completion-manifest.json"


def test_handoff_outside_root_fails_before_read(tmp_path: Path) -> None:
    with pytest.raises(HandoffInputError, match="HANDOFF_PATH_OUTSIDE_ROOT"):
        _capture(tmp_path.parent / "outside", tmp_path)


@pytest.mark.parametrize("target", ["consumer-handoff.json", "paired-input.json"])
def test_rejects_symlinked_mapping_file(tmp_path: Path, target: str) -> None:
    handoff = _write_mapping(tmp_path)
    original = handoff / target
    moved = tmp_path / "moved"
    original.rename(moved)
    original.symlink_to(moved)
    with pytest.raises(HandoffInputError) as caught:
        _capture(handoff, tmp_path)
    assert caught.value.code == "SOURCE_READ_FAILED"


@pytest.mark.parametrize("raw", [b'{"schema":1,"schema":2}', b"\xff", b'{"a":NaN}'])
def test_rejects_non_strict_handoff_json(tmp_path: Path, raw: bytes) -> None:
    handoff = _write_mapping(tmp_path)
    (handoff / "consumer-handoff.json").write_bytes(raw)
    with pytest.raises(HandoffInputError) as caught:
        _capture(handoff, tmp_path)
    assert caught.value.reason == "CAPTURE_INVALID_JSON"


@pytest.mark.parametrize("mutation", ["extra", "missing", "duplicate", "hash", "bytes", "limit"])
def test_mapping_manifest_claims_are_enforced(tmp_path: Path, mutation: str) -> None:
    handoff = _write_mapping(tmp_path)
    path = handoff / "manifest.json"
    manifest = json.loads(path.read_bytes())
    files = manifest["files"]
    if mutation == "extra":
        files.append({"path": "auth-key.json", "sha256": "sha256:" + "0" * 64, "bytes": 0})
    elif mutation == "missing":
        files.pop()
    elif mutation == "duplicate":
        files.append(files[0])
    elif mutation == "hash":
        files[0]["sha256"] = "sha256:" + "0" * 64
    elif mutation == "bytes":
        files[0]["bytes"] += 1
    else:
        files[0]["bytes"] = POLICY.max_file_bytes + 1
    path.write_text(json.dumps(manifest))
    with pytest.raises(HandoffInputError) as caught:
        _capture(handoff, tmp_path)
    assert caught.value.reason in {
        "HANDOFF_MANIFEST_MEMBERSHIP_INVALID",
        "HANDOFF_MANIFEST_INVALID",
        "HANDOFF_DIGEST_MISMATCH",
        "HANDOFF_SIZE_MISMATCH",
        "HANDOFF_CLAIM_LIMIT_EXCEEDED",
    }


def test_no_same_name_fallback(tmp_path: Path) -> None:
    handoff = _write_mapping(tmp_path)
    (handoff / "paired-input.json").unlink()
    (tmp_path / "paired-input.json").write_bytes(b"{}")
    with pytest.raises(HandoffInputError) as caught:
        _capture(handoff, tmp_path)
    assert caught.value.code == "SOURCE_READ_FAILED"


def test_source_root_is_not_implicitly_expanded(tmp_path: Path) -> None:
    handoff = _write_mapping(
        tmp_path, completion=str(tmp_path / "regrade/completion-manifest.json")
    )
    with pytest.raises(HandoffInputError, match="HANDOFF_PATH_OUTSIDE_ROOT"):
        _capture(handoff, None)


def _repair_capture_claims(root: Path, handoff: Path) -> None:
    def identity(path: Path) -> dict[str, object]:
        raw = path.read_bytes()
        return {"sha256": "sha256:" + hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}

    completion_path = root / "study/offline-verifier-regrade/completion-manifest.json"
    completion = json.loads(completion_path.read_bytes())
    for entry in completion["files"]:
        entry.update(identity(completion_path.parent / entry["path"]))
    completion_path.write_text(json.dumps(completion))
    handoff_path = handoff / "consumer-handoff.json"
    doc = json.loads(handoff_path.read_bytes())
    doc["entry_files"]["source_regrade_completion"]["sha256"] = identity(completion_path)["sha256"]
    handoff_path.write_text(json.dumps(doc))
    manifest_path = handoff / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    for entry in manifest["files"]:
        entry.update(identity(handoff / entry["path"]))
    manifest_path.write_text(json.dumps(manifest))


def test_full_synthetic_capture_is_read_once_and_detached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from drivers.goppi_p7_fixture import build_goppi_p7_fixture

    from underwrite.instrument.ingest import local_capture

    fixture = build_goppi_p7_fixture()
    handoff = fixture.write(tmp_path)
    reads: list[tuple[str, ...]] = []
    original = local_capture.read_at

    def read_once(root: int, parts: list[str], maximum: int, flags: tuple[int, int]) -> bytes:
        reads.append(tuple(parts))
        return original(root, parts, maximum, flags)

    monkeypatch.setattr(local_capture, "read_at", read_once)
    captured = capture_goppi_p7_handoff(handoff, source_root=tmp_path, policy=POLICY)
    assert set(captured.artifact_by_name) == set(fixture.files)
    assert len(reads) == len(set(reads)) == len(fixture.files)
    raw = captured.artifact_by_name["mapping/paired-input.json"].data
    (handoff / "paired-input.json").write_bytes(b"changed after capture")
    assert captured.artifact_by_name["mapping/paired-input.json"].data == raw
    assert captured.documents.paired_input == fixture.documents.paired_input


@pytest.mark.parametrize("key", ["result", "plan", "execution_scope"])
@pytest.mark.parametrize("forbidden", [".env", "auth-key.json", "raw-answer.json"])
def test_original_sensitive_pointer_rejected_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    forbidden: str,
) -> None:
    from drivers.goppi_p7_fixture import build_goppi_p7_fixture

    from underwrite.instrument.ingest import local_capture

    handoff = build_goppi_p7_fixture().write(tmp_path)
    path = tmp_path / "study/offline-verifier-regrade/private/source.json"
    source = json.loads(path.read_bytes())
    source["original"][key + "_path"] = forbidden
    path.write_text(json.dumps(source))
    _repair_capture_claims(tmp_path, handoff)
    reads: list[str] = []
    original = local_capture.read_at

    def guarded_read(root: int, parts: list[str], maximum: int, flags: tuple[int, int]) -> bytes:
        reads.append("/".join(parts))
        return original(root, parts, maximum, flags)

    monkeypatch.setattr(local_capture, "read_at", guarded_read)
    with pytest.raises(HandoffInputError, match="HANDOFF_ORIGINAL_PATH_INVALID"):
        _capture(handoff, tmp_path)
    assert forbidden not in reads


def test_manifest_receipt_paths_use_completion_root(tmp_path: Path) -> None:
    from drivers.goppi_p7_fixture import build_goppi_p7_fixture

    fixture = build_goppi_p7_fixture()
    handoff = fixture.write(tmp_path)
    captured = capture_goppi_p7_handoff(handoff, source_root=tmp_path, policy=POLICY)
    assert captured.documents.protocol_receipts == fixture.documents.protocol_receipts


def test_source_count_limit_is_explicit_and_exact(tmp_path: Path) -> None:
    from drivers.goppi_p7_fixture import build_goppi_p7_fixture

    fixture = build_goppi_p7_fixture()
    handoff = fixture.write(tmp_path)
    bound = len(fixture.files)
    exact = CapturePolicy(bound, POLICY.max_file_bytes, POLICY.max_total_bytes)
    capture_goppi_p7_handoff(handoff, source_root=tmp_path, policy=exact)
    with pytest.raises(HandoffInputError) as caught:
        _capture(
            handoff,
            tmp_path,
            CapturePolicy(bound - 1, POLICY.max_file_bytes, POLICY.max_total_bytes),
        )
    assert caught.value.code == "INPUT_LIMIT_EXCEEDED"


def test_absolute_completion_inside_root_is_supported(tmp_path: Path) -> None:
    from drivers.goppi_p7_fixture import build_goppi_p7_fixture

    handoff = build_goppi_p7_fixture().write(tmp_path)
    path = handoff / "consumer-handoff.json"
    doc = json.loads(path.read_bytes())
    doc["entry_files"]["source_regrade_completion"]["path"] = str(
        tmp_path / "study/offline-verifier-regrade/completion-manifest.json"
    )
    path.write_text(json.dumps(doc))
    _repair_capture_claims(tmp_path, handoff)
    capture_goppi_p7_handoff(handoff, source_root=tmp_path, policy=POLICY)


def test_intermediate_symlink_does_not_expand_trust_root(tmp_path: Path) -> None:
    from drivers.goppi_p7_fixture import build_goppi_p7_fixture

    handoff = build_goppi_p7_fixture().write(tmp_path)
    private = tmp_path / "study/offline-verifier-regrade/private"
    target = tmp_path / "moved-private"
    private.rename(target)
    private.symlink_to(target, target_is_directory=True)
    with pytest.raises(HandoffInputError) as caught:
        _capture(handoff, tmp_path)
    assert caught.value.code == "SOURCE_READ_FAILED"


def test_receipt_manifest_cannot_select_arbitrary_file(tmp_path: Path) -> None:
    from drivers.goppi_p7_fixture import build_goppi_p7_fixture

    handoff = build_goppi_p7_fixture().write(tmp_path)
    path = tmp_path / "study/offline-verifier-regrade/private/protocol-receipts-manifest.json"
    doc = json.loads(path.read_bytes())
    doc["files"][0]["path"] = "private/authentication-key.json"
    path.write_text(json.dumps(doc))
    _repair_capture_claims(tmp_path, handoff)
    with pytest.raises(HandoffInputError, match="HANDOFF_MANIFEST_MEMBERSHIP_INVALID"):
        _capture(handoff, tmp_path)


def test_total_actual_bytes_bound_is_exact(tmp_path: Path) -> None:
    from drivers.goppi_p7_fixture import build_goppi_p7_fixture

    fixture = build_goppi_p7_fixture()
    handoff = fixture.write(tmp_path)
    total = sum(len(raw) for raw in fixture.files.values())
    capture_goppi_p7_handoff(
        handoff,
        source_root=tmp_path,
        policy=CapturePolicy(POLICY.max_files, POLICY.max_file_bytes, total),
    )
    with pytest.raises(HandoffInputError) as caught:
        _capture(
            handoff, tmp_path, CapturePolicy(POLICY.max_files, POLICY.max_file_bytes, total - 1)
        )
    assert caught.value.code == "INPUT_LIMIT_EXCEEDED"


@pytest.mark.parametrize("pointer", [".env", "private/auth-key.json", "private/raw-answer.json"])
def test_completion_sensitive_pointer_rejected_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pointer: str,
) -> None:
    from underwrite.instrument.ingest import local_capture

    handoff = _write_mapping(tmp_path, completion=pointer)
    reads: list[str] = []
    original = local_capture.read_at

    def guarded_read(root: int, parts: list[str], maximum: int, flags: tuple[int, int]) -> bytes:
        reads.append("/".join(parts))
        return original(root, parts, maximum, flags)

    monkeypatch.setattr(local_capture, "read_at", guarded_read)
    with pytest.raises(HandoffInputError, match="HANDOFF_COMPLETION_PATH_INVALID"):
        _capture(handoff, tmp_path)
    assert pointer not in reads


def test_caller_relative_parent_paths_are_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from drivers.goppi_p7_fixture import build_goppi_p7_fixture

    source = tmp_path / "source"
    fixture = build_goppi_p7_fixture()
    fixture.write(source)
    sibling = tmp_path / "consumer"
    sibling.mkdir()
    monkeypatch.chdir(sibling)
    captured = capture_goppi_p7_handoff(
        Path("../source/study/paired-analysis-input"),
        source_root=Path("../source"),
        policy=POLICY,
    )
    assert captured.documents.paired_input == fixture.documents.paired_input


@pytest.mark.parametrize("caller_path", ["root", "handoff"])
def test_caller_canceled_symlink_component_is_not_hidden_by_normalization(
    tmp_path: Path,
    caller_path: str,
) -> None:
    from drivers.goppi_p7_fixture import build_goppi_p7_fixture

    source = tmp_path / "source"
    handoff = build_goppi_p7_fixture().write(source)
    target = tmp_path / "target"
    target.mkdir()
    link = source / "link"
    link.symlink_to(target, target_is_directory=True)
    root = source
    if caller_path == "root":
        root = source / "link/.."
    else:
        handoff = source / "link/../study/paired-analysis-input"
    with pytest.raises(HandoffInputError) as caught:
        _capture(handoff, root)
    assert caught.value.reason == "CAPTURE_ROOT_SYMLINK_REJECTED"

"""Private immutable filesystem records for selected local evidence files."""

from __future__ import annotations

import errno
import hashlib
import os
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, NamedTuple, cast

from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.service._record_format import (
    MAX_FILE_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_RECORDS,
    MAX_TOTAL_BYTES,
    FormatError,
    record_id,
    strict_manifest,
    validate_manifest,
    validate_spec,
)

_DIR_MODE: Final = 0o700
_FILE_MODE: Final = 0o600
_READ_SIZE: Final = 1024 * 1024
ERROR_CODES: Final = frozenset(
    {
        "INVALID_SPEC",
        "INVALID_RECORD",
        "INVALID_STORE_LAYOUT",
        "INSECURE_STORE",
        "STORE_NOT_FOUND",
        "RECORD_NOT_FOUND",
        "RECORD_EXISTS",
        "RECORD_CAPTURE_INCOMPLETE",
        "RECORD_NOT_PUBLISHED",
        "SOURCE_READ_FAILED",
        "INPUT_LIMIT_EXCEEDED",
        "RECORD_LIMIT_EXCEEDED",
        "STORE_IO_ERROR",
        "UNSUPPORTED_PLATFORM",
        "USAGE_ERROR",
        "SCHEMA_CONFIGURATION_ERROR",
        "OUTPUT_LIMIT_EXCEEDED",
        "INTERNAL_ERROR",
    }
)


class RecordSnapshot(NamedTuple):
    """One validated published manifest and its canonical byte digest."""

    manifest: dict[str, object]
    digest: str


class RecordError(ValueError):
    """Finite service failure; the message never contains received source text."""

    def __init__(self, code: str, location: str = "") -> None:
        if code not in ERROR_CODES:
            code = "INTERNAL_ERROR"
            location = ""
        super().__init__(code)
        self.code = code
        self.location = location


class _ManifestPublished(OSError):
    """A post-link durability step failed; committed record contents must remain."""

    def __init__(self, staging_file: Path) -> None:
        super().__init__(errno.EIO, "post-publication failure")
        self.staging_file = staging_file


def _platform_flags() -> tuple[int, int]:
    names = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK", "O_NOCTTY")
    values = [getattr(os, name, None) for name in names]
    if any(value is None for value in values):
        raise RecordError("UNSUPPORTED_PLATFORM")
    directory, nofollow, nonblock, noctty = cast(list[int], values)
    return os.O_RDONLY | directory | nofollow, os.O_RDONLY | nofollow | nonblock | noctty


def _private(mode: int) -> bool:
    return mode & 0o077 == 0


def _validate_control(path: Path, *, directory: bool, missing: str) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise RecordError(missing) from None
    except OSError:
        raise RecordError("STORE_IO_ERROR") from None
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not expected or stat.S_ISLNK(info.st_mode):
        raise RecordError("INVALID_STORE_LAYOUT")
    if not _private(info.st_mode):
        raise RecordError("INSECURE_STORE")
    return info


def _make_private(path: Path) -> None:
    try:
        path.mkdir(mode=_DIR_MODE)
        path.chmod(_DIR_MODE)
        _fsync_dir(path)
        _fsync_dir(path.parent)
    except FileExistsError:
        _validate_control(path, directory=True, missing="STORE_NOT_FOUND")
    except OSError:
        raise RecordError("STORE_IO_ERROR") from None


def _store_for_save(root: Path) -> tuple[Path, Path]:
    _make_private(root)
    records = root / "records"
    staging = root / ".staging"
    _make_private(records)
    _make_private(staging)
    return records, staging


def _store_for_read(root: Path) -> Path:
    _validate_control(root, directory=True, missing="STORE_NOT_FOUND")
    records = root / "records"
    _validate_control(records, directory=True, missing="STORE_NOT_FOUND")
    staging = root / ".staging"
    if staging.exists() or staging.is_symlink():
        _validate_control(staging, directory=True, missing="STORE_NOT_FOUND")
    return records


def _record_dir(records: Path, identifier: str, *, missing: str = "RECORD_NOT_FOUND") -> Path:
    try:
        checked = record_id(identifier)
    except FormatError:
        raise RecordError("INVALID_SPEC", "/record_id") from None
    path = records / checked
    _validate_control(path, directory=True, missing=missing)
    return path


def _read_manifest(record_dir: Path, expected_id: str) -> RecordSnapshot:
    manifest_path = record_dir / "manifest.json"
    try:
        info = _validate_control(manifest_path, directory=False, missing="RECORD_NOT_PUBLISHED")
        if info.st_size > MAX_MANIFEST_BYTES:
            raise RecordError("INVALID_RECORD", "/record")
        _, file_flags = _platform_flags()
        descriptor = os.open(manifest_path, file_flags)
        try:
            raw = os.read(descriptor, MAX_MANIFEST_BYTES + 1)
            if os.read(descriptor, 1):
                raise RecordError("INVALID_RECORD", "/record")
        finally:
            os.close(descriptor)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise RecordError("INVALID_RECORD", "/record")
        if len(raw) != info.st_size:
            raise RecordError("INVALID_RECORD", "/record")
        manifest = strict_manifest(raw)
        if manifest["record_id"] != expected_id:
            raise RecordError("INVALID_RECORD", "/record")
        return RecordSnapshot(manifest, digest_bytes(raw))
    except RecordError:
        raise
    except (OSError, FormatError):
        raise RecordError("INVALID_RECORD", "/record") from None


def read_record(root: Path, identifier: str) -> RecordSnapshot:
    """Read one strictly canonical, published record manifest."""
    records = _store_for_read(root)
    directory = _record_dir(records, identifier)
    return _read_manifest(directory, identifier)


def _validate_references(root: Path, spec: dict[str, object]) -> None:
    linked: dict[str, RecordSnapshot] = {}
    for item in cast(list[dict[str, object]], spec["links"]):
        identifier = cast(str, item["record_id"])
        try:
            snapshot = read_record(root, identifier)
        except RecordError as exc:
            if exc.code == "RECORD_NOT_FOUND":
                raise RecordError("INVALID_SPEC", "/spec/links") from None
            raise RecordError("INVALID_SPEC", "/spec/links") from None
        if snapshot.digest != item["manifest_digest"]:
            raise RecordError("INVALID_SPEC", "/spec/links")
        linked[identifier] = snapshot
    references = [
        cast(dict[str, object], spec["rerun_verification"])["reference"],
        cast(dict[str, object], spec["provenance"])["attestation_ref"],
    ]
    own = {cast(str, item["name"]): item for item in cast(list[dict[str, object]], spec["files"])}
    for index, value in enumerate(references):
        if value is None:
            continue
        reference = cast(dict[str, object], value)
        if reference["scope"] == "this_record":
            target = own.get(cast(str, reference["name"]))
        else:
            target_record = linked.get(cast(str, reference["record_id"]))
            if target_record is None or target_record.digest != reference["manifest_digest"]:
                raise RecordError("INVALID_SPEC", "/spec")
            target = next(
                (
                    item
                    for item in cast(list[dict[str, object]], target_record.manifest["files"])
                    if item["name"] == reference["name"]
                ),
                None,
            )
        if target is None or (index == 0 and target["role"] != "verification"):
            raise RecordError("INVALID_SPEC", "/spec")


def _open_sources(
    spec: dict[str, object], source_base: Path
) -> list[tuple[dict[str, object], int, int]]:
    _, file_flags = _platform_flags()
    opened: list[tuple[dict[str, object], int, int]] = []
    total = 0
    try:
        for item in cast(list[dict[str, object]], spec["files"]):
            source = source_base / cast(str, item["path"])
            descriptor: int | None = None
            try:
                descriptor = os.open(source, file_flags)
                info = os.fstat(descriptor)
            except OSError:
                if descriptor is not None:
                    os.close(descriptor)
                raise RecordError("SOURCE_READ_FAILED", "/spec/files") from None
            if not stat.S_ISREG(info.st_mode):
                os.close(descriptor)
                raise RecordError("SOURCE_READ_FAILED", "/spec/files")
            if info.st_size > MAX_FILE_BYTES:
                os.close(descriptor)
                raise RecordError("INPUT_LIMIT_EXCEEDED", "/spec/files")
            total += info.st_size
            if total > MAX_TOTAL_BYTES:
                os.close(descriptor)
                raise RecordError("INPUT_LIMIT_EXCEEDED", "/spec/files")
            opened.append((item, descriptor, info.st_size))
        return opened
    except Exception:
        for _, descriptor, _ in opened:
            os.close(descriptor)
        raise


def _reserve(records: Path, identifier: str) -> Path:
    target = records / identifier
    try:
        target.mkdir(mode=_DIR_MODE)
        target.chmod(_DIR_MODE)
        _fsync_dir(target)
        _fsync_dir(records)
        return target
    except FileExistsError:
        try:
            _validate_control(target, directory=True, missing="RECORD_CAPTURE_INCOMPLETE")
        except RecordError as exc:
            if exc.code in {"INVALID_STORE_LAYOUT", "INSECURE_STORE"}:
                raise
            raise RecordError("RECORD_CAPTURE_INCOMPLETE", "/record_id") from None
        if (target / "manifest.json").exists() or (target / "manifest.json").is_symlink():
            raise RecordError("RECORD_EXISTS", "/record_id") from None
        raise RecordError("RECORD_CAPTURE_INCOMPLETE", "/record_id") from None
    except OSError:
        raise RecordError("STORE_IO_ERROR") from None


def _fsync_dir(path: Path) -> None:
    directory_flags, _ = _platform_flags()
    descriptor = os.open(path, directory_flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_output_parent(record_dir: Path, name: str, owned_dirs: list[Path]) -> Path:
    current = record_dir
    for component in name.split("/")[:-1]:
        current /= component
        if not current.exists():
            current.mkdir(mode=_DIR_MODE)
            current.chmod(_DIR_MODE)
            owned_dirs.append(current)
    return record_dir / name


def _files_root(record_dir: Path, owned_dirs: list[Path]) -> Path:
    root = record_dir / "files"
    root.mkdir(mode=_DIR_MODE)
    root.chmod(_DIR_MODE)
    owned_dirs.append(root)
    return root


def _copy_one(
    descriptor: int, expected: int, output: Path, total_allowance: int
) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    target = os.open(output, flags, _FILE_MODE)
    size = 0
    hasher = hashlib.sha256()
    try:
        os.fchmod(target, _FILE_MODE)
        while True:
            chunk = os.read(descriptor, _READ_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FILE_BYTES or size > total_allowance:
                raise RecordError("INPUT_LIMIT_EXCEEDED", "/spec/files")
            hasher.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(target, view)
                if written <= 0:
                    raise OSError(errno.EIO, "write failed")
                view = view[written:]
        if size != expected:
            raise RecordError("SOURCE_READ_FAILED", "/spec/files")
        os.fsync(target)
    finally:
        os.close(target)
    return size, "sha256:" + hasher.hexdigest()


def _cleanup(paths: list[Path], directories: list[Path], records: Path) -> None:
    for path in reversed(paths):
        try:
            path.unlink()
        except OSError:
            pass
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass
    _fsync_dir(records)


def _cleanup_before_publication(paths: list[Path], directories: list[Path], records: Path) -> None:
    try:
        _cleanup(paths, directories, records)
    except OSError:
        reservation = directories[0]
        try:
            reservation.mkdir(mode=_DIR_MODE)
            reservation.chmod(_DIR_MODE)
        except FileExistsError:
            pass
        except OSError:
            pass
        try:
            _fsync_dir(reservation)
            _fsync_dir(records)
        except (OSError, RecordError):
            pass
        raise RecordError("STORE_IO_ERROR") from None


def _manifest(spec: dict[str, object], stored: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": "result_record.v1",
        "record_id": spec["record_id"],
        "kind": spec["kind"],
        "label": spec["label"],
        "metadata": spec["metadata"],
        "selection": spec["selection"],
        "rerun_verification": spec["rerun_verification"],
        "provenance": spec["provenance"],
        "links": spec["links"],
        "captured_at": datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "files": stored,
    }


def _checked_spec(spec: object, root: Path) -> dict[str, object]:
    try:
        checked = validate_spec(spec)
    except FormatError:
        raise RecordError("INVALID_SPEC", "/spec") from None
    _validate_references(root, checked) if cast(list[object], checked["links"]) else None
    try:
        preview_files = [
            {
                "name": item["name"],
                "role": item["role"],
                "sha256": "sha256:" + "0" * 64,
                "bytes": 0,
                "media_type": item["media_type"],
                "source_locator": item["source_locator"],
            }
            for item in cast(list[dict[str, object]], checked["files"])
        ]
        preview = _manifest(checked, preview_files)
        validate_manifest(preview)
    except FormatError:
        raise RecordError("INVALID_SPEC", "/spec") from None
    return checked


def _capture_files(
    record_dir: Path,
    sources: list[tuple[dict[str, object], int, int]],
    owned_files: list[Path],
    owned_dirs: list[Path],
) -> list[dict[str, object]]:
    stored: list[dict[str, object]] = []
    total = 0
    files_root = _files_root(record_dir, owned_dirs)
    for item, descriptor, expected in sources:
        output = _ensure_output_parent(files_root, cast(str, item["name"]), owned_dirs)
        owned_files.append(output)
        size, checksum = _copy_one(descriptor, expected, output, MAX_TOTAL_BYTES - total)
        total += size
        if total > MAX_TOTAL_BYTES:
            raise RecordError("INPUT_LIMIT_EXCEEDED", "/spec/files")
        stored.append(
            {
                "name": item["name"],
                "role": item["role"],
                "sha256": checksum,
                "bytes": size,
                "media_type": item["media_type"],
                "source_locator": item["source_locator"],
            }
        )
    for directory in sorted(owned_dirs, key=lambda item: len(item.parts), reverse=True):
        _fsync_dir(directory)
    return stored


def _write_staging(staging: Path, raw: bytes) -> Path:
    path = staging / f"manifest-{secrets.token_hex(16)}.tmp"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
    try:
        os.fchmod(descriptor, _FILE_MODE)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(errno.EIO, "write failed")
            view = view[written:]
        os.fsync(descriptor)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)
    return path


def _publish(record_dir: Path, staging: Path, raw: bytes) -> Path:
    staging_file = _write_staging(staging, raw)
    try:
        os.link(staging_file, record_dir / "manifest.json", follow_symlinks=False)
    except OSError:
        try:
            staging_file.unlink()
        except OSError:
            pass
        raise
    try:
        _fsync_dir(record_dir)
        try:
            staging_file.unlink()
            _fsync_dir(staging)
        except OSError:
            pass
    except OSError as exc:
        raise _ManifestPublished(staging_file) from exc
    return staging_file


def save_record(root: Path, spec: object, source_base: Path) -> RecordSnapshot:
    """Capture selected files once and publish an immutable manifest create-only."""
    checked = _checked_spec(spec, root)
    sources = _open_sources(checked, source_base)
    records: Path | None = None
    record_dir: Path | None = None
    owned_files: list[Path] = []
    owned_dirs: list[Path] = []
    manifest_published = False
    staging_file: Path | None = None
    try:
        records, staging = _store_for_save(root)
        record_dir = _reserve(records, cast(str, checked["record_id"]))
        owned_dirs.append(record_dir)
        stored = _capture_files(record_dir, sources, owned_files, owned_dirs)
        manifest = _manifest(checked, stored)
        validate_manifest(manifest)
        raw = canonical_bytes(manifest)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise RecordError("INPUT_LIMIT_EXCEEDED", "/spec")
        staging_file = _publish(record_dir, staging, raw)
        manifest_published = True
        return RecordSnapshot(manifest, digest_bytes(raw))
    except _ManifestPublished as exc:
        staging_file = exc.staging_file
        manifest_published = True
        raise RecordError("STORE_IO_ERROR") from None
    except FormatError:
        raise RecordError("INTERNAL_ERROR") from None
    except RecordError:
        raise
    except OSError:
        raise RecordError("STORE_IO_ERROR") from None
    finally:
        for _, descriptor, _ in sources:
            os.close(descriptor)
        if staging_file is not None and not manifest_published:
            try:
                staging_file.unlink()
            except OSError:
                pass
        if not manifest_published and records is not None and record_dir is not None:
            _cleanup_before_publication(owned_files, owned_dirs, records)


def list_records(root: Path) -> dict[str, object]:
    """Derive a bounded, sorted catalog from complete and unpublished directories."""
    records = _store_for_read(root)
    try:
        entries = list(os.scandir(records))
    except OSError:
        raise RecordError("STORE_IO_ERROR", "/catalog") from None
    if len(entries) > MAX_RECORDS:
        raise RecordError("RECORD_LIMIT_EXCEEDED", "/catalog")
    result: list[dict[str, object]] = []
    for entry in sorted(entries, key=lambda item: item.name):
        try:
            identifier = record_id(entry.name)
            if not entry.is_dir(follow_symlinks=False):
                raise FormatError("INVALID_ENTRY")
            directory = _record_dir(records, identifier)
            manifest_path = directory / "manifest.json"
            if not manifest_path.exists() and not manifest_path.is_symlink():
                result.append({"record_id": identifier, "state": "unpublished"})
                continue
            snapshot = _read_manifest(directory, identifier)
            result.append(
                {
                    "record_id": identifier,
                    "state": "complete",
                    "manifest_digest": snapshot.digest,
                    "kind": snapshot.manifest["kind"],
                    "label": snapshot.manifest["label"],
                    "captured_at": snapshot.manifest["captured_at"],
                }
            )
        except FormatError:
            raise RecordError("INVALID_STORE_LAYOUT", "/catalog") from None
    return {"schema": "record_catalog.v1", "record_count": len(result), "records": result}


def _stored_files_root(record_dir: Path) -> tuple[str | None, Path | None]:
    files_root = record_dir / "files"
    try:
        root_info = files_root.lstat()
    except FileNotFoundError:
        return "FILE_MISSING", None
    except OSError:
        return "FILE_READ_FAILED", None
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode):
        return "FILE_NOT_REGULAR", None
    if not _private(root_info.st_mode):
        return "FILE_NOT_PRIVATE", None
    return None, files_root


def _file_finding(record_dir: Path, item: dict[str, object]) -> str | None:
    root_code, files_root = _stored_files_root(record_dir)
    if root_code is not None or files_root is None:
        return root_code
    path = files_root.joinpath(*cast(str, item["name"]).split("/"))
    code, info = _file_metadata_finding(path, cast(str, item["name"]), files_root)
    if code is not None or info is None:
        return code
    return _file_content_finding(path, info, item)


def _file_content_finding(
    path: Path, info: os.stat_result, item: dict[str, object]
) -> str | None:
    if info.st_size != item["bytes"]:
        return "FILE_SIZE_MISMATCH"
    try:
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            remaining = cast(int, item["bytes"])
            while remaining:
                chunk = stream.read(min(_READ_SIZE, remaining + 1))
                if not chunk or len(chunk) > remaining:
                    return "FILE_SIZE_MISMATCH"
                hasher.update(chunk)
                remaining -= len(chunk)
            if stream.read(1):
                return "FILE_SIZE_MISMATCH"
    except OSError:
        return "FILE_READ_FAILED"
    return None if "sha256:" + hasher.hexdigest() == item["sha256"] else "FILE_DIGEST_MISMATCH"


def _file_metadata_finding(
    path: Path, name: str, record_dir: Path
) -> tuple[str | None, os.stat_result | None]:
    current = record_dir
    code: str | None = None
    info: os.stat_result | None = None
    try:
        for component in name.split("/")[:-1]:
            current /= component
            parent_info = current.lstat()
            if not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode):
                code = "FILE_NOT_REGULAR"
                break
            if not _private(parent_info.st_mode):
                code = "FILE_NOT_PRIVATE"
                break
        if code is None:
            info = path.lstat()
    except FileNotFoundError:
        code = "FILE_MISSING"
    except OSError:
        code = "FILE_READ_FAILED"
    if code is None and info is not None:
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            code = "FILE_NOT_REGULAR"
        elif not _private(info.st_mode):
            code = "FILE_NOT_PRIVATE"
    return code, info


def _linked_artifact_refs(
    manifest: dict[str, object], identifier: str
) -> list[tuple[dict[str, object], str | None]]:
    rerun = cast(dict[str, object], manifest["rerun_verification"])["reference"]
    provenance = cast(dict[str, object], manifest["provenance"])["attestation_ref"]
    result: list[tuple[dict[str, object], str | None]] = []
    for reference, required_role in ((rerun, "verification"), (provenance, None)):
        if reference is None:
            continue
        ref = cast(dict[str, object], reference)
        if ref["scope"] == "linked_record" and ref["record_id"] == identifier:
            result.append((ref, required_role))
    return result


def _link_finding(root: Path, item: dict[str, object], manifest: dict[str, object]) -> str | None:
    identifier = cast(str, item["record_id"])
    try:
        target = read_record(root, identifier)
    except RecordError as exc:
        return {
            "RECORD_NOT_FOUND": "LINK_RECORD_NOT_FOUND",
            "RECORD_NOT_PUBLISHED": "LINK_RECORD_NOT_PUBLISHED",
        }.get(exc.code, "LINK_MANIFEST_INVALID")
    if target.digest != item["manifest_digest"]:
        return "LINK_DIGEST_MISMATCH"
    target_files = {
        cast(str, file["name"]): file
        for file in cast(list[dict[str, object]], target.manifest["files"])
    }
    for reference, required_role in _linked_artifact_refs(manifest, identifier):
        target_file = target_files.get(cast(str, reference["name"]))
        if target_file is None or (
            required_role is not None and target_file["role"] != required_role
        ):
            return "LINK_MANIFEST_INVALID"
    return None


def verify_record(root: Path, identifier: str) -> dict[str, object]:
    """Check stored bytes and direct links without granting provenance or authority."""
    snapshot = read_record(root, identifier)
    records = _store_for_read(root)
    directory = _record_dir(records, identifier)
    files = cast(list[dict[str, object]], snapshot.manifest["files"])
    links = cast(list[dict[str, object]], snapshot.manifest["links"])
    findings: list[dict[str, object]] = []
    for item in files:
        code = _file_finding(directory, item)
        if code is not None:
            findings.append({"code": code, "name": item["name"], "record_id": None})
    for item in links:
        code = _link_finding(root, item, snapshot.manifest)
        if code is not None:
            findings.append({"code": code, "name": None, "record_id": item["record_id"]})
    return {
        "schema": "record_verification.v1",
        "record_id": identifier,
        "manifest_digest": snapshot.digest,
        "valid": not findings,
        "files_checked": len(files),
        "links_checked": len(links),
        "findings": findings,
        "verification_scope": "stored_bytes_and_direct_links",
    }

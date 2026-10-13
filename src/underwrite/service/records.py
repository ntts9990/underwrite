"""Private immutable filesystem records for selected local evidence files."""

from __future__ import annotations

import errno
import hashlib
import os
import secrets
import stat
from collections.abc import Callable, Generator, Iterable
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import Final, NamedTuple, cast

from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite._fileio import TransportError, read_descriptor
from underwrite.service._record_errors import ERROR_SPECS
from underwrite.service._record_format import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_LINKS,
    MAX_MANIFEST_BYTES,
    MAX_RECORDS,
    MAX_SELECTION_EXCLUDED,
    MAX_TOTAL_BYTES,
    FormatError,
    logical_name,
    record_id,
    strict_manifest,
    validate_captured_spec,
    validate_manifest,
    validate_spec,
)

_DIR_MODE: Final = 0o700
_FILE_MODE: Final = 0o600
_READ_SIZE: Final = 1024 * 1024
RECORD_MAX_FILES: Final = MAX_FILES
RECORD_READ_MAX_BYTES: Final = 1024 * 1024
ERROR_CODES: Final = frozenset(ERROR_SPECS)


class RecordSnapshot(NamedTuple):
    """One validated published manifest and its canonical byte digest."""

    manifest: dict[str, object]
    digest: str


@dataclass(frozen=True)
class RecordFileSnapshot:
    """Captured bytes checked against one captured manifest, not whole-record validity.

    Card: record-reuse | Paper: record_document.v1 | Stage: storage-read
    """

    record_id: str
    name: str
    media_type: str
    data: bytes
    sha256: str
    manifest_digest: str


@dataclass(frozen=True)
class CapturedRecordFile:
    """One already-captured file; its bytes are the sole storage source."""

    name: str
    role: str
    data: bytes
    media_type: str = "application/octet-stream"
    source_locator: str | None = None


@dataclass(frozen=True)
class CapturedRecordSpec:
    """A path-free immutable record request over captured bytes."""

    record_id: str
    kind: str
    label: str
    metadata: dict[str, str]
    links: tuple[dict[str, str], ...]
    selection_excluded: tuple[str, ...] | None
    rerun_verification: dict[str, object]
    provenance: dict[str, object]
    files: tuple[CapturedRecordFile, ...]


class RecordError(ValueError):
    """Finite service failure; the message never contains received source text."""

    def __init__(self, code: str, location: str = "") -> None:
        if code not in ERROR_CODES:
            code = "INTERNAL_ERROR"
            location = ""
        super().__init__(code)
        self.code = code
        self.location = location


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
            raw = read_descriptor(descriptor, MAX_MANIFEST_BYTES)
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
    except (OSError, FormatError, TransportError):
        raise RecordError("INVALID_RECORD", "/record") from None


def read_record(root: Path, identifier: str) -> RecordSnapshot:
    """Read one strictly canonical, published record manifest."""
    records = _store_for_read(root)
    directory = _record_dir(records, identifier)
    return _read_manifest(directory, identifier)


@contextmanager
def _private_descriptor(
    path: str | Path,
    *,
    parent: int | None = None,
    directory: bool,
    missing: str,
) -> Generator[int]:
    """Validate the opened control node; descendants remain relative to its fd."""
    directory_flags, file_flags = _platform_flags()
    try:
        descriptor = os.open(path, directory_flags if directory else file_flags, dir_fd=parent)
    except FileNotFoundError:
        raise RecordError(missing, "/record") from None
    except NotImplementedError:
        raise RecordError("UNSUPPORTED_PLATFORM") from None
    except OSError as exc:
        code = (
            "INVALID_STORE_LAYOUT"
            if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.EISDIR}
            else "STORE_IO_ERROR"
        )
        raise RecordError(code, "/record") from None
    try:
        info = os.fstat(descriptor)
        expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not expected:
            raise RecordError("INVALID_STORE_LAYOUT", "/record")
        if not _private(info.st_mode):
            raise RecordError("INSECURE_STORE", "/record")
        yield descriptor
    except OSError:
        raise RecordError("STORE_IO_ERROR", "/record") from None
    finally:
        os.close(descriptor)


def _read_descriptor_bytes(descriptor: int, limit: int) -> bytes:
    if os.fstat(descriptor).st_size > limit:
        raise RecordError("INPUT_LIMIT_EXCEEDED", "/record")
    raw = read_descriptor(descriptor, limit)
    if len(raw) > limit:
        raise RecordError("INPUT_LIMIT_EXCEEDED", "/record")
    return raw


def _manifest_at(directory: int, identifier: str) -> RecordSnapshot:
    try:
        with _private_descriptor(
            "manifest.json", parent=directory, directory=False, missing="RECORD_NOT_PUBLISHED"
        ) as descriptor:
            raw = _read_descriptor_bytes(descriptor, MAX_MANIFEST_BYTES)
        manifest = strict_manifest(raw)
        if manifest["record_id"] != identifier:
            raise RecordError("INVALID_RECORD", "/record")
        return RecordSnapshot(manifest, digest_bytes(raw))
    except RecordError as exc:
        if exc.code == "INPUT_LIMIT_EXCEEDED":
            raise RecordError("INVALID_RECORD", "/record") from None
        raise
    except FormatError:
        raise RecordError("INVALID_RECORD", "/record") from None


def _selected_bytes_at(directory: int, name: str, limit: int) -> bytes:
    try:
        with ExitStack() as opened:
            parent = directory
            parts = ["files", *name.split("/")]
            for part in parts[:-1]:
                parent = opened.enter_context(
                    _private_descriptor(
                        part, parent=parent, directory=True, missing="SOURCE_READ_FAILED"
                    )
                )
            descriptor = opened.enter_context(
                _private_descriptor(
                    parts[-1], parent=parent, directory=False, missing="SOURCE_READ_FAILED"
                )
            )
            return _read_descriptor_bytes(descriptor, limit)
    except RecordError as exc:
        if exc.code == "STORE_IO_ERROR":
            raise RecordError("SOURCE_READ_FAILED", "/record") from None
        raise


def _selected_file(snapshot: RecordSnapshot, name: str, limit: int) -> dict[str, object]:
    for file in cast(list[dict[str, object]], snapshot.manifest["files"]):
        if file["name"] == name:
            if cast(int, file["bytes"]) > limit:
                raise RecordError("INPUT_LIMIT_EXCEEDED", "/record")
            return file
    raise RecordError("RECORD_FILE_NOT_FOUND", "/record")


def read_record_file(
    root: Path, identifier: str, name: str, *, max_bytes: int = RECORD_READ_MAX_BYTES
) -> RecordFileSnapshot:
    """Return bounded bytes checked against a manifest under the same record fd.

    Card: record-reuse | Paper: record_document.v1 | Stage: storage-read
    The caller selects root through normal ancestor resolution. Root itself and
    all descendants are opened no-follow and checked for private permissions.
    Unrelated files/links and original sources are not read or verified.
    The default budget is 1 MiB; explicit budgets may reach the storage-owned
    MAX_FILE_BYTES ceiling without changing JSON document read limits.
    """
    try:
        checked_id = record_id(identifier)
    except FormatError:
        raise RecordError("INVALID_SPEC", "/record_id") from None
    try:
        checked_name = logical_name(name)
    except FormatError:
        raise RecordError("INVALID_SPEC", "/spec/files") from None
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_FILE_BYTES:
        raise RecordError("INVALID_SPEC", "/spec/files")
    with ExitStack() as opened:
        root_fd = opened.enter_context(
            _private_descriptor(root, directory=True, missing="STORE_NOT_FOUND")
        )
        records_fd = opened.enter_context(
            _private_descriptor(
                "records", parent=root_fd, directory=True, missing="STORE_NOT_FOUND"
            )
        )
        try:
            with _private_descriptor(
                ".staging", parent=root_fd, directory=True, missing="STORE_NOT_FOUND"
            ):
                pass
        except RecordError as exc:
            if exc.code != "STORE_NOT_FOUND":
                raise
        directory = opened.enter_context(
            _private_descriptor(
                checked_id, parent=records_fd, directory=True, missing="RECORD_NOT_FOUND"
            )
        )
        snapshot = _manifest_at(directory, checked_id)
        selected = _selected_file(snapshot, checked_name, max_bytes)
        raw = _selected_bytes_at(directory, checked_name, max_bytes)
    digest = digest_bytes(raw)
    if len(raw) != selected["bytes"] or digest != selected["sha256"]:
        raise RecordError("RECORD_FILE_INTEGRITY_MISMATCH", "/record")
    return RecordFileSnapshot(
        checked_id, checked_name, cast(str, selected["media_type"]), raw, digest, snapshot.digest
    )


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


@contextmanager
def _open_sources(
    spec: dict[str, object], source_base: Path
) -> Generator[list[tuple[dict[str, object], int, int]]]:
    _, file_flags = _platform_flags()
    opened: list[tuple[dict[str, object], int, int]] = []
    total = 0
    with ExitStack() as acquisition:
        for item in cast(list[dict[str, object]], spec["files"]):
            source = source_base / cast(str, item["path"])
            try:
                descriptor = os.open(source, file_flags)
                acquisition.callback(os.close, descriptor)
                info = os.fstat(descriptor)
            except OSError:
                raise RecordError("SOURCE_READ_FAILED", "/spec/files") from None
            if not stat.S_ISREG(info.st_mode):
                raise RecordError("SOURCE_READ_FAILED", "/spec/files")
            if info.st_size > MAX_FILE_BYTES:
                raise RecordError("INPUT_LIMIT_EXCEEDED", "/spec/files")
            total += info.st_size
            if total > MAX_TOTAL_BYTES:
                raise RecordError("INPUT_LIMIT_EXCEEDED", "/spec/files")
            opened.append((item, descriptor, info.st_size))
        yield opened


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
    def chunks() -> Iterable[bytes]:
        try:
            while chunk := os.read(descriptor, _READ_SIZE):
                yield chunk
        except OSError:
            raise RecordError("SOURCE_READ_FAILED", "/spec/files") from None

    return _write_one(chunks(), expected, output, total_allowance, source_backed=True)


def _write_one(
    chunks: Iterable[bytes],
    expected: int,
    output: Path,
    total_allowance: int,
    *,
    source_backed: bool,
) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    target = os.open(output, flags, _FILE_MODE)
    size = 0
    hasher = hashlib.sha256()
    try:
        os.fchmod(target, _FILE_MODE)
        for chunk in chunks:
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
            code = "SOURCE_READ_FAILED" if source_backed else "INTERNAL_ERROR"
            raise RecordError(code, "/spec/files" if source_backed else "")
        os.fsync(target)
    finally:
        os.close(target)
    return size, "sha256:" + hasher.hexdigest()


def _write_bytes(data: bytes, output: Path, total_allowance: int) -> tuple[int, str]:
    return _write_one((data,), len(data), output, total_allowance, source_backed=False)


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


def _validate_checked_spec(checked: dict[str, object], root: Path) -> dict[str, object]:
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


def _checked_spec(spec: object, root: Path) -> dict[str, object]:
    try:
        checked = validate_spec(spec)
    except FormatError:
        raise RecordError("INVALID_SPEC", "/spec") from None
    return _validate_checked_spec(checked, root)


def _checked_captured_spec(
    spec: CapturedRecordSpec, root: Path
) -> tuple[dict[str, object], tuple[tuple[dict[str, object], bytes], ...]]:
    if (
        type(spec) is not CapturedRecordSpec
        or type(spec.files) is not tuple
        or not 1 <= len(spec.files) <= RECORD_MAX_FILES
        or type(spec.links) is not tuple
        or len(spec.links) > MAX_LINKS
        or (
            spec.selection_excluded is not None
            and (
                type(spec.selection_excluded) is not tuple
                or len(spec.selection_excluded) > MAX_SELECTION_EXCLUDED
            )
        )
    ):
        raise RecordError("INVALID_SPEC", "/spec")
    file_documents: list[dict[str, object]] = []
    data: list[bytes] = []
    total = 0
    for item in spec.files:
        if type(item) is not CapturedRecordFile or type(item.data) is not bytes:
            raise RecordError("INVALID_SPEC", "/spec/files")
        if len(item.data) > MAX_FILE_BYTES:
            raise RecordError("INPUT_LIMIT_EXCEEDED", "/spec/files")
        total += len(item.data)
        if total > MAX_TOTAL_BYTES:
            raise RecordError("INPUT_LIMIT_EXCEEDED", "/spec/files")
        file_documents.append(
            {
                "name": item.name,
                "role": item.role,
                "media_type": item.media_type,
                "source_locator": item.source_locator,
            }
        )
        data.append(item.data)
    document = {
        "schema": "record_spec.v1",
        "record_id": spec.record_id,
        "kind": spec.kind,
        "label": spec.label,
        "metadata": spec.metadata,
        "selection": {
            "scope": "explicit_files",
            "excluded": (
                None if spec.selection_excluded is None else list(spec.selection_excluded)
            ),
        },
        "rerun_verification": spec.rerun_verification,
        "provenance": spec.provenance,
        "links": list(spec.links),
        "files": file_documents,
    }
    try:
        checked = validate_captured_spec(document)
    except FormatError:
        raise RecordError("INVALID_SPEC", "/spec") from None
    checked = _validate_checked_spec(checked, root)
    checked_files = cast(list[dict[str, object]], checked["files"])
    return checked, tuple(zip(checked_files, data, strict=True))


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


def _capture_bytes(
    record_dir: Path,
    files: tuple[tuple[dict[str, object], bytes], ...],
    owned_files: list[Path],
    owned_dirs: list[Path],
) -> list[dict[str, object]]:
    stored: list[dict[str, object]] = []
    total = 0
    files_root = _files_root(record_dir, owned_dirs)
    for item, data in files:
        output = _ensure_output_parent(files_root, cast(str, item["name"]), owned_dirs)
        owned_files.append(output)
        size, checksum = _write_bytes(data, output, MAX_TOTAL_BYTES - total)
        total += size
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


def _publication_absent(record_dir: Path) -> bool:
    """Permit destructive cleanup only when the completion marker is absent."""
    try:
        (record_dir / "manifest.json").lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _publish(record_dir: Path, staging: Path, raw: bytes) -> None:
    staging_file = _write_staging(staging, raw)
    try:
        os.link(staging_file, record_dir / "manifest.json", follow_symlinks=False)
    except BaseException:
        if _publication_absent(record_dir):
            try:
                staging_file.unlink()
            except OSError:
                pass
        raise
    _fsync_dir(record_dir)
    try:
        staging_file.unlink()
        _fsync_dir(staging)
    except OSError:
        pass


def _save_checked(
    root: Path,
    checked: dict[str, object],
    capture: Callable[[Path, list[Path], list[Path]], list[dict[str, object]]],
) -> RecordSnapshot:
    records: Path | None = None
    record_dir: Path | None = None
    owned_files: list[Path] = []
    owned_dirs: list[Path] = []
    try:
        records, staging = _store_for_save(root)
        record_dir = _reserve(records, cast(str, checked["record_id"]))
        owned_dirs.append(record_dir)
        stored = capture(record_dir, owned_files, owned_dirs)
        manifest = _manifest(checked, stored)
        validate_manifest(manifest)
        raw = canonical_bytes(manifest)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise RecordError("INPUT_LIMIT_EXCEEDED", "/spec")
        _publish(record_dir, staging, raw)
        return RecordSnapshot(manifest, digest_bytes(raw))
    except FormatError:
        raise RecordError("INTERNAL_ERROR") from None
    except RecordError:
        raise
    except OSError:
        raise RecordError("STORE_IO_ERROR") from None
    finally:
        if records is not None and record_dir is not None and _publication_absent(record_dir):
            _cleanup_before_publication(owned_files, owned_dirs, records)


def save_record(root: Path, spec: object, source_base: Path) -> RecordSnapshot:
    """Capture selected files once and publish an immutable manifest create-only."""
    checked = _checked_spec(spec, root)
    with _open_sources(checked, source_base) as sources:

        def capture(
            record_dir: Path, owned_files: list[Path], owned_dirs: list[Path]
        ) -> list[dict[str, object]]:
            return _capture_files(record_dir, sources, owned_files, owned_dirs)

        return _save_checked(root, checked, capture)


def save_captured_record(root: Path, spec: CapturedRecordSpec) -> RecordSnapshot:
    """Publish already-captured bytes without reopening any source path."""
    checked, files = _checked_captured_spec(spec, root)

    def capture(
        record_dir: Path, owned_files: list[Path], owned_dirs: list[Path]
    ) -> list[dict[str, object]]:
        return _capture_bytes(record_dir, files, owned_files, owned_dirs)

    return _save_checked(root, checked, capture)


def list_records(root: Path) -> dict[str, object]:
    """Derive a bounded, sorted catalog from complete and unpublished directories."""
    records = _store_for_read(root)
    try:
        with os.scandir(records) as iterator:
            entries = list(islice(iterator, MAX_RECORDS + 1))
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


def _file_content_finding(path: Path, info: os.stat_result, item: dict[str, object]) -> str | None:
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

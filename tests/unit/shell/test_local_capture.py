"""The local capture boundary reads declared root-contained files once."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from underwrite.instrument.ingest import local_capture
from underwrite.instrument.ingest.local_capture import (
    CaptureError,
    CapturePolicy,
    RootReader,
    decode_captured_object,
)

THREE_BYTES = 3


def policy(
    *, files: int = 4, file_bytes: int = 32, total_bytes: int = 64, depth: int = 8
) -> CapturePolicy:
    return CapturePolicy(files, file_bytes, total_bytes, depth)


def assert_error(code: str, reason: str, call: Any) -> CaptureError:
    with pytest.raises(CaptureError) as caught:
        call()
    assert caught.value.code == code
    assert caught.value.reason == reason
    return caught.value


def patch_open(monkeypatch: pytest.MonkeyPatch, opening: Callable[..., int]) -> None:
    supported = {*os.supports_dir_fd, opening}
    monkeypatch.setattr(local_capture.os, "open", opening)
    monkeypatch.setattr(local_capture.os, "supports_dir_fd", supported)


def test_nested_capture_returns_exact_identity_and_caches_one_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    nested = root / "a/b"
    nested.mkdir(parents=True)
    data = b"exact bytes\x00\xff"
    (nested / "value.bin").write_bytes(data)
    real_open = os.open
    leaf_opens = 0

    def opening(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal leaf_opens
        if path == "value.bin":
            leaf_opens += 1
        return real_open(path, flags, *args, **kwargs)

    patch_open(monkeypatch, opening)
    with RootReader.open(root, policy()) as reader:
        first = reader.capture_relative(
            "a/b/value.bin", logical_name="source/value.bin", max_bytes=32, location="/file"
        )
        second = reader.capture_relative(
            "a/b/value.bin", logical_name="source/value.bin", max_bytes=32, location="/file"
        )
        assert first is second
        assert first.data == data and first.size == len(data)
        assert first.sha256 == "sha256:" + hashlib.sha256(data).hexdigest()
        assert reader.file_count == 1 and reader.total_bytes == len(data)
    assert leaf_opens == 1


def test_cached_file_honors_a_later_tighter_limit_without_reread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "value").write_bytes(b"1234")
    real_open = os.open
    leaf_opens = 0

    def opening(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal leaf_opens
        if path == "value":
            leaf_opens += 1
        return real_open(path, flags, *args, **kwargs)

    patch_open(monkeypatch, opening)
    with RootReader.open(root, policy()) as reader:
        reader.capture_relative("value", logical_name="value", max_bytes=4, location="/first")
        error = assert_error(
            "INPUT_LIMIT_EXCEEDED",
            "CAPTURE_FILE_LIMIT_EXCEEDED",
            lambda: reader.capture_relative(
                "value", logical_name="value", max_bytes=3, location="/second"
            ),
        )
        assert error.location == "/second"
    assert leaf_opens == 1


@pytest.mark.parametrize("conflict", ["path", "logical"])
def test_conflicting_path_or_logical_name_fails_without_reread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conflict: str
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "one").write_bytes(b"1")
    (root / "two").write_bytes(b"2")
    real_open = os.open
    leaf_opens = 0

    def opening(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal leaf_opens
        if path in {"one", "two"}:
            leaf_opens += 1
        return real_open(path, flags, *args, **kwargs)

    patch_open(monkeypatch, opening)
    with RootReader.open(root, policy()) as reader:
        reader.capture_relative("one", logical_name="first", max_bytes=4, location="/one")
        relative, logical = ("one", "other") if conflict == "path" else ("two", "first")

        def call() -> object:
            return reader.capture_relative(
                relative, logical_name=logical, max_bytes=4, location="/conflict"
            )

        assert_error("SOURCE_READ_FAILED", "CAPTURE_LOGICAL_NAME_CONFLICT", call)
    assert leaf_opens == 1


def test_file_count_and_total_limits_apply_to_distinct_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "one").write_bytes(b"123")
    (root / "two").write_bytes(b"456")
    real_open = os.open
    opened: list[object] = []

    def opening(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        opened.append(path)
        return real_open(path, flags, *args, **kwargs)

    patch_open(monkeypatch, opening)
    with RootReader.open(root, policy(files=1)) as reader:
        reader.capture_relative("one", logical_name="one", max_bytes=8, location="/one")
        before = len(opened)
        assert_error(
            "INPUT_LIMIT_EXCEEDED",
            "CAPTURE_FILE_COUNT_EXCEEDED",
            lambda: reader.capture_relative(
                "two", logical_name="two", max_bytes=8, location="/two"
            ),
        )
        assert len(opened) == before
    with RootReader.open(root, policy(total_bytes=5)) as reader:
        reader.capture_relative("one", logical_name="one", max_bytes=8, location="/one")
        assert_error(
            "INPUT_LIMIT_EXCEEDED",
            "CAPTURE_TOTAL_LIMIT_EXCEEDED",
            lambda: reader.capture_relative(
                "two", logical_name="two", max_bytes=8, location="/two"
            ),
        )
        assert reader.file_count == 1 and reader.total_bytes == THREE_BYTES


def test_file_limit_uses_bounded_descriptor_read(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "large").write_bytes(b"1234")
    with RootReader.open(root, policy(file_bytes=3)) as reader:
        assert_error(
            "INPUT_LIMIT_EXCEEDED",
            "CAPTURE_FILE_LIMIT_EXCEEDED",
            lambda: reader.capture_relative(
                "large", logical_name="large", max_bytes=8, location="/large"
            ),
        )


@pytest.mark.parametrize(
    "relative",
    ["", "/absolute", "a//b", "a/./b", "a/../b", "a\\b", "a\x00b"],
)
def test_invalid_relative_paths_fail_before_leaf_open(tmp_path: Path, relative: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with RootReader.open(root, policy()) as reader:
        assert_error(
            "SOURCE_READ_FAILED",
            "CAPTURE_RELATIVE_PATH_INVALID",
            lambda: reader.capture_relative(
                relative, logical_name="logical", max_bytes=8, location="/path"
            ),
        )


def test_path_component_limit_is_caller_policy(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "a/b").mkdir(parents=True)
    (root / "a/b/value").write_bytes(b"x")
    with RootReader.open(root, policy(depth=2)) as reader:
        assert_error(
            "INPUT_LIMIT_EXCEEDED",
            "CAPTURE_PATH_DEPTH_EXCEEDED",
            lambda: reader.capture_relative(
                "a/b/value", logical_name="value", max_bytes=8, location="/path"
            ),
        )


def test_root_and_parent_symlinks_are_rejected(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    root = real_parent / "root"
    root.mkdir(parents=True)
    root_link = tmp_path / "root-link"
    root_link.symlink_to(root, target_is_directory=True)
    assert_error(
        "SOURCE_READ_FAILED",
        "CAPTURE_ROOT_SYMLINK_REJECTED",
        lambda: RootReader.open(root_link, policy()),
    )
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(real_parent, target_is_directory=True)
    assert_error(
        "SOURCE_READ_FAILED",
        "CAPTURE_ROOT_SYMLINK_REJECTED",
        lambda: RootReader.open(parent_link / "root", policy()),
    )


@pytest.mark.parametrize("target", ["directory", "leaf"])
def test_nested_symlinks_are_rejected(tmp_path: Path, target: str) -> None:
    root = tmp_path / "root"
    real = root / "real"
    real.mkdir(parents=True)
    (real / "value").write_bytes(b"x")
    if target == "directory":
        (root / "link").symlink_to("real", target_is_directory=True)
        relative = "link/value"
    else:
        (root / "value").symlink_to("real/value")
        relative = "value"
    with RootReader.open(root, policy()) as reader:
        assert_error(
            "SOURCE_READ_FAILED",
            "CAPTURE_READ_FAILED",
            lambda: reader.capture_relative(
                relative, logical_name="value", max_bytes=8, location="/value"
            ),
        )


def test_fifo_and_directory_leaf_are_rejected_without_blocking(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    os.mkfifo(root / "fifo")
    (root / "directory").mkdir()
    with RootReader.open(root, policy()) as reader:
        for name in ("fifo", "directory"):
            assert_error(
                "SOURCE_READ_FAILED",
                "CAPTURE_REGULAR_FILE_REQUIRED",
                lambda name=name: reader.capture_relative(
                    name, logical_name=name, max_bytes=8, location=f"/{name}"
                ),
            )


def test_intermediate_and_leaf_descriptors_close_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    (root / "nested").mkdir(parents=True)
    (root / "nested/value").write_bytes(b"too large")
    real_open, real_fstat = os.open, os.fstat
    opened: list[int] = []

    def opening(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        descriptor = real_open(path, flags, *args, **kwargs)
        if kwargs.get("dir_fd") is not None:
            opened.append(descriptor)
        return descriptor

    patch_open(monkeypatch, opening)
    with RootReader.open(root, policy(file_bytes=2)) as reader:
        opened.clear()
        assert_error(
            "INPUT_LIMIT_EXCEEDED",
            "CAPTURE_FILE_LIMIT_EXCEEDED",
            lambda: reader.capture_relative(
                "nested/value", logical_name="value", max_bytes=8, location="/value"
            ),
        )
        for descriptor in opened:
            with pytest.raises(OSError):
                real_fstat(descriptor)


def test_close_is_idempotent_and_closed_reader_refuses_capture(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "value").write_bytes(b"x")
    reader = RootReader.open(root, policy())
    reader.close()
    reader.close()
    assert_error(
        "SOURCE_READ_FAILED",
        "CAPTURE_READER_CLOSED",
        lambda: reader.capture_relative(
            "value", logical_name="value", max_bytes=8, location="/value"
        ),
    )


def test_root_descriptor_closes_when_fstat_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    real_open, real_fstat = os.open, os.fstat
    descriptor: int | None = None

    def opening(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal descriptor
        descriptor = real_open(path, flags, *args, **kwargs)
        return descriptor

    def fail_fstat(fd: int) -> os.stat_result:
        del fd
        raise OSError("injected")

    patch_open(monkeypatch, opening)
    monkeypatch.setattr(local_capture.os, "fstat", fail_fstat)
    assert_error(
        "SOURCE_READ_FAILED",
        "CAPTURE_ROOT_READ_FAILED",
        lambda: RootReader.open(root, policy()),
    )
    assert descriptor is not None
    with pytest.raises(OSError):
        real_fstat(descriptor)


def test_missing_platform_flags_are_typed_before_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(local_capture.os, "O_NOFOLLOW")
    error = assert_error(
        "UNSUPPORTED_PLATFORM",
        "CAPTURE_PLATFORM_UNSUPPORTED",
        lambda: RootReader.open(tmp_path / "absent", policy()),
    )
    assert error.location == ""


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b'{"a":1,"a":2}', "CAPTURE_INVALID_JSON"),
        (b"[]", "CAPTURE_JSON_OBJECT_REQUIRED"),
        (b'{"a":{"b":1}}', "CAPTURE_INVALID_JSON"),
    ],
)
def test_captured_json_uses_existing_strict_decoder(
    tmp_path: Path, body: bytes, reason: str
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "value.json").write_bytes(body)
    with RootReader.open(root, policy()) as reader:
        captured = reader.capture_relative(
            "value.json", logical_name="value.json", max_bytes=32, location="/value"
        )
        depth = 1 if body.startswith(b'{"a":{"b"') else 8
        assert_error(
            "INVALID_INPUT",
            reason,
            lambda: decode_captured_object(captured, max_depth=depth, location="/value"),
        )


def test_valid_captured_object_decodes_without_reopening(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "value.json").write_bytes(b'{"ok":true}')
    with RootReader.open(root, policy()) as reader:
        captured = reader.capture_relative(
            "value.json", logical_name="value.json", max_bytes=32, location="/value"
        )
        assert decode_captured_object(captured, max_depth=8, location="/value") == {"ok": True}


@pytest.mark.parametrize("values", [(0, 1, 1, 1), (1, 0, 1, 1), (1, 1, 0, 1), (1, 1, 1, 0)])
def test_policy_limits_are_exact_positive_integers(values: tuple[int, int, int, int]) -> None:
    with pytest.raises(ValueError, match="^CAPTURE_POLICY_REQUIRES_POSITIVE_INTEGER_LIMITS$"):
        CapturePolicy(*values)


def test_capture_error_does_not_retain_unowned_reason_or_location() -> None:
    error = CaptureError("SOURCE_READ_FAILED", "received private text", "/private/path")
    assert error.code == "SOURCE_READ_FAILED"
    assert error.reason == "CAPTURE_READ_FAILED"
    assert error.location == ""


def test_root_ancestor_swap_cannot_redirect_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "parent"
    root = parent / "root"
    root.mkdir(parents=True)
    (root / "value").write_bytes(b"inside")
    outside = tmp_path / "outside"
    (outside / "root").mkdir(parents=True)
    (outside / "root/value").write_bytes(b"outside")
    real_open = os.open
    swapped = False

    def opening(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if not swapped and path in {root, "root"}:
            parent.rename(tmp_path / "original")
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, *args, **kwargs)

    patch_open(monkeypatch, opening)
    with RootReader.open(root, policy()) as reader:
        captured = reader.capture_relative(
            "value", logical_name="value", max_bytes=32, location="/value"
        )
        assert captured.data == b"inside"
    assert swapped


@pytest.mark.parametrize("relative", ["root", "child/../root", "."])
def test_relative_root_keeps_caller_path_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    (tmp_path / "root").mkdir()
    (tmp_path / "child").mkdir()
    root = tmp_path if relative == "." else tmp_path / "root"
    (root / "value").write_bytes(b"inside")
    monkeypatch.chdir(tmp_path)
    with RootReader.open(Path(relative), policy()) as reader:
        assert (
            reader.capture_relative(
                "value", logical_name="value", max_bytes=32, location="/value"
            ).data
            == b"inside"
        )


def test_root_walk_closes_descriptors_when_an_ancestor_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open, real_fstat = os.open, os.fstat
    opened: list[int] = []

    def opening(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        descriptor = real_open(path, flags, *args, **kwargs)
        opened.append(descriptor)
        return descriptor

    patch_open(monkeypatch, opening)
    assert_error(
        "SOURCE_READ_FAILED",
        "CAPTURE_ROOT_READ_FAILED",
        lambda: RootReader.open(tmp_path / "absent/root", policy()),
    )
    assert opened
    for descriptor in opened:
        with pytest.raises(OSError):
            real_fstat(descriptor)


def test_root_component_swap_to_symlink_is_rejected_at_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "parent"
    (parent / "root").mkdir(parents=True)
    outside = tmp_path / "outside"
    (outside / "root").mkdir(parents=True)
    real_open, real_fstat = os.open, os.fstat
    opened: list[int] = []
    swapped = False

    def opening(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if not swapped and path == "parent":
            parent.rename(tmp_path / "original")
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        descriptor = real_open(path, flags, *args, **kwargs)
        opened.append(descriptor)
        return descriptor

    patch_open(monkeypatch, opening)
    assert_error(
        "SOURCE_READ_FAILED",
        "CAPTURE_ROOT_READ_FAILED",
        lambda: RootReader.open(parent / "root", policy()),
    )
    assert swapped
    for descriptor in opened:
        with pytest.raises(OSError):
            real_fstat(descriptor)


@pytest.mark.parametrize("invalid", ["private\x00root", "private\ud800root"])
def test_invalid_root_encoding_has_only_owned_error_text(tmp_path: Path, invalid: str) -> None:
    error = assert_error(
        "SOURCE_READ_FAILED",
        "CAPTURE_ROOT_READ_FAILED",
        lambda: RootReader.open(tmp_path / invalid, policy()),
    )
    assert str(error) == "CAPTURE_ROOT_READ_FAILED"
    assert error.location == ""


def test_unencodable_relative_path_is_a_typed_failure(tmp_path: Path) -> None:
    with RootReader.open(tmp_path, policy()) as reader:
        error = assert_error(
            "SOURCE_READ_FAILED",
            "CAPTURE_RELATIVE_PATH_INVALID",
            lambda: reader.capture_relative(
                "private\ud800file", logical_name="logical", max_bytes=8, location="/file"
            ),
        )
        assert str(error) == "CAPTURE_RELATIVE_PATH_INVALID"
        assert error.location == "/file"
        assert reader.file_count == 0


def test_zero_bytes_fit_after_total_allowance_is_exhausted(tmp_path: Path) -> None:
    (tmp_path / "full").write_bytes(b"123")
    (tmp_path / "empty").write_bytes(b"")
    (tmp_path / "extra").write_bytes(b"x")
    with RootReader.open(tmp_path, policy(total_bytes=3)) as reader:
        reader.capture_relative("full", logical_name="full", max_bytes=8, location="/full")
        empty = reader.capture_relative(
            "empty", logical_name="empty", max_bytes=8, location="/empty"
        )
        assert empty.data == b"" and empty.size == 0
        assert reader.total_bytes == THREE_BYTES
        assert reader.file_count == len(("full", "empty"))
        assert_error(
            "INPUT_LIMIT_EXCEEDED",
            "CAPTURE_TOTAL_LIMIT_EXCEEDED",
            lambda: reader.capture_relative(
                "extra", logical_name="extra", max_bytes=8, location="/extra"
            ),
        )
        assert reader.total_bytes == THREE_BYTES
        assert reader.file_count == len(("full", "empty"))

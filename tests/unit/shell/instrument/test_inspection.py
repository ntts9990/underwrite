"""A completed inspection is distinct from invalid invocation and source authority."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from _inspection_cases import case, update
from _inspection_cases import inspection_contracts as inspection_contracts
from _repo_paths import repo_root
from jsonschema import Draft202012Validator
from underwrite_core.canonical import content_digest, digest_bytes

from underwrite.instrument.ingest import inspection
from underwrite.instrument.ingest import schema as schemas
from underwrite.instrument.ingest.application import packaged_ingestor
from underwrite.instrument.ingest.project import Source

INVOCATION_EXIT = 2
ROOT = repo_root(Path(__file__).resolve())
pytestmark = pytest.mark.usefixtures("inspection_contracts")


@pytest.fixture(autouse=True)
def denied_schema_requests(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """No schema negative test may reach proxies/network if its guard is mutated.

    Installed jsonschema._warn_for_remote_retrieve imports urlopen from
    urllib.request at call time; this denies that exact retrieval boundary.
    """
    attempted: list[str] = []

    def deny(request: urllib.request.Request, *args: object, **kwargs: object) -> None:
        attempted.append(request.full_url)
        raise OSError("External schema retrieval denied by test")

    monkeypatch.setattr(urllib.request, "urlopen", deny)
    return attempted


def invoke(path: Path) -> tuple[int, dict[str, Any]]:
    try:
        document = inspection.inspect_artifact(path)
        code = 0 if document["projection"] == "created" else 1
    except inspection.InspectionError as exc:
        document = exc.payload
        code = int(str(document["exit_code"]))
    schema = json.loads((ROOT / "contracts" / f"{document['schema']}.schema.json").read_bytes())
    Draft202012Validator(schema).validate(document)  # pyright: ignore[reportUnknownMemberType]
    return code, document


@pytest.mark.parametrize(
    "format,version,relative",
    [
        ("deepeval.test-run", "4.1.1", "deepeval/test_run_20260914_132634.json"),
        ("langfuse.observations-v2", "4.35.0", "langfuse/observations_v2-2026-09-14T06-40-00.json"),
    ],
)
@pytest.mark.parametrize("expected", [False, True])
def test_real_capture_matches_existing_ingest(
    tmp_path: Path,
    format: str,
    version: str,
    relative: str,
    expected: bool,
) -> None:
    raw = (ROOT / "fixtures/golden/external" / relative).read_bytes()
    path = case(tmp_path, raw)
    update(path, "source", {"format": format, "format_version": version})
    if expected:
        update(path, "artifact", {"expected_hash": digest_bytes(raw)})
    before = path.read_bytes()
    code, report = invoke(path)
    reference = packaged_ingestor(max_bytes=inspection.DEFAULT_MAX_BYTES, max_depth=64)
    observation = reference.file(
        tmp_path / "artifact.json", Source(format, version, None)
    ).observation
    assert code == 0
    assert report["observation"] == {
        "kind": observation["kind"],
        "digest": content_digest(observation),
    }
    assert report["expected_hash_check"] == ("matched" if expected else "not_supplied")
    assert report["raw_hash"] == digest_bytes(raw)
    assert report["manifest_hash"] == digest_bytes(before)
    assert report["limitations"] == list(inspection.LIMITATIONS)
    assert report["diagnostics"] == []
    assert path.read_bytes() == before and (tmp_path / "artifact.json").read_bytes() == raw


def test_mismatch_never_builds_or_calls_codec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = case(tmp_path, b"not JSON")
    update(path, "artifact", {"expected_hash": "sha256:" + "0" * 64})

    def forbidden(**kwargs: object) -> None:
        pytest.fail("mismatch invoked codec factory")

    monkeypatch.setattr(inspection, "packaged_ingestor", forbidden)
    code, report = invoke(path)
    assert code == 1 and report["observation"] is None
    assert report["expected_hash_check"] == "mismatched"
    assert report["diagnostics"][0]["code"] == "EXPECTED_HASH_MISMATCH"


@pytest.mark.parametrize(
    "metadata,code",
    [
        ({"format": "secret unsupported\x1b"}, "UNSUPPORTED_FORMAT"),
        ({"ref": " "}, "INVALID_SOURCE"),
        ({"locator": "e\u0301"}, "INVALID_SOURCE"),
        ({"format_version": " "}, "INVALID_SOURCE"),
        ({"ref": None}, "INVALID_MANIFEST"),
    ],
)
def test_source_rejected_before_artifact_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata: dict[str, object],
    code: str,
) -> None:
    path = case(tmp_path)
    update(path, "source", metadata)
    update(path, "artifact", {"expected_hash": "sha256:" + "0" * 64})
    original_open = os.open
    original_read = inspection.read_descriptor
    artifact_identity = (tmp_path / "artifact.json").stat()
    artifact_accesses: list[str] = []

    def watch_open(file: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        if Path(file).name == "artifact.json":
            artifact_accesses.append("open")
        return original_open(file, flags, mode, dir_fd=dir_fd)

    def watch_read(fd: int, limit: int) -> bytes:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) == (artifact_identity.st_dev, artifact_identity.st_ino):
            artifact_accesses.append("read")
        return original_read(fd, limit)

    monkeypatch.setattr(os, "open", watch_open)
    monkeypatch.setattr(os, "supports_dir_fd", {*os.supports_dir_fd, watch_open})
    monkeypatch.setattr(inspection, "read_descriptor", watch_read)
    exit_code, error = invoke(path)
    assert artifact_accesses == []
    assert exit_code == INVOCATION_EXIT and error["code"] == code
    assert error["next_action"] == (
        "SELECT_SUPPORTED_PROFILE" if code == "UNSUPPORTED_FORMAT" else "CORRECT_MANIFEST"
    )
    assert error["location"] == ("" if code == "INVALID_MANIFEST" else "/source")
    assert "secret" not in json.dumps(error)


@pytest.mark.parametrize("failure", ["nul", "missing-parent", "parent-is-file"])
def test_invalid_manifest_location_is_actionable_not_a_completed_report(
    tmp_path: Path,
    failure: str,
) -> None:
    if failure == "nul":
        path = tmp_path / "secret\x00case.json"
    elif failure == "missing-parent":
        path = tmp_path / "missing-parent" / "case.json"
    else:
        parent = tmp_path / "not-a-directory"
        parent.write_bytes(b"secret parent data")
        path = parent / "case.json"
    code, error = invoke(path)
    assert code == INVOCATION_EXIT
    assert error == {
        "schema": "artifact_inspection_error.v1",
        "code": "SOURCE_READ_FAILED",
        "reason": "SOURCE_READ_FAILED",
        "location": "",
        "next_action": "PROVIDE_REGULAR_LOCAL_FILE",
        "retryable": False,
        "exit_code": 2,
    }


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("wrong-contract", "WRONG_OBSERVATION_CONTRACT"),
        ("broken-reference", "OBSERVATION_SCHEMA_EVALUATION_FAILED"),
    ],
)
def test_broken_observation_resource_is_invocation_failure_after_valid_manifest(
    tmp_path: Path,
    fault: str,
    reason: str,
) -> None:
    raw = (
        b'{"schema_version":"underwrite.evidence-bundle.v1",'
        b'"manifest":{"run_id":"example"},'
        b'"sources":[],"readings":[],"availability":[]}'
    )
    path = case(tmp_path, raw)
    update(path, "source", {"format": "underwrite.evidence-bundle", "format_version": "v1"})
    resource = tmp_path / "trusted/_contracts/observation.v1.schema.json"
    document = json.loads(resource.read_bytes())
    if fault == "wrong-contract":
        document["$id"] = "urn:secret-invalid-contract"
    else:
        document["$ref"] = "#/$defs/secret-missing-reference"
    resource.write_text(json.dumps(document))
    code, error = invoke(path)
    assert code == INVOCATION_EXIT
    assert error == {
        "schema": "artifact_inspection_error.v1",
        "code": "SCHEMA_CONFIGURATION_ERROR",
        "reason": reason,
        "location": "",
        "next_action": "REINSTALL_PACKAGE",
        "retryable": False,
        "exit_code": 2,
    }


def test_structurally_invalid_manifest_has_exact_safe_reason(tmp_path: Path) -> None:
    path = case(tmp_path)
    document = json.loads(path.read_bytes())
    document["unknown-secret-field"] = "secret"
    path.write_text(json.dumps(document))
    code, error = invoke(path)
    assert code == INVOCATION_EXIT
    assert error == {
        "schema": "artifact_inspection_error.v1",
        "code": "INVALID_MANIFEST",
        "reason": "INVALID_MANIFEST",
        "location": "",
        "next_action": "CORRECT_MANIFEST",
        "retryable": False,
        "exit_code": 2,
    }


@pytest.mark.parametrize(
    "body,reason",
    [
        (b"{", "INVALID_JSON_INPUT"),
        (b'{"x":1,"x":2}', "DUPLICATE_JSON_KEY"),
        (b'{"x":NaN}', "NON_FINITE_JSON_NUMBER"),
        (b'"\\ud800"', "INVALID_JSON_INPUT"),
        (b"\xff", "INVALID_JSON_INPUT"),
        (b"[" * 65 + b"]" * 65, "DEPTH_LIMIT_EXCEEDED"),
    ],
)
def test_complete_malformed_artifact_has_hash_but_not_observation(
    tmp_path: Path,
    body: bytes,
    reason: str,
) -> None:
    code, report = invoke(case(tmp_path, body))
    assert code == 1 and report["raw_hash"] == digest_bytes(body)
    assert report["observation"] is None
    assert report["diagnostics"][0]["reason"] == reason


@pytest.mark.parametrize("body", [b"{", b'{"schema":1,"schema":2}', b"\xff", b'"\\ud800"'])
def test_invalid_manifest_is_not_completed_report(
    tmp_path: Path,
    body: bytes,
) -> None:
    path = case(tmp_path)
    path.write_bytes(body)
    code, error = invoke(path)
    assert code == INVOCATION_EXIT and error["code"] == "INVALID_MANIFEST"
    assert "raw_hash" not in error


def test_oversize_artifact_does_not_hash_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inspection, "DEFAULT_MAX_BYTES", 4)
    code, report = invoke(case(tmp_path, b"null"))
    assert code == 1 and report["raw_hash"] == digest_bytes(b"null")
    assert report["observation"] is None
    code, error = invoke(case(tmp_path, b"12345"))
    assert code == INVOCATION_EXIT and error["code"] == "INPUT_LIMIT_EXCEEDED"
    assert "raw_hash" not in error and error["reason"] == "BYTE_LIMIT_EXCEEDED"
    assert error["location"] == "/artifact/path"
    assert error["next_action"] == "CORRECT_ARTIFACT"


@pytest.mark.parametrize(
    "relative", ["../outside", "/absolute", "a//b", "./a", "a/../b", "a\\b", "file:x", "a\x00b"]
)
def test_artifact_path_grammar(
    tmp_path: Path,
    relative: str,
) -> None:
    path = case(tmp_path)
    update(path, "artifact", {"path": relative})
    code, error = invoke(path)
    assert code == INVOCATION_EXIT and error["code"] == "INVALID_ARTIFACT_PATH"
    assert error["reason"] == "INVALID_ARTIFACT_PATH"
    assert error["location"] == "/artifact/path"
    assert error["next_action"] == "CORRECT_MANIFEST"


@pytest.mark.parametrize("kind", ["manifest-link", "leaf-link", "parent-link", "directory"])
def test_nonregular_and_symlink_inputs_refused(
    tmp_path: Path,
    kind: str,
) -> None:
    path = case(tmp_path)
    artifact = tmp_path / "artifact.json"
    if kind == "manifest-link":
        link = tmp_path / "link.json"
        link.symlink_to(path)
        path = link
    elif kind == "parent-link":
        (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
        update(path, "artifact", {"path": "link/artifact.json"})
    else:
        artifact.unlink()
        if kind == "leaf-link":
            artifact.symlink_to(path)
        else:
            artifact.mkdir()
    code, error = invoke(path)
    assert code == INVOCATION_EXIT and error["code"] == "SOURCE_READ_FAILED"
    assert error["next_action"] == "PROVIDE_REGULAR_LOCAL_FILE"


@pytest.mark.parametrize("flag", ["O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK", "O_NOCTTY"])
def test_platform_capability_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
) -> None:
    path = case(tmp_path)
    monkeypatch.delattr(os, flag)
    code, error = invoke(path)
    assert code == INVOCATION_EXIT and error["code"] == "UNSUPPORTED_PLATFORM"
    assert error["reason"] == "UNSUPPORTED_PLATFORM"
    assert error["next_action"] == "USE_SUPPORTED_PLATFORM"


def test_filesystem_syscalls_enforce_directory_and_nonblocking_capabilities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check security-relevant open flags while executing real descriptor traversal.

    Missing directory-only opens can block on an intermediate FIFO; missing leaf
    nonblocking/no-controlling-terminal flags can block or acquire terminal state.
    Assert the syscall admission contract rather than hanging a mutation runner.
    """
    path = case(tmp_path, b"intentionally malformed original")
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "artifact.json").rename(nested / "artifact.json")
    update(path, "artifact", {"path": "nested/artifact.json"})
    original_open = os.open
    root_fd: int | None = None
    nested_fd: int | None = None
    seen: list[str] = []

    def secured_open(
        file: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal root_fd, nested_fd
        assert flags & os.O_ACCMODE == os.O_RDONLY
        if file == tmp_path:
            assert flags & os.O_DIRECTORY
            root_fd = original_open(file, flags, mode, dir_fd=dir_fd)
            seen.append("root")
            return root_fd
        if file == "nested":
            assert flags & os.O_DIRECTORY
            assert flags & os.O_NOFOLLOW
            assert root_fd is not None and dir_fd == root_fd
            nested_fd = original_open(file, flags, mode, dir_fd=dir_fd)
            seen.append("nested")
            return nested_fd
        assert file in {"case.json", "artifact.json"}
        assert flags & os.O_NOFOLLOW
        assert flags & os.O_NONBLOCK
        assert flags & os.O_NOCTTY
        expected_parent = root_fd if file == "case.json" else nested_fd
        assert expected_parent is not None and dir_fd == expected_parent
        seen.append(file)
        return original_open(file, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", secured_open)
    monkeypatch.setattr(os, "supports_dir_fd", {*os.supports_dir_fd, secured_open})
    code, report = invoke(path)
    assert code == 1 and report["observation"] is None
    assert seen == ["root", "case.json", "nested", "artifact.json"]


def test_parent_replacement_does_not_redirect_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "original"
    root.mkdir()
    path = case(root, b"original malformed bytes")
    original_manifest = inspection._manifest  # pyright: ignore[reportPrivateUsage]

    def replace(raw: bytes) -> dict[str, object]:
        result = original_manifest(raw)
        root.rename(tmp_path / "moved")
        root.mkdir()
        (root / "artifact.json").write_bytes(b"replacement")
        return result

    monkeypatch.setattr(inspection, "_manifest", replace)
    code, report = invoke(path)
    assert code == 1
    assert report["raw_hash"] == digest_bytes(b"original malformed bytes")


def test_root_swap_before_manifest_read_keeps_both_reads_anchored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "selected"
    root.mkdir()
    original_raw = b"original artifact, intentionally malformed"
    path = case(root, original_raw)
    original_manifest = path.read_bytes()
    original_open = os.open
    swaps: list[str] = []

    def swap_after_anchor(
        file: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(file, flags, mode, dir_fd=dir_fd)
        if file == root:
            root.rename(tmp_path / "anchored")
            root.mkdir()
            replacement = case(root, b"replacement artifact")
            update(replacement, "source", {"ref": "replacement-reference"})
            swaps.append("replaced before manifest open")
        return descriptor

    monkeypatch.setattr(os, "open", swap_after_anchor)
    monkeypatch.setattr(os, "supports_dir_fd", {*os.supports_dir_fd, swap_after_anchor})
    code, report = invoke(path)
    assert code == 1 and swaps == ["replaced before manifest open"]
    assert report["manifest_hash"] == digest_bytes(original_manifest)
    assert report["raw_hash"] == digest_bytes(original_raw)


@pytest.mark.parametrize("failure", [False, True])
def test_intermediate_anchor_and_all_owned_descriptors_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: bool,
) -> None:
    path = case(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    body = b"original nested artifact"
    (nested / "artifact.json").write_bytes(body)
    update(path, "artifact", {"path": "nested/artifact.json"})
    original_open, original_close = os.open, os.close
    opened: list[int] = []
    closed: list[int] = []

    def tracking_open(
        file: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if file == "artifact.json" and failure:
            raise OSError("private filesystem failure")
        descriptor = original_open(file, flags, mode, dir_fd=dir_fd)
        opened.append(descriptor)
        if file == "nested":
            nested.rename(tmp_path / "moved")
            nested.symlink_to(tmp_path, target_is_directory=True)
        return descriptor

    def tracking_close(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "close", tracking_close)
    monkeypatch.setattr(os, "supports_dir_fd", {*os.supports_dir_fd, tracking_open})
    code, result = invoke(path)
    assert sorted(opened) == sorted(closed)
    if failure:
        assert code == INVOCATION_EXIT and result["code"] == "SOURCE_READ_FAILED"
    else:
        assert code == 1 and result["raw_hash"] == digest_bytes(body)


@pytest.mark.parametrize("extra", [False, True])
def test_manifest_byte_bound_and_depth(
    tmp_path: Path,
    extra: bool,
) -> None:
    path = case(tmp_path)
    body = b" " * (inspection.MANIFEST_MAX_BYTES + 1) if extra else b"[" * 17 + b"]" * 17
    path.write_bytes(body)
    code, error = invoke(path)
    assert code == INVOCATION_EXIT and error["code"] == "INPUT_LIMIT_EXCEEDED"
    assert "hash" not in error
    assert error["location"] == "" and error["next_action"] == "CORRECT_MANIFEST"


def test_unknown_source_never_opens_artifact_even_when_file_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = case(tmp_path)
    update(path, "source", {"format": "not-supported"})
    original = os.open

    def guard(file: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        assert file != "artifact.json"
        return original(file, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", guard)
    monkeypatch.setattr(os, "supports_dir_fd", {*os.supports_dir_fd, guard})
    code, error = invoke(path)
    assert code == INVOCATION_EXIT and error["code"] == "UNSUPPORTED_FORMAT"


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("missing", "INVALID_INSPECTION_SCHEMA"),
        ("malformed", "INVALID_INSPECTION_SCHEMA"),
        ("wrong-id", "WRONG_INSPECTION_CONTRACT"),
        ("metaschema", "INVALID_INSPECTION_SCHEMA"),
        ("remote", "NON_LOCAL_SCHEMA_REFERENCE"),
        ("nested", "NESTED_SCHEMA_RESOURCE"),
        ("unresolved", "INSPECTION_SCHEMA_EVALUATION_FAILED"),
    ],
)
def test_case_schema_failure_never_uses_cwd_or_echoes_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    reason: str,
    denied_schema_requests: list[str],
) -> None:
    path = case(tmp_path)
    trusted = tmp_path / "package"
    resources = trusted / "_contracts"
    resources.mkdir(parents=True)
    document = json.loads((ROOT / "contracts/artifact_case.v1.schema.json").read_bytes())
    edits = {
        "wrong-id": {"$id": "urn:secret"},
        "metaschema": {"type": "secret"},
        "remote": {"$ref": "https://secret.invalid"},
        "nested": {"$defs": {"secret": {"$id": "urn:secret"}}},
        "unresolved": {"$ref": "#/$defs/secret"},
    }
    document.update(edits.get(fault, {}))
    if fault != "missing":
        (resources / "artifact_case.v1.schema.json").write_text(
            "secret malformed{" if fault == "malformed" else json.dumps(document)
        )
    # A valid attacker-controlled CWD schema must not repair broken deployed assets.
    (tmp_path / "artifact_case.v1.schema.json").write_bytes(
        (ROOT / "contracts/artifact_case.v1.schema.json").read_bytes()
    )
    monkeypatch.chdir(tmp_path)

    def package_root(name: str) -> Path:
        assert name == "underwrite"
        return trusted

    monkeypatch.setattr(schemas, "files", package_root)
    code, error = invoke(path)
    assert code == INVOCATION_EXIT and error["code"] == "SCHEMA_CONFIGURATION_ERROR"
    assert error["reason"] == reason and error["next_action"] == "REINSTALL_PACKAGE"
    assert "secret" not in json.dumps(error)
    assert denied_schema_requests == []


def test_schema_network_denial_remains_effective_if_local_reference_guard_is_bypassed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    denied_schema_requests: list[str],
) -> None:
    """Exercise the real validator retrieval path without making a network request."""
    path = case(tmp_path)
    resource = tmp_path / "trusted/_contracts/artifact_case.v1.schema.json"
    document = json.loads(resource.read_bytes())
    document["$ref"] = "https://schema-test.invalid/forbidden"
    resource.write_text(json.dumps(document))

    def bypass(schema: dict[str, object]) -> None:
        pass

    monkeypatch.setattr(schemas, "_check_local_references", bypass)
    code, error = invoke(path)
    assert denied_schema_requests == ["https://schema-test.invalid/forbidden"]
    assert code == INVOCATION_EXIT
    assert error["code"] == "SCHEMA_CONFIGURATION_ERROR"
    assert error["reason"] == "INSPECTION_SCHEMA_EVALUATION_FAILED"

"""The wheel smoke gate fails visibly when installation inputs or CLI output drift."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import smoke_wheels


def test_direct_script_help_outside_checkout(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(smoke_wheels.wheel.ROOT / "scripts/smoke_wheels.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    assert "--dist" in result.stdout and "--registry-core" in result.stdout


def test_command_timeout_has_finite_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def timeout(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == smoke_wheels.wheel.COMMAND_TIMEOUT
        raise subprocess.TimeoutExpired(args, smoke_wheels.wheel.COMMAND_TIMEOUT)

    monkeypatch.setattr(smoke_wheels.wheel.subprocess, "run", timeout)
    with pytest.raises(smoke_wheels.wheel.SmokeFailure) as caught:
        smoke_wheels.wheel.command(["uv", "sync"], tmp_path)
    assert str(caught.value) == (
        f"command timed out after {smoke_wheels.wheel.COMMAND_TIMEOUT}s: uv"
    )


def test_local_install_failure_prevents_origin_checks_and_pilots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("underwrite_core", "underwrite"):
        (tmp_path / f"{name}-0.1.1-py3-none-any.whl").write_bytes(b"candidate")
    calls: list[list[str]] = []

    def response(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "cannot install dependencies")

    def forbidden(*args: object) -> None:
        pytest.fail("failed local installation must stop before origin checks or pilots")

    monkeypatch.setattr(smoke_wheels.wheel.subprocess, "run", response)
    monkeypatch.setattr(smoke_wheels.wheel, "check_wheel_origins", forbidden)
    monkeypatch.setattr(smoke_wheels, "_evidence_pilots", forbidden)
    assert smoke_wheels.main(["--dist", str(tmp_path)]) == 1
    assert len(calls) == 1 and calls[0][:2] == ["uv", "sync"]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot install dependencies" in captured.err


def test_default_mode_does_not_select_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[Path] = []

    def local(dist: Path) -> None:
        calls.append(dist)

    def registry(dist: Path) -> None:
        pytest.fail("default smoke must not select registry mode")

    monkeypatch.setattr(smoke_wheels, "_smoke", local)
    monkeypatch.setattr(smoke_wheels, "_registry_core_smoke", registry)
    assert smoke_wheels.main(["--dist", str(tmp_path)]) == 0
    assert calls == [tmp_path.resolve()]
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        "installed wheel native/evidence flows and malformed-input smoke passed\n"
    )


def test_locked_dependency_sync_precedes_app_only_local_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(tmp_path / "main-venv"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "source"))
    calls: list[tuple[list[str], dict[str, str]]] = []

    def record(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, cast(dict[str, str], kwargs["env"])))
        return subprocess.CompletedProcess(
            args, 1 if args[:2] == [str(venv / "bin/python"), "-I"] else 0, "", ""
        )

    monkeypatch.setattr(smoke_wheels.wheel.subprocess, "run", record)
    venv = tmp_path / "smoke-venv"
    wheels = (tmp_path / "core.whl", tmp_path / "app.whl")
    smoke_wheels.wheel.install_wheels(tmp_path, venv, wheels)

    sync, absent, install, check = (args for args, _ in calls)
    assert sync[:2] == ["uv", "sync"]
    assert {"--frozen", "--no-default-groups", "--no-install-workspace", "--project"} <= set(sync)
    assert install[:3] == ["uv", "pip", "install"]
    assert absent[:2] == [str(venv / "bin/python"), "-I"]
    assert {"--offline", "--no-index", "--find-links", str(wheels[1])} <= set(install)
    assert str(wheels[0].parent) in install
    assert str(wheels[0]) not in install
    assert "--no-deps" not in install
    assert check[:3] == ["uv", "pip", "check"]
    assert all(env["UV_PROJECT_ENVIRONMENT"] == str(venv) for _, env in calls)
    assert all("PYTHONPATH" not in env for _, env in calls)


def test_origin_guard_rejects_editable_install(tmp_path: Path) -> None:
    version = tomllib.loads(
        (smoke_wheels.wheel.ROOT / "packages/underwrite-core/pyproject.toml").read_text()
    )["project"]["version"]
    venv = tmp_path / "venv"
    site = (
        venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    )
    wheels = (
        tmp_path / f"underwrite_core-{version}-py3-none-any.whl",
        tmp_path / f"underwrite-{version}-py3-none-any.whl",
    )
    for name, wheel in zip(("underwrite_core", "underwrite"), wheels, strict=True):
        metadata = f"Name: {name.replace('_', '-')}\nVersion: {version}\n"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(f"{name}/__init__.py", b"# installed fixture\n")
            archive.writestr(f"{name}-{version}.dist-info/METADATA", metadata)
        package = site / name
        package.mkdir(parents=True)
        (package / "__init__.py").write_bytes(b"# installed fixture\n")
        record = site / f"{name}-{version}.dist-info"
        record.mkdir()
        (record / "METADATA").write_text(metadata)
        if name == "underwrite":
            (record / "direct_url.json").write_text(json.dumps({"url": wheel.as_uri()}))
    smoke_wheels.wheel.check_wheel_origins(venv, wheels)
    core_file = site / "underwrite_core/__init__.py"
    core_file.write_text("# wrong core bytes\n")
    with pytest.raises(smoke_wheels.wheel.SmokeFailure, match="core bytes differ"):
        smoke_wheels.wheel.check_wheel_origins(venv, wheels)
    core_file.write_bytes(b"# installed fixture\n")
    editable_record = site / f"underwrite-{version}.dist-info/direct_url.json"
    editable_record.write_text(
        json.dumps({"url": tmp_path.as_uri(), "dir_info": {"editable": True}}), encoding="utf-8"
    )
    with pytest.raises(smoke_wheels.wheel.SmokeFailure, match="not installed from its built wheel"):
        smoke_wheels.wheel.check_wheel_origins(venv, wheels)


def test_missing_wheel_fails_before_install(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert smoke_wheels.main(["--dist", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "expected one underwrite_core-*.whl" in captured.err


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "failure"),
    [
        (1, "", "CLI failed", "command failed"),
        (0, "not JSON", "", "invalid CLI JSON"),
        (0, "[]", "", "CLI JSON is not an object"),
    ],
)
def test_cli_output_failures_are_not_written_as_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    stderr: str,
    failure: str,
) -> None:
    def response(
        args: list[str],
        cwd: Path,
        *,
        project_environment: Path | None = None,
        registry_only: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    monkeypatch.setattr(smoke_wheels.wheel, "command", response)
    output = tmp_path / "observation.json"
    with pytest.raises(smoke_wheels.wheel.SmokeFailure, match=failure):
        smoke_wheels._cli_json(  # pyright: ignore[reportPrivateUsage]
            tmp_path / "underwrite", ["ingest"], output, tmp_path
        )
    assert not output.exists()


@pytest.mark.parametrize(
    "returncode,stdout,payload",
    [
        (0, "", {"code": "INVALID_READ", "reason": "SEMANTIC_DIGEST_MISMATCH"}),
        (2, "partial", {"code": "INVALID_READ", "reason": "SEMANTIC_DIGEST_MISMATCH"}),
        (2, "", {"code": "INVALID_READ", "reason": "OTHER"}),
        (2, "", []),
    ],
)
def test_native_refusal_gate_rejects_wrong_exit_output_or_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    payload: object,
) -> None:
    def response(
        args: list[str],
        cwd: Path,
        *,
        project_environment: Path | None = None,
        registry_only: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, returncode, stdout, json.dumps(payload))

    monkeypatch.setattr(smoke_wheels.wheel, "command", response)
    with pytest.raises(smoke_wheels.wheel.SmokeFailure):
        smoke_wheels._native_refusal(  # pyright: ignore[reportPrivateUsage]
            tmp_path / "cli",
            ["accept"],
            tmp_path,
            "INVALID_READ",
            "SEMANTIC_DIGEST_MISMATCH",
        )


def test_registry_core_install_has_no_local_dependency_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UV_INDEX", "https://untrusted.invalid/simple")
    monkeypatch.setenv("UV_FIND_LINKS", str(tmp_path / "local-core"))
    monkeypatch.setenv("PIP_INDEX_URL", "https://untrusted.invalid/simple")
    monkeypatch.setenv("UV_CONSTRAINT", str(tmp_path / "constraint.txt"))
    monkeypatch.setenv("PYTHONPATH", str(smoke_wheels.wheel.ROOT))
    calls: list[tuple[list[str], dict[str, str]]] = []
    checked: list[tuple[Path, tuple[Path, Path]]] = []
    venv, app = tmp_path / "venv", tmp_path / "underwrite-0.1.1-py3-none-any.whl"
    core = tmp_path / "registry-core.whl"

    def record(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, cast(dict[str, str], kwargs["env"])))
        return subprocess.CompletedProcess(
            args, 0, "0.1.1\n" if args[0] == str(venv / "bin/python") else "", ""
        )

    def published(work: Path, version: str) -> tuple[Path, dict[str, object]]:
        assert work == tmp_path and version == "0.1.1"
        return core, {"version": version}

    monkeypatch.setattr(smoke_wheels.wheel.subprocess, "run", record)
    monkeypatch.setattr(smoke_wheels.wheel, "published_core", published)

    def origin(venv: Path, wheels: tuple[Path, Path]) -> None:
        checked.append((venv, wheels))

    monkeypatch.setattr(smoke_wheels.wheel, "check_wheel_origins", origin)
    smoke_wheels.wheel.install_registry_core(tmp_path, venv, app)
    create, install, check, version = (args for args, _ in calls)
    assert create[:4] == ["uv", "--no-config", "--no-cache", "venv"]
    assert {
        "--no-config",
        "--no-cache",
        "--no-sources",
        "--default-index",
        smoke_wheels.wheel.REGISTRY_INDEX,
        str(app),
    } <= set(install)
    assert not {"--find-links", "--no-deps", "--offline", "--no-index"} & set(install)
    assert str(core) not in install
    assert check[:4] == ["uv", "--no-config", "pip", "check"]
    assert version[:2] == [str(venv / "bin/python"), "-I"]
    assert all(not any(key.startswith(("UV_", "PIP_")) for key in env) for _, env in calls)
    assert all("PYTHONPATH" not in env for _, env in calls)
    assert checked == [(venv, (core, app))]


def test_registry_install_failure_prevents_fetch_and_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "underwrite-0.1.1-py3-none-any.whl").write_bytes(b"candidate")
    calls: list[list[str]] = []

    def response(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 1 if "install" in args else 0, "", "no compatible registry core"
        )

    def forbidden(*args: object) -> None:
        pytest.fail("failed dependency resolution must stop before origin fetch or tasks")

    monkeypatch.setattr(smoke_wheels.wheel.subprocess, "run", response)
    monkeypatch.setattr(smoke_wheels.wheel, "published_core", forbidden)
    monkeypatch.setattr(smoke_wheels, "_native_measurement_pilot", forbidden)
    assert smoke_wheels.main(["--dist", str(tmp_path), "--registry-core"]) == 1
    assert ["venv" in args for args in calls] == [True, False]
    assert "install" in calls[-1]


@pytest.mark.parametrize("fault", ["hash", "name", "version", "missing"])
def test_published_core_identity_failure_is_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    metadata: dict[str, Any] = {
        "info": {"name": "underwrite-core", "version": "0.1.1"},
        "urls": [
            {
                "packagetype": "bdist_wheel",
                "filename": "underwrite_core-0.1.1-py3-none-any.whl",
                "url": "https://files.pythonhosted.org/core.whl",
                "digests": {"sha256": "wrong"},
            }
        ],
    }
    if fault == "name":
        metadata["info"]["name"] = "other"
    elif fault == "version":
        metadata["info"]["version"] = "0.0.0"
    elif fault == "missing":
        metadata["urls"] = []

    def read(url: str) -> bytes:
        return json.dumps(metadata).encode() if url.endswith("/json") else b"wrong archive"

    monkeypatch.setattr(smoke_wheels.wheel, "registry_bytes", read)
    with pytest.raises(smoke_wheels.wheel.SmokeFailure):
        smoke_wheels.wheel.published_core(tmp_path, "0.1.1")
    assert list(tmp_path.glob("*.whl")) == []


def test_registry_download_disallows_alternate_host() -> None:
    with pytest.raises(smoke_wheels.wheel.SmokeFailure, match="unexpected registry artifact URL"):
        smoke_wheels.wheel.registry_bytes("https://untrusted.invalid/core.whl")


@pytest.mark.parametrize("raw", [b"{}", b"[]", b"null", b"not JSON"])
def test_malformed_registry_metadata_has_finite_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
) -> None:
    def read(url: str) -> bytes:
        return raw

    monkeypatch.setattr(smoke_wheels.wheel, "registry_bytes", read)
    with pytest.raises(
        smoke_wheels.wheel.SmokeFailure, match="invalid published core metadata or archive"
    ):
        smoke_wheels.wheel.published_core(tmp_path, "0.1.1")


def test_registry_redirect_is_refused_before_following(monkeypatch: pytest.MonkeyPatch) -> None:
    from http.client import HTTPMessage
    from io import BytesIO
    from urllib.request import HTTPRedirectHandler, Request

    requested: list[str] = []

    class RedirectResponse:
        def __init__(self, handler: HTTPRedirectHandler) -> None:
            self.handler = handler

        def open(self, url: str, *, timeout: int) -> None:
            requested.append(url)
            headers = HTTPMessage()
            headers["Location"] = "https://untrusted.invalid/core.whl"
            self.handler.http_error_302(Request(url), BytesIO(), 302, "Found", headers)
            pytest.fail("redirect must stop before another request")

    def opener(handler: HTTPRedirectHandler) -> RedirectResponse:
        return RedirectResponse(handler)

    monkeypatch.setattr(smoke_wheels.wheel.urllib.request, "build_opener", opener)
    with pytest.raises(
        smoke_wheels.wheel.SmokeFailure, match="registry redirects are not accepted"
    ):
        smoke_wheels.wheel.registry_bytes("https://pypi.org/pypi/underwrite-core/0.1.1/json")
    assert requested == ["https://pypi.org/pypi/underwrite-core/0.1.1/json"]

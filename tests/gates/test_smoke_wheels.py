"""The wheel smoke gate fails visibly when installation inputs or CLI output drift."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import cast

import pytest

from scripts import smoke_wheels


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

    monkeypatch.setattr(smoke_wheels.subprocess, "run", record)
    venv = tmp_path / "smoke-venv"
    wheels = (tmp_path / "core.whl", tmp_path / "app.whl")
    smoke_wheels._install_wheels(tmp_path, venv, wheels)  # pyright: ignore[reportPrivateUsage]

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
        (smoke_wheels.ROOT / "packages/underwrite-core/pyproject.toml").read_text()
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
    smoke_wheels._check_wheel_origins(venv, wheels)  # pyright: ignore[reportPrivateUsage]
    core_file = site / "underwrite_core/__init__.py"
    core_file.write_text("# wrong core bytes\n")
    with pytest.raises(smoke_wheels.SmokeFailure, match="core bytes differ"):
        smoke_wheels._check_wheel_origins(venv, wheels)  # pyright: ignore[reportPrivateUsage]
    core_file.write_bytes(b"# installed fixture\n")
    editable_record = site / f"underwrite-{version}.dist-info/direct_url.json"
    editable_record.write_text(
        json.dumps({"url": tmp_path.as_uri(), "dir_info": {"editable": True}}), encoding="utf-8"
    )
    with pytest.raises(smoke_wheels.SmokeFailure, match="not installed from its built wheel"):
        smoke_wheels._check_wheel_origins(venv, wheels)  # pyright: ignore[reportPrivateUsage]


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
        args: list[str], cwd: Path, *, project_environment: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    monkeypatch.setattr(smoke_wheels, "_command", response)
    output = tmp_path / "observation.json"
    with pytest.raises(smoke_wheels.SmokeFailure, match=failure):
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
        args: list[str], cwd: Path, *, project_environment: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, returncode, stdout, json.dumps(payload))

    monkeypatch.setattr(smoke_wheels, "_command", response)
    with pytest.raises(smoke_wheels.SmokeFailure):
        smoke_wheels._native_refusal(  # pyright: ignore[reportPrivateUsage]
            tmp_path / "cli",
            ["accept"],
            tmp_path,
            "INVALID_READ",
            "SEMANTIC_DIGEST_MISMATCH",
        )

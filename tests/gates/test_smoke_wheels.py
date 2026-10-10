"""The wheel smoke gate fails visibly when installation inputs or CLI output drift."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from scripts import smoke_wheels


def test_locked_dependency_sync_precedes_offline_wheels_in_temp_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(tmp_path / "main-venv"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "source"))
    calls: list[tuple[list[str], dict[str, str]]] = []

    def record(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, cast(dict[str, str], kwargs["env"])))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(smoke_wheels.subprocess, "run", record)
    venv = tmp_path / "smoke-venv"
    wheels = (tmp_path / "core.whl", tmp_path / "app.whl")
    smoke_wheels._install_wheels(tmp_path, venv, wheels)  # pyright: ignore[reportPrivateUsage]

    sync, install, check = (args for args, _ in calls)
    assert sync[:2] == ["uv", "sync"]
    assert {"--frozen", "--no-default-groups", "--no-install-workspace", "--project"} <= set(sync)
    assert install[:3] == ["uv", "pip", "install"]
    assert {"--offline", "--no-deps", str(wheels[0]), str(wheels[1])} <= set(install)
    assert check[:3] == ["uv", "pip", "check"]
    assert all(env["UV_PROJECT_ENVIRONMENT"] == str(venv) for _, env in calls)
    assert all("PYTHONPATH" not in env for _, env in calls)


def test_origin_guard_rejects_editable_install(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    site = (
        venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    )
    wheels = (tmp_path / "underwrite_core.whl", tmp_path / "underwrite.whl")
    for name, wheel in zip(("underwrite_core", "underwrite"), wheels, strict=True):
        record = site / f"{name}-0.0.1.dist-info/direct_url.json"
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"url": wheel.as_uri()}), encoding="utf-8")
    smoke_wheels._check_wheel_origins(venv, wheels)  # pyright: ignore[reportPrivateUsage]
    editable_record = site / "underwrite-0.0.1.dist-info/direct_url.json"
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

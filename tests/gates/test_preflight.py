"""The local preflight selector never turns uncertain changes into a docs shortcut."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import preflight

FAILED_STEP_EXIT = 7
INTERRUPTED_EXIT = 130


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Preflight Test")
    (tmp_path / "README.md").write_text("original\n")
    (tmp_path / "CONTRIBUTING.md").write_text("original\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "baseline")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


def test_auto_includes_committed_staged_worktree_and_untracked_docs(
    repo: tuple[Path, str]
) -> None:
    root, base = repo
    (root / "SECURITY.md").write_text("published\n")
    _git(root, "add", "SECURITY.md")
    _git(root, "commit", "-qm", "docs")
    (root / "CONTRIBUTING.md").write_text("staged\n")
    _git(root, "add", "CONTRIBUTING.md")
    (root / "README.md").write_text("worktree\n")
    (root / "AGENT_USAGE.md").write_text("untracked\n")

    selection = preflight.select(root, base)
    assert selection.mode == "docs"
    assert selection.reason == "allowlisted_docs_only"
    assert {change.path for change in selection.changes} == {
        "SECURITY.md",
        "CONTRIBUTING.md",
        "README.md",
        "AGENT_USAGE.md",
    }


@pytest.mark.parametrize("kind", ["committed", "staged", "worktree", "untracked"])
def test_code_at_every_diff_surface_selects_full(repo: tuple[Path, str], kind: str) -> None:
    root, base = repo
    source = root / "src/code.py"
    source.parent.mkdir()
    if kind == "worktree":
        source.write_text("original\n")
        _git(root, "add", ".")
        _git(root, "commit", "-qm", "add code")
        base = _git(root, "rev-parse", "HEAD")
        source.write_text("edited\n")
    else:
        source.write_text("new\n")
        if kind in {"staged", "committed"}:
            _git(root, "add", "src/code.py")
        if kind == "committed":
            _git(root, "commit", "-qm", "code")
    assert preflight.select(root, base).mode == "full"


def test_missing_base_and_empty_change_set_select_full(repo: tuple[Path, str]) -> None:
    root, base = repo
    assert preflight.select(root, "missing-ref").reason == "base_unavailable"
    assert preflight.select(root, base).reason == "no_changes"


def test_malformed_change_output_falls_back_to_full(
    repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, base = repo
    original = preflight._git  # pyright: ignore[reportPrivateUsage]

    def malformed(directory: Path, *args: str) -> bytes | None:
        return b"M\0README.md" if args[0] == "diff" else original(directory, *args)

    monkeypatch.setattr(preflight, "_git", malformed)
    assert preflight.select(root, base).reason == "change_scan_failed"


@pytest.mark.parametrize("kind", ["delete", "rename", "symlink", "unknown_markdown"])
def test_unsafe_doc_shapes_select_full(repo: tuple[Path, str], kind: str) -> None:
    root, base = repo
    if kind == "delete":
        (root / "README.md").unlink()
    elif kind == "rename":
        (root / "README.md").rename(root / "SECURITY.md")
        _git(root, "add", "-A")
    elif kind == "symlink":
        (root / "AGENT_USAGE.md").symlink_to("README.md")
    else:
        (root / "OTHER.md").write_text("unknown\n")
    assert preflight.select(root, base).mode == "full"


def test_plans_preserve_full_gates_and_doc_package_checks() -> None:
    full = preflight.steps_for("full", "dist")
    docs = preflight.steps_for("docs", "dist")
    assert [step.name for step in full] == [
        "sync",
        "ruff",
        "pyright",
        "lint_imports",
        "deptry",
        "pytest",
        "build",
        "wheel_smoke",
        "pure_sync",
        "pure_pytest",
    ]
    assert [step.name for step in docs] == ["sync", "doc_gates", "build", "wheel_smoke"]
    assert "tests/gates/test_licensing.py" in docs[1].argv
    assert "tests/gates/test_packaged_contracts.py" in docs[1].argv
    assert full[-1].pure and full[-2].pure
    assert all(step.timeout_seconds > 0 for step in (*full, *docs))


@pytest.mark.parametrize("pure,expected", [(False, ".venv"), (True, ".venv-pure")])
def test_steps_ignore_ambient_pytest_filter_and_pin_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pure: bool, expected: str
) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k nonexistent")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "unrelated")
    destination = tmp_path / "environment.json"
    script = (
        "import json,os,sys,pathlib; "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
        "'pytest_addopts':os.environ.get('PYTEST_ADDOPTS'),"
        "'uv_project_environment':os.environ.get('UV_PROJECT_ENVIRONMENT')}))"
    )
    step = preflight.Step("environment", (sys.executable, "-c", script, str(destination)), 5, pure)
    assert preflight._execute_step(step, tmp_path)[0] == "passed"  # pyright: ignore[reportPrivateUsage]
    assert json.loads(destination.read_text()) == {
        "pytest_addopts": None,
        "uv_project_environment": expected,
    }


def test_failure_overwrites_stale_success_and_leaves_later_steps_not_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steps = (
        preflight.Step("first", ("true",), 10),
        preflight.Step("second", ("false",), 10),
        preflight.Step("third", ("true",), 10),
    )
    report_path = tmp_path / "preflight.json"
    report_path.write_text('{"status":"passed"}')
    seen: list[str] = []

    def fake_execute(step: preflight.Step, root: Path) -> tuple[str, int | None, float]:
        seen.append(step.name)
        return ("failed", FAILED_STEP_EXIT, 0.1) if step.name == "second" else ("passed", 0, 0.1)

    monkeypatch.setattr(preflight, "_execute_step", fake_execute)
    report = preflight.make_report(
        "full", "origin/main", preflight.Selection("full", "forced_full", (), None, None), steps
    )
    assert preflight.run_steps(tmp_path, report_path, report, steps) == 1
    saved = json.loads(report_path.read_text())
    assert saved["status"] == "failed"
    assert [step["status"] for step in saved["steps"]] == ["passed", "failed", "not_run"]
    assert saved["steps"][1]["returncode"] == FAILED_STEP_EXIT
    assert seen == ["first", "second"]


def test_plan_never_executes_or_writes_report(
    repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, base = repo
    (root / "AGENT_USAGE.md").write_text("docs\n")
    monkeypatch.setattr(preflight, "ROOT", root)
    monkeypatch.setattr(preflight, "REPORT", root / "output/preflight.json")
    assert preflight.main(["--base", base, "--plan"]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["status"] == "planned"
    assert planned["selected_mode"] == "docs"
    assert all(step["status"] == "not_run" for step in planned["steps"])
    assert not (root / "output").exists()


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
@pytest.mark.parametrize("finish", ["failure", "timeout"])
def test_failed_or_timed_out_step_stops_child_processes(tmp_path: Path, finish: str) -> None:
    heartbeat = tmp_path / "heartbeat"
    child_code = (
        "from pathlib import Path\nimport sys,time\np=Path(sys.argv[1])\n"
        "while True:\n p.write_text(str(time.time_ns()))\n time.sleep(0.02)\n"
    )
    parent_code = (
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        f"child_code={child_code!r}\n"
        "subprocess.Popen([sys.executable,'-c',child_code,sys.argv[1]])\n"
        "while not Path(sys.argv[1]).exists(): time.sleep(0.01)\n"
        "if sys.argv[2]=='failure': raise SystemExit(9)\n"
        "time.sleep(30)\n"
    )
    step = preflight.Step(
        "child_cleanup", (sys.executable, "-c", parent_code, str(heartbeat), finish), 1
    )
    status, _, _ = preflight._execute_step(step, tmp_path)  # pyright: ignore[reportPrivateUsage]
    assert status == ("failed" if finish == "failure" else "timed_out")
    before = heartbeat.read_text()
    time.sleep(0.2)
    assert heartbeat.read_text() == before


def test_interrupted_step_is_reported_as_nonpass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steps = (preflight.Step("interrupted", ("ignored",), 5),)

    def interrupted(step: preflight.Step, root: Path) -> tuple[str, int | None, float]:
        return "interrupted", None, 0.1

    monkeypatch.setattr(preflight, "_execute_step", interrupted)
    report = preflight.make_report(
        "full", "origin/main", preflight.Selection("full", "forced_full", (), None, None), steps
    )
    report_path = tmp_path / "preflight.json"
    assert preflight.run_steps(tmp_path, report_path, report, steps) == INTERRUPTED_EXIT
    saved = json.loads(report_path.read_text())
    assert saved["status"] == saved["steps"][0]["status"] == "interrupted"

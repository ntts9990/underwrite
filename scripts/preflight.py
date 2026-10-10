"""Run the repository checks locally or in CI; the report is informational, not proof."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Literal, NamedTuple, TypedDict

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "output/preflight.json"
DOC_PATHS = frozenset(
    {
        "AGENT_USAGE.md",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "LICENSING.md",
        "README.ko.md",
        "README.md",
        "SECURITY.md",
        "TRADEMARKS.md",
        "packages/underwrite-core/README.md",
    }
)
GIT_TIMEOUT = 10


class Change(NamedTuple):
    status: str
    path: str


class Selection(NamedTuple):
    mode: Literal["docs", "full"]
    reason: str
    changes: tuple[Change, ...]
    head: str | None
    merge_base: str | None


class Step(NamedTuple):
    name: str
    argv: tuple[str, ...]
    timeout_seconds: int
    pure: bool = False


class StepRecord(TypedDict):
    name: str
    argv: list[str]
    timeout_seconds: int
    pure: bool
    status: str
    returncode: int | None
    duration_seconds: float | None


class ReportData(TypedDict):
    schema: str
    requested_mode: str
    selected_mode: str
    selection_reason: str
    base: str
    head: str | None
    merge_base: str | None
    changed_paths: list[str]
    status: str
    steps: list[StepRecord]


def _git(root: Path, *args: str) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, check=False, timeout=GIT_TIMEOUT
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _name_status(data: bytes) -> list[Change] | None:
    if not data:
        return []
    fields = data.split(b"\0")
    if fields[-1] != b"" or (len(fields) - 1) % 2:
        return None
    changes: list[Change] = []
    for index in range(0, len(fields) - 1, 2):
        try:
            status = fields[index].decode("ascii")
        except UnicodeDecodeError:
            return None
        changes.append(Change(status, os.fsdecode(fields[index + 1])))
    return changes


def _untracked(data: bytes) -> list[Change] | None:
    if not data:
        return []
    fields = data.split(b"\0")
    if fields[-1] != b"":
        return None
    return [Change("A", os.fsdecode(path)) for path in fields[:-1]]


def _scan_changes(root: Path, merge_base: str, head: str) -> tuple[Change, ...] | None:
    all_changes: list[Change] = []
    for args in (
        ("diff", "--name-status", "-z", "--no-renames", merge_base, head),
        ("diff", "--name-status", "-z", "--no-renames", "--cached"),
        ("diff", "--name-status", "-z", "--no-renames"),
    ):
        data = _git(root, *args)
        changes = None if data is None else _name_status(data)
        if changes is None:
            return None
        all_changes.extend(changes)
    data = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    untracked = None if data is None else _untracked(data)
    if untracked is None:
        return None
    all_changes.extend(untracked)
    return tuple(all_changes)


def _classify_changes(
    root: Path, changes: tuple[Change, ...], head: str, merge_base: str
) -> Selection:
    if not changes:
        return Selection("full", "no_changes", (), head, merge_base)
    for change in changes:
        path = root / change.path
        if (
            change.status not in {"A", "M"}
            or change.path not in DOC_PATHS
            or path.is_symlink()
            or not path.is_file()
        ):
            return Selection("full", "non_doc_or_unsafe_change", changes, head, merge_base)
    return Selection("docs", "allowlisted_docs_only", changes, head, merge_base)


def select(root: Path, base: str) -> Selection:
    head_bytes = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
    head = head_bytes.decode("ascii").strip() if head_bytes else None
    if head is None:
        return Selection("full", "head_unavailable", (), None, None)
    base_bytes = _git(root, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}")
    if base_bytes is None:
        return Selection("full", "base_unavailable", (), head, None)
    base_commit = base_bytes.decode("ascii").strip()
    merge_bytes = _git(root, "merge-base", base_commit, head)
    if merge_bytes is None:
        return Selection("full", "merge_base_unavailable", (), head, None)
    merge_base = merge_bytes.decode("ascii").strip()
    changes = _scan_changes(root, merge_base, head)
    if changes is None:
        return Selection("full", "change_scan_failed", (), head, merge_base)
    return _classify_changes(root, changes, head, merge_base)


def steps_for(mode: Literal["docs", "full"], dist: str) -> tuple[Step, ...]:
    uv = ("uv", "run", "--frozen", "--no-sync")
    sync = Step("sync", ("uv", "sync", "--frozen", "--all-groups"), 600)
    build = Step("build", ("uv", "build", "--all-packages", "--out-dir", dist), 600)
    smoke = Step("wheel_smoke", (sys.executable, "scripts/smoke_wheels.py", "--dist", dist), 600)
    if mode == "docs":
        return (
            sync,
            Step(
                "doc_gates",
                (
                    *uv,
                    "python",
                    "-m",
                    "pytest",
                    "tests/gates/test_licensing.py",
                    "tests/gates/test_packaged_contracts.py",
                ),
                600,
            ),
            build,
            smoke,
        )
    return (
        sync,
        Step("ruff", (*uv, "ruff", "check"), 300),
        Step("pyright", (*uv, "pyright"), 600),
        Step("lint_imports", (*uv, "lint-imports"), 300),
        Step("deptry", (*uv, "deptry", "."), 300),
        Step("pytest", (*uv, "python", "-m", "pytest"), 900),
        build,
        smoke,
        Step("pure_sync", ("uv", "sync", "--frozen", "--only-group", "pure"), 600, True),
        Step(
            "pure_pytest",
            (
                *uv,
                "python",
                "-I",
                "-m",
                "pytest",
                "tests/unit/pure",
                "packages/underwrite-core/tests",
            ),
            600,
            True,
        ),
    )


def make_report(
    requested: str, base: str, selection: Selection, steps: tuple[Step, ...]
) -> ReportData:
    return {
        "schema": "preflight_result.v1",
        "requested_mode": requested,
        "selected_mode": selection.mode,
        "selection_reason": selection.reason,
        "base": base,
        "head": selection.head,
        "merge_base": selection.merge_base,
        "changed_paths": sorted({change.path for change in selection.changes}),
        "status": "planned",
        "steps": [
            {
                "name": step.name,
                "argv": list(step.argv),
                "timeout_seconds": step.timeout_seconds,
                "pure": step.pure,
                "status": "not_run",
                "returncode": None,
                "duration_seconds": None,
            }
            for step in steps
        ],
    }


def _write_report(path: Path, report: ReportData) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as out:
        temporary = Path(out.name)
        json.dump(report, out, sort_keys=True, separators=(",", ":"))
        out.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _stop_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()
    process.wait()


def _execute_step(step: Step, root: Path) -> tuple[str, int | None, float]:
    env = os.environ.copy()
    env["UV_PROJECT_ENVIRONMENT"] = ".venv-pure" if step.pure else ".venv"
    env.pop("PYTEST_ADDOPTS", None)
    start = time.monotonic()
    print(f"preflight: {step.name}: {' '.join(step.argv)}", flush=True)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            step.argv, cwd=root, env=env, start_new_session=os.name == "posix"
        )
        returncode = process.wait(timeout=step.timeout_seconds)
    except subprocess.TimeoutExpired:
        if process is not None:
            _stop_group(process)
        return "timed_out", None, round(time.monotonic() - start, 3)
    except KeyboardInterrupt:
        if process is not None:
            _stop_group(process)
        return "interrupted", None, round(time.monotonic() - start, 3)
    except OSError:
        return "failed", None, round(time.monotonic() - start, 3)
    if returncode != 0:
        _stop_group(process)
    return (
        "passed" if returncode == 0 else "failed",
        returncode,
        round(time.monotonic() - start, 3),
    )


def run_steps(root: Path, report_path: Path, report: ReportData, steps: tuple[Step, ...]) -> int:
    report["status"] = "running"
    _write_report(report_path, report)
    for index, step in enumerate(steps):
        status, returncode, elapsed = _execute_step(step, root)
        record = report["steps"][index]
        record.update(status=status, returncode=returncode, duration_seconds=elapsed)
        if status != "passed":
            report["status"] = "interrupted" if status == "interrupted" else "failed"
            _write_report(report_path, report)
            return 130 if status == "interrupted" else 1
        _write_report(report_path, report)
    report["status"] = "passed"
    _write_report(report_path, report)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("auto", "full"), default="auto")
    parser.add_argument("--base", default="origin/main", help="Diff base for auto mode.")
    parser.add_argument(
        "--plan", action="store_true", help="Show selected checks without running them."
    )
    args = parser.parse_args(argv)
    selection = (
        Selection("full", "forced_full", (), None, None)
        if args.mode == "full"
        else select(ROOT, args.base)
    )
    if args.plan:
        report = make_report(
            args.mode, args.base, selection, steps_for(selection.mode, "<temporary-dist>")
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    try:
        with tempfile.TemporaryDirectory(prefix="underwrite-preflight-") as dist:
            steps = steps_for(selection.mode, dist)
            report = make_report(args.mode, args.base, selection, steps)
            result = run_steps(ROOT, REPORT, report, steps)
    except OSError:
        print("preflight: unable to write report or create build directory", file=sys.stderr)
        return 2
    print(f"preflight: {report['status']} ({selection.mode}); report: {REPORT}", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

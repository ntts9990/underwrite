"""Standalone behavior and boundary checks."""

from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
from pathlib import Path

from _paths import core_root, repo_root
from _verify_loader import load_module_from_path

REPO_ROOT = repo_root()
CORE_DIR = core_root()
CORE_SRC = CORE_DIR / "src" / "underwrite_core"
GEN_SCRIPT = CORE_DIR / "build" / "gen_verify.py"
VERIFY_PY = CORE_DIR / "build" / "verify.py"
_CORE_MODULES: tuple[str, ...] = ("canonical.py", "chain.py", "bundle.py")


def _run_gen_verify(*extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "--frozen", "--no-sync", "python", str(GEN_SCRIPT), *extra_args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


# --- (a) drift: regenerating reproduces the committed file byte-for-byte ---


def test_committed_verify_py_exists() -> None:
    assert VERIFY_PY.is_file()


def test_regenerating_into_a_temp_path_matches_the_committed_file(tmp_path: Path) -> None:
    out_path = tmp_path / "verify.py"
    result = _run_gen_verify("--out", str(out_path))
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert out_path.read_bytes() == VERIFY_PY.read_bytes()


def test_check_flag_reports_no_drift_against_the_committed_file() -> None:
    result = _run_gen_verify("--out", str(VERIFY_PY), "--check")
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "no drift" in result.stdout


def test_check_flag_reports_drift_against_a_deliberately_wrong_file(tmp_path: Path) -> None:
    wrong = tmp_path / "verify.py"
    wrong.write_bytes(b"# not the generated file\n")
    result = _run_gen_verify("--out", str(wrong), "--check")
    assert result.returncode == 1
    assert "DRIFT" in result.stderr


# --- (b) SOURCE_DIGESTS matches the current source modules -----------------


def test_source_digests_match_current_source_sha256() -> None:
    verify_gen = load_module_from_path("verify_gen_drift", VERIFY_PY)
    digests = verify_gen.SOURCE_DIGESTS
    for name in _CORE_MODULES:
        source_bytes = (CORE_SRC / name).read_bytes()
        expected = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
        assert digests[name] == expected


# --- (c) import hygiene: stdlib only, nothing named underwrite -------------


def _top_level_import_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0 or node.module is None:
                continue
            names.add(node.module.split(".")[0])
    return names


def test_verify_py_imports_stdlib_only() -> None:
    allowed = set(sys.stdlib_module_names)
    imported = _top_level_import_modules(VERIFY_PY)
    disallowed = imported - allowed
    assert not disallowed, f"non-stdlib imports in verify.py: {disallowed}"


def test_verify_py_imports_nothing_starting_with_underwrite() -> None:
    imported = _top_level_import_modules(VERIFY_PY)
    offenders = {name for name in imported if name.startswith("underwrite")}
    assert not offenders, f"underwrite-prefixed imports in verify.py: {offenders}"


def test_gen_verify_py_also_imports_stdlib_only() -> None:
    # The generator itself is also a counted, stdlib-only dev tool.
    allowed = set(sys.stdlib_module_names)
    imported = _top_level_import_modules(GEN_SCRIPT)
    disallowed = imported - allowed
    assert not disallowed, f"non-stdlib imports in gen_verify.py: {disallowed}"


# --- (d) runs standalone with no site-packages ------------------------------


def test_verify_py_help_runs_with_no_site_packages() -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(VERIFY_PY), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "verify.py" in result.stdout

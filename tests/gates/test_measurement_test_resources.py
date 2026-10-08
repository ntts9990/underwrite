"""Copied measurement tests discover fixture data from their repository."""

import shutil
import subprocess
import sys
from pathlib import Path

from _repo_paths import repo_root

_COPIED_SUITE_BOUND_SECONDS = 120


def test_copied_read_projection_suite_uses_outer_fixture_data(tmp_path: Path) -> None:
    root = repo_root(Path(__file__).resolve())
    candidate = tmp_path / "candidate"
    copied_tests = candidate / "copied/tests"
    relative = Path("unit/pure/measurement/test_read_projection.py")
    copied_module = copied_tests / relative
    copied_module.parent.mkdir(parents=True)
    shutil.copy2(root / "pyproject.toml", candidate / "pyproject.toml")
    shutil.copy2(root / "tests/_repo_paths.py", copied_tests / "_repo_paths.py")
    shutil.copy2(root / "tests" / relative, copied_module)
    fixtures = Path("fixtures/examples/measurement")
    shutil.copytree(root / fixtures, candidate / fixtures)
    contracts = candidate / "contracts"
    contracts.mkdir()
    for name in ("read.v1.schema.json", "statistical_seed.v1.schema.json"):
        shutil.copy2(root / "contracts" / name, contracts / name)

    # This checks data discovery using real inputs and the entire copied suite.

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            "/dev/null",
            "--rootdir",
            str(candidate / "copied"),
            "-o",
            f"pythonpath={copied_tests}",
            "-q",
            "--tb=short",
            str(copied_module),
        ],
        cwd=candidate / "copied",
        capture_output=True,
        text=True,
        check=False,
        timeout=_COPIED_SUITE_BOUND_SECONDS,
    )
    assert result.returncode == 0, result.stdout + result.stderr

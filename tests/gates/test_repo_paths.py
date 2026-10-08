"""Shared test-root discovery survives copied test directories."""

from pathlib import Path

import pytest
from _repo_paths import repo_root


@pytest.mark.parametrize(
    "relative", ["tests/test_a.py", "copied/tests/test_a.py", "a/b/c/test.py", "."]
)
def test_marker_locates_repository_without_fixed_parent_count(
    tmp_path: Path, relative: str
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "pyproject.toml").write_text("# root marker\n")
    assert repo_root(root / relative) == root


def test_missing_marker_fails_instead_of_guessing(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="repository marker unavailable"):
        repo_root(tmp_path / "a/b/test.py")


def test_existing_repository_marker_is_found() -> None:
    assert (repo_root(Path(__file__).resolve()) / "pyproject.toml").is_file()

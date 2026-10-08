"""Test-only repository path discovery."""

from pathlib import Path


def repo_root(anchor: Path) -> Path:
    """Find the nearest repository manifest without assuming a test folder depth."""
    for candidate in (anchor, *anchor.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(f"repository marker unavailable above {anchor}")

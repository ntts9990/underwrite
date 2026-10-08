"""Standalone behavior and boundary checks."""

from __future__ import annotations

from pathlib import Path

_ROOT_MARKER = "pyproject.toml"


def repo_root() -> Path:
    """Find the repository containing the application and core package."""
    start = Path(__file__).resolve().parent
    for candidate in (start, *start.parents):
        if (candidate / _ROOT_MARKER).is_file() and (candidate / "src/underwrite").is_dir():
            return candidate
    raise RuntimeError(f"could not find {_ROOT_MARKER!r} above {start}")


def core_root() -> Path:
    """Return packages/underwrite-core under the true repository root."""
    return repo_root() / "packages" / "underwrite-core"

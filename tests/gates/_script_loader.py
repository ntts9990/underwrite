"""Load repository utility scripts for their behavior tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def load_module_from_path(name: str, path: Path) -> ModuleType:
    """Load and execute the Python module at `path`, under the module name `name`."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

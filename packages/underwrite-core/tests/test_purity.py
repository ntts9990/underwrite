"""Standalone behavior and boundary checks."""

import ast
import sys
from pathlib import Path

import pytest
import underwrite_core

_PACKAGE_ROOT = Path(underwrite_core.__file__ or "").parent
_MODULE_PATHS = tuple(sorted(_PACKAGE_ROOT.rglob("*.py")))
_MODULE_IDS = tuple(str(path.relative_to(_PACKAGE_ROOT)) for path in _MODULE_PATHS)

# Attribute access: datetime.now/.today, date.today, time.time/.monotonic,
# os.environ. Bare names: open(...), socket, subprocess, random.
_FORBIDDEN_ATTRS = {"now", "today", "time", "monotonic", "environ"}
_FORBIDDEN_NAMES = {"open", "socket", "subprocess", "random"}

_ALLOWED_TOP_LEVEL_IMPORTS = set(sys.stdlib_module_names) | {"underwrite_core"}


@pytest.mark.parametrize("module_path", _MODULE_PATHS, ids=_MODULE_IDS)
def test_module_avoids_wallclock_io_and_non_stdlib_imports(module_path: Path) -> None:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in _FORBIDDEN_ATTRS, f"{module_path.name}: found .{node.attr}"
        elif isinstance(node, ast.Name):
            assert node.id not in _FORBIDDEN_NAMES, f"{module_path.name}: found name {node.id}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top_level = alias.name.split(".")[0]
                assert top_level in _ALLOWED_TOP_LEVEL_IMPORTS, (
                    f"{module_path.name}: disallowed import {alias.name!r}"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0 or node.module is None:
                continue
            top_level = node.module.split(".")[0]
            assert top_level in _ALLOWED_TOP_LEVEL_IMPORTS, (
                f"{module_path.name}: disallowed import {node.module!r}"
            )

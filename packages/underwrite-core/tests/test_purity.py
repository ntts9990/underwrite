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


def _uses_pathlib_read_bytes(tree: ast.AST) -> bool:
    path_aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "pathlib"
        for alias in node.names
        if alias.name == "Path"
    }
    pathlib_aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "pathlib"
    }
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "read_bytes"
            and isinstance(node.func.value, ast.Call)
        ):
            continue
        constructor = node.func.value.func
        if isinstance(constructor, ast.Name) and constructor.id in path_aliases:
            return True
        if (
            isinstance(constructor, ast.Attribute)
            and constructor.attr == "Path"
            and isinstance(constructor.value, ast.Name)
            and constructor.value.id in pathlib_aliases
        ):
            return True
    return False


@pytest.mark.parametrize("module_path", _MODULE_PATHS, ids=_MODULE_IDS)
def test_module_avoids_wallclock_io_and_non_stdlib_imports(module_path: Path) -> None:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    assert not _uses_pathlib_read_bytes(tree), f"{module_path.name}: found pathlib .read_bytes"
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


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from pathlib import Path as P\nP('x').read_bytes()", True),
        ("import pathlib as pl\npl.Path('x').read_bytes()", True),
        ("class Token:\n def read_bytes(self): return b'x'\nToken().read_bytes()", False),
    ],
)
def test_pathlib_read_bytes_gate_distinguishes_file_io(source: str, expected: bool) -> None:
    assert _uses_pathlib_read_bytes(ast.parse(source)) is expected

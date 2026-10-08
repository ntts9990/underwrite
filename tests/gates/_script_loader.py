"""Load repository utility scripts for their behavior tests."""

from __future__ import annotations

import ast
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


def path_loaded_scripts(script: Path) -> list[Path]:
    """Standalone behavior and boundary checks."""
    found, pending = [script], [script]
    while pending:
        current = pending.pop()
        tree = ast.parse(current.read_text(encoding="utf-8"))
        loaders = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        inside = {id(c) for f in loaders if f.name == "_load_script" for c in ast.walk(f)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "spec_from_file_location":
                assert id(node) in inside, "path-load outside `_load_script`"
            elif name == "_load_script" and node.args:
                arg = node.args[0]
                assert isinstance(arg, ast.Constant) and isinstance(arg.value, str), arg
                target = current.parent / arg.value
                if target not in found:
                    found.append(target)
                    pending.append(target)
    return found

"""Test-only source-idiom policy for explicit function-local seeded randomness.

Not a Python sandbox or general dataflow analysis. Supported constructors use
one required, unmodified function parameter (or its direct attribute) as their
positional seed.
Imported module/constructor aliases and annotations are recognized; rebinding,
constructor escapes, global RNG state and reseeding are unsupported.
"""

from __future__ import annotations

import ast

_Function = ast.FunctionDef | ast.AsyncFunctionDef


def _rng_imports(tree: ast.AST) -> tuple[set[str], set[str], set[int]]:
    modules = {"random"}
    constructors: set[str] = set()
    bad: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(
                alias.asname or alias.name for alias in node.names if alias.name == "random"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "random":
            for alias in node.names:
                if alias.name == "Random":
                    constructors.add(alias.asname or alias.name)
                else:
                    bad.add(node.lineno)
    return modules, constructors, bad


def _seed_defaults(tree: ast.AST) -> set[int]:
    bad: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [*node.args.posonlyargs, *node.args.args]
            defaults = zip(
                args[len(args) - len(node.args.defaults) :], node.args.defaults, strict=True
            )
            for argument, _default in defaults:
                if argument.arg == "seed":
                    bad.add(argument.lineno)
            for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
                if argument.arg == "seed" and default is not None:
                    bad.add(argument.lineno)
        elif isinstance(node, ast.ClassDef):
            for field in node.body:
                if (
                    isinstance(field, ast.AnnAssign)
                    and isinstance(field.target, ast.Name)
                    and field.target.id == "seed"
                    and field.value is not None
                ):
                    bad.add(field.lineno)
    return bad


def _annotations(tree: ast.AST) -> set[ast.AST]:
    result: set[ast.AST] = set()
    for node in ast.walk(tree):
        annotation: ast.AST | None = None
        if isinstance(node, (ast.arg, ast.AnnAssign)):
            annotation = node.annotation
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotation = node.returns
        if annotation is not None:
            result.update(ast.walk(annotation))
    return result


def _local_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> _Function | None:
    while node in parents:
        parent = parents[node]
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return parent if node in parent.body else None
        if isinstance(parent, (ast.ClassDef, ast.Lambda)):
            return None
        node = parent
    return None


def _parameter_seed(node: ast.AST, function: _Function) -> bool:
    root = node.value if isinstance(node, ast.Attribute) else node
    if not isinstance(root, ast.Name):
        return False
    positional = [*function.args.posonlyargs, *function.args.args]
    required = positional[: len(positional) - len(function.args.defaults)]
    required.extend(
        argument
        for argument, default in zip(
            function.args.kwonlyargs, function.args.kw_defaults, strict=True
        )
        if default is None
    )
    if root.id not in {argument.arg for argument in required}:
        return False
    for statement in function.body:
        for child in ast.walk(statement):
            if isinstance(child, (ast.Global, ast.Nonlocal)):
                return False
            if (
                isinstance(child, ast.Name)
                and child.id == root.id
                and isinstance(child.ctx, ast.Store)
            ):
                return False
            if (
                isinstance(child, ast.Attribute)
                and isinstance(child.ctx, ast.Store)
                and isinstance(child.value, ast.Name)
                and child.value.id == root.id
            ):
                return False
    return True


def _constructor(node: ast.AST, modules: set[str], constructors: set[str]) -> bool:
    return (isinstance(node, ast.Name) and node.id in constructors) or (
        isinstance(node, ast.Attribute)
        and node.attr == "Random"
        and isinstance(node.value, ast.Name)
        and node.value.id in modules
    )


def _escaped_references(tree: ast.AST, names: set[str], allowed: set[ast.AST]) -> set[int]:
    bad: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in names and node not in allowed:
            bad.add(node.lineno)
        elif isinstance(node, ast.arg) and node.arg in names:
            bad.add(node.lineno)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in names:
                bad.add(node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"seed", "setstate"}:
                bad.add(node.lineno)
    return bad


def seed_violations(source: str) -> list[int]:
    """Return source lines violating the shared app RNG/seed idiom policy."""
    tree = ast.parse(source)
    modules, constructors, bad = _rng_imports(tree)
    bad.update(_seed_defaults(tree))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    annotations = _annotations(tree)
    allowed: set[ast.AST] = set()
    for node in ast.walk(tree):
        if not _constructor(node, modules, constructors):
            continue
        parent = parents[node]
        if isinstance(parent, ast.Call) and parent.func is node:
            function = _local_function(parent, parents)
            if (
                function is None
                or len(parent.args) != 1
                or parent.keywords
                or not _parameter_seed(parent.args[0], function)
            ):
                bad.add(parent.lineno)
            allowed.update(ast.walk(node))
        elif node in annotations:
            allowed.update(ast.walk(node))
    bad.update(_escaped_references(tree, modules | constructors, allowed))
    return sorted(bad)

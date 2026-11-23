"""Measure the structure program's reference values from source files.

The output measures the error-literal ratchet gate. Import direction and layer
independence stay owned by `.importlinter` and `tests/gates/test_layers.py`.
Like import-linter, imports inside functions and `TYPE_CHECKING` blocks count.
The error-code estimate counts upper-case literals in every argument of error
constructors and error/require helpers. It includes detailed reasons and actions,
so it is a candidate-literal count rather than a count of public category codes.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[1]
APP = Path("src/underwrite")
CORE = Path("packages/underwrite-core/src/underwrite_core")
# `instrument` mixes the effectful ingest shell with pure evidence verifiers.
SPLIT_UNITS = frozenset({"instrument"})
CODE = re.compile(r"[A-Z][A-Z0-9_]{5,}")
ERROR_CALLS = ("_fail", "_error", "_invalid", "_refuse", "_require", "require")
HELPER_MODULE_MINIMUM = 3
INTERNAL = frozenset({"underwrite", "underwrite_core"})


class Edge(TypedDict):
    source: str
    target: str
    sites: int


class UnitEdges(TypedDict):
    distinct: int
    import_sites: int
    cycles: list[list[str]]
    edges: list[Edge]


class Metrics(TypedDict):
    schema: str
    packages: dict[str, dict[str, int]]
    largest_modules: list[dict[str, str | int]]
    unit_edges: UnitEdges
    repeated_private_helpers: list[dict[str, str | int]]
    error_code_estimate: dict[str, int]
    registered_error_literal_sites: dict[str, int]
    dynamic_evidence_error_forwarding: dict[str, int]


PUBLIC_ERROR_CONTRACTS = (
    "acceptance_error.v1",
    "artifact_inspection_error.v1",
    "evidence_error.v1",
    "instrument_error.v1",
    "measurement_error.v1",
    "record_error.v1",
    "release_inspection_error.v1",
)


def registered_error_codes(root: Path) -> set[str]:
    """Read the seven public category-code sets from their JSON contracts."""
    codes: set[str] = set()
    for name in PUBLIC_ERROR_CONTRACTS:
        path = root / "contracts" / f"{name}.schema.json"
        if not path.exists():
            continue
        schema = json.loads(path.read_text(encoding="utf-8"))
        properties = schema.get("properties", {})
        category = properties.get("code", {})
        codes.update(category.get("enum", ()))
        codes.update(schema.get("$defs", {}))
    return codes


def _lines(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _module(root: Path, path: Path) -> str:
    package = "underwrite_core" if path.is_relative_to(root / CORE) else "underwrite"
    base = root / (CORE if package == "underwrite_core" else APP)
    parts = path.relative_to(base).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join((package, *parts))


def unit(module: str) -> str:
    """Return the measured unit: a top-level subpackage, split where layers differ."""
    package, *rest = module.split(".")
    if package == "underwrite_core" or not rest:
        return package
    if rest[0] in SPLIT_UNITS and rest[1:]:
        return ".".join(rest[:2])
    return rest[0]


def _imported_units(tree: ast.Module, module: str, *, package: bool) -> list[str]:
    """Return one target unit per import statement and unit, for internal imports."""
    # A relative import resolves against the containing package, which is the
    # module itself for `__init__.py`.
    parent = module.split(".") if package else module.split(".")[:-1]
    found: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = parent[: len(parent) - node.level + 1] if node.level else []
            prefix = ".".join([*base, node.module] if node.module else base)
            # `from pkg import name` may import a submodule, so name joins the path.
            names = [
                f"{prefix}.{alias.name}" if alias.name != "*" else prefix for alias in node.names
            ]
        internal = {unit(name) for name in names if name.split(".")[0] in INTERNAL}
        found.extend(sorted(internal))
    return found


def _callee(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def error_literals(tree: ast.Module) -> list[str]:
    """Upper-case literal sites at any argument position in error and require calls."""
    codes: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee(node)
        if not (name.endswith("Error") or name.startswith(ERROR_CALLS)):
            continue
        for argument in (*node.args, *(keyword.value for keyword in node.keywords)):
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                if CODE.fullmatch(argument.value):
                    codes.append(argument.value)
    return codes


def error_codes(tree: ast.Module) -> set[str]:
    """Distinct upper-case literal candidates, including adapter-owned reasons."""
    return set(error_literals(tree))


def _registered_sites(
    literals: list[str], public_codes: set[str], relative: str, modules: set[str]
) -> int:
    if relative == "src/underwrite/error_contract/registry.py":
        return 0
    count = sum(code in public_codes for code in literals)
    if count:
        modules.add(relative)
    return count


def _call_argument(node: ast.Call, position: int, keyword: str) -> ast.expr | None:
    if len(node.args) > position:
        return node.args[position]
    return next((item.value for item in node.keywords if item.arg == keyword), None)


def dynamic_evidence_error_forwarding(tree: ast.Module) -> int:
    """Count EvidenceError sites with a non-literal reason or location.

    This deliberately measures syntax only. A literal does not prove provenance,
    and a dynamic expression can still be safely derived from a closed owner enum.
    """
    aliases = {"EvidenceError"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            aliases.update(
                item.asname for item in node.names if item.name == "EvidenceError" and item.asname
            )
    sites = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _callee(node) not in aliases:
            continue
        if any(isinstance(argument, ast.Starred) for argument in node.args) or any(
            item.arg is None for item in node.keywords
        ):
            sites += 1
            continue
        fields = (
            _call_argument(node, 1, "reason"),
            _call_argument(node, 2, "location"),
        )
        if any(
            value is not None
            and not (isinstance(value, ast.Constant) and isinstance(value.value, str))
            for value in fields
        ):
            sites += 1
    return sites


def _record_dynamic_evidence_forwarding(
    tree: ast.Module, relative: str, modules: set[str]
) -> int:
    sites = dynamic_evidence_error_forwarding(tree)
    if sites:
        modules.add(relative)
    return sites


def _reachable(edges: dict[str, set[str]], start: str) -> set[str]:
    seen: set[str] = set()
    pending = list(edges.get(start, ()))
    while pending:
        node = pending.pop()
        if node not in seen:
            seen.add(node)
            pending.extend(edges.get(node, ()))
    return seen


def cycles(edges: dict[str, set[str]]) -> list[list[str]]:
    """Return groups of units that import each other, directly or transitively."""
    reach = {node: _reachable(edges, node) for node in edges}
    groups = {
        tuple(sorted({node} | {other for other in reach[node] if node in reach.get(other, ())}))
        for node in edges
        if node in reach[node]
    }
    return [list(group) for group in sorted(groups)]


def measure(root: Path) -> Metrics:
    files = sorted(
        path
        for base in (APP, CORE)
        for path in (root / base).rglob("*.py")
        if "__pycache__" not in path.parts
    )
    packages: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "lines": 0})
    sizes: list[tuple[int, str]] = []
    sites: Counter[tuple[str, str]] = Counter()
    helpers: dict[str, set[str]] = defaultdict(set)
    codes: dict[str, set[str]] = defaultdict(set)
    public_codes = registered_error_codes(root)
    registered_sites = 0
    registered_modules: set[str] = set()
    dynamic_forwarding_sites = 0
    dynamic_forwarding_modules: set[str] = set()
    for path in files:
        module = _module(root, path)
        source = unit(module)
        lines = _lines(path)
        package = packages[source.split(".")[0]]
        package["files"] += 1
        package["lines"] += lines
        relative = path.relative_to(root).as_posix()
        sizes.append((lines, relative))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for destination in _imported_units(tree, module, package=path.name == "__init__.py"):
            if destination != source:
                sites[(source, destination)] += 1
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name.startswith("_") and not node.name.startswith("__"):
                    helpers[node.name].add(relative)
        literals = error_literals(tree)
        for code in literals:
            codes[code].add(relative)
        registered_sites += _registered_sites(literals, public_codes, relative, registered_modules)
        dynamic_forwarding_sites += _record_dynamic_evidence_forwarding(
            tree, relative, dynamic_forwarding_modules
        )
    edges: dict[str, set[str]] = defaultdict(set)
    for source, destination in sites:
        edges[source].add(destination)
    repeated = sorted(
        ((len(modules), name) for name, modules in helpers.items()),
        key=lambda item: (-item[0], item[1]),
    )
    modules_with_codes: set[str] = set()
    for paths in codes.values():
        modules_with_codes.update(paths)
    return {
        "schema": "underwrite.structure_metrics.v1",
        "packages": dict(sorted(packages.items())),
        "largest_modules": [
            {"path": path, "lines": lines}
            for lines, path in sorted(sizes, key=lambda item: (-item[0], item[1]))[:15]
        ],
        "unit_edges": {
            "distinct": len(sites),
            "import_sites": sum(sites.values()),
            "cycles": cycles(edges),
            "edges": [
                {"source": source, "target": target, "sites": count}
                for (source, target), count in sorted(sites.items())
            ],
        },
        "repeated_private_helpers": [
            {"name": name, "modules": count}
            for count, name in repeated
            if count >= HELPER_MODULE_MINIMUM
        ],
        "error_code_estimate": {"distinct": len(codes), "modules": len(modules_with_codes)},
        "registered_error_literal_sites": {
            "sites": registered_sites,
            "modules": len(registered_modules),
        },
        "dynamic_evidence_error_forwarding": {
            "sites": dynamic_forwarding_sites,
            "modules": len(dynamic_forwarding_modules),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    json.dump(measure(args.root), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

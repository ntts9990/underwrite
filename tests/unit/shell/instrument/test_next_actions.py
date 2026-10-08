"""Standalone behavior and boundary checks."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from _repo_paths import repo_root

from underwrite.instrument.ingest.application import NEXT_ACTIONS

ROOT = repo_root(Path(__file__).resolve().parent)
SCANNED = ("src/underwrite/cli", "src/underwrite/instrument/ingest")
OWNER = "src/underwrite/instrument/ingest/application.py"
TABLE = "NEXT_ACTIONS"
ACTION_PARAMETERS = ("next_action", "action")
# The inspect/check parsers name CORRECT_MANIFEST for a usage error because their v1 error
# contracts (the value) admit no CORRECT_ARGUMENTS, and a v1 contract does not change here.
KNOWN_DIVERGENCES = {
    ("src/underwrite/cli/inspection.py", "USAGE_ERROR", "'CORRECT_MANIFEST'"): (
        "artifact_inspection_error.v1"
    ),
    ("src/underwrite/cli/release.py", "USAGE_ERROR", "'CORRECT_MANIFEST'"): (
        "release_inspection_error.v1"
    ),
}

# (path, line, code expression, action expression, the compared subject of an if-branch)
Pair = tuple[str, int, ast.expr, ast.expr, ast.expr | None]


def _sources() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for folder in SCANNED
        for path in sorted((ROOT / folder).rglob("*.py"))
    }


def _name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else None


def _text(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _signatures(trees: dict[str, ast.Module]) -> dict[str, tuple[int, int, str]]:
    """Callable -> (code index, action index, action keyword) from its own parameters."""
    found: dict[str, tuple[int, int, str]] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                inits = [
                    item
                    for item in node.body
                    if isinstance(item, ast.FunctionDef) and item.name == "__init__"
                ]
                functions, name = inits, node.name
            elif isinstance(node, ast.FunctionDef) and not node.name.startswith("__"):
                functions, name = [node], node.name
            else:
                continue
            for function in functions:
                parameters = [argument.arg for argument in function.args.args]
                parameters = parameters[1:] if parameters[:1] == ["self"] else parameters
                action = next((p for p in ACTION_PARAMETERS if p in parameters), None)
                if "code" in parameters and action is not None:
                    found[name] = (parameters.index("code"), parameters.index(action), action)
    return found


def _call_pair(node: ast.Call, signatures: dict[str, tuple[int, int, str]]) -> tuple[ast.expr, ...]:
    keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
    name = _name(node.func)
    if name in signatures:
        code_index, action_index, action_keyword = signatures[name]
        code = node.args[code_index] if len(node.args) > code_index else keywords.get("code")
        action = (
            node.args[action_index]
            if len(node.args) > action_index
            else keywords.get(action_keyword)
        )
        return () if code is None or action is None else (code, action)
    action = next((keywords[p] for p in ACTION_PARAMETERS if p in keywords), None)
    return () if "code" not in keywords or action is None else (keywords["code"], action)


def _branch_pairs(node: ast.If) -> list[tuple[ast.expr, ast.expr, ast.expr]]:
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return []
    subject, operator, target = test.left, test.ops[0], test.comparators[0]
    if isinstance(operator, ast.Eq) and _text(target) is not None:
        codes = [target]
    elif isinstance(operator, ast.In) and isinstance(target, ast.Set | ast.Tuple | ast.List):
        codes = [item for item in target.elts if _text(item) is not None]
    else:
        return []
    pairs: list[tuple[ast.expr, ast.expr, ast.expr]] = []
    for statement in node.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target_node, value = statement.targets[0], statement.value
        if isinstance(target_node, ast.Tuple) and isinstance(value, ast.Tuple):
            assigned = list(zip(target_node.elts, value.elts, strict=True))
        else:
            assigned = [(target_node, value)]
        pairs.extend(
            (code, action, subject)
            for name, action in assigned
            if _name(name) in ACTION_PARAMETERS
            for code in codes
        )
    return pairs


def _table_pairs(node: ast.Assign, path: str) -> list[tuple[ast.expr, ast.expr]]:
    names = [_name(target) for target in node.targets]
    if not isinstance(node.value, ast.Dict) or not any(
        name and name.endswith("ACTIONS") and not (name == TABLE and path == OWNER)
        for name in names
    ):
        return []
    return [
        (key, value) for key, value in zip(node.value.keys, node.value.values, strict=True) if key
    ]


def pairs(sources: dict[str, str]) -> list[Pair]:
    trees = {path: ast.parse(text, filename=path) for path, text in sources.items()}
    signatures = _signatures(trees)
    found: list[Pair] = []
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (pair := _call_pair(node, signatures)):
                found.append((path, node.lineno, pair[0], pair[1], None))
            elif isinstance(node, ast.Dict):
                keys = {
                    _text(key): value
                    for key, value in zip(node.keys, node.values, strict=True)
                    if key
                }
                if "code" in keys and "next_action" in keys:
                    found.append((path, node.lineno, keys["code"], keys["next_action"], None))
            elif isinstance(node, ast.If):
                found.extend((path, node.lineno, *pair) for pair in _branch_pairs(node))
            elif isinstance(node, ast.Assign):
                found.extend((path, node.lineno, *pair, None) for pair in _table_pairs(node, path))
    return found


def divergences(sources: dict[str, str]) -> set[tuple[str, str, str]]:
    """Pairs for a table code not taking NEXT_ACTIONS[that code], and mis-keyed lookups."""
    diverging: set[tuple[str, str, str]] = set()
    for path, _, code, action, subject in pairs(sources):
        text = _text(code)
        if isinstance(action, ast.Subscript) and _name(action.value) == TABLE:
            key = ast.dump(action.slice)
            same = {ast.dump(node) for node in (code, subject) if node is not None}
            if text is None or key in same:
                continue
        elif text not in NEXT_ACTIONS:
            continue
        diverging.add((path, ast.unparse(code).strip("'\""), ast.unparse(action)))
    return diverging


def restated(sources: dict[str, str]) -> list[tuple[str, int, str]]:
    """Every literal equal to a table action outside the table's own definition."""
    actions = set(NEXT_ACTIONS.values())
    hits: list[tuple[str, int, str]] = []
    for path, text in sources.items():
        tree = ast.parse(text, filename=path)
        owned: set[int] = set()
        if path == OWNER:
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and _name(node.targets[0]) == TABLE:
                    owned.update(id(child) for child in ast.walk(node.value))
        hits.extend(
            (path, node.lineno, node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value in actions and id(node) not in owned
        )
    return hits


def test_table_holds_only_the_context_free_codes() -> None:
    assert set(NEXT_ACTIONS) == {
        "SCHEMA_CONFIGURATION_ERROR",
        "SOURCE_READ_FAILED",
        "USAGE_ERROR",
        "INTERNAL_ERROR",
        "UNSUPPORTED_PLATFORM",
    }


def test_scan_sees_pairs_in_both_trees() -> None:
    table_pairs = {path for path, _, code, *_ in pairs(_sources()) if _text(code) in NEXT_ACTIONS}
    for folder in SCANNED:
        assert any(path.startswith(folder + "/") for path in table_pairs), folder


def test_every_table_code_pair_takes_the_table_action() -> None:
    assert divergences(_sources()) == set(KNOWN_DIVERGENCES)


def test_each_known_divergence_is_forced_by_its_contract() -> None:
    """Once a contract admits the table action, its site must take NEXT_ACTIONS instead."""
    for (_, code, action), contract in KNOWN_DIVERGENCES.items():
        schema = json.loads((ROOT / "contracts" / f"{contract}.schema.json").read_text("utf-8"))
        admitted = schema["properties"]["next_action"]["enum"]
        assert NEXT_ACTIONS[code] not in admitted and ast.literal_eval(action) in admitted


def test_no_module_restates_a_table_action() -> None:
    assert restated(_sources()) == []


SIGNATURE = (
    "class E(ValueError):\n"
    "    def __init__(self, code, reason, location, action):\n"
    "        super().__init__(code)\n"
)


@pytest.mark.parametrize(
    "injected",
    [
        'E("INTERNAL_ERROR", "INTERNAL_ERROR", "", "CORRECT_MANIFEST")',
        'E(code="INTERNAL_ERROR", reason="", location="", action="RETRY")',
        'E("INTERNAL_ERROR", "INTERNAL_ERROR", "", NEXT_ACTIONS["USAGE_ERROR"])',
        'if code == "SOURCE_READ_FAILED":\n    action = "RETRY"',
        'if code in {"UNSUPPORTED_PLATFORM", "OTHER"}:\n    path, action = "", "RETRY"',
        'payload = {"code": "SCHEMA_CONFIGURATION_ERROR", "next_action": "RETRY"}',
        'payload = dict(code="USAGE_ERROR", next_action="RETRY")',
        '_ACTIONS = {"INTERNAL_ERROR": "RETRY"}',
    ],
)
def test_divergent_pair_fails_the_scan(injected: str) -> None:
    assert divergences({"synthetic.py": SIGNATURE + injected + "\n"})


def test_restated_literal_fails_the_scan() -> None:
    source = SIGNATURE + 'E("INTERNAL_ERROR", "INTERNAL_ERROR", "", "REPORT_INTERNAL_ERROR")\n'
    assert restated({"synthetic.py": source}) == [("synthetic.py", 4, "REPORT_INTERNAL_ERROR")]


def test_table_lookups_pass_the_scan() -> None:
    source = SIGNATURE + (
        'E("INTERNAL_ERROR", "INTERNAL_ERROR", "", NEXT_ACTIONS["INTERNAL_ERROR"])\n'
        'if code in {"OTHER", "SOURCE_READ_FAILED"}:\n'
        "    action = NEXT_ACTIONS[code]\n"
        'E(str(reason), reason, location, NEXT_ACTIONS["SOURCE_READ_FAILED"])\n'
        '_ACTIONS = {**NEXT_ACTIONS, "OTHER": "RETRY"}\n'
    )
    assert divergences({"synthetic.py": source}) == set()
    assert restated({"synthetic.py": source}) == []


def _inject(sources: dict[str, str], path: str, node: ast.expr, literal: str) -> dict[str, str]:
    lines = sources[path].encode().splitlines(keepends=True)
    row = lines[node.lineno - 1]  # AST column offsets count UTF-8 bytes
    lines[node.lineno - 1] = row[: node.col_offset] + literal.encode() + row[node.end_col_offset :]
    return sources | {path: b"".join(lines).decode()}


def test_injection_at_each_real_lookup_fails_the_scan() -> None:
    sources = _sources()
    lookups = [
        (path, str(_text(code)), action)
        for path, _, code, action, _ in pairs(sources)
        if isinstance(action, ast.Subscript) and _text(code) in NEXT_ACTIONS
    ]
    assert lookups
    for path, code, action in lookups:
        restated_sources = _inject(sources, path, action, repr(NEXT_ACTIONS[code]))
        assert [hit[:2] for hit in restated(restated_sources)] == [(path, action.lineno)]
        diverged = divergences(_inject(sources, path, action, '"CORRECT_MANIFEST"'))
        assert (path, code, "'CORRECT_MANIFEST'") in diverged - set(KNOWN_DIVERGENCES)

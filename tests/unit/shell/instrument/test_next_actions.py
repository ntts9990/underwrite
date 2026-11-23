"""Standalone behavior and boundary checks."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from _repo_paths import repo_root

from underwrite.error_contract.registry import (
    ACCEPTANCE_ACTIONS,
    BASE_ACTIONS,
    EVIDENCE_ACTIONS,
    INSPECTION_ACTIONS,
    MEASUREMENT_PROJECTIONS,
    RECORD_PROJECTIONS,
    RELEASE_ACTIONS,
)

NEXT_ACTIONS = BASE_ACTIONS

ROOT = repo_root(Path(__file__).resolve().parent)
SCANNED = (
    "src/underwrite/cli",
    "src/underwrite/instrument/ingest",
    "src/underwrite/service",
)
OWNER = "src/underwrite/instrument/ingest/application.py"
TABLE = "NEXT_ACTIONS"
REGISTRY = "src/underwrite/error_contract/registry.py"
ACTION_PARAMETERS = ("next_action", "action")
# The inspect/check parsers name CORRECT_MANIFEST for a usage error because their v1 error
# contracts (the value) admit no CORRECT_ARGUMENTS, and a v1 contract does not change here.
KNOWN_DIVERGENCES = {
    ("inspection", "USAGE_ERROR", "CORRECT_MANIFEST"): "artifact_inspection_error.v1",
    ("release", "USAGE_ERROR", "CORRECT_MANIFEST"): "release_inspection_error.v1",
}

# The same limit code has a different recovery action for analysis output,
# immutable record output, and a release batch. These are current output baselines;
# most v1 schemas admit code and action independently.
LIMIT_ACTIONS = {
    ("acceptance", "INPUT_LIMIT_EXCEEDED", "REDUCE_INPUT_SIZE"),
    ("acceptance", "OUTPUT_LIMIT_EXCEEDED", "REDUCE_INPUT_SIZE"),
    ("evidence", "INPUT_LIMIT_EXCEEDED", "REDUCE_INPUT_SIZE"),
    ("evidence", "OUTPUT_LIMIT_EXCEEDED", "REDUCE_INPUT_SIZE"),
    ("inspection", "INPUT_LIMIT_EXCEEDED", "CORRECT_MANIFEST"),
    ("measurement", "INPUT_LIMIT_EXCEEDED", "REDUCE_INPUT_SIZE"),
    ("measurement", "OUTPUT_LIMIT_EXCEEDED", "REDUCE_INPUT_SIZE"),
    ("record", "INPUT_LIMIT_EXCEEDED", "REDUCE_INPUT_SIZE"),
    ("record", "OUTPUT_LIMIT_EXCEEDED", "REDUCE_OUTPUT_SIZE"),
    ("release", "INPUT_LIMIT_EXCEEDED", "CORRECT_MANIFEST"),
    ("release", "OUTPUT_LIMIT_EXCEEDED", "REDUCE_BATCH_SIZE"),
}
REGISTRY_LITERAL_LIMIT_ACTIONS = {
    ("inspection", "INPUT_LIMIT_EXCEEDED", "CORRECT_MANIFEST"),
    ("record", "INPUT_LIMIT_EXCEEDED", "REDUCE_INPUT_SIZE"),
    ("record", "OUTPUT_LIMIT_EXCEEDED", "REDUCE_OUTPUT_SIZE"),
    ("release", "INPUT_LIMIT_EXCEEDED", "CORRECT_MANIFEST"),
    ("release", "OUTPUT_LIMIT_EXCEEDED", "REDUCE_BATCH_SIZE"),
}
REGISTRY_BASE_ACTION_LOOKUPS = {
    ("inspection", "INTERNAL_ERROR", "INTERNAL_ERROR"),
    ("inspection", "SCHEMA_CONFIGURATION_ERROR", "SCHEMA_CONFIGURATION_ERROR"),
    ("inspection", "SOURCE_READ_FAILED", "SOURCE_READ_FAILED"),
    ("inspection", "UNSUPPORTED_PLATFORM", "UNSUPPORTED_PLATFORM"),
    ("release", "INTERNAL_ERROR", "INTERNAL_ERROR"),
    ("release", "SCHEMA_CONFIGURATION_ERROR", "SCHEMA_CONFIGURATION_ERROR"),
    ("release", "SOURCE_READ_FAILED", "SOURCE_READ_FAILED"),
    ("release", "UNSUPPORTED_PLATFORM", "UNSUPPORTED_PLATFORM"),
}
KNOWN_DYNAMIC_LITERAL_ACTIONS = {
    ("src/underwrite/instrument/ingest/inspection.py", "str(detail['code'])", "CORRECT_ARTIFACT"),
    (
        "src/underwrite/instrument/ingest/release_compose.py",
        "unsupported",
        "SELECT_SUPPORTED_PROFILE",
    ),
}
ERROR_EMITTER_ACTIONS = {
    "src/underwrite/cli/accept.py": {("code", "projection.next_action")},
    "src/underwrite/cli/evidence.py": {("code", "projection.next_action")},
    "src/underwrite/cli/measurement_flow.py": {("code", "projection.next_action")},
    "src/underwrite/cli/records.py": {("code", "projection.next_action")},
    "src/underwrite/instrument/ingest/inspection.py": {
        ("code", "action"),
        ("code", "projection.next_action"),
        ("str(detail['code'])", "'CORRECT_ARTIFACT'"),
    },
    "src/underwrite/instrument/ingest/release_compose.py": {
        ("code", "action"),
        ("code", "projection.next_action"),
        ("unsupported", "'SELECT_SUPPORTED_PROFILE'"),
        ("str(detail['reason'])", "NEXT_ACTIONS['SOURCE_READ_FAILED']"),
        ("str(item['reason'])", "str(item['next_action'])"),
    },
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


def _table_pairs(node: ast.Assign | ast.AnnAssign, path: str) -> list[tuple[ast.expr, ast.expr]]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names = [_name(target) for target in targets]
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
            elif isinstance(node, ast.Assign | ast.AnnAssign):
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


def limit_actions(sources: dict[str, str]) -> set[tuple[str, str, str]]:
    return {
        (path, code_text, action_text)
        for path, _, code, action, _ in pairs(sources)
        if (code_text := _text(code)) in {"INPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED"}
        if (action_text := _text(action)) is not None
    }


def dynamic_literal_actions(sources: dict[str, str]) -> set[tuple[str, str, str]]:
    return {
        (path, ast.unparse(code), action_text)
        for path, _, code, action, _ in pairs(sources)
        if _text(code) is None and (action_text := _text(action)) is not None
    }


def emitter_actions(sources: dict[str, str]) -> dict[str, set[tuple[str, str]]]:
    found: dict[str, set[tuple[str, str]]] = {path: set() for path in ERROR_EMITTER_ACTIONS}
    for path, _, code, action, _ in pairs(sources):
        if path in found and _text(code) is None:
            found[path].add((ast.unparse(code), ast.unparse(action)))
    return found


def registry_literal_limit_actions(source: str) -> set[tuple[str, str, str]]:
    """Read explicit contextual limit actions from the registry syntax."""
    contexts = {
        "INSPECTION_ACTIONS": "inspection",
        "RELEASE_ACTIONS": "release",
        "_RECORD_DEFINITIONS": "record",
    }
    found: set[tuple[str, str, str]] = set()
    for node in ast.walk(ast.parse(source, filename=REGISTRY)):
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        context = contexts.get(node.target.id)
        if context is None or not isinstance(node.value, ast.Call) or not node.value.args:
            continue
        mapping = node.value.args[0]
        if not isinstance(mapping, ast.Dict):
            continue
        for key, value in zip(mapping.keys, mapping.values, strict=True):
            code = _text(key) if key is not None else None
            if code not in {"INPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED"}:
                continue
            if context == "record" and isinstance(value, ast.Call):
                _, action_node = value.args
                action = _text(action_node)
            else:
                action = _text(value)
            if action is not None:
                found.add((context, code, action))
    return found


def registry_base_action_lookups(source: str) -> set[tuple[str, str, str]]:
    """Read each contextual public code and the exact base-action key it uses."""
    contexts = {
        "INSPECTION_ACTIONS": "inspection",
        "RELEASE_ACTIONS": "release",
    }
    found: set[tuple[str, str, str]] = set()
    for node in ast.walk(ast.parse(source, filename=REGISTRY)):
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        context = contexts.get(node.target.id)
        if context is None or not isinstance(node.value, ast.Call) or not node.value.args:
            continue
        mapping = node.value.args[0]
        if not isinstance(mapping, ast.Dict):
            continue
        for key, value in zip(mapping.keys, mapping.values, strict=True):
            code = _text(key) if key is not None else None
            if (
                code is not None
                and isinstance(value, ast.Subscript)
                and _name(value.value) == "BASE_ACTIONS"
                and (lookup_key := _text(value.slice)) is not None
            ):
                found.add((context, code, lookup_key))
    return found


def test_table_holds_only_the_context_free_codes() -> None:
    assert set(NEXT_ACTIONS) == {
        "SCHEMA_CONFIGURATION_ERROR",
        "SOURCE_READ_FAILED",
        "USAGE_ERROR",
        "INTERNAL_ERROR",
        "UNSUPPORTED_PLATFORM",
    }


def test_scan_sees_pairs_in_both_trees() -> None:
    table_pairs = {
        path for path, actions in emitter_actions(_sources()).items() if actions
    }
    for folder in SCANNED[:2]:
        assert any(path.startswith(folder + "/") for path in table_pairs), folder


def test_every_table_code_pair_takes_the_table_action() -> None:
    assert divergences(_sources()) == set()


def test_each_known_divergence_is_forced_by_its_contract() -> None:
    """Contextual registry actions remain constrained by their public contracts."""
    context_actions = {"inspection": INSPECTION_ACTIONS, "release": RELEASE_ACTIONS}
    for (context, code, action), contract in KNOWN_DIVERGENCES.items():
        schema = json.loads((ROOT / "contracts" / f"{contract}.schema.json").read_text("utf-8"))
        admitted = schema["properties"]["next_action"]["enum"]
        assert NEXT_ACTIONS[code] not in admitted
        assert context_actions[context][code] == action
        assert action in admitted


def test_limit_actions_match_the_current_output_baseline() -> None:
    actual = {
        (context, code, action)
        for context, actions in {
            "acceptance": ACCEPTANCE_ACTIONS,
            "evidence": EVIDENCE_ACTIONS,
            "inspection": INSPECTION_ACTIONS,
            "measurement": {
                code: projection.next_action for code, projection in MEASUREMENT_PROJECTIONS.items()
            },
            "record": {
                code: projection.next_action for code, projection in RECORD_PROJECTIONS.items()
            },
            "release": RELEASE_ACTIONS,
        }.items()
        for code, action in actions.items()
        if code in {"INPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED"}
    }
    assert actual == LIMIT_ACTIONS
    registry_source = (ROOT / REGISTRY).read_text(encoding="utf-8")
    assert registry_literal_limit_actions(registry_source) == REGISTRY_LITERAL_LIMIT_ACTIONS


def test_contextual_tables_use_the_exact_base_action_key_for_each_code() -> None:
    registry_source = (ROOT / REGISTRY).read_text(encoding="utf-8")
    assert registry_base_action_lookups(registry_source) == REGISTRY_BASE_ACTION_LOOKUPS


def test_dynamic_codes_cannot_gain_unreviewed_literal_actions() -> None:
    assert dynamic_literal_actions(_sources()) == KNOWN_DYNAMIC_LITERAL_ACTIONS


def test_error_payload_literal_action_fails_the_scan() -> None:
    sources = _sources()
    path = "src/underwrite/cli/evidence.py"
    original = '"next_action": projection.next_action,'
    assert sources[path].count(original) == 1
    changed = sources[path].replace(original, '"next_action": "REDUCE_OUTPUT_SIZE",')
    assert dynamic_literal_actions(sources | {path: changed}) != KNOWN_DYNAMIC_LITERAL_ACTIONS


def test_error_emitters_keep_the_code_bound_action() -> None:
    assert emitter_actions(_sources()) == ERROR_EMITTER_ACTIONS


def test_wrong_error_action_lookup_fails_the_scan() -> None:
    sources = _sources()
    path = "src/underwrite/cli/evidence.py"
    changed = sources[path].replace(
        "projection.next_action",
        'ERROR_ACTIONS["OUTPUT_LIMIT_EXCEEDED"]',
    )
    assert changed != sources[path]
    assert emitter_actions(sources | {path: changed}) != ERROR_EMITTER_ACTIONS


def test_release_emitter_action_lookup_fails_the_scan() -> None:
    sources = _sources()
    path = "src/underwrite/instrument/ingest/release_compose.py"
    changed = sources[path].replace(
        "next_action=projection.next_action,", "next_action=NEXT_ACTIONS['SOURCE_READ_FAILED'],"
    )
    assert changed != sources[path]
    assert emitter_actions(sources | {path: changed}) != ERROR_EMITTER_ACTIONS


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
    sources = {REGISTRY: (ROOT / REGISTRY).read_text(encoding="utf-8")}
    lookups = [
        node
        for node in ast.walk(ast.parse(sources[REGISTRY], filename=REGISTRY))
        if isinstance(node, ast.Subscript)
        and _name(node.value) == "BASE_ACTIONS"
        and _text(node.slice) in NEXT_ACTIONS
    ]
    assert lookups
    for lookup in lookups:
        changed = _inject(sources, REGISTRY, lookup, '"CORRECT_MANIFEST"')[REGISTRY]
        remaining = [
            node
            for node in ast.walk(ast.parse(changed, filename=REGISTRY))
            if isinstance(node, ast.Subscript)
            and _name(node.value) == "BASE_ACTIONS"
            and _text(node.slice) in NEXT_ACTIONS
        ]
        assert len(remaining) == len(lookups) - 1


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            '"INPUT_LIMIT_EXCEEDED": "CORRECT_MANIFEST"',
            '"INPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE"',
        ),
        (
            '"OUTPUT_LIMIT_EXCEEDED": "REDUCE_BATCH_SIZE"',
            '"OUTPUT_LIMIT_EXCEEDED": "REDUCE_OUTPUT_SIZE"',
        ),
        (
            '"OUTPUT_LIMIT_EXCEEDED", "REDUCE_OUTPUT_SIZE"',
            '"OUTPUT_LIMIT_EXCEEDED", "REDUCE_INPUT_SIZE"',
        ),
    ],
)
def test_registry_limit_action_mutation_changes_the_static_baseline(
    old: str, new: str
) -> None:
    source = (ROOT / REGISTRY).read_text(encoding="utf-8")
    assert old in source
    changed = source.replace(old, new, 1)
    assert registry_literal_limit_actions(changed) != REGISTRY_LITERAL_LIMIT_ACTIONS


@pytest.mark.parametrize(
    ("old_key", "wrong_key"),
    [
        ("SOURCE_READ_FAILED", "SCHEMA_CONFIGURATION_ERROR"),
        ("SCHEMA_CONFIGURATION_ERROR", "SOURCE_READ_FAILED"),
        ("UNSUPPORTED_PLATFORM", "USAGE_ERROR"),
    ],
)
def test_registry_base_action_miskey_fails_the_static_baseline(
    old_key: str, wrong_key: str
) -> None:
    source = (ROOT / REGISTRY).read_text(encoding="utf-8")
    old = f'BASE_ACTIONS["{old_key}"]'
    new = f'BASE_ACTIONS["{wrong_key}"]'
    assert old in source
    changed = source.replace(old, new, 1)
    assert registry_base_action_lookups(changed) != REGISTRY_BASE_ACTION_LOOKUPS

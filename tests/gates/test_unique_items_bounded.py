"""Standalone behavior and boundary checks."""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from _repo_paths import repo_root
from jsonschema import Draft202012Validator

ROOT = repo_root(Path(__file__).resolve())
PACKAGED = ROOT / "src/underwrite/_contracts"
SORTABLE_TYPES = frozenset({"string", "number", "integer"})
EXHAUSTIVE = ("oneOf", "anyOf")

RESIDUAL = {
    (
        "measurement_policy.v2.schema.json",
        "#/properties/stratification/properties/requested",
    ): "UNSORTABLE_UNBOUNDED: measure input; worst about 4.2 s (4,171 one-char objects in a "
    "64 KiB policy); MAX_CELLS is checked after",
    (
        "release_inspection.v1.schema.json",
        "#/properties/requirements/items/properties/diagnostics",
    ): "UNSORTABLE_UNBOUNDED: output contract over product-built diagnostics",
    (
        "release_inspection.v1.schema.json",
        "#/$defs/evidence/properties/diagnostics",
    ): "UNSORTABLE_UNBOUNDED: output contract over product-built diagnostics",
}

Schema = Mapping[str, Any]


def _nodes(node: object, pointer: str) -> Iterator[tuple[str, Schema]]:
    if isinstance(node, Mapping):
        mapping = cast(Schema, node)
        yield pointer, mapping
        for key, value in mapping.items():
            yield from _nodes(value, f"{pointer}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(cast(list[object], node)):
            yield from _nodes(value, f"{pointer}/{index}")


def _resolve(schemas: Mapping[str, Schema], name: str, ref: str) -> tuple[str, str] | None:
    """(file, pointer) a ``$ref`` names inside the population, or None when it leaves it."""
    base, _, fragment = ref.partition("#")
    if base:
        owners = [file for file, schema in schemas.items() if schema.get("$id") == base]
        if not owners:
            return None
        name = owners[0]
    return name, f"#{fragment}"


def _at(schemas: Mapping[str, Schema], name: str, pointer: str) -> object:
    node: object = schemas[name]
    for part in [p for p in pointer.removeprefix("#").split("/") if p]:
        node = cast(Any, node)[int(part) if isinstance(node, list) else part]
    return node


def _item_type(schemas: Mapping[str, Schema], name: str, items: object) -> object:
    while isinstance(items, Mapping) and "$ref" in items:
        target = _resolve(schemas, name, cast(Schema, items)["$ref"])
        if target is None:
            return None
        name, pointer = target
        items = _at(schemas, name, pointer)
    return cast(Schema, items).get("type") if isinstance(items, Mapping) else None


def _order_and_bound(schemas: Mapping[str, Schema]) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for name, schema in schemas.items():
        for pointer, node in _nodes(schema, "#"):
            if node.get("uniqueItems") is not True:
                continue
            keys = list(node)
            bounds = [key for key in ("items", "maxItems") if key in node]
            if "items" not in node or any(
                keys.index(key) > keys.index("uniqueItems") for key in bounds
            ):
                found.add((name, pointer, "UNIQUE_BEFORE_BOUND"))
            if "maxItems" not in node and (
                _item_type(schemas, name, node.get("items")) not in SORTABLE_TYPES
            ):
                found.add((name, pointer, "UNSORTABLE_UNBOUNDED"))
    return found


Visit = tuple[str, str, bool]  # (file, pointer, under a oneOf/anyOf branch)


def _next(schemas: Mapping[str, Schema], visit: Visit, node: object) -> list[Visit]:
    name, pointer, exhaustive = visit
    if isinstance(node, list):
        items = cast(list[object], node)
        return [(name, f"{pointer}/{index}", exhaustive) for index in range(len(items))]
    following: list[Visit] = []
    for key, value in cast(Schema, node).items():
        if key == "$ref" and (target := _resolve(schemas, name, value)) is not None:
            following.append((*target, exhaustive))
        elif key != "$defs" and isinstance(value, (Mapping, list)):  # $defs: via $ref only
            following.append((name, f"{pointer}/{key}", exhaustive or key in EXHAUSTIVE))
    return following


def _under_exhaustive(schemas: Mapping[str, Schema]) -> set[tuple[str, str, str]]:
    """uniqueItems arrays reachable from a oneOf/anyOf branch, following $ref."""
    found: set[tuple[str, str, str]] = set()
    pending: list[Visit] = [(name, "#", False) for name in schemas]
    seen: set[Visit] = set()
    while pending:
        visit = pending.pop()
        node: object = _at(schemas, visit[0], visit[1])
        if visit in seen or not isinstance(node, (Mapping, list)):
            continue
        seen.add(visit)
        if visit[2] and isinstance(node, Mapping) and cast(Schema, node).get("uniqueItems"):
            found.add((visit[0], visit[1], "UNIQUE_UNDER_EXHAUSTIVE_APPLICATOR"))
        pending += _next(schemas, visit, cast(object, node))
    return found


def violations(schemas: Mapping[str, Schema]) -> set[tuple[str, str, str]]:
    return _order_and_bound(schemas) | _under_exhaustive(schemas)


def packaged() -> dict[str, Schema]:
    return {
        path.name: cast(Schema, json.loads(path.read_bytes()))
        for path in sorted(PACKAGED.glob("*.schema.json"))
    }


def test_population_is_the_packaged_contracts_and_has_unique_arrays() -> None:
    schemas = packaged()
    assert len(schemas) == len(list(PACKAGED.iterdir())) > 0
    assert any(node.get("uniqueItems") for s in schemas.values() for _, node in _nodes(s, "#"))


def test_every_unique_array_is_bounded_before_its_uniqueness_check() -> None:
    found = violations(packaged())
    assert {(name, pointer) for name, pointer, _ in found} == set(RESIDUAL)
    for name, pointer, rule in found:
        assert RESIDUAL[(name, pointer)].startswith(f"{rule}:")


ARRAY = {"type": "array"}
BEFORE, UNSORTABLE, EXHAUSTED = (
    "UNIQUE_BEFORE_BOUND",
    "UNSORTABLE_UNBOUNDED",
    "UNIQUE_UNDER_EXHAUSTIVE_APPLICATOR",
)


@pytest.mark.parametrize(
    ("schema", "rules"),
    [
        ({**ARRAY, "uniqueItems": True, "items": {"type": "string"}}, {BEFORE}),
        ({**ARRAY, "uniqueItems": True}, {BEFORE, UNSORTABLE}),
        ({**ARRAY, "items": {"type": "object"}, "uniqueItems": True, "maxItems": 4}, {BEFORE}),
        ({**ARRAY, "items": {"type": "object"}, "uniqueItems": True}, {UNSORTABLE}),
        ({**ARRAY, "items": {"$ref": "#/$defs/o"}, "uniqueItems": True}, {UNSORTABLE}),
        ({**ARRAY, "items": {"$ref": "urn:x:elsewhere"}, "uniqueItems": True}, {UNSORTABLE}),
        ({"oneOf": [{"$ref": "#/$defs/s"}, {"type": "null"}]}, {EXHAUSTED}),
        ({"anyOf": [{**ARRAY, "items": {"type": "string"}, "uniqueItems": True}]}, {EXHAUSTED}),
    ],
)
def test_a_synthetic_violation_is_found(schema: dict[str, Any], rules: set[str]) -> None:
    document = {
        "$id": "urn:x:synthetic",
        "properties": {"a": schema},
        "$defs": {
            "o": {"type": "object"},
            "s": {**ARRAY, "items": {"type": "string"}, "uniqueItems": True},
        },
    }
    assert {rule for _, _, rule in violations({"synthetic.schema.json": document})} == rules


@pytest.mark.parametrize(
    "schema",
    [
        {**ARRAY, "items": {"type": "string"}, "uniqueItems": True},
        {**ARRAY, "items": {"$ref": "#/$defs/n"}, "uniqueItems": True},
        {**ARRAY, "maxItems": 4, "items": {"type": "object"}, "uniqueItems": True},
        {"if": {"type": "object"}, "then": {"$ref": "#/$defs/s"}, "else": {"type": "null"}},
    ],
)
def test_a_bounded_unique_array_passes(schema: dict[str, Any]) -> None:
    document = {
        "properties": {"a": schema},
        "$defs": {
            "n": {"type": "integer"},
            "s": {**ARRAY, "items": {"type": "string"}, "uniqueItems": True},
        },
    }
    assert violations({"synthetic.schema.json": document}) == set()


def test_a_reference_into_another_packaged_contract_is_followed() -> None:
    owner = {"$id": "urn:x:owner", "$defs": {"s": {**ARRAY, "uniqueItems": True}}}
    user = {"properties": {"a": {"oneOf": [{"$ref": "urn:x:owner#/$defs/s"}]}}}
    found = violations({"owner.schema.json": owner, "user.schema.json": user})
    assert ("owner.schema.json", "#/$defs/s", "UNIQUE_UNDER_EXHAUSTIVE_APPLICATOR") in found


# --- the if/then/else rewrites accept exactly what their oneOf did -----------------------
# Each branch set is disjoint: eprocess is an object, never null; measured_read's verdict
# enum excludes not_measured_read's const, and both require verdict. On disjoint branches
# "exactly one" is "any one", which the if/then/else routes to by the discriminating key.
# The comparison below varies exactly those keys (type, verdict) and the arrays beneath.

GOLDEN = ROOT / "fixtures/examples/acceptance"
WHOLE: tuple[object, ...] = (None, True, 0, 1.5, "x", [], {}, [None])
REWRITES: dict[str, tuple[tuple[str, ...], list[object]]] = {
    "read.v1": (
        ("properties", "eprocess"),
        [{"$ref": "#/$defs/eprocess"}, {"type": "null"}],
    ),
    "acceptance_decision.v1": (
        ("$defs", "claim", "properties", "read"),
        [
            {"type": "null"},
            {"$ref": "#/$defs/measured_read"},
            {"$ref": "#/$defs/not_measured_read"},
        ],
    ),
}


class _Validity(Protocol):
    def is_valid(self, instance: object) -> bool: ...


def _validity(schema: Schema, path: tuple[str, ...], node: object, seed: Schema) -> _Validity:
    """``schema`` with ``node`` at ``path`` and the seed localized as measure does."""
    copied: Any = json.loads(json.dumps(schema))
    if "seed" in copied["properties"]:
        copied["properties"]["seed"] = {k: v for k, v in seed.items() if k != "$id"}
    parent = copied
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = node
    return cast(_Validity, Draft202012Validator(copied))


def _shapes(base: Mapping[str, Any], key: str, values: tuple[object, ...]) -> list[object]:
    """Whole-value swaps, then base with ``key`` set to each value or dropped, reasons varied."""
    shapes: list[object] = [*WHOLE, {**base, "extra": 1}]
    for value in (*values, None, 1, "x"):
        for reasons in ([], ["below_quorum"], ["x"], [1, "a"]):
            shapes.append({**base, key: value, "reason_codes": reasons})
    shapes.append({k: v for k, v in base.items() if k != key})
    return shapes


def _instances(name: str) -> list[object]:
    if name == "read.v1":
        read = json.loads((GOLDEN / "read.json").read_bytes())
        values = ("complete", "threshold", "absorbed")
        return [{**read, "eprocess": s} for s in _shapes(read["eprocess"], "stop_reason", values)]
    decision = json.loads((GOLDEN / "expected/accept-not-measured.json").read_bytes())
    found: list[object] = []
    verdicts = ("pass", "warn", "fail", "not_measured")
    for index, claim in enumerate(decision["claims"]):
        for shape in _shapes(claim["read"], "verdict", verdicts):
            claims = [*decision["claims"]]
            claims[index] = {**claim, "read": shape}
            found.append({**decision, "claims": claims})
    return found


@pytest.mark.parametrize("name", sorted(REWRITES))
def test_the_if_then_else_accepts_exactly_what_one_of_did(name: str) -> None:
    schemas = packaged()
    seed, file = schemas["statistical_seed.v1.schema.json"], f"{name}.schema.json"
    path, one_of = REWRITES[name]
    rewritten = cast(Schema, _at(schemas, file, "#/" + "/".join(path)))
    assert {"if", "then", "else"} & set(rewritten) and "oneOf" not in rewritten
    now = _validity(schemas[file], path, rewritten, seed)
    before = _validity(schemas[file], path, {"oneOf": one_of}, seed)
    outcomes = [(now.is_valid(i), before.is_valid(i)) for i in _instances(name)]
    assert all(a == b for a, b in outcomes)
    assert {a for a, _ in outcomes} == {True, False}  # both sides of the boundary are exercised


# --- product code validates to the first error only --------------------------------------
# jsonschema's module-level validate() and best_match() walk iter_errors() to the end, which
# evaluates every keyword -- uniqueItems included -- whatever precedes it.

EXHAUSTIVE_ERRORS = frozenset({"iter_errors", "best_match"})


def exhaustive_error_uses(source: str) -> list[int]:
    """Lines that collect every validation error instead of stopping at the first."""
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("jsonschema"):
            names = {alias.name for alias in node.names}
            if names & (EXHAUSTIVE_ERRORS | {"validate"}):
                found.append(node.lineno)
        elif isinstance(node, ast.Attribute) and (
            node.attr in EXHAUSTIVE_ERRORS
            or (
                node.attr == "validate"
                and isinstance(node.value, ast.Name)
                and node.value.id == "jsonschema"
            )
        ):
            found.append(node.lineno)
    return found


def product_sources() -> list[Path]:
    roots = [ROOT / "src", *sorted((ROOT / "packages").glob("*/src"))]
    return sorted(path for root in roots for path in root.rglob("*.py"))


def test_product_code_never_collects_every_validation_error() -> None:
    sources = product_sources()
    assert ROOT / "src/underwrite/instrument/ingest/schema.py" in sources
    assert any(path.is_relative_to(ROOT / "packages") for path in sources)
    found = {
        f"{path.relative_to(ROOT)}:{line}"
        for path in sources
        for line in exhaustive_error_uses(path.read_text(encoding="utf-8"))
    }
    assert found == set()


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ("errors = list(validator.iter_errors(document))", True),
        ("from jsonschema.exceptions import best_match", True),
        ("error = exceptions.best_match(errors)", True),
        ("import jsonschema\njsonschema.validate(document, schema)", True),
        ("from jsonschema import validate", True),
        ("validator.validate(document)", False),
        ("ok = validator.is_valid(document)", False),
        ("from jsonschema import Draft202012Validator", False),
    ],
)
def test_an_exhaustive_error_use_is_found(source: str, flagged: bool) -> None:
    assert bool(exhaustive_error_uses(source)) is flagged

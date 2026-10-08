"""The one runtime YAML reader: ``load_gate_rules`` turns one local gate-rules file
into plain ``gate_rules.v1`` data -- dict, list, str, int, bool and None only -- or fails
with one fixed reason. It loads; it does not decide (ordering, priority meaning,
restrictiveness and honesty-state routing require a separate evaluator).

``_Strict`` accepts a small YAML subset. ``compose_node`` refuses aliases, anchors, any
explicit tag and depth before a node is built; ``construct_document`` builds through its
own tag allowlist (str; canonical int of at most 16 digits; ``true``/``false``; ``null`` or
empty) and never consults the inherited constructor table. ``RULES_MAX_DEPTH`` counts every
node -- the root is 1, and each key, scalar and collection is one deeper than its parent --
unlike ``POLICY_MAX_DEPTH`` (``cli.measure``), which counts JSON containers only.

A refusal is ``GateRulesError(reason, location, keyword)``. ``location`` is ``line N`` in
the YAML phase and a JSON pointer of declared names and indices in the data phase, else
empty; ``keyword`` is the failing JSON Schema keyword. None of them holds received text,
and every refusal is raised outside any exception handler, so no transport, codec, PyYAML
or jsonschema exception is its ``__cause__`` or ``__context__``.

The file is strict UTF-8. One leading UTF-8 BOM (U+FEFF) is accepted: PyYAML's reader skips
it before the first token, as YAML 1.1 allows."""

from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path
from typing import Any, Protocol, TypeGuard

import yaml
from jsonschema.exceptions import ValidationError
from jsonschema.protocols import Validator
from underwrite_core.canonical import CanonicalizationError, canonical_bytes
from yaml.composer import ComposerError

from underwrite.instrument.ingest.schema import packaged_validator
from underwrite.instrument.ingest.transport import TransportError, read_local_file

RULES_MAX_BYTES = 64 * 1024
RULES_MAX_DEPTH = 16
CONTRACT = "gate_rules.v1"
REASONS = frozenset(
    {
        # read phase (the first four are ingest.transport's)
        "SOURCE_READ_FAILED",
        "SOURCE_NOT_REGULAR_FILE",
        "UNSUPPORTED_PLATFORM",
        "BYTE_LIMIT_EXCEEDED",
        "INVALID_UTF8",
        # YAML phase
        "INVALID_YAML",
        "EMPTY_DOCUMENT",
        "MULTI_DOCUMENT",
        "DEPTH_LIMIT_EXCEEDED",
        "ALIAS_NOT_ALLOWED",
        "ANCHOR_NOT_ALLOWED",
        "TAG_NOT_ALLOWED",
        "MERGE_KEY_NOT_ALLOWED",
        "DUPLICATE_KEY",
        "NON_STRING_KEY",
        "NONFINITE_SCALAR",
        "FLOAT_NOT_ALLOWED",
        "AMBIGUOUS_SCALAR",
        # data phase (NONCANONICAL_INPUT is also a YAML-phase reason: an integer of more
        # than _INT_MAX_DIGITS digits is refused at its ``line N`` before it is built)
        "OBJECT_REQUIRED",
        "NONCANONICAL_INPUT",
        "SCHEMA_VALIDATION_FAILED",
        "DUPLICATE_RULE_ID",
        "INVALID_BOUNDS",
    }
)
_TAG = "tag:yaml.org,2002:"
_INT = re.compile(r"0|-?[1-9][0-9]*")
_INT_MAX_DIGITS = 16


class GateRulesError(ValueError):
    """A refused gate-rules file: a fixed ``reason``, where, and the schema keyword."""

    def __init__(self, reason: str, location: str = "", keyword: str = "") -> None:
        self.reason = reason
        self.location = location
        self.keyword = keyword

    def __str__(self) -> str:
        return f"{self.reason} at {self.location}" if self.location else self.reason


class _Mark(Protocol):
    line: int


class _EventFields(Protocol):
    anchor: str | None
    tag: str | None
    start_mark: _Mark


def _line(mark: _Mark) -> str:
    return f"line {mark.line + 1}"


class _Strict(yaml.SafeLoader):
    depth: int = 0
    composed: bool = False

    def compose_node(self, parent: yaml.nodes.Node | None, index: int) -> yaml.nodes.Node | None:
        self.composed = True
        event = self._next_event()
        if isinstance(event, yaml.events.AliasEvent):
            raise GateRulesError("ALIAS_NOT_ALLOWED", _line(event.start_mark))
        if event.anchor is not None:
            raise GateRulesError("ANCHOR_NOT_ALLOWED", _line(event.start_mark))
        if event.tag is not None:
            raise GateRulesError("TAG_NOT_ALLOWED", _line(event.start_mark))
        self.depth += 1
        if self.depth > RULES_MAX_DEPTH:
            raise GateRulesError("DEPTH_LIMIT_EXCEEDED", _line(event.start_mark))
        node = super().compose_node(parent, index)
        self.depth -= 1
        return node

    def _next_event(self) -> _EventFields:
        return self.peek_event()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

    def construct_document(self, node: yaml.nodes.Node) -> object:
        return _plain(node)


def _is_dict(value: object) -> TypeGuard[dict[str, Any]]:
    return isinstance(value, dict)


def _is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _plain(node: yaml.nodes.Node) -> object:
    if isinstance(node, yaml.nodes.SequenceNode):
        return [_plain(item) for item in node.value]
    if isinstance(node, yaml.nodes.MappingNode):
        pairs: list[tuple[yaml.nodes.Node, yaml.nodes.Node]] = node.value
        seen: set[str] = set()
        for key, _ in pairs:
            name = _key(key)
            if name in seen:
                raise GateRulesError("DUPLICATE_KEY", _line(key.start_mark))
            seen.add(name)
        return {_key(key): _plain(value) for key, value in pairs}
    return _scalar(node.tag.removeprefix(_TAG), node.value, _line(node.start_mark))


def _key(node: yaml.nodes.Node) -> str:
    if isinstance(node, yaml.nodes.ScalarNode) and node.tag == _TAG + "str":
        return node.value
    if node.tag == _TAG + "merge":
        raise GateRulesError("MERGE_KEY_NOT_ALLOWED", _line(node.start_mark))
    raise GateRulesError("NON_STRING_KEY", _line(node.start_mark))


def _scalar(tag: str, value: str, where: str) -> object:
    if tag == "str":
        return value
    if tag == "int" and _INT.fullmatch(value):
        if len(value.removeprefix("-")) > _INT_MAX_DIGITS:
            raise GateRulesError("NONCANONICAL_INPUT", where)
        return int(value)
    if tag == "bool" and value in ("true", "false"):
        return value == "true"
    if tag == "null" and value in ("null", ""):
        return None
    if tag == "float":
        finite = value.lower() not in (".inf", "+.inf", "-.inf", ".nan")
        raise GateRulesError("FLOAT_NOT_ALLOWED" if finite else "NONFINITE_SCALAR", where)
    raise GateRulesError("AMBIGUOUS_SCALAR", where)


def _declared_names(schema: object) -> frozenset[str]:
    """Every property name the contract declares (it has no patternProperties)."""
    names: set[str] = set()
    pending: list[object] = [schema]
    for node in pending:
        if _is_dict(node):
            properties = node.get("properties")
            if _is_dict(properties):
                names.update(properties)
            pending.extend(node.values())
    return frozenset(names)


def _check_canonical(value: object, pointer: str, names: frozenset[str]) -> None:
    """Each leaf under declared names canonicalizes; an undeclared member is the
    schema's to refuse, so no received name reaches a pointer."""
    if _is_dict(value):
        for name, item in value.items():
            if name in names:
                _check_canonical(item, f"{pointer}/{name}", names)
    elif _is_list(value):
        for index, item in enumerate(value):
            _check_canonical(item, f"{pointer}/{index}", names)
    elif not _canonical(value):
        raise GateRulesError("NONCANONICAL_INPUT", pointer)


def _canonical(value: object) -> bool:
    try:
        canonical_bytes(value)
    except CanonicalizationError:
        return False
    return True


def _check_semantics(document: dict[str, Any]) -> None:
    """What the schema cannot state: unique rule ids, then min <= max in each bounds."""
    seen: set[str] = set()
    for index, rule in enumerate(document["rules"]):
        if rule["id"] in seen:
            raise GateRulesError("DUPLICATE_RULE_ID", f"/rules/{index}/id")
        seen.add(rule["id"])
    for index, rule in enumerate(document["rules"]):
        for mode, predicates in rule["when"].items():
            for position, predicate in enumerate(predicates):
                for metric, bounds in predicate.items():
                    if (
                        _is_dict(bounds)
                        and {"min", "max"} <= bounds.keys()
                        and Fraction(bounds["min"]) > Fraction(bounds["max"])
                    ):
                        pointer = f"/rules/{index}/when/{mode}/{position}/{metric}"
                        raise GateRulesError("INVALID_BOUNDS", pointer)


def _validate(validator: Validator, document: dict[str, Any]) -> None:
    """Canonical leaves first (no received name in a pointer), then the contract."""
    _check_canonical(document, "", _declared_names(validator.schema))
    failure: tuple[str, str] | None = None
    try:
        validator.validate(document)  # the first error only
    except ValidationError as exc:
        failure = ("".join(f"/{part}" for part in exc.absolute_path), str(exc.validator))
    if failure is not None:
        raise GateRulesError("SCHEMA_VALIDATION_FAILED", *failure)


def _text(path: Path) -> str | GateRulesError:
    """The file as strict UTF-8 text, or its read-phase refusal -- returned, not raised, so
    the caller raises it outside any handler."""
    try:
        return read_local_file(path, RULES_MAX_BYTES).decode()
    except TransportError as exc:
        return GateRulesError(str(exc))
    except UnicodeDecodeError:
        return GateRulesError("INVALID_UTF8")


def _parse(text: str) -> tuple[bool, object] | GateRulesError:
    """(whether a node was composed, the plain document), or the PyYAML refusal (returned,
    as in ``_text``). A ``GateRulesError`` from ``_Strict`` passes through unhandled."""
    try:
        loader = _Strict(text)
        document = loader.get_single_data()
    except ComposerError:
        return GateRulesError("MULTI_DOCUMENT")
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark
        return GateRulesError("INVALID_YAML", "" if mark is None else _line(mark))
    except yaml.YAMLError:
        return GateRulesError("INVALID_YAML")
    return loader.composed, document


def load_gate_rules(path: Path) -> dict[str, Any]:
    """Read, strictly decode and validate one gate-rules file (see the module docstring)."""
    text = _text(path)
    if isinstance(text, GateRulesError):
        raise text
    parsed = _parse(text)
    if isinstance(parsed, GateRulesError):
        raise parsed
    composed, document = parsed
    if not composed:
        raise GateRulesError("EMPTY_DOCUMENT")
    if not _is_dict(document):
        raise GateRulesError("OBJECT_REQUIRED")
    _validate(packaged_validator(CONTRACT), document)
    _check_semantics(document)
    return document

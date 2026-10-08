"""Standalone behavior and boundary checks."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from _repo_paths import repo_root
from _trusted_contracts import trust_contracts
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from underwrite.cli import gate_rules
from underwrite.cli.gate_rules import GateRulesError, load_gate_rules
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = repo_root(Path(__file__).resolve())
CONTRACT = "gate_rules.v1"

Rules = dict[str, Any]

MINIMAL: Rules = {
    "schema": "gate_rules.v1",
    "default": {"decision": "Hold"},
    "rules": [
        {
            "id": "failure_rate_ceiling",
            "kind": "hard_gate",
            "priority": 0,
            "when": {"all": [{"failure_rate_current": {"min": "0.2"}}]},
            "decision": "Reject",
        }
    ],
}


@pytest.fixture
def validator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Draft202012Validator:
    trust_contracts(tmp_path, monkeypatch, CONTRACT)
    return packaged_validator(CONTRACT)


def first_error(validator: Draft202012Validator, instance: object) -> tuple[str, str]:
    with pytest.raises(ValidationError) as caught:
        validator.validate(instance)  # pyright: ignore[reportUnknownMemberType]
    return str(caught.value.validator), "/".join(str(part) for part in caught.value.absolute_path)


def minimal_with(edit: Callable[[Rules], object]) -> Rules:
    instance = copy.deepcopy(MINIMAL)
    edit(instance)
    return instance


def rule(instance: Rules) -> Rules:
    first: Rules = instance["rules"][0]
    return first


def predicate(instance: Rules) -> Rules:
    first: Rules = rule(instance)["when"]["all"][0]
    return first


def threshold(value: object) -> Callable[[Rules], object]:
    return lambda instance: predicate(instance).update({"failure_rate_current": {"min": value}})


def test_contract_is_a_local_draft_2020_12_schema_loaded_as_a_trusted_contract(
    validator: Draft202012Validator,
) -> None:
    schema = json.loads((ROOT / "contracts" / f"{CONTRACT}.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    assert schema["$id"] == f"urn:underwrite:contract:{CONTRACT}"
    assert validator.schema == schema


def test_every_vocabulary_member_and_bound_is_accepted(validator: Draft202012Validator) -> None:
    decisions = ["Approve", "Conditional", "Shadow", "HITL-only", "Hold", "Reject", "Rollback"]
    rules: list[Rules] = [
        {
            "id": f"r{index}",
            "kind": "optimization_metric" if index % 2 else "hard_gate",
            "priority": 1000 if index % 2 else 0,
            "when": {"any" if index % 2 else "all": [{"acceptance": ["confirmed"]}]},
            "decision": decision,
        }
        for index, decision in enumerate(decisions)
    ]
    rules[0]["when"] = {
        "all": [
            {"read_verdict": ["pass", "warn", "fail"]},
            {"acceptance": ["screened", "confirmed", "rejected"]},
            {"bootstrap_ci_lower": {"min": "-0.2"}},
            {"bootstrap_ci_upper": {"max": "1.05"}},
            {"cohens_d": {"min": "0", "max": "10"}},
            {"failure_rate_current": {"max": "0.2"}},
            *[{"cohens_d": {"min": "0.5"}}] * 26,
        ]
    }
    validator.validate(  # pyright: ignore[reportUnknownMemberType]
        {"schema": CONTRACT, "default": {"decision": "Hold"}, "rules": rules}
    )
    validator.validate(  # pyright: ignore[reportUnknownMemberType]
        {"schema": CONTRACT, "default": {"decision": "Hold"}, "rules": [rules[1]] * 256}
    )


@pytest.mark.parametrize(
    "value", ["0", "0.2", "-0.2", "1.05", "10", "-0.5", "1234567890123456.123456789012345"]
)
def test_canonical_decimal_thresholds_are_accepted(
    validator: Draft202012Validator, value: str
) -> None:
    validator.validate(minimal_with(threshold(value)))  # pyright: ignore[reportUnknownMemberType]


def _set(*path: str | int, value: object) -> Callable[[Rules], object]:
    def edit(instance: Rules) -> None:
        node: Any = instance
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = value

    return edit


CASES: list[tuple[str, Callable[[Rules], object], str, str]] = [
    ("empty-rules", _set("rules", value=[]), "minItems", "rules"),
    ("too-many-rules", _set("rules", value=[MINIMAL["rules"][0]] * 257), "maxItems", "rules"),
    ("unknown-root-key", _set("version", value=1), "additionalProperties", ""),
    ("missing-default", lambda i: i.pop("default"), "required", ""),
    ("default-not-hold", _set("default", "decision", value="Reject"), "const", "default/decision"),
    ("default-extra-key", _set("default", "reason", value="x"), "additionalProperties", "default"),
    ("wrong-schema", _set("schema", value="gate_rules.v2"), "const", "schema"),
    ("missing-priority", lambda i: rule(i).pop("priority"), "required", "rules/0"),
    ("unknown-rule-key", _set("rules", 0, "action", value="x"), "additionalProperties", "rules/0"),
    ("unknown-kind", _set("rules", 0, "kind", value="soft_gate"), "enum", "rules/0/kind"),
    ("promote-decision", _set("rules", 0, "decision", value="Promote"), "enum", "rules/0/decision"),
    ("lower-decision", _set("rules", 0, "decision", value="hold"), "enum", "rules/0/decision"),
    ("priority-negative", _set("rules", 0, "priority", value=-1), "minimum", "rules/0/priority"),
    ("priority-above", _set("rules", 0, "priority", value=1001), "maximum", "rules/0/priority"),
    ("priority-string", _set("rules", 0, "priority", value="10"), "type", "rules/0/priority"),
    ("priority-bool", _set("rules", 0, "priority", value=True), "type", "rules/0/priority"),
    ("priority-float", _set("rules", 0, "priority", value=1.5), "type", "rules/0/priority"),
    ("id-newline", _set("rules", 0, "id", value="a\n"), "pattern", "rules/0/id"),
    ("id-upper", _set("rules", 0, "id", value="Rule"), "pattern", "rules/0/id"),
    ("id-long", _set("rules", 0, "id", value="a" * 65), "pattern", "rules/0/id"),
    (
        "when-both",
        _set("rules", 0, "when", "any", value=[{"cohens_d": {"min": "1"}}]),
        "maxProperties",
        "rules/0/when",
    ),
    ("when-empty", _set("rules", 0, "when", value={}), "minProperties", "rules/0/when"),
    (
        "when-unknown",
        _set("rules", 0, "when", value={"none": []}),
        "additionalProperties",
        "rules/0/when",
    ),
    ("all-empty", _set("rules", 0, "when", "all", value=[]), "minItems", "rules/0/when/all"),
    (
        "all-too-many",
        _set("rules", 0, "when", "all", value=[{"cohens_d": {"min": "1"}}] * 33),
        "maxItems",
        "rules/0/when/all",
    ),
    (
        "predicate-unknown",
        _set("rules", 0, "when", "all", 0, value={"cohen_d": {"min": "1"}}),
        "additionalProperties",
        "rules/0/when/all/0",
    ),
    (
        "predicate-two-keys",
        _set("rules", 0, "when", "all", 0, "cohens_d", value={"min": "1"}),
        "maxProperties",
        "rules/0/when/all/0",
    ),
    (
        "predicate-empty",
        _set("rules", 0, "when", "all", 0, value={}),
        "minProperties",
        "rules/0/when/all/0",
    ),
    (
        "verdict-not-measured",
        _set("rules", 0, "when", "all", 0, value={"read_verdict": ["not_measured"]}),
        "enum",
        "rules/0/when/all/0/read_verdict/0",
    ),
    (
        "acceptance-indeterminate",
        _set("rules", 0, "when", "all", 0, value={"acceptance": ["indeterminate"]}),
        "enum",
        "rules/0/when/all/0/acceptance/0",
    ),
    (
        "verdict-empty",
        _set("rules", 0, "when", "all", 0, value={"read_verdict": []}),
        "minItems",
        "rules/0/when/all/0/read_verdict",
    ),
    (
        "verdict-repeat",
        _set("rules", 0, "when", "all", 0, value={"read_verdict": ["pass", "pass"]}),
        "uniqueItems",
        "rules/0/when/all/0/read_verdict",
    ),
    (
        "verdict-too-many",
        _set(
            "rules", 0, "when", "all", 0, value={"read_verdict": ["pass", "warn", "fail", "pass"]}
        ),
        "maxItems",
        "rules/0/when/all/0/read_verdict",
    ),
    (
        "bounds-empty",
        _set("rules", 0, "when", "all", 0, "failure_rate_current", value={}),
        "minProperties",
        "rules/0/when/all/0/failure_rate_current",
    ),
    (
        "bounds-unknown",
        _set("rules", 0, "when", "all", 0, "failure_rate_current", value={"gte": "1"}),
        "additionalProperties",
        "rules/0/when/all/0/failure_rate_current",
    ),
    ("threshold-number", threshold(0.2), "type", "rules/0/when/all/0/failure_rate_current/min"),
    *[
        (
            "threshold-trailing-newline" if text == "0.5\n" else f"threshold-{text!r}",
            threshold(text),
            "pattern",
            "rules/0/when/all/0/failure_rate_current/min",
        )
        for text in [
            "1e-3",
            "0.20",
            "-0",
            "-0.0",
            "00",
            "01",
            "1.0",
            "0.5\n",
            "+1",
            ".5",
            "1.",
            "",
            "12345678901234567",
            "0.1234567890123456",
        ]
    ],
]


@pytest.mark.parametrize(
    "edit,keyword,path", [case[1:] for case in CASES], ids=[case[0] for case in CASES]
)
def test_first_error_names_the_keyword_and_instance_path(
    validator: Draft202012Validator,
    edit: Callable[[Rules], object],
    keyword: str,
    path: str,
) -> None:
    assert first_error(validator, minimal_with(edit)) == (keyword, path)


# --- the strict loader (T45-06: YAML and read corpus) ----------------------------

TOKEN = "zqcanary7f3a"
RULES = ROOT / "rules/gate-rules.yaml"
REASONS = frozenset(
    {
        "SOURCE_READ_FAILED",
        "SOURCE_NOT_REGULAR_FILE",
        "UNSUPPORTED_PLATFORM",
        "BYTE_LIMIT_EXCEEDED",
        "INVALID_UTF8",
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
        "OBJECT_REQUIRED",
        "NONCANONICAL_INPUT",
        "SCHEMA_VALIDATION_FAILED",
        "DUPLICATE_RULE_ID",
        "INVALID_BOUNDS",
    }
)
# MINIMAL as YAML. The line numbers below count from 1: kind is line 6, priority line 7.
BASE = """\
schema: gate_rules.v1
default:
  decision: Hold
rules:
  - id: failure_rate_ceiling
    kind: hard_gate
    priority: 0
    when:
      all:
        - failure_rate_current: {min: "0.2"}
    decision: Reject
"""
# (id, file content, reason, location, keyword)
Row = tuple[str, str | bytes, str, str, str]


@pytest.fixture
def trusted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trust_contracts(tmp_path, monkeypatch, CONTRACT)


def edit(old: str, new: str, text: str = BASE) -> str:
    assert text.count(old) == 1
    return text.replace(old, new)


def kind(value: str) -> str:
    return edit("    kind: hard_gate", f"    kind: {value}  # {TOKEN}")


def priority(value: str) -> str:
    return edit("    priority: 0", f"    priority: {value}  # {TOKEN}")


def default_key(key: str) -> str:
    return edit("  decision: Hold", f"  {key}: {TOKEN}\n  decision: Hold")


def nested(levels: int) -> str:
    """BASE plus one undeclared member whose innermost scalar is at node depth levels + 2."""
    return f"{BASE}{TOKEN}: {'[' * levels}a{']' * levels}\n"


def nested_mappings(levels: int) -> str:
    """As ``nested``, through mappings that each finish a sibling pair first: leaving a node
    must restore its parent's depth, not reset it."""
    return f"{BASE}{TOKEN}: {'{k: 0, v: ' * levels}a{'}' * levels}\n"


def write(tmp_path: Path, content: str | bytes) -> Path:
    path = tmp_path / f"{TOKEN}.yaml"
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    return path


def refusal(path: Path) -> GateRulesError:
    """The error, after the canary: no received text and no library exception as its cause or
    its context (every refusal is raised outside any handler)."""
    with pytest.raises(GateRulesError) as caught:
        load_gate_rules(path)
    error = caught.value
    assert TOKEN not in str(error)
    assert all(TOKEN not in str(arg) for arg in error.args)
    assert TOKEN not in error.location
    assert TOKEN not in error.keyword
    assert error.__cause__ is None
    assert error.__context__ is None
    return error


YAML_ROWS: list[Row] = [
    (
        "duplicate-key-block",
        edit("    kind: hard_gate\n", f"    kind: hard_gate\n    kind: {TOKEN}\n"),
        "DUPLICATE_KEY",
        "line 7",
        "",
    ),
    (
        "duplicate-key-flow",
        edit('{min: "0.2"}', f'{{min: "0.2", min: "{TOKEN}"}}'),
        "DUPLICATE_KEY",
        "line 10",
        "",
    ),
    ("anchor", priority(f"&{TOKEN} 0"), "ANCHOR_NOT_ALLOWED", "line 7", ""),
    ("alias", kind(f"*{TOKEN}"), "ALIAS_NOT_ALLOWED", "line 6", ""),
    (
        "alias-bomb",
        f"a: &a [{TOKEN}, {TOKEN}]\nb: &b [*a, *a]\nc: &c [*b, *b]\n{BASE}",
        "ANCHOR_NOT_ALLOWED",
        "line 1",
        "",
    ),
    ("merge-key", default_key("<<"), "MERGE_KEY_NOT_ALLOWED", "line 3", ""),
    *[
        (f"tag-{tagged}", kind(tagged), "TAG_NOT_ALLOWED", "line 6", "")
        for tagged in [
            "!custom x",
            "!!python/object:os.system x",
            "!!str 1",
            "!!binary aGk=",
            "!!set {a}",
            "!!omap []",
            "! x",
        ]
    ],
    ("multi-document", f"{BASE}---\nb: {TOKEN}\n", "MULTI_DOCUMENT", "", ""),
    ("empty-file", "", "EMPTY_DOCUMENT", "", ""),
    ("comments-only", f"# {TOKEN}\n", "EMPTY_DOCUMENT", "", ""),
    ("null-document", f"null  # {TOKEN}\n", "OBJECT_REQUIRED", "", ""),
    ("explicit-empty-document", f"---  # {TOKEN}\n", "OBJECT_REQUIRED", "", ""),
    ("sequence-root", f"- {TOKEN}\n", "OBJECT_REQUIRED", "", ""),
    *[
        (f"key-{key}", default_key(key), "NON_STRING_KEY", "line 3", "")
        for key in ["1", "true", "null", "yes"]
    ],
    (
        "key-sequence",
        edit("  decision: Hold", f"  ? [{TOKEN}]\n  : x\n  decision: Hold"),
        "NON_STRING_KEY",
        "line 3",
        "",
    ),
    *[
        (f"nonfinite-{value}", priority(value), "NONFINITE_SCALAR", "line 7", "")
        for value in [".inf", "-.Inf", "+.INF", ".NaN"]
    ],
    *[
        (f"float-{value}", priority(value), "FLOAT_NOT_ALLOWED", "line 7", "")
        for value in ["0.2", "1.0e+3"]
    ],
    *[
        (f"ambiguous-{value}", priority(value), "AMBIGUOUS_SCALAR", "line 7", "")
        for value in [
            "yes",
            "on",
            "True",
            "0x1F",
            "017",
            "0b101",
            "1_000",
            "+1",
            "-0",
            "1:30",
            "~",
            "Null",
            "2026-05-13",
            "=",
        ]
    ],
    ("int-17-digits", priority("12345678901234567"), "NONCANONICAL_INPUT", "line 7", ""),
    (
        "int-16-digits-reaches-the-schema",
        priority("1000000000000000"),
        "SCHEMA_VALIDATION_FAILED",
        "/rules/0/priority",
        "maximum",
    ),
    (
        "int-beyond-safe-range",
        priority("9999999999999999"),
        "NONCANONICAL_INPUT",
        "/rules/0/priority",
        "",
    ),
    ("depth-16-passes-yaml", nested(14), "SCHEMA_VALIDATION_FAILED", "", "additionalProperties"),
    ("depth-17", nested(15), "DEPTH_LIMIT_EXCEEDED", "line 12", ""),
    (
        "depth-16-mappings-pass-yaml",
        nested_mappings(14),
        "SCHEMA_VALIDATION_FAILED",
        "",
        "additionalProperties",
    ),
    ("depth-17-mappings", nested_mappings(15), "DEPTH_LIMIT_EXCEEDED", "line 12", ""),
    ("depth-5000", "[" * 5000 + TOKEN, "DEPTH_LIMIT_EXCEEDED", "line 1", ""),
    ("byte-ff", f"{BASE}# {TOKEN}\n".encode() + b"\xff", "INVALID_UTF8", "", ""),
    ("utf-16-bom", f"{BASE}# {TOKEN}\n".encode("utf-16"), "INVALID_UTF8", "", ""),
    ("nul", kind("hard\x00gate"), "INVALID_YAML", "", ""),
    ("bell", kind("hard\x07gate"), "INVALID_YAML", "", ""),
    ("tab-indent", edit("    kind: hard_gate", f"\tkind: {TOKEN}"), "INVALID_YAML", "line 6", ""),
    (
        "byte-limit",
        f"{BASE}# {TOKEN}".ljust(gate_rules.RULES_MAX_BYTES + 1, "#"),
        "BYTE_LIMIT_EXCEEDED",
        "",
        "",
    ),
    (
        "non-nfc-value",
        edit('"0.2"', f'"{TOKEN}é"'),
        "NONCANONICAL_INPUT",
        "/rules/0/when/all/0/failure_rate_current/min",
        "",
    ),
    (
        "lone-surrogate-id",
        edit("  - id: failure_rate_ceiling", f'  - id: "\\ud800"  # {TOKEN}'),
        "NONCANONICAL_INPUT",
        "/rules/0/id",
        "",
    ),
    (
        "lone-surrogate-read-verdict-item",
        edit(
            '        - failure_rate_current: {min: "0.2"}',
            f'        - read_verdict: [pass, "\\udc00"]  # {TOKEN}',
        ),
        "NONCANONICAL_INPUT",
        "/rules/0/when/all/0/read_verdict/1",
        "",
    ),
    (
        "non-nfc-under-undeclared-name",
        f'{BASE}{TOKEN}: "é"\n',
        "SCHEMA_VALIDATION_FAILED",
        "",
        "additionalProperties",
    ),
]


def _fifo(path: Path) -> Path:
    os.mkfifo(path)
    return path


def _directory(path: Path) -> Path:
    path.mkdir()
    return path


READ_ROWS: list[tuple[str, Callable[[Path], Path], str]] = [
    ("directory", _directory, "SOURCE_NOT_REGULAR_FILE"),
    ("fifo", _fifo, "SOURCE_NOT_REGULAR_FILE"),
    ("missing", lambda path: path, "SOURCE_READ_FAILED"),
]


@pytest.mark.parametrize("make,reason", [r[1:] for r in READ_ROWS], ids=[r[0] for r in READ_ROWS])
def test_unreadable_path_is_refused_with_the_transport_reason(
    tmp_path: Path, make: Callable[[Path], Path], reason: str
) -> None:
    error = refusal(make(tmp_path / TOKEN))
    assert (error.reason, error.location, error.keyword) == (reason, "", "")


@pytest.mark.parametrize("flag", ["O_NONBLOCK", "O_NOCTTY"])
def test_missing_safe_open_capability_is_an_unsupported_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    path = write(tmp_path, BASE)
    monkeypatch.delattr(os, flag)
    error = refusal(path)
    assert (error.reason, error.location, error.keyword) == ("UNSUPPORTED_PLATFORM", "", "")


def test_error_text_is_the_reason_and_its_location() -> None:
    assert str(GateRulesError("DUPLICATE_KEY", "line 3")) == "DUPLICATE_KEY at line 3"
    assert str(GateRulesError("EMPTY_DOCUMENT")) == "EMPTY_DOCUMENT"
    error = GateRulesError("SCHEMA_VALIDATION_FAILED", "/rules", "minItems")
    assert (error.reason, error.location, error.keyword) == (
        "SCHEMA_VALIDATION_FAILED",
        "/rules",
        "minItems",
    )


def test_accepted_scalars_decode_to_exact_plain_types() -> None:
    text = "{t: true, f: false, n: null, e: , z: 0, m: -12, big: 1234567890123456, s: x, q: 'true'}"
    decoded = gate_rules._Strict(text).get_single_data()  # pyright: ignore[reportPrivateUsage]
    expected = {
        "t": True,
        "f": False,
        "n": None,
        "e": None,
        "z": 0,
        "m": -12,
        "big": 1234567890123456,
        "s": "x",
        "q": "true",
    }
    assert decoded == expected
    assert [type(value) for value in decoded.values()] == [type(v) for v in expected.values()]


def test_a_leading_utf8_bom_is_accepted(trusted: None, tmp_path: Path) -> None:
    """PyYAML's reader skips one leading U+FEFF; the loader documents and keeps that."""
    assert load_gate_rules(write(tmp_path, "\ufeff" + BASE)) == MINIMAL


def test_a_yaml_error_without_a_mark_is_refused_without_a_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PyYAML's own errors always carry a mark; the loader still refuses one that does not."""

    def unmarked(self: object) -> object:
        raise yaml.MarkedYAMLError(problem=TOKEN)

    monkeypatch.setattr(gate_rules._Strict, "get_single_data", unmarked)  # pyright: ignore[reportPrivateUsage]
    error = refusal(write(tmp_path, BASE))
    assert (error.reason, error.location, error.keyword) == ("INVALID_YAML", "", "")


def test_surface_driver_names_each_refusal_on_its_last_stderr_line(tmp_path: Path) -> None:
    last_lines: list[str] = []
    for name, content in [("dup", YAML_ROWS[0][1]), ("anchor", YAML_ROWS[2][1])]:
        path = tmp_path / f"{name}.yaml"
        path.write_text(str(content), encoding="utf-8")
        driver = (
            "from pathlib import Path; from underwrite.cli.gate_rules import load_gate_rules; "
            f"load_gate_rules(Path({str(path)!r}))"
        )
        run = subprocess.run(
            [sys.executable, "-B", "-c", driver],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            check=False,
        )
        assert run.returncode != 0
        last_lines.append(run.stderr.strip().splitlines()[-1])
    assert last_lines[0] != last_lines[1]
    assert "DUPLICATE_KEY" in last_lines[0]
    assert "ANCHOR_NOT_ALLOWED" in last_lines[1]


RULES_TEXT = RULES.read_text(encoding="utf-8")


def rules_edit(old: str, new: str) -> str:
    return edit(old, new, RULES_TEXT)


def line_of(text: str, needle: str) -> str:
    return f"line {text.splitlines().index(needle) + 1}"


SEMANTIC_ROWS: list[Row] = [
    (
        "unknown-predicate",
        rules_edit("- read_verdict: [fail]", f"- {TOKEN}: [fail]"),
        "SCHEMA_VALIDATION_FAILED",
        "/rules/0/when/all/0",
        "additionalProperties",
    ),
    (
        "unknown-decision",
        rules_edit("    decision: Approve", f"    decision: Promote  # {TOKEN}"),
        "SCHEMA_VALIDATION_FAILED",
        "/rules/4/decision",
        "enum",
    ),
    (
        "missing-default",
        rules_edit("default:\n", f"# {TOKEN}\n").replace("  decision: Hold\n", ""),
        "SCHEMA_VALIDATION_FAILED",
        "",
        "required",
    ),
    (
        "default-reject",
        rules_edit("  decision: Hold", f"  decision: Reject  # {TOKEN}"),
        "SCHEMA_VALIDATION_FAILED",
        "/default/decision",
        "const",
    ),
    *[
        (
            f"priority-{value}",
            rules_edit("    priority: 400", f"    priority: {value}  # {TOKEN}"),
            "SCHEMA_VALIDATION_FAILED",
            "/rules/0/priority",
            keyword,
        )
        for value, keyword in [
            ("-1", "minimum"),
            ("1001", "maximum"),
            ('"10"', "type"),
            ("true", "type"),
        ]
    ],
    (
        "priority-float",
        rules_edit("    priority: 400", f"    priority: 1.5  # {TOKEN}"),
        "FLOAT_NOT_ALLOWED",
        line_of(RULES_TEXT, "    priority: 400"),
        "",
    ),
    (
        "duplicate-rule-id",
        rules_edit("  - id: acceptance_rejected", f"  - id: read_verdict_fail  # {TOKEN}"),
        "DUPLICATE_RULE_ID",
        "/rules/1/id",
        "",
    ),
    *[
        (
            f"inverted-bounds-{low}-{high}",
            rules_edit('- cohens_d: {max: "-0.2"}', f'- cohens_d: {{min: "{low}", max: "{high}"}}'),
            "INVALID_BOUNDS",
            "/rules/2/when/all/1/cohens_d",
            "",
        )
        for low, high in [("1", "0"), ("10", "9"), ("-0.1", "-0.2")]
    ],
    (
        "inverted-bounds-under-any",
        rules_edit(
            '      all:\n        - failure_rate_current: {min: "0.2"}',
            '      any:\n        - failure_rate_current: {min: "0.5", max: "0.2"}',
        ),
        "INVALID_BOUNDS",
        "/rules/3/when/any/0/failure_rate_current",
        "",
    ),
    (
        "unknown-top-level-key",
        f"{RULES_TEXT}{TOKEN}: 1\n",
        "SCHEMA_VALIDATION_FAILED",
        "",
        "additionalProperties",
    ),
    (
        "noncanonical-threshold",
        rules_edit('failure_rate_current: {min: "0.2"}', 'failure_rate_current: {min: "0.20"}'),
        "SCHEMA_VALIDATION_FAILED",
        "/rules/3/when/all/0/failure_rate_current/min",
        "pattern",
    ),
]


@pytest.mark.parametrize(
    "content,reason,location,keyword",
    [row[1:] for row in [*YAML_ROWS, *SEMANTIC_ROWS]],
    ids=[row[0] for row in [*YAML_ROWS, *SEMANTIC_ROWS]],
)
def test_input_is_refused_with_a_fixed_reason_location_and_keyword(
    trusted: None, tmp_path: Path, content: str | bytes, reason: str, location: str, keyword: str
) -> None:
    error = refusal(write(tmp_path, content))
    assert (error.reason, error.location, error.keyword) == (reason, location, keyword)


def test_equal_bounds_are_accepted(trusted: None, tmp_path: Path) -> None:
    content = rules_edit('- cohens_d: {max: "-0.2"}', '- cohens_d: {min: "-0.2", max: "-0.2"}')
    loaded = load_gate_rules(write(tmp_path, content))
    assert loaded["rules"][2]["when"]["all"][1] == {"cohens_d": {"min": "-0.2", "max": "-0.2"}}


def test_reason_vocabulary_is_pinned_and_every_reason_is_exercised() -> None:
    assert gate_rules.REASONS == REASONS
    exercised = {row[2] for row in [*YAML_ROWS, *SEMANTIC_ROWS]} | {r[2] for r in READ_ROWS}
    assert exercised | {"UNSUPPORTED_PLATFORM"} == REASONS


def _plain_types(value: object) -> set[type]:
    if isinstance(value, dict):
        members = cast("dict[object, object]", value)
        assert all(type(key) is str for key in members)
        return {dict}.union(*(_plain_types(item) for item in members.values()))
    if isinstance(value, list):
        return {list}.union(*(_plain_types(item) for item in cast("list[object]", value)))
    return {type(value)}


def test_committed_rules_load_as_exact_plain_data(trusted: None) -> None:
    loaded = load_gate_rules(RULES)
    assert _plain_types(loaded) <= {dict, list, str, int, bool, type(None)}
    assert loaded == {
        "schema": "gate_rules.v1",
        "default": {"decision": "Hold"},
        "rules": [
            {
                "id": "read_verdict_fail",
                "kind": "hard_gate",
                "priority": 400,
                "when": {"all": [{"read_verdict": ["fail"]}]},
                "decision": "Reject",
            },
            {
                "id": "acceptance_rejected",
                "kind": "hard_gate",
                "priority": 300,
                "when": {"all": [{"acceptance": ["rejected"]}]},
                "decision": "Reject",
            },
            {
                "id": "regression_confirmed",
                "kind": "hard_gate",
                "priority": 200,
                "when": {
                    "all": [{"bootstrap_ci_upper": {"max": "0"}}, {"cohens_d": {"max": "-0.2"}}]
                },
                "decision": "Rollback",
            },
            {
                "id": "failure_rate_ceiling",
                "kind": "hard_gate",
                "priority": 100,
                "when": {"all": [{"failure_rate_current": {"min": "0.2"}}]},
                "decision": "Rollback",
            },
            {
                "id": "improvement_confirmed",
                "kind": "optimization_metric",
                "priority": 0,
                "when": {
                    "all": [{"bootstrap_ci_lower": {"min": "0"}}, {"cohens_d": {"min": "0.2"}}]
                },
                "decision": "Approve",
            },
        ],
    }

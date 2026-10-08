"""Standalone behavior and boundary checks."""

from __future__ import annotations

import ast
import itertools
import json
import math
import re
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

import pytest
from _random_policy import seed_violations
from _repo_paths import repo_root
from jsonschema import Draft202012Validator, ValidationError, validate

from underwrite.measurement import eprocess, read_input

ROOT = repo_root(Path(__file__).resolve())
POLICY = ROOT / "contracts/measurement_policy.v1.schema.json"
POLICY_V2 = ROOT / "contracts/measurement_policy.v2.schema.json"
SEED_OWNER = ROOT / "contracts/statistical_seed.v1.schema.json"
MEASUREMENT = ROOT / "src/underwrite/measurement"
POLICY_LOADER = ROOT / "src/underwrite/cli/measure.py"


THRESHOLDS = ("required_instruments", "quorum", "min_cell_n", "min_bin_n")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ENUMERATION_MAX_N = 12
_PARITY_RELATIVE_TOLERANCE = 1e-9


def _json(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))


# That each pinned copy still matches its recorded digest is tests/unit/shell/test_pins.py's
# `test_real_manifest_sha256_matches_pinned_bytes` (every real manifest); not repeated here.


# --- D1: the two-sided e-value, in exact arithmetic ---


def _integral(s: int, f: int, lo: Fraction, hi: Fraction) -> Fraction:
    """Exact ∫_lo^hi p^s (1-p)^f dp by binomial expansion of (1-p)^f."""
    return sum(
        (
            Fraction(math.comb(f, j) * (-1) ** j, s + j + 1)
            * (hi ** (s + j + 1) - lo ** (s + j + 1))
            for j in range(f + 1)
        ),
        Fraction(0),
    )


def _likelihood_under_null(s: int, f: int, p0: Fraction) -> Fraction:
    return p0**s * (1 - p0) ** f


def _one_sided(s: int, f: int, p0: Fraction, *, upper: bool) -> Fraction:
    """Beta(1,1) mixture restricted to one side of p0, normalised to that side's mass."""
    lo, hi = (p0, Fraction(1)) if upper else (Fraction(0), p0)
    return _integral(s, f, lo, hi) / (hi - lo) / _likelihood_under_null(s, f, p0)


def _two_sided(s: int, f: int, p0: Fraction) -> Fraction:
    """Standalone behavior and boundary checks."""
    return (_one_sided(s, f, p0, upper=True) + _one_sided(s, f, p0, upper=False)) / 2


def _uniform_full_mixture(s: int, f: int, p0: Fraction) -> Fraction:
    """Standalone behavior and boundary checks."""
    return _integral(s, f, Fraction(0), Fraction(1)) / _likelihood_under_null(s, f, p0)


def _outcome_counts(n: int) -> list[tuple[int, int]]:
    return [(sum(bits), n - sum(bits)) for bits in itertools.product((0, 1), repeat=n)]


def test_the_equal_mixture_is_the_uniform_mixture_at_the_contract_p0() -> None:
    half = Fraction(1, 2)
    for n in range(1, _ENUMERATION_MAX_N + 1):
        for s in range(n + 1):
            assert _two_sided(s, n - s, half) == _uniform_full_mixture(s, n - s, half)


def test_off_centre_p0_is_where_the_two_forms_part_and_the_decision_matters() -> None:
    quarter = Fraction(1, 4)
    assert _two_sided(3, 1, quarter) != _uniform_full_mixture(3, 1, quarter)


def test_each_side_and_their_equal_mixture_are_e_values_but_their_maximum_is_not() -> None:
    half = Fraction(1, 2)
    for n in range(1, 9):
        counts = _outcome_counts(n)
        weight = Fraction(1, len(counts))
        mean_upper = sum((_one_sided(s, f, half, upper=True) for s, f in counts), Fraction(0))
        mean_lower = sum((_one_sided(s, f, half, upper=False) for s, f in counts), Fraction(0))
        mean_two_sided = sum((_two_sided(s, f, half) for s, f in counts), Fraction(0))
        mean_maximum = sum(
            (
                max(_one_sided(s, f, half, upper=True), _one_sided(s, f, half, upper=False))
                for s, f in counts
            ),
            Fraction(0),
        )
        assert mean_upper * weight == 1
        assert mean_lower * weight == 1
        assert mean_two_sided * weight == 1
        assert mean_maximum * weight > 1


def test_a_one_sided_process_never_reports_the_direction_the_two_sided_form_does() -> None:
    half = Fraction(1, 2)
    failures = 8
    pass_e = Fraction(20)
    assert all(_one_sided(0, f, half, upper=True) < 1 for f in range(1, failures + 1))
    assert _two_sided(0, failures, half) >= pass_e
    assert _two_sided(0, failures, half) == _two_sided(failures, 0, half)


# --- D3: one seed owner, read rather than copied ---


# --- Pure-layer copies of schema consts: the pure layer cannot read contracts/, so its
# numeric e-process consts and the seed are pinned to their authored owners here ---


def _pure_copies() -> dict[str, object]:
    return {
        "p0": eprocess.CONTRACT_P0,
        "prior_a": eprocess.CONTRACT_PRIOR_A,
        "prior_b": eprocess.CONTRACT_PRIOR_B,
        "pass_e": eprocess.CONTRACT_PASS_E,
        "seed": read_input.CANONICAL_SEED,
    }


def _owner_consts() -> dict[str, object]:
    ep = cast("dict[str, dict[str, Any]]", _json(POLICY_V2)["properties"]["eprocess"]["properties"])
    consts: dict[str, object] = {k: ep[k]["const"] for k in ("p0", "prior_a", "prior_b", "pass_e")}
    return {**consts, "seed": _json(SEED_OWNER)["const"]}


def _drift(copies: dict[str, object], owners: dict[str, object]) -> list[str]:
    assert set(copies) == set(owners)
    return sorted(k for k in owners if copies[k] != owners[k])


def test_pure_layer_contract_copies_equal_their_schema_owners() -> None:
    assert _drift(_pure_copies(), _owner_consts()) == []


@pytest.mark.parametrize("side", ["copy", "owner"])
@pytest.mark.parametrize("key,value", [("pass_e", 21.0), ("seed", 43)])
def test_contract_copy_gate_fails_on_either_side_drifting(
    side: str, key: str, value: object
) -> None:
    copies, owners = _pure_copies(), _owner_consts()
    (copies if side == "copy" else owners)[key] = value
    assert _drift(copies, owners) == [key]


def _count_real_modules(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.rglob("*.py") if path.name != "__init__.py")


def test_measurement_modules_have_explicit_local_rngs_or_report_their_absence() -> None:
    count = _count_real_modules(MEASUREMENT)
    assert count > 0
    paths = sorted(p for p in MEASUREMENT.rglob("*.py") if p.name != "__init__.py")
    errors = {p.name: lines for p in paths if (lines := seed_violations(p.read_text()))}
    assert errors == {}


# --- D4: required policy inputs, no defaults anywhere ---


def _keys(node: object) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in cast("dict[str, Any]", node).items():
            found.add(key)
            found |= _keys(value)
    elif isinstance(node, list):
        for item in cast("list[Any]", node):
            found |= _keys(item)
    return found


def _resolved_policy() -> dict[str, Any]:
    """The policy schema with its seed `$ref` replaced by the owner's own constraints."""
    policy = _json(POLICY)
    owner = {k: v for k, v in _json(SEED_OWNER).items() if not k.startswith("$")}
    properties = dict(cast("dict[str, Any]", policy["properties"]))
    properties["seed"] = owner
    return {**policy, "properties": properties}


def _complete_policy() -> dict[str, Any]:
    return {
        "schema": "measurement_policy.v1",
        "required_instruments": ["judge", "retrieval"],
        "quorum": 2,
        "min_cell_n": 20,
        "min_bin_n": 25,
        "seed": _json(SEED_OWNER)["const"],
    }


def test_the_policy_schema_requires_every_threshold_and_declares_no_default() -> None:
    policy = _json(POLICY)
    Draft202012Validator.check_schema(policy)
    assert set(THRESHOLDS) <= set(cast("list[str]", policy["required"]))
    assert policy["additionalProperties"] is False
    assert "default" not in _keys(policy)
    assert "default" not in _keys(_json(POLICY_V2))
    properties = cast("dict[str, dict[str, Any]]", policy["properties"])
    for name in ("quorum", "min_cell_n", "min_bin_n"):
        assert properties[name]["type"] == "integer"
        assert properties[name]["minimum"] == 1


def _property_names(node: object) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        mapping = cast("dict[str, Any]", node)
        if isinstance(properties := mapping.get("properties"), dict):
            found |= set(cast("dict[str, Any]", properties))
        for value in mapping.values():
            found |= _property_names(value)
    elif isinstance(node, list):
        for item in cast("list[Any]", node):
            found |= _property_names(item)
    return found


def _policy_default_lines(source: str, keys: set[str]) -> list[int]:
    """Lines that give a policy key a fallback: `m.get("seed", 42)`, `setdefault`, `pop`."""
    return sorted(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"get", "setdefault", "pop"}
        and len(node.args) + len(node.keywords) > 1
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value in keys
    )


def test_measurement_code_never_defaults_a_policy_key() -> None:
    """D3/D4: a consumer-side fallback is a second canon even where validation makes it dead.

    The CLI loader is in scope because it holds the policy before validation, where a
    fallback would be live and no engine-level behavioural test would see it.
    """
    keys = _property_names(_json(POLICY_V2))
    modules = sorted(MEASUREMENT.rglob("*.py"))
    assert modules
    offenders = {
        p.name: lines
        for p in [*modules, POLICY_LOADER]
        if (lines := _policy_default_lines(p.read_text(), keys))
    }
    assert offenders == {}


@pytest.mark.parametrize(
    "source,caught",
    [
        ('seed = policy.get("seed", 42)', True),
        ('panel.setdefault("pass_at", 0.75)', True),
        ('p.pop("quorum", None)', True),
        ('p.get("seed", default=42)', True),
        ('losses.get(name, "missing")', False),
        ('p.get("schema") != "measurement_policy.v2"', False),
        ('row.get("payload", {})', False),
    ],
)
def test_policy_default_guard_distinguishes_fallbacks(source: str, caught: bool) -> None:
    keys = _property_names(_json(POLICY_V2))
    assert bool(_policy_default_lines(source, keys)) is caught


@pytest.mark.parametrize("missing", THRESHOLDS)
def test_a_policy_missing_a_threshold_is_a_configuration_failure(missing: str) -> None:
    schema = _resolved_policy()
    validate(_complete_policy(), schema)
    incomplete = {k: v for k, v in _complete_policy().items() if k != missing}
    with pytest.raises(ValidationError):
        validate(incomplete, schema)


@pytest.mark.parametrize(
    "override",
    [
        {"quorum": 0},
        {"min_cell_n": "20"},
        {"min_bin_n": 2.5},
        {"required_instruments": ["judge", "judge"]},
        {"seed": 7},
        {"undeclared_threshold": 1},
    ],
)
def test_a_policy_with_a_hidden_or_malformed_input_is_rejected(override: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        validate({**_complete_policy(), **override}, _resolved_policy())


# --- Statistical boundary decisions ---

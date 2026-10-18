"""Independent exact arithmetic oracles and current explicit-policy/RNG guards."""

from __future__ import annotations

import ast
import itertools
import json
import math
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

import pytest
from _random_policy import seed_violations
from _repo_paths import repo_root

ROOT = repo_root(Path(__file__).resolve())
POLICY = ROOT / "contracts/measurement_policy.v3.schema.json"
MEASUREMENT = ROOT / "src/underwrite/measurement"
POLICY_LOADER = ROOT / "src/underwrite/cli/measure.py"


_ENUMERATION_MAX_N = 12


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


def test_the_equal_mixture_is_the_uniform_mixture_at_the_midpoint() -> None:
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
    """Lines that give a policy key a fallback: `m.get("p0", 0.5)`, `setdefault`, `pop`."""
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
    """A consumer-side fallback is a second policy owner even where validation makes it dead.

    The CLI loader is in scope because it holds the policy before validation, where a
    fallback would be live and no engine-level behavioural test would see it.
    """
    keys = _property_names(_json(POLICY))
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
        ('p0 = policy.get("p0", 0.5)', True),
        ('p.setdefault("expected_case_ids", [])', True),
        ('p.pop("assumptions", {})', True),
        ('p.get("max_observations", default=None)', True),
        ('losses.get(name, "missing")', False),
        ('p.get("schema") != "measurement_policy.v3"', False),
        ('row.get("payload", {})', False),
    ],
)
def test_policy_default_guard_distinguishes_fallbacks(source: str, caught: bool) -> None:
    keys = _property_names(_json(POLICY))
    assert bool(_policy_default_lines(source, keys)) is caught

"""Standalone behavior and boundary checks."""

from __future__ import annotations

import math
from typing import TypeGuard

REL_TOL = 1e-12


def _same_number(actual: object, expected: object) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if not isinstance(actual, (int, float)) or not isinstance(expected, (int, float)):
        return False
    if actual == expected:
        return True
    if actual == 0 or expected == 0:
        return False
    return math.isclose(actual, expected, rel_tol=REL_TOL, abs_tol=0.0)


def _is_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def _is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _walk_dict(actual: object, expected: object, path: str, faults: list[str]) -> None:
    if not (_is_dict(expected) and _is_dict(actual)):
        faults.append(f"{path}: {type(actual).__name__} != {type(expected).__name__}")
        return
    got, want = actual, expected
    if set(got) != set(want):
        faults.append(f"{path}: keys {sorted(got)} != {sorted(want)}")
        return
    for key in want:
        _walk(got[key], want[key], f"{path}/{key}", faults)


def _walk_list(actual: object, expected: object, path: str, faults: list[str]) -> None:
    if not (_is_list(expected) and _is_list(actual)):
        faults.append(f"{path}: {type(actual).__name__} != {type(expected).__name__}")
        return
    items, wants = actual, expected
    if len(items) != len(wants):
        faults.append(f"{path}: length {len(items)} != {len(wants)}")
        return
    for index, (item, want) in enumerate(zip(items, wants, strict=True)):
        _walk(item, want, f"{path}/{index}", faults)


def _walk(actual: object, expected: object, path: str, faults: list[str]) -> None:
    if _is_dict(expected) or _is_dict(actual):
        _walk_dict(actual, expected, path, faults)
    elif _is_list(expected) or _is_list(actual):
        _walk_list(actual, expected, path, faults)
    elif isinstance(expected, (int, float)) or isinstance(actual, (int, float)):
        if not _same_number(actual, expected):
            faults.append(f"{path}: {actual!r} !~ {expected!r}")
    elif actual != expected or type(actual) is not type(expected):
        faults.append(f"{path}: {actual!r} != {expected!r}")


def assert_parity(actual: object, expected: object) -> None:
    """``actual`` equals ``expected`` exactly, except floats within REL_TOL (never 0)."""
    faults: list[str] = []
    _walk(actual, expected, "", faults)
    assert not faults, "\n".join([f"{len(faults)} parity fault(s):", *faults[:20]])

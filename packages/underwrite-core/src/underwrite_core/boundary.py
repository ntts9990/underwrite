"""Scan string values for decision or authority vocabulary leaks.

English terms use word boundaries that include underscores and digits; Korean
terms use substring matching so particles do not conceal them. Hyphenated terms
are matched literally. Keys are never scanned. Pure scanning does not grant any
measurement, acceptance or deployment authority."""

import re
from dataclasses import dataclass
from typing import Literal, TypeGuard

LeakFamily = Literal["gate_vocab", "authority_vocab"]

GATE_VOCAB: tuple[str, ...] = (
    "promote",
    "promotion",
    "promoted",
    "approve",
    "approved",
    "approval",
    "reject",
    "rejected",
    "rollback",
    "roll back",
    "deploy",
    "deployed",
    "deployment",
    "hold",
    "certify",
    "certified",
    # Deployment-gate 7-value decision literals (registry ids: approve,
    # conditional, shadow, hitl_only, hold_decision, reject, rollback_decision).
    # Approve/Hold/Reject/Rollback are also matched case-insensitively by the
    # lowercase verb forms above; Conditional/Shadow/HITL-only are not.
    "Approve",
    "Conditional",
    "Shadow",
    "HITL-only",
    "Hold",
    "Reject",
    "Rollback",
    # Korean equivalents (registry §4b, first_mention_rules).
    "승격",
    "승인",
    "배포",
    "롤백",
    "반려",
)

RECOMMENDATION_AUTHORITY_VOCAB: tuple[str, ...] = (
    "recommend",
    "recommended",
    "recommendation",
    "authorize",
    "authorized",
    "should promote",
    "should deploy",
    "ready for production",
    "production-ready",
    "권고",
    "권장",
)

_HANGUL_RANGES: tuple[tuple[int, int], ...] = (
    (0xAC00, 0xD7A3),  # Hangul syllables
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x3130, 0x318F),  # Hangul Compatibility Jamo
)


def _is_hangul_term(term: str) -> bool:
    return any(any(lo <= ord(ch) <= hi for lo, hi in _HANGUL_RANGES) for ch in term)


def _split_vocab(vocab: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Partition `vocab` into (latin_terms, hangul_terms) — see module docstring."""
    latin = tuple(term for term in vocab if not _is_hangul_term(term))
    hangul = tuple(term for term in vocab if _is_hangul_term(term))
    return latin, hangul


def _compile_latin_pattern(terms: tuple[str, ...]) -> re.Pattern[str]:
    """ASCII-lookaround, case-insensitive alternation over `terms` (never `\\b`)."""
    if not terms:
        return re.compile(r"(?!)")  # matches nothing
    alternation = "|".join(re.escape(term) for term in terms)
    return re.compile(rf"(?<![A-Za-z0-9_])(?:{alternation})(?![A-Za-z0-9_])", re.IGNORECASE)


_GATE_LATIN, _GATE_HANGUL = _split_vocab(GATE_VOCAB)
_AUTHORITY_LATIN, _AUTHORITY_HANGUL = _split_vocab(RECOMMENDATION_AUTHORITY_VOCAB)
_GATE_PATTERN = _compile_latin_pattern(_GATE_LATIN)
_AUTHORITY_PATTERN = _compile_latin_pattern(_AUTHORITY_LATIN)


@dataclass(frozen=True)
class Leak:
    """One vocabulary-boundary violation: `term` found at `path`, in `family`'s vocabulary."""

    path: str
    term: str
    family: LeakFamily


def _find_hangul(text: str, terms: tuple[str, ...], family: LeakFamily, path: str) -> list[Leak]:
    leaks: list[Leak] = []
    for term in terms:
        start = 0
        while (idx := text.find(term, start)) != -1:
            leaks.append(Leak(path=path, term=term, family=family))
            start = idx + len(term)
            # Every registered Hangul term is non-empty, so `start` must
            # strictly pass the match it just found -- otherwise the next
            # `text.find(term, start)` finds the SAME occurrence again and
            # this loop never terminates. A failure here is fail-fast (an
            # empty term, or a mutation of the line above), not a hang.
            if start <= idx:
                raise AssertionError(f"boundary scan did not advance past index {idx}")
    return leaks


def _scan_text(text: str, path: str) -> list[Leak]:
    leaks = [
        Leak(path=path, term=m.group(0), family="gate_vocab") for m in _GATE_PATTERN.finditer(text)
    ]
    leaks += _find_hangul(text, _GATE_HANGUL, "gate_vocab", path)
    leaks += [
        Leak(path=path, term=m.group(0), family="authority_vocab")
        for m in _AUTHORITY_PATTERN.finditer(text)
    ]
    leaks += _find_hangul(text, _AUTHORITY_HANGUL, "authority_vocab", path)
    return leaks


def _is_object_dict(value: object) -> TypeGuard[dict[object, object]]:
    """`isinstance(value, dict)`, narrowed to `dict[object, object]` (not
    `dict[Unknown, Unknown]`) so `_walk` below type-checks with no
    `typing.cast`."""
    return isinstance(value, dict)


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    """`isinstance(value, list)`, narrowed to `list[object]` for the same
    reason as `_is_object_dict` above."""
    return isinstance(value, list)


def _walk(value: object, path: str, leaks: list[Leak]) -> None:
    """Recurse into dicts (keys and values) and lists only — see `scan_strings`."""
    if _is_object_dict(value):
        for key, item in value.items():
            key_path = f"{path}.{key}" if isinstance(key, str) else f"{path}.{key!r}"
            if isinstance(key, str):
                leaks.extend(_scan_text(key, key_path))
            _walk(item, key_path, leaks)
    elif _is_object_list(value):
        for index, item in enumerate(value):
            _walk(item, f"{path}[{index}]", leaks)
    elif isinstance(value, str):
        leaks.extend(_scan_text(value, path))
    # Tuples and other scalars (int/float/bool/None) are not recursed into:
    # JSON has no tuples, and a non-string scalar cannot carry vocabulary.


def scan_strings(value: object, *, path: str = "$") -> list[Leak]:
    """Recursively scan `value` for `GATE_VOCAB`/`RECOMMENDATION_AUTHORITY_VOCAB` leaks.

    Walks dict keys and values, and list items, with a JSONPath-like `path`
    (e.g. `$.a.b[3]`); scans every string leaf and every string dict key
    against both vocabularies. Tuples are not walked and non-string scalars
    are ignored (module docstring). Returns every leak found, in traversal
    order; an empty list means `value` is measurement-only.
    """
    leaks: list[Leak] = []
    _walk(value, path, leaks)
    return leaks


_MAX_SHOWN_LEAKS = 5


class VocabularyLeak(ValueError):
    """`scan_strings` found at least one leak; `.leaks` carries the full list."""

    def __init__(self, leaks: list[Leak]) -> None:
        self.leaks = leaks
        head = leaks[:_MAX_SHOWN_LEAKS]
        shown = "; ".join(f"{leak.path}={leak.term!r}({leak.family})" for leak in head)
        omitted = len(leaks) - len(head)
        suffix = f" (+{omitted} more)" if omitted else ""
        super().__init__(f"vocabulary boundary violated: {len(leaks)} leak(s): {shown}{suffix}")


def assert_measurement_only(value: object) -> None:
    """Raise `VocabularyLeak` if `value` contains any gate or authority vocabulary term."""
    leaks = scan_strings(value)
    if leaks:
        raise VocabularyLeak(leaks)

"""Resolve canonical terms, aliases and explicitly forbidden mappings.

The registry consumes a caller-supplied document; no vocabulary file is loaded.
Distinct honesty states must remain distinct rather than becoming synonyms."""

from dataclasses import dataclass
from typing import Any, TypeGuard


class RegistryError(ValueError):
    """Raised when a term registry is malformed, or a lookup cannot be resolved.

    ``args[0]`` is one of: UNKNOWN_TERM, DUPLICATE_ID, DUPLICATE_CANONICAL,
    DUPLICATE_ALIAS, FORBIDDEN_MERGE_AS_ALIAS, or a fail-closed structural
    ``MALFORMED_REGISTRY:<context>[:<key>]:<reason>`` from `_expect_dict`/
    `_expect_list`/`_expect_str` below.
    """


@dataclass(frozen=True)
class Term:
    """One caller-supplied vocabulary entry with flattened aliases."""

    id: str
    canonical: str
    meaning: str
    tier: str
    layer: str
    aliases: tuple[str, ...]
    source: str


# Any below: `data` is parsed JSON with no static shape until the
# `_expect_*` helpers pick out and validate the fields this module actually
# uses -- fail-closed (raising `RegistryError`), never `typing.cast`, so a
# malformed field is a caught, reasoned rejection instead of an uncontrolled
# `KeyError`/`TypeError` (or, worse, a silently wrong-typed `Term` field).
def _is_dict(value: object) -> TypeGuard[dict[str, Any]]:
    """`isinstance(value, dict)`, narrowed to `dict[str, Any]` (not
    `dict[Unknown, Unknown]`) so callers type-check with no `typing.cast`."""
    return isinstance(value, dict)


def _is_list(value: object) -> TypeGuard[list[Any]]:
    """`isinstance(value, list)`, narrowed to `list[Any]` for the same
    reason as `_is_dict` above."""
    return isinstance(value, list)


def _expect_dict(value: object, ctx: str) -> dict[str, Any]:
    """Return `value` narrowed to a `dict`, or raise `RegistryError` naming `ctx`."""
    if not _is_dict(value):
        raise RegistryError(f"MALFORMED_REGISTRY:{ctx}:expected_dict")
    return value


def _expect_list(value: object, ctx: str) -> list[Any]:
    """Return `value` narrowed to a `list`, or raise `RegistryError` naming `ctx`."""
    if not _is_list(value):
        raise RegistryError(f"MALFORMED_REGISTRY:{ctx}:expected_list")
    return value


def _expect_str(fields: dict[str, Any], key: str, ctx: str) -> str:
    """Return `fields[key]` narrowed to a `str`, or raise `RegistryError`.

    A missing key and a wrong-typed one fail the same way: `.get` returns
    `None` on a miss, and `None` is not a `str` either, so both collapse
    into one fail-closed reason instead of missing-key raising a bare
    `KeyError` while wrong-type raises something else.
    """
    value = fields.get(key)
    if not isinstance(value, str):
        raise RegistryError(f"MALFORMED_REGISTRY:{ctx}:{key}:expected_str")
    return value


def _alias_from_dict(raw: object) -> str:
    fields = _expect_dict(raw, "terms[].aliases[]")
    return _expect_str(fields, "term", "terms[].aliases[]")


def _term_from_dict(raw: object) -> Term:
    fields = _expect_dict(raw, "terms[]")
    raw_aliases = _expect_list(fields.get("aliases", []), "terms[].aliases")
    aliases = tuple(_alias_from_dict(entry) for entry in raw_aliases)
    return Term(
        id=_expect_str(fields, "id", "terms[]"),
        canonical=_expect_str(fields, "canonical", "terms[]"),
        meaning=_expect_str(fields, "meaning", "terms[]"),
        tier=_expect_str(fields, "tier", "terms[]"),
        layer=_expect_str(fields, "layer", "terms[]"),
        aliases=aliases,
        source=_expect_str(fields, "source", "terms[]"),
    )


def _mapping_from_dict(raw: object) -> tuple[str, str]:
    fields = _expect_dict(raw, "forbidden_mappings[]")
    return (
        _expect_str(fields, "from", "forbidden_mappings[]"),
        _expect_str(fields, "to", "forbidden_mappings[]"),
    )


def _check_unique(terms: tuple[Term, ...]) -> None:
    """Raise `RegistryError` on a duplicate id, duplicate canonical, or a shared alias."""
    seen_ids: set[str] = set()
    seen_canonicals: set[str] = set()
    alias_owner: dict[str, str] = {}
    for term in terms:
        if term.id in seen_ids:
            raise RegistryError(f"DUPLICATE_ID:{term.id}")
        seen_ids.add(term.id)
        if term.canonical in seen_canonicals:
            raise RegistryError(f"DUPLICATE_CANONICAL:{term.canonical}")
        seen_canonicals.add(term.canonical)
        for alias in term.aliases:
            owner = alias_owner.setdefault(alias, term.id)
            if owner != term.id:
                raise RegistryError(f"DUPLICATE_ALIAS:{alias}")


def _check_forbidden_merge_aliases(
    terms: tuple[Term, ...], mappings: tuple[tuple[str, str], ...]
) -> None:
    """Raise `RegistryError` if a mapping's `to` is also listed as its `from` term's alias."""
    by_key = {key: term for term in terms for key in (term.id, term.canonical)}
    for from_key, to_key in mappings:
        from_term = by_key.get(from_key)
        if from_term is not None and to_key in from_term.aliases:
            raise RegistryError(f"FORBIDDEN_MERGE_AS_ALIAS:{from_key}->{to_key}")


class Registry:
    """Resolves canonical vocabulary terms and forbidden-mapping checks."""

    def __init__(
        self, terms: tuple[Term, ...], forbidden_mappings: tuple[tuple[str, str], ...]
    ) -> None:
        self._terms = terms
        self._by_id = {term.id: term for term in terms}
        self._by_canonical = {term.canonical: term for term in terms}
        self._by_alias = {alias: term for term in terms for alias in term.aliases}
        self._forbidden_mappings = forbidden_mappings

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Registry":
        """Build and validate a registry from an already-parsed document."""
        raw_terms = _expect_list(data.get("terms", []), "terms")
        terms = tuple(_term_from_dict(raw) for raw in raw_terms)
        _check_unique(terms)

        raw_mappings = _expect_list(data.get("forbidden_mappings", []), "forbidden_mappings")
        mappings = tuple(_mapping_from_dict(raw) for raw in raw_mappings)
        _check_forbidden_merge_aliases(terms, mappings)

        return cls(terms, mappings)

    def resolve(self, term: str) -> Term:
        """Exact canonical, then exact alias, then the same two case-insensitively."""
        if term in self._by_canonical:
            return self._by_canonical[term]
        if term in self._by_alias:
            return self._by_alias[term]

        lowered = term.lower()
        for canonical, resolved in self._by_canonical.items():
            if canonical.lower() == lowered:
                return resolved
        for alias, resolved in self._by_alias.items():
            if alias.lower() == lowered:
                return resolved
        raise RegistryError("UNKNOWN_TERM")

    def _keys_for(self, term: str) -> frozenset[str]:
        """The (id, canonical) pair backing `term`, or `term` itself if unresolvable."""
        resolved = self._by_id.get(term)
        if resolved is None:
            try:
                resolved = self.resolve(term)
            except RegistryError:
                return frozenset((term,))
        return frozenset((resolved.id, resolved.canonical))

    def is_forbidden(self, from_term: str, to_term: str) -> bool:
        """Whether (`from_term` -> `to_term`) matches a registered forbidden mapping."""
        from_keys = self._keys_for(from_term)
        to_keys = self._keys_for(to_term)
        return any(
            mapping_from in from_keys and mapping_to in to_keys
            for mapping_from, mapping_to in self._forbidden_mappings
        )

    def tier(self, term: str) -> str:
        """The tier (`core`/`contract`/`appendix`) of the resolved term."""
        return self.resolve(term).tier

    def terms_in_layer(self, layer: str) -> tuple[Term, ...]:
        """All terms whose `layer` field matches, in registry order."""
        return tuple(term for term in self._terms if term.layer == layer)

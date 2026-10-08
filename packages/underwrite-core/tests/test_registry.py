"""Standalone behavior and boundary checks."""

from typing import Any

import pytest
from underwrite_core.registry import (
    Registry,
    RegistryError,
    Term,
    _check_forbidden_merge_aliases,  # pyright: ignore[reportPrivateUsage]
)


def _minimal_term(term_id: str, canonical: str) -> dict[str, Any]:
    return {
        "id": term_id,
        "canonical": canonical,
        "meaning": "test fixture",
        "tier": "contract",
        "layer": "core",
        "aliases": [],
        "source": "test",
    }


@pytest.fixture(scope="module")
def real_registry() -> Registry:
    terms: list[dict[str, Any]] = []
    for term_id, canonical, tier, layer in (
        ("assurance", "Agent Assurance", "core", "core"),
        ("system", "Agent System", "core", "core"),
        ("promote", "promote", "contract", "acceptance"),
        ("hold", "Hold", "contract", "acceptance"),
        ("abstain", "abstain", "contract", "acceptance"),
        ("not_measured", "not_measured", "contract", "measurement"),
        ("verdict", "verdict", "contract", "measurement"),
    ):
        term = _minimal_term(term_id, canonical)
        term.update(tier=tier, layer=layer)
        terms.append(term)
    return Registry.from_dict(
        {
            "terms": terms,
            "forbidden_mappings": [
                {"from": "abstain", "to": "hold", "reason": "distinct outcomes"}
            ],
        }
    )


def test_from_dict_loads_real_registry(real_registry: Registry) -> None:
    assert real_registry.resolve("Agent Assurance").tier == "core"


def test_resolve_promote_is_not_hold_decision(real_registry: Registry) -> None:
    # "promote" is both its own contract-tier term and an alias of "approve";
    # canonical match wins, so it must not resolve to the unrelated "Hold" literal.
    resolved = real_registry.resolve("promote")
    assert resolved.canonical != "Hold"


def test_is_forbidden_abstain_to_hold(real_registry: Registry) -> None:
    assert real_registry.is_forbidden("abstain", "hold") is True


def test_is_forbidden_pass_to_warn_is_false(real_registry: Registry) -> None:
    assert real_registry.is_forbidden("pass", "warn") is False


def test_tier_of_a_contract_term(real_registry: Registry) -> None:
    assert real_registry.tier("not_measured") == "contract"


def test_tier_of_a_core_term(real_registry: Registry) -> None:
    assert real_registry.tier("Agent System") == "core"


def test_terms_in_layer_measurement_includes_verdict(real_registry: Registry) -> None:
    layer_terms = real_registry.terms_in_layer("measurement")
    assert any(term.id == "verdict" for term in layer_terms)


def test_resolve_unknown_term_raises(real_registry: Registry) -> None:
    with pytest.raises(RegistryError, match=r"^UNKNOWN_TERM$"):
        real_registry.resolve("definitely_not_a_registered_term")


def test_from_dict_rejects_duplicate_canonical() -> None:
    data = {
        "terms": [_minimal_term("a", "Shared"), _minimal_term("b", "Shared")],
        "forbidden_mappings": [],
    }
    with pytest.raises(RegistryError, match=r"^DUPLICATE_CANONICAL:Shared$"):
        Registry.from_dict(data)


def test_from_dict_rejects_duplicate_id() -> None:
    data = {
        "terms": [_minimal_term("dup", "One"), _minimal_term("dup", "Two")],
        "forbidden_mappings": [],
    }
    with pytest.raises(RegistryError, match=r"^DUPLICATE_ID:dup$"):
        Registry.from_dict(data)


def test_from_dict_rejects_duplicate_alias_across_terms() -> None:
    term_a = _minimal_term("a", "A")
    term_a["aliases"] = [{"term": "shared_alias", "source": "test"}]
    term_b = _minimal_term("b", "B")
    term_b["aliases"] = [{"term": "shared_alias", "source": "test"}]
    data = {"terms": [term_a, term_b], "forbidden_mappings": []}
    with pytest.raises(RegistryError, match=r"^DUPLICATE_ALIAS:shared_alias$"):
        Registry.from_dict(data)


def test_from_dict_defaults_missing_aliases_key_to_empty() -> None:
    raw = _minimal_term("no_aliases", "NoAliases")
    del raw["aliases"]
    registry = Registry.from_dict({"terms": [raw], "forbidden_mappings": []})
    assert registry.resolve("NoAliases").aliases == ()


def test_from_dict_rejects_forbidden_merge_registered_as_alias() -> None:
    term = _minimal_term("pass_state", "pass_state")
    term["aliases"] = [{"term": "promote_bad", "source": "test"}]
    data = {
        "terms": [term],
        "forbidden_mappings": [{"from": "pass_state", "to": "promote_bad", "reason": "test"}],
    }
    with pytest.raises(RegistryError, match=r"^FORBIDDEN_MERGE_AS_ALIAS:pass_state->promote_bad$"):
        Registry.from_dict(data)


def test_check_forbidden_merge_aliases_skips_an_unresolvable_from_key() -> None:
    # from_key "ghost" resolves to no term (by_key has no such id/canonical),
    # so the mapping must be silently skipped, not raise.
    term = Term(
        id="real",
        canonical="Real",
        meaning="test fixture",
        tier="contract",
        layer="core",
        aliases=(),
        source="test",
    )
    _check_forbidden_merge_aliases((term,), (("ghost", "whatever"),))  # no raise


def test_resolve_is_case_insensitive_on_canonical() -> None:
    data = {"terms": [_minimal_term("x", "MixedCase")], "forbidden_mappings": []}
    registry = Registry.from_dict(data)
    assert registry.resolve("mixedcase").id == "x"
    assert registry.resolve("MIXEDCASE").id == "x"


def test_resolve_is_case_insensitive_on_alias() -> None:
    term = _minimal_term("x", "Canonical")
    term["aliases"] = [{"term": "AliasTerm", "source": "test"}]
    registry = Registry.from_dict({"terms": [term], "forbidden_mappings": []})
    assert registry.resolve("aliasterm").id == "x"


def test_is_forbidden_false_when_from_term_is_unresolvable() -> None:
    term_a = _minimal_term("a", "A")
    term_b = _minimal_term("b", "B")
    data = {
        "terms": [term_a, term_b],
        "forbidden_mappings": [{"from": "A", "to": "B", "reason": "test"}],
    }
    registry = Registry.from_dict(data)
    assert registry.is_forbidden("A", "B") is True
    # "totally_unknown" resolves to nothing, so _keys_for falls back to the
    # literal string itself, which never matches a registered mapping side.
    assert registry.is_forbidden("totally_unknown", "B") is False
    assert registry.is_forbidden("A", "totally_unknown") is False


def test_keys_for_fast_path_resolves_by_id_directly() -> None:
    term_a = _minimal_term("ia", "CA")
    term_b = _minimal_term("ib", "CB")
    data = {
        "terms": [term_a, term_b],
        "forbidden_mappings": [{"from": "CA", "to": "CB", "reason": "test"}],
    }
    registry = Registry.from_dict(data)
    # Passing the ids ("ia"/"ib"), not the canonicals: _keys_for must reach
    # each term via the _by_id fast path -- which also contributes the
    # canonical to the returned key set -- not only via the resolve()
    # fallback, which would recognize just the literal id string and never
    # see "CA"/"CB", so the mapping (stored by canonical) would never match.
    assert registry.is_forbidden("ia", "ib") is True


# --- structural validation: every field that used to be typing.cast ------
# These replaced typing.cast()-based narrowing in _term_from_dict/from_dict
# (a runtime no-op cannot detect a changed condition) with explicit
# _expect_dict/_expect_list/_expect_str checks; each case below exercises
# exactly one of those checks so a mutation to its condition or message is
# observable.


@pytest.mark.parametrize(
    ("data", "expected_message"),
    [
        pytest.param(
            {"terms": "nope", "forbidden_mappings": []},
            r"^MALFORMED_REGISTRY:terms:expected_list$",
            id="terms_not_a_list",
        ),
        pytest.param(
            {"terms": ["nope"], "forbidden_mappings": []},
            r"^MALFORMED_REGISTRY:terms\[\]:expected_dict$",
            id="term_entry_not_a_dict",
        ),
        pytest.param(
            {"terms": [{**_minimal_term("a", "A"), "id": 1}], "forbidden_mappings": []},
            r"^MALFORMED_REGISTRY:terms\[\]:id:expected_str$",
            id="term_id_not_a_string",
        ),
        pytest.param(
            {"terms": [{k: v for k, v in _minimal_term("a", "A").items() if k != "canonical"}]},
            r"^MALFORMED_REGISTRY:terms\[\]:canonical:expected_str$",
            id="term_canonical_missing",
        ),
        pytest.param(
            {"terms": [{**_minimal_term("a", "A"), "meaning": 1}], "forbidden_mappings": []},
            r"^MALFORMED_REGISTRY:terms\[\]:meaning:expected_str$",
            id="term_meaning_not_a_string",
        ),
        pytest.param(
            {"terms": [{**_minimal_term("a", "A"), "tier": 1}], "forbidden_mappings": []},
            r"^MALFORMED_REGISTRY:terms\[\]:tier:expected_str$",
            id="term_tier_not_a_string",
        ),
        pytest.param(
            {"terms": [{**_minimal_term("a", "A"), "layer": 1}], "forbidden_mappings": []},
            r"^MALFORMED_REGISTRY:terms\[\]:layer:expected_str$",
            id="term_layer_not_a_string",
        ),
        pytest.param(
            {"terms": [{**_minimal_term("a", "A"), "source": 1}], "forbidden_mappings": []},
            r"^MALFORMED_REGISTRY:terms\[\]:source:expected_str$",
            id="term_source_not_a_string",
        ),
        pytest.param(
            {"terms": [{**_minimal_term("a", "A"), "aliases": "nope"}], "forbidden_mappings": []},
            r"^MALFORMED_REGISTRY:terms\[\]\.aliases:expected_list$",
            id="aliases_not_a_list",
        ),
        pytest.param(
            {"terms": [{**_minimal_term("a", "A"), "aliases": ["nope"]}], "forbidden_mappings": []},
            r"^MALFORMED_REGISTRY:terms\[\]\.aliases\[\]:expected_dict$",
            id="alias_entry_not_a_dict",
        ),
        pytest.param(
            {
                "terms": [{**_minimal_term("a", "A"), "aliases": [{"term": 1, "source": "t"}]}],
                "forbidden_mappings": [],
            },
            r"^MALFORMED_REGISTRY:terms\[\]\.aliases\[\]:term:expected_str$",
            id="alias_term_not_a_string",
        ),
        pytest.param(
            {"terms": [], "forbidden_mappings": "nope"},
            r"^MALFORMED_REGISTRY:forbidden_mappings:expected_list$",
            id="forbidden_mappings_not_a_list",
        ),
        pytest.param(
            {"terms": [], "forbidden_mappings": ["nope"]},
            r"^MALFORMED_REGISTRY:forbidden_mappings\[\]:expected_dict$",
            id="mapping_entry_not_a_dict",
        ),
        pytest.param(
            {"terms": [], "forbidden_mappings": [{"to": "b"}]},
            r"^MALFORMED_REGISTRY:forbidden_mappings\[\]:from:expected_str$",
            id="mapping_from_missing",
        ),
        pytest.param(
            {"terms": [], "forbidden_mappings": [{"from": "a", "to": 2}]},
            r"^MALFORMED_REGISTRY:forbidden_mappings\[\]:to:expected_str$",
            id="mapping_to_not_a_string",
        ),
    ],
)
def test_from_dict_rejects_malformed_shapes(data: dict[str, Any], expected_message: str) -> None:
    with pytest.raises(RegistryError, match=expected_message):
        Registry.from_dict(data)


def test_term_from_dict_populates_meaning_layer_and_source_from_the_input() -> None:
    # meaning/layer/source are not exercised by any resolve()/tier()/DUPLICATE_*
    # check, so nothing above observes their VALUE on the happy path -- only a
    # direct field assertion can catch a validator being bypassed entirely
    # (e.g. the field silently becoming None instead of the parsed string).
    raw = _minimal_term("x", "X")
    raw["meaning"] = "distinctive meaning text"
    raw["layer"] = "distinctive-layer"
    raw["source"] = "distinctive-source"
    registry = Registry.from_dict({"terms": [raw], "forbidden_mappings": []})
    term = registry.resolve("X")
    assert term.meaning == "distinctive meaning text"
    assert term.layer == "distinctive-layer"
    assert term.source == "distinctive-source"


def test_from_dict_defaults_missing_terms_and_forbidden_mappings_keys_to_empty() -> None:
    # Both top-level keys are optional (`.get(key, [])`); omitting either must
    # build an empty-but-valid Registry, not raise.
    registry = Registry.from_dict({})
    with pytest.raises(RegistryError, match=r"^UNKNOWN_TERM$"):
        registry.resolve("anything")


def test_registry_distinguishes_id_canonical_and_alias_lookup_across_two_terms() -> None:
    # Two distinct terms with distinct id/canonical/alias strings: resolving
    # each of the three keys for each term must land on that term specifically,
    # which would fail if __init__ mixed up which dict backs which lookup.
    term_one = _minimal_term("id-one", "Canonical-One")
    term_one["aliases"] = [{"term": "alias-one", "source": "test"}]
    term_two = _minimal_term("id-two", "Canonical-Two")
    term_two["aliases"] = [{"term": "alias-two", "source": "test"}]
    registry = Registry.from_dict({"terms": [term_one, term_two], "forbidden_mappings": []})

    assert registry.resolve("Canonical-One").id == "id-one"
    assert registry.resolve("alias-one").id == "id-one"
    assert registry.resolve("Canonical-Two").id == "id-two"
    assert registry.resolve("alias-two").id == "id-two"

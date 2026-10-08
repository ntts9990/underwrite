"""Standalone behavior and boundary checks."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from underwrite_core.boundary import (
    GATE_VOCAB,
    RECOMMENDATION_AUTHORITY_VOCAB,
    Leak,
    VocabularyLeak,
    _compile_latin_pattern,  # pyright: ignore[reportPrivateUsage]
    _find_hangul,  # pyright: ignore[reportPrivateUsage]
    _is_hangul_term,  # pyright: ignore[reportPrivateUsage]
    _split_vocab,  # pyright: ignore[reportPrivateUsage]
    _walk,  # pyright: ignore[reportPrivateUsage]
    assert_measurement_only,
    scan_strings,
)

_ALLOWLISTED_COMPOUNDS = (
    "threshold",
    "shareholder",
    "household",
    "withhold",
    "holder",
    "holdout",
    "foothold",
    "stronghold",
    "uphold",
)


def test_korean_particle_after_gate_term_is_detected() -> None:
    leaks = scan_strings({"note": "게이트 판정 promote와 함께"})
    assert len(leaks) == 1
    assert leaks[0].path == "$.note"
    assert leaks[0].term == "promote"
    assert leaks[0].family == "gate_vocab"


def test_threshold_and_shareholder_do_not_leak() -> None:
    assert scan_strings({"summary": "threshold 0.7 for shareholder value"}) == []


def test_authority_leak_in_deeply_nested_list() -> None:
    leaks = scan_strings({"a": {"b": [{"c": "Ready for production"}]}})
    assert len(leaks) == 1
    assert leaks[0].path == "$.a.b[0].c"
    assert leaks[0].term == "Ready for production"
    assert leaks[0].family == "authority_vocab"


def test_dict_key_itself_can_leak() -> None:
    leaks = scan_strings({"promote": 1})
    assert len(leaks) == 1
    assert leaks[0].path == "$.promote"
    assert leaks[0].term == "promote"
    assert leaks[0].family == "gate_vocab"


def test_nesting_depth_five_finds_leak_at_correct_path() -> None:
    payload = {"l1": {"l2": {"l3": [{"l4": {"l5": "please approve this rollout"}}]}}}
    leaks = scan_strings(payload)
    assert len(leaks) == 1
    assert leaks[0].path == "$.l1.l2.l3[0].l4.l5"
    assert leaks[0].term == "approve"
    assert leaks[0].family == "gate_vocab"


def test_both_families_are_exercised_independently() -> None:
    leaks = scan_strings({"gate_field": "promote it", "authority_field": "we recommend this"})
    assert {leak.family for leak in leaks} == {"gate_vocab", "authority_vocab"}


@pytest.mark.parametrize("compound", _ALLOWLISTED_COMPOUNDS)
def test_allowlisted_english_compounds_do_not_leak(compound: str) -> None:
    assert scan_strings(compound) == []
    assert scan_strings(f"the {compound} was noted") == []


def test_korean_promote_alias_is_detected() -> None:
    leaks = scan_strings("승격 가능")
    assert len(leaks) == 1
    assert leaks[0].path == "$"
    assert leaks[0].term == "승격"
    assert leaks[0].family == "gate_vocab"


def test_hangul_term_repeated_finds_every_occurrence_in_order() -> None:
    leaks = scan_strings({"note": "배포 이후 다시 배포"})
    assert [leak.term for leak in leaks] == ["배포", "배포"]
    assert [leak.path for leak in leaks] == ["$.note", "$.note"]
    assert {leak.family for leak in leaks} == {"gate_vocab"}


def test_korean_authority_term_is_detected_with_authority_family() -> None:
    # The only existing Korean-vocabulary tests use GATE_VOCAB hangul terms
    # (승격/배포); RECOMMENDATION_AUTHORITY_VOCAB's hangul terms (권고/권장)
    # were otherwise never exercised, leaving _scan_text's authority-family
    # _find_hangul call (as opposed to its gate-family one, a few lines
    # earlier) unverified.
    leaks = scan_strings({"note": "이 변경을 권고합니다"})
    assert len(leaks) == 1
    assert leaks[0].path == "$.note"
    assert leaks[0].term == "권고"
    assert leaks[0].family == "authority_vocab"


def test_holdout_set_does_not_leak() -> None:
    assert scan_strings("Holdout set") == []


def test_bare_hold_leaks() -> None:
    leaks = scan_strings("hold")
    assert len(leaks) == 1
    assert leaks[0].term == "hold"
    assert leaks[0].family == "gate_vocab"


def test_case_insensitive_match() -> None:
    leaks = scan_strings("PROMOTE")
    assert len(leaks) == 1
    assert leaks[0].term == "PROMOTE"
    assert leaks[0].family == "gate_vocab"


def test_tuples_are_not_walked() -> None:
    assert scan_strings({"t": ("promote",)}) == []


def test_top_level_list_indexing() -> None:
    leaks = scan_strings(["safe", "please reject this"])
    assert len(leaks) == 1
    assert leaks[0].path == "$[1]"
    assert leaks[0].term == "reject"
    assert leaks[0].family == "gate_vocab"


def test_assert_measurement_only_passes_clean_payload() -> None:
    assert assert_measurement_only({"note": "all measurements nominal"}) is None


def test_assert_measurement_only_raises_vocabulary_leak_with_leaks() -> None:
    with pytest.raises(VocabularyLeak) as exc_info:
        assert_measurement_only({"decision": "Approve deployment"})
    assert exc_info.value.leaks
    assert all(isinstance(leak, Leak) for leak in exc_info.value.leaks)


def test_vocabulary_leak_message_lists_each_leak_semicolon_joined() -> None:
    # Exact equality (not just `in`), and at/under _MAX_SHOWN_LEAKS (5) so
    # `omitted` is falsy: this is the only way to observe that `suffix` is
    # exactly "" here, not e.g. a stray literal appended in its place.
    leaks = [
        Leak(path="$.a", term="promote", family="gate_vocab"),
        Leak(path="$.b", term="recommend", family="authority_vocab"),
    ]
    message = str(VocabularyLeak(leaks))
    assert message == (
        "vocabulary boundary violated: 2 leak(s): "
        "$.a='promote'(gate_vocab); $.b='recommend'(authority_vocab)"
    )


def test_vocabulary_leak_message_reports_the_omitted_count_beyond_the_shown_cap() -> None:
    # 7 leaks > _MAX_SHOWN_LEAKS (5): only a leak count strictly above the cap
    # makes `omitted` truthy, so this is the only way to observe its actual
    # value (as opposed to merely its truthiness) in the rendered message.
    leaks = [Leak(path=f"$.{i}", term="promote", family="gate_vocab") for i in range(7)]
    message = str(VocabularyLeak(leaks))
    assert message.endswith(" (+2 more)")
    assert "7 leak(s)" in message


_SAFE_WORDS = ("alpha", "beta", "gamma", "delta", "epsilon", "widget", "value", "note")
_SAFE_KOREAN = ("가나다", "다라마", "바사아", "자차카", "파타하")
_ALL_VOCAB = GATE_VOCAB + RECOMMENDATION_AUTHORITY_VOCAB


def _safe_leaves() -> st.SearchStrategy[object]:
    """Leaves drawn from an alphabet that excludes every vocabulary token."""
    return st.one_of(
        st.sampled_from(_SAFE_WORDS),
        st.sampled_from(_SAFE_KOREAN),
        st.integers(),
        st.booleans(),
        st.none(),
    )


def _safe_objects() -> st.SearchStrategy[object]:
    return st.recursive(
        _safe_leaves(),
        lambda children: st.one_of(
            st.lists(children, max_size=4),
            st.dictionaries(st.sampled_from(_SAFE_WORDS), children, max_size=4),
        ),
        max_leaves=20,
    )


@settings(max_examples=50, deadline=None)
@given(_safe_objects())
def test_property_safe_alphabet_never_leaks(obj: object) -> None:
    assert scan_strings(obj) == []


@settings(max_examples=50, deadline=None)
@given(_safe_objects(), st.sampled_from(_ALL_VOCAB))
def test_property_injected_vocabulary_term_always_leaks(obj: object, term: str) -> None:
    tainted = {"field": f"alpha {term} beta", "context": obj}
    leaks = scan_strings(tainted)
    assert len(leaks) >= 1
    assert any(leak.term == term for leak in leaks)


# --- direct unit coverage of the private helpers ----------------------------


def test_is_hangul_term_true_for_hangul_false_for_latin() -> None:
    assert _is_hangul_term("승격") is True
    assert _is_hangul_term("promote") is False
    assert _is_hangul_term("mixed승") is True


def test_is_hangul_term_true_at_the_exact_range_boundaries() -> None:
    # _HANGUL_RANGES bounds are inclusive on both ends; a strict `<` or `<=`
    # swap at either end only shows up at the boundary code points themselves.
    assert _is_hangul_term(chr(0xAC00)) is True  # first Hangul syllable
    assert _is_hangul_term(chr(0xD7A3)) is True  # last Hangul syllable


def test_split_vocab_partitions_latin_and_hangul() -> None:
    latin, hangul = _split_vocab(("promote", "승격", "approve", "배포"))
    assert latin == ("promote", "approve")
    assert hangul == ("승격", "배포")


def test_split_vocab_empty_input_gives_two_empty_tuples() -> None:
    assert _split_vocab(()) == ((), ())


def test_compile_latin_pattern_of_empty_terms_matches_nothing() -> None:
    pattern = _compile_latin_pattern(())
    assert pattern.search("promote approve reject anything at all") is None


def test_compile_latin_pattern_is_case_insensitive_and_word_bounded() -> None:
    pattern = _compile_latin_pattern(("promote",))
    assert pattern.search("PROMOTE") is not None
    assert pattern.search("promotion") is None  # blocked by the trailing lookaround


def test_compile_latin_pattern_joins_multiple_terms_with_a_bare_pipe() -> None:
    # A single-term tuple can't show a broken join separator (nothing to
    # join). With two terms, a corrupted separator (anything but a bare "|")
    # turns the alternation into unrelated concatenated branches that no
    # longer match either original term.
    pattern = _compile_latin_pattern(("promote", "approve"))
    assert pattern.search("please approve this") is not None
    assert pattern.search("please promote this") is not None


def test_walk_ignores_bare_scalars_at_the_top_level() -> None:
    leaks: list[Leak] = []
    _walk(42, "$", leaks)
    _walk(True, "$", leaks)
    _walk(None, "$", leaks)
    assert leaks == []


def test_walk_formats_a_non_string_dict_key_with_repr() -> None:
    leaks: list[Leak] = []
    _walk({1: "promote"}, "$", leaks)
    assert len(leaks) == 1
    assert leaks[0].path == "$.1"  # non-str key path uses !r, value still scanned


def test_walk_scans_string_dict_keys_themselves() -> None:
    leaks: list[Leak] = []
    _walk({"reject": "safe value"}, "$", leaks)
    assert len(leaks) == 1
    assert leaks[0].path == "$.reject"
    assert leaks[0].term == "reject"


# --- _find_hangul: progress guarantee ---------------------------------------


def test_find_hangul_raises_instead_of_looping_forever_on_an_empty_term() -> None:
    # An empty term would never advance `start` past a match (str.find("",
    # start) always returns `start` itself unchanged), which would otherwise
    # scan forever; the progress guard converts that into a fast,
    # deterministic failure instead of a hang. This also documents why the
    # scan is safe in real use: every registered vocabulary term is
    # non-empty (see GATE_VOCAB/RECOMMENDATION_AUTHORITY_VOCAB above).
    with pytest.raises(AssertionError, match=r"did not advance past index 0"):
        _find_hangul("anything at all", ("",), "gate_vocab", "$")


def test_find_hangul_advances_past_adjacent_occurrences_of_a_real_term() -> None:
    # A real (non-empty) term never trips the guard, including back-to-back
    # occurrences with no separator between them.
    leaks = _find_hangul("배포배포", ("배포",), "gate_vocab", "$note")
    assert [leak.term for leak in leaks] == ["배포", "배포"]

"""Standalone behavior and boundary checks."""

from __future__ import annotations

import hashlib
import json
import unicodedata

import pytest
from _paths import repo_root
from hypothesis import given, settings
from hypothesis import strategies as st
from underwrite_core import canonical
from underwrite_core.canonical import (
    CANONICAL_SCHEMA,
    SAFE_INT_MAX,
    CanonicalizationError,
    canonical_bytes,
    content_digest,
    digest_bytes,
    tagged_digest,
)

REPO_ROOT = repo_root()
CROSS_LANGUAGE_DIR = REPO_ROOT / "fixtures" / "golden" / "cross-language"

_BACKSLASH = chr(0x5C)
_QUOTE = chr(0x22)
_EURO = chr(0x20AC)
_CR = chr(0x0D)
_CTRL_0X80 = chr(0x80)
_O_DIAERESIS = chr(0xF6)
_FULLWIDTH_TILDE = chr(0xFF5E)
_U10000 = chr(0x10000)
_EMOJI_GRINNING = chr(0x1F600)
_HEBREW_PRESENTATION_FORM = chr(0xFB33)


# --- constants ---------------------------------------------------------


def test_constants() -> None:
    assert SAFE_INT_MAX == 2**53
    assert CANONICAL_SCHEMA == "underwrite-canonical-json/v1"
    assert issubclass(CanonicalizationError, ValueError)


# --- RFC 8785 §3.2.3 Example 1: numbers / string / literals ------------


def test_rfc8785_example_numbers_string_literals() -> None:
    string_value = (
        _EURO
        + "$"
        + chr(0x0F)  # control char with no named escape
        + chr(0x0A)  # line feed
        + "A'B"
        + _QUOTE
        + _BACKSLASH
        + _BACKSLASH
        + _QUOTE
        + "/"
    )
    value = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 0.000000000000000000000000001],
        "string": string_value,
        "literals": [None, True, False],
    }
    string_escaped = (
        _EURO
        + "$"
        + _BACKSLASH
        + "u000f"
        + _BACKSLASH
        + "n"
        + "A'B"
        + _BACKSLASH
        + _QUOTE
        + _BACKSLASH
        + _BACKSLASH
        + _BACKSLASH
        + _BACKSLASH
        + _BACKSLASH
        + _QUOTE
        + "/"
    )
    expected = (
        '{"literals":[null,true,false],'
        '"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"' + string_escaped + '"}'
    )
    result = canonical_bytes(value)
    assert isinstance(result, bytes)
    assert result == expected.encode("utf-8")


# --- RFC 8785 §3.2.3 Example 2: property-name sort order ---------------


def test_rfc8785_key_sort_order_matches_spec() -> None:
    """Standalone behavior and boundary checks."""
    keys = {
        "euro": _EURO,
        "carriage_return": _CR,
        "hebrew_presentation": _HEBREW_PRESENTATION_FORM,
        "one": "1",
        "emoji": _EMOJI_GRINNING,
        "control_0x80": _CTRL_0X80,
        "o_diaeresis": _O_DIAERESIS,
    }
    sort_key = canonical._member_sort_key  # pyright: ignore[reportPrivateUsage]
    by_sort_key = sorted(keys.items(), key=lambda kv: sort_key(kv[1]))
    ordered = [name for name, _ in by_sort_key]
    assert ordered == [
        "carriage_return",
        "one",
        "control_0x80",
        "o_diaeresis",
        "euro",
        "emoji",
        "hebrew_presentation",
    ]


def test_object_key_sort_order_via_public_api() -> None:
    """Same property as the RFC example, adapted to all-NFC-safe keys so it
    runs through the real public canonical_bytes end to end."""
    value = {
        _CR: "Carriage Return",
        "1": "One",
        _CTRL_0X80: "Control",
        _O_DIAERESIS: "Latin Small Letter O With Diaeresis",
        _EURO: "Euro Sign",
        _EMOJI_GRINNING: "Emoji: Grinning Face",
        _FULLWIDTH_TILDE: "Fullwidth Tilde",
    }
    decoded = json.loads(canonical_bytes(value))
    assert list(decoded.values()) == [
        "Carriage Return",
        "One",
        "Control",
        "Latin Small Letter O With Diaeresis",
        "Euro Sign",
        "Emoji: Grinning Face",
        "Fullwidth Tilde",
    ]


def test_surrogate_pair_sorts_before_high_bmp_code_unit() -> None:
    """U+10000 (surrogate pair D800 DC00) sorts between U+20AC and U+FF5E
    under UTF-16 code-unit ordering, even though by code point U+FF5E
    (65310) is smaller than U+10000 (65536) — the exact naive-sort trap
    RFC 8785 §3.2.3 requires UTF-16 code-unit comparison to avoid.
    """
    value = {_EURO: 1, _U10000: 2, _FULLWIDTH_TILDE: 3}
    decoded = json.loads(canonical_bytes(value))
    assert list(decoded.keys()) == [_EURO, _U10000, _FULLWIDTH_TILDE]
    # Naive code-point ordering would disagree with the required order above.
    assert sorted([_EURO, _U10000, _FULLWIDTH_TILDE]) == [_EURO, _FULLWIDTH_TILDE, _U10000]


# --- number formatting (ECMAScript Number::toString layout) ------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0, "1"),
        (-0.0, "0"),
        (1e21, "1e+21"),
        (0.000001, "0.000001"),
        (0.0000001, "1e-7"),
        (5e-324, "5e-324"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),
        (0.1 + 0.2, "0.30000000000000004"),
        (333333333.33333329, "333333333.3333333"),
        (1e30, "1e+30"),
        (4.50, "4.5"),
        (2e-3, "0.002"),
        (0.000000000000000000000000001, "1e-27"),
    ],
)
def test_float_formatting(value: float, expected: str) -> None:
    # 1e20 and 123456789012345680000.0 used to be accepted vectors here;

    # see their new home in test_canonical_bytes_rejects below.
    assert canonical_bytes(value) == expected.encode("utf-8")


# --- reject rules --------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected_code"),
    [
        pytest.param("e" + chr(0x0301), "NON_NFC_STRING", id="decomposed_e_acute"),
        pytest.param(_HEBREW_PRESENTATION_FORM, "NON_NFC_STRING", id="hebrew_presentation_value"),
        pytest.param(2**53 + 1, "INT_OUT_OF_SAFE_RANGE", id="int_above_safe_range"),
        pytest.param(-(2**53 + 1), "INT_OUT_OF_SAFE_RANGE", id="negative_int_above_safe_range"),
        # (no "." or "e") above SAFE_INT_MAX is now rejected too. 1e20 and
        # 123456789012345680000.0 (== 1.2345678901234568e20) were accepted
        # vectors in test_float_formatting before this change.
        pytest.param(1e16, "INT_OUT_OF_SAFE_RANGE", id="integer_literal_float_1e16"),
        pytest.param(1.5e16, "INT_OUT_OF_SAFE_RANGE", id="integer_literal_float_1_5e16"),
        pytest.param(1e20, "INT_OUT_OF_SAFE_RANGE", id="integer_literal_float_1e20"),
        pytest.param(
            123456789012345680000.0,
            "INT_OUT_OF_SAFE_RANGE",
            id="integer_literal_float_above_safe_range",
        ),
        pytest.param(float("nan"), "NON_FINITE_NUMBER", id="nan"),
        pytest.param(float("inf"), "NON_FINITE_NUMBER", id="positive_infinity"),
        pytest.param(float("-inf"), "NON_FINITE_NUMBER", id="negative_infinity"),
        pytest.param((1, 2), "UNSUPPORTED_TYPE", id="tuple"),
        pytest.param(b"abc", "UNSUPPORTED_TYPE", id="bytes"),
        pytest.param({1, 2}, "UNSUPPORTED_TYPE", id="set"),
        pytest.param({1: "a"}, "UNSUPPORTED_TYPE", id="non_str_dict_key"),
        pytest.param(
            {_HEBREW_PRESENTATION_FORM: 1}, "NON_NFC_STRING", id="hebrew_presentation_form_key"
        ),
    ],
)
def test_canonical_bytes_rejects(value: object, expected_code: str) -> None:
    with pytest.raises(CanonicalizationError) as exc_info:
        canonical_bytes(value)
    assert exc_info.value.args[0] == expected_code


_LONE_HIGH = chr(0xD800)
_LONE_LOW = chr(0xDC00)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(_LONE_HIGH, id="lone_high_value"),
        pytest.param("a" + _LONE_LOW, id="lone_low_value"),
        pytest.param(["x", _LONE_HIGH], id="list_item"),
        pytest.param({_LONE_HIGH: 1}, id="only_key"),
        # Two keys: the RFC 8785 member sort encodes each key as UTF-16 first.
        pytest.param({"a": 1, _LONE_LOW: 2}, id="sorted_key"),
        pytest.param({"a": _LONE_HIGH}, id="member_value"),
    ],
)
def test_lone_surrogate_is_a_canonicalization_error(value: object) -> None:
    """A lone surrogate is no Unicode scalar value (I-JSON, RFC 7493 §2.1): a fixed
    reason, never a leaked ``UnicodeEncodeError``, on every public path."""
    for serialize in (canonical_bytes, content_digest):
        with pytest.raises(CanonicalizationError) as exc_info:
            serialize(value)
        assert exc_info.value.args == ("NON_UTF8_STRING",)
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None


def test_safe_int_boundary_is_accepted() -> None:
    assert canonical_bytes(2**53) == b"9007199254740992"
    assert canonical_bytes(-(2**53)) == b"-9007199254740992"
    # 2**53 exactly, as a float, renders as the same bare integer literal
    # and is accepted too -- only strictly-greater magnitudes are rejected.
    assert canonical_bytes(float(2**53)) == b"9007199254740992"
    assert canonical_bytes(float(-(2**53))) == b"-9007199254740992"


def test_integer_valued_float_above_safe_range_is_rejected_immediately() -> None:
    """Standalone behavior and boundary checks."""
    value = float(SAFE_INT_MAX + 2)  # 9007199254740994.0
    with pytest.raises(CanonicalizationError) as exc_info:
        canonical_bytes(value)
    assert exc_info.value.args[0] == "INT_OUT_OF_SAFE_RANGE"


# --- idempotence property ------------------------------------------------

_SAFE_ALPHABET: list[str] = (
    [chr(cp) for cp in range(0x30, 0x3A)]  # 0-9
    + [chr(cp) for cp in range(0x41, 0x5B)]  # A-Z
    + [chr(cp) for cp in range(0x61, 0x7B)]  # a-z
    + [chr(cp) for cp in range(0xAC00, 0xAC00 + 40)]  # Korean syllables (precomposed, NFC-stable)
    + [chr(cp) for cp in range(0x1F600, 0x1F600 + 10)]  # emoji (no decomposition, NFC-stable)
)


def test_safe_alphabet_is_nfc_stable() -> None:
    for ch in _SAFE_ALPHABET:
        assert unicodedata.normalize("NFC", ch) == ch


def _json_safe_values() -> st.SearchStrategy[object]:
    # Floats are unbounded (any finite float, including magnitudes above

    # integer-literal float rendering above SAFE_INT_MAX on its very first
    # encode (see test_integer_valued_float_above_safe_range_is_rejected_
    # immediately), so this property no longer needs to avoid that range to
    # stay meaningful -- it asserts the rejection directly instead.
    leaves = st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-SAFE_INT_MAX, max_value=SAFE_INT_MAX),
        st.floats(allow_nan=False, allow_infinity=False),
        st.text(alphabet=_SAFE_ALPHABET, max_size=8),
    )
    return st.recursive(
        leaves,
        lambda children: st.one_of(
            st.lists(children, max_size=5),
            st.dictionaries(st.text(alphabet=_SAFE_ALPHABET, max_size=8), children, max_size=5),
        ),
        max_leaves=30,
    )


@settings(max_examples=200, deadline=None)
@given(_json_safe_values())
def test_canonicalization_is_idempotent(value: object) -> None:
    try:
        first = canonical_bytes(value)
    except CanonicalizationError as exc:
        assert exc.args[0] == "INT_OUT_OF_SAFE_RANGE"
        return
    round_tripped = json.loads(first)
    second = canonical_bytes(round_tripped)
    assert first == second


# --- digests ---------------------------------------------------------------


def test_digest_bytes_empty_is_well_known_sha256() -> None:
    assert (
        digest_bytes(b"")
        == "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_content_digest_is_stable_and_matches_recorded_value() -> None:
    value = {"a": 1, "b": [1.0, chr(0xAC00)]}
    expected = "sha256:3740150898919f76606fe32aab53a41b1ad9047f6460967bf8c30b2fa517cae1"
    assert content_digest(value) == expected
    assert content_digest(value) == expected  # stable across calls


def test_local_fixture_content_digest_is_stable() -> None:
    value = json.loads((REPO_ROOT / "fixtures/examples/transport.json").read_text())
    assert content_digest(value) == content_digest(
        {"run_id": "candidate", "model": {"spec": "mock:golden"}}
    )


# --- tagged_digest: domain-separated sha256 -------------------------------


def test_tagged_digest_matches_hand_computed_sha256() -> None:
    # Computed directly with hashlib here (not via underwrite_core), per the
    # length-prefix-then-tag-then-data construction documented on tagged_digest.
    tag = "receipt.v1"
    data = b"hello world"
    tag_bytes = tag.encode("ascii")
    header = len(tag_bytes).to_bytes(8, "big")
    expected = "sha256:" + hashlib.sha256(header + tag_bytes + data).hexdigest()
    assert tagged_digest(tag, data) == expected


def test_tagged_digest_is_stable_across_calls() -> None:
    assert tagged_digest("stable-tag", b"x") == tagged_digest("stable-tag", b"x")


def test_tagged_digest_different_tags_over_the_same_data_differ() -> None:
    data = b"same payload"
    assert tagged_digest("tag-one", data) != tagged_digest("tag-two", data)


def test_tagged_digest_length_prefix_prevents_tag_data_boundary_collision() -> None:
    # Without the 8-byte length prefix, ("ab", "cdata") and ("abc", "data")
    # would hash identically (both concatenate to "abcdata"); the prefix
    # binds each tag's own length so the boundary can never shift.
    assert tagged_digest("ab", b"cdata") != tagged_digest("abc", b"data")


def test_tagged_digest_differs_from_untagged_content_digest() -> None:
    assert tagged_digest("some-tag", b"payload") != digest_bytes(b"payload")


@pytest.mark.parametrize(
    "bad_tag",
    [
        "",
        "Has_Upper",
        "has space",
        "has/slash",
        "has.unicodeé",
    ],
)
def test_tagged_digest_rejects_malformed_tag(bad_tag: str) -> None:
    with pytest.raises(CanonicalizationError) as exc_info:
        tagged_digest(bad_tag, b"data")
    assert exc_info.value.args[0] == "INVALID_TAG"


def test_tagged_digest_accepts_the_full_allowed_character_set() -> None:
    tag = "abc-123._xyz"
    # Just must not raise; the exact digest is already covered by the
    # hand-computed test above.
    assert tagged_digest(tag, b"").startswith("sha256:")


# --- cross-language golden: RFC 8785 reference implementation parity ------
#
# fixtures/golden/cross-language/*.canonical.bytes were produced by the npm
# `canonicalize` package (RFC 8785 JCS reference implementation co-authored
# by RFC 8785 author Anders Rundgren) run under Node.js -- see SOURCE.json
# for the exact package version, Node version, and procedure. Each fixture
# is NFC-safe and every number in it (int or float) stays within the

# reject rules, which RFC 8785 itself does not impose and the reference
# implementation does not enforce.


@pytest.mark.parametrize(
    "fixture_name", ["unicode-keys", "numbers", "nested"], ids=["unicode_keys", "numbers", "nested"]
)
def test_canonical_bytes_matches_cross_language_reference_golden(fixture_name: str) -> None:
    value = json.loads((CROSS_LANGUAGE_DIR / f"{fixture_name}.json").read_text(encoding="utf-8"))
    expected = (CROSS_LANGUAGE_DIR / f"{fixture_name}.canonical.bytes").read_bytes()
    assert canonical_bytes(value) == expected


def test_cross_language_golden_bytes_match_recorded_source_sha256() -> None:
    source = json.loads((CROSS_LANGUAGE_DIR / "SOURCE.json").read_text(encoding="utf-8"))
    for name, info in source["files"].items():
        canonical_path = CROSS_LANGUAGE_DIR / info["canonical_bytes_file"]
        actual = hashlib.sha256(canonical_path.read_bytes()).hexdigest()
        assert actual == info["sha256"], name

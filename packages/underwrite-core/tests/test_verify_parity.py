"""Standalone behavior and boundary checks."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from _paths import core_root
from _verify_loader import load_module_from_path
from hypothesis import given, settings
from hypothesis import strategies as st
from underwrite_core import bundle, canonical, chain

VERIFY_PY_PATH = core_root() / "build" / "verify.py"

verify_gen: Any = load_module_from_path("verify_gen", VERIFY_PY_PATH)


# --- fixed sample set: canonical_bytes / content_digest parity -------------

_KOREAN_OBJECT: dict[str, object] = {
    "이름": "홍길동",
    "나이": 30,
    "직업": "개발자",
    "취미": ["독서", "등산"],
}
_NESTED_OBJECT: dict[str, object] = {
    "a": [1, 2, {"b": [3.0, "한글", None, True, False]}],
    "empty_list": [],
    "empty_dict": {},
}


#  test_integer_literal_float_above_safe_range_rejected_identically below.
_FIXED_SAMPLES: tuple[tuple[str, object], ...] = (
    ("korean_object", _KOREAN_OBJECT),
    ("korean_string", "한글 문자열입니다"),
    ("integer_valued_float", 1.0),
    ("integer_valued_float_450", 4.50),
    ("es6_exponential_boundary_1e21", 1e21),
    ("nested_dicts_and_lists", _NESTED_OBJECT),
    ("safe_int_max_2_53", 2**53),
    ("negative_safe_int_max", -(2**53)),
    ("safe_int_max_as_float", float(2**53)),
    ("empty_list", []),
    ("empty_dict", {}),
    ("empty_string", ""),
)


def test_fixed_samples_canonical_bytes_and_content_digest_parity() -> None:
    for _label, value in _FIXED_SAMPLES:
        assert verify_gen.canonical_bytes(value) == canonical.canonical_bytes(value)
        assert verify_gen.content_digest(value) == canonical.content_digest(value)


def test_decomposed_nfc_string_rejected_identically() -> None:
    decomposed_e_acute = "e" + chr(0x0301)

    core_error = None
    try:
        canonical.canonical_bytes(decomposed_e_acute)
    except Exception as exc:  # noqa: BLE001 -- parity test wants any raised error's code
        core_error = exc

    gen_error = None
    try:
        verify_gen.canonical_bytes(decomposed_e_acute)
    except Exception as exc:  # noqa: BLE001
        gen_error = exc

    assert core_error is not None and gen_error is not None
    assert core_error.args[0] == "NON_NFC_STRING"
    assert gen_error.args[0] == "NON_NFC_STRING"


def test_int_above_safe_range_rejected_identically() -> None:
    over_range = canonical.SAFE_INT_MAX + 1

    core_error = None
    try:
        canonical.canonical_bytes(over_range)
    except Exception as exc:  # noqa: BLE001
        core_error = exc

    gen_error = None
    try:
        verify_gen.canonical_bytes(over_range)
    except Exception as exc:  # noqa: BLE001
        gen_error = exc

    assert core_error is not None and gen_error is not None
    assert core_error.args[0] == "INT_OUT_OF_SAFE_RANGE"
    assert gen_error.args[0] == "INT_OUT_OF_SAFE_RANGE"


def test_integer_literal_float_above_safe_range_rejected_identically() -> None:
    """Standalone behavior and boundary checks."""
    over_range = 1e20

    core_error = None
    try:
        canonical.canonical_bytes(over_range)
    except Exception as exc:  # noqa: BLE001
        core_error = exc

    gen_error = None
    try:
        verify_gen.canonical_bytes(over_range)
    except Exception as exc:  # noqa: BLE001
        gen_error = exc

    assert core_error is not None and gen_error is not None
    assert core_error.args[0] == "INT_OUT_OF_SAFE_RANGE"
    assert gen_error.args[0] == "INT_OUT_OF_SAFE_RANGE"


# --- hypothesis: >=200 JSON-safe objects ------------------------------------

_SAFE_ALPHABET: list[str] = (
    [chr(cp) for cp in range(0x30, 0x3A)]  # 0-9
    + [chr(cp) for cp in range(0x41, 0x5B)]  # A-Z
    + [chr(cp) for cp in range(0x61, 0x7B)]  # a-z
    + [chr(cp) for cp in range(0xAC00, 0xAC00 + 40)]  # Korean syllables (NFC-stable)
)


def _json_safe_values() -> st.SearchStrategy[object]:
    leaves = st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-canonical.SAFE_INT_MAX, max_value=canonical.SAFE_INT_MAX),
        st.floats(
            min_value=float(-canonical.SAFE_INT_MAX),
            max_value=float(canonical.SAFE_INT_MAX),
            allow_nan=False,
            allow_infinity=False,
        ),
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
def test_canonical_bytes_and_content_digest_parity_property(value: object) -> None:
    assert verify_gen.canonical_bytes(value) == canonical.canonical_bytes(value)
    assert verify_gen.content_digest(value) == canonical.content_digest(value)


# --- chain parity: a 10-row chain verifies identically ----------------------

_CHAIN_LEN = 10
_TAMPERED_SEQ = 4


def _build_core_chain(length: int) -> list[chain.ChainRow]:
    rows: list[chain.ChainRow] = []
    for i in range(length):
        rows.append(chain.append_row(rows, kind="seal", payload_digest=f"sha256:payload-{i}"))
    return rows


def _as_gen_rows(rows: list[chain.ChainRow]) -> list[Any]:
    return [
        verify_gen.ChainRow(
            seq=row.seq,
            kind=row.kind,
            payload_digest=row.payload_digest,
            prev_hash=row.prev_hash,
            row_hash=row.row_hash,
        )
        for row in rows
    ]


def test_chain_verify_parity_on_a_clean_ten_row_chain() -> None:
    rows = _build_core_chain(_CHAIN_LEN)
    gen_rows = _as_gen_rows(rows)

    core_verdict = chain.verify_chain(rows)
    gen_verdict = verify_gen.verify_chain(gen_rows)

    assert core_verdict.ok is True
    assert gen_verdict.ok is True
    assert core_verdict.reason.value == gen_verdict.reason.value == "OK"
    assert core_verdict.at_seq == gen_verdict.at_seq is None


def test_chain_verify_parity_on_an_edited_row_reports_the_same_reason() -> None:
    rows = _build_core_chain(_CHAIN_LEN)
    edited = list(rows)
    edited[_TAMPERED_SEQ] = dataclasses.replace(edited[_TAMPERED_SEQ], payload_digest="TAMPERED")
    gen_rows = _as_gen_rows(edited)

    core_verdict = chain.verify_chain(edited)
    gen_verdict = verify_gen.verify_chain(gen_rows)

    assert core_verdict.ok is False
    assert gen_verdict.ok is False
    assert core_verdict.reason.value == gen_verdict.reason.value == "CHAIN_EDIT"
    assert core_verdict.at_seq == gen_verdict.at_seq == _TAMPERED_SEQ


# --- bundle layout parity: identical manifest/index bytes -------------------


def test_bundle_layout_parity_same_manifest_and_index_bytes() -> None:
    core_blobs = [
        bundle.Blob(role="receipt", media_type="application/json", data=b'{"a":1}'),
        bundle.Blob(role="evidence", media_type="application/json", data=b"hello world"),
    ]
    gen_blobs = [
        verify_gen.Blob(role=b.role, media_type=b.media_type, data=b.data) for b in core_blobs
    ]

    core_files = bundle.plan_layout(core_blobs)
    gen_files = verify_gen.plan_layout(gen_blobs)

    assert set(core_files) == set(gen_files)
    assert core_files["manifest-sha256.txt"] == gen_files["manifest-sha256.txt"]
    assert core_files["index.json"] == gen_files["index.json"]
    for path, data in core_files.items():
        assert gen_files[path] == data


def test_bundle_verify_layout_parity_on_a_one_byte_tamper() -> None:
    core_blob = bundle.Blob(role="receipt", media_type="application/json", data=b"hello world")
    core_files = dict(bundle.plan_layout([core_blob]))
    gen_blob = verify_gen.Blob(role="receipt", media_type="application/json", data=b"hello world")
    gen_files = dict(verify_gen.plan_layout([gen_blob]))

    path = next(p for p in core_files if p.startswith("blobs/sha256/"))
    mutated = bytearray(core_files[path])
    mutated[0] ^= 0xFF
    core_files[path] = bytes(mutated)
    gen_files[path] = bytes(mutated)

    core_verdict = bundle.verify_layout(core_files, bundle.ExpectedSet(frozenset({"receipt"})))
    gen_verdict = verify_gen.verify_layout(
        gen_files, verify_gen.ExpectedSet(frozenset({"receipt"}))
    )

    assert core_verdict.ok is False
    assert gen_verdict.ok is False
    core_reason_pairs = {(reason.value, detail) for reason, detail in core_verdict.reasons}
    gen_reason_pairs = {(reason.value, detail) for reason, detail in gen_verdict.reasons}
    assert core_reason_pairs == gen_reason_pairs


# --- SOURCE_DIGESTS is present and well-formed (drift itself is covered by
#     test_verify_generated_drift.py) -----------------------------------------


def test_source_digests_dict_has_the_three_inlined_modules() -> None:
    digests = verify_gen.SOURCE_DIGESTS
    assert set(digests) == {"canonical.py", "chain.py", "bundle.py"}
    for value in digests.values():
        assert isinstance(value, str)
        assert value.startswith("sha256:")
        json.dumps(value)  # plain str, not e.g. a lazily-computed object

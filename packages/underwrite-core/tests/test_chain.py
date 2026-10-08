"""Standalone behavior and boundary checks."""

import dataclasses

from hypothesis import given, settings
from hypothesis import strategies as st
from underwrite_core.chain import (
    AnchorRecord,
    ChainReason,
    ChainRow,
    anchor_for,
    append_row,
    row_hash_for,
    verify_chain,
)

_CHAIN_LEN = 10
_TAMPERED_SEQ = 4
_DELETED_SEQ = 5
_LAST_SEQ = _CHAIN_LEN - 1


def _build_chain(length: int) -> tuple[ChainRow, ...]:
    rows: list[ChainRow] = []
    for i in range(length):
        rows.append(append_row(rows, kind="seal", payload_digest=f"sha256:payload-{i}"))
    return tuple(rows)


def test_row_hash_for_is_deterministic_and_schema_scoped() -> None:
    first = row_hash_for(0, "seal", "sha256:payload-0", None)
    second = row_hash_for(0, "seal", "sha256:payload-0", None)
    assert first == second
    assert row_hash_for(0, "other-kind", "sha256:payload-0", None) != first


def test_append_row_starts_at_zero_with_no_prev_hash() -> None:
    row = append_row((), kind="seal", payload_digest="sha256:payload-0")
    assert row.seq == 0
    assert row.prev_hash is None
    assert row.row_hash == row_hash_for(0, "seal", "sha256:payload-0", None)


def test_append_row_chains_off_the_previous_row() -> None:
    rows = _build_chain(3)
    assert [row.seq for row in rows] == [0, 1, 2]
    assert rows[1].prev_hash == rows[0].row_hash
    assert rows[2].prev_hash == rows[1].row_hash


def test_verify_chain_ok_on_a_clean_ten_row_chain() -> None:
    verdict = verify_chain(_build_chain(_CHAIN_LEN))
    assert verdict.ok is True
    assert verdict.reason is ChainReason.OK
    assert verdict.at_seq is None


def test_verify_chain_detects_edited_payload_as_chain_edit() -> None:
    rows = list(_build_chain(_CHAIN_LEN))
    # Rebuild row 4 with a different payload_digest but the SAME (now stale) row_hash.
    rows[_TAMPERED_SEQ] = dataclasses.replace(rows[_TAMPERED_SEQ], payload_digest="TAMPERED")
    verdict = verify_chain(rows)
    assert verdict.ok is False
    assert verdict.reason is ChainReason.CHAIN_EDIT
    assert verdict.at_seq == _TAMPERED_SEQ


def test_verify_chain_detects_deleted_row_as_chain_gap() -> None:
    rows = _build_chain(_CHAIN_LEN)
    gapped = rows[:_DELETED_SEQ] + rows[_DELETED_SEQ + 1 :]
    verdict = verify_chain(gapped)
    assert verdict.ok is False
    assert verdict.reason is ChainReason.CHAIN_GAP
    assert verdict.at_seq == _DELETED_SEQ


def test_verify_chain_detects_truncated_tail_against_anchor() -> None:
    rows = _build_chain(_CHAIN_LEN)
    anchor = anchor_for(rows)
    assert anchor.upto_seq == _LAST_SEQ
    verdict = verify_chain(rows[:-2], anchor=anchor)
    assert verdict.ok is False
    assert verdict.reason is ChainReason.CHAIN_TRUNCATED
    assert verdict.at_seq == anchor.upto_seq


def test_verify_chain_empty_rows_against_an_anchor_is_truncated() -> None:
    anchor = AnchorRecord(upto_seq=0, chain_hash="sha256:whatever")
    verdict = verify_chain((), anchor=anchor)
    assert verdict.ok is False
    assert verdict.reason is ChainReason.CHAIN_TRUNCATED
    assert verdict.at_seq == 0


def test_verify_chain_correct_seq_but_wrong_prev_hash_is_still_chain_gap() -> None:
    # row.seq matches its index (so the seq half of the OR is false) but its
    # prev_hash points at a hash that is not the real previous row's hash
    # (the prev_hash half is true) -- this must still be a CHAIN_GAP. An `or`
    # mutated to `and` would miss this row (both a real gap AND the row_hash
    # recomputation below happen to also pass, since row_hash was rebuilt
    # from the same bogus prev_hash), and would only be caught, wrongly, by
    # comparing the NEXT row's prev_hash against this row's now-different
    # hash -- so an `and` mutant reports `ok=True` outright when this is the
    # last row before an unaffected tail, as here.
    rows = list(_build_chain(3))
    bad_prev = "sha256:" + "0" * 64
    rows[1] = dataclasses.replace(
        rows[1],
        prev_hash=bad_prev,
        row_hash=row_hash_for(1, rows[1].kind, rows[1].payload_digest, bad_prev),
    )
    verdict = verify_chain(rows)
    assert verdict.ok is False
    assert verdict.reason is ChainReason.CHAIN_GAP
    assert verdict.at_seq == 1


def test_verify_chain_detects_rewritten_head_against_anchor() -> None:
    rows = _build_chain(_CHAIN_LEN)
    anchor = anchor_for(rows)  # pinned to the ORIGINAL last row

    # Replace the last row with a fresh, internally self-consistent row: this
    # passes verify_chain on its own, but its hash no longer matches the anchor.
    replacement = append_row(
        rows[:_LAST_SEQ], kind="seal", payload_digest="sha256:payload-REWRITTEN"
    )
    rewritten = rows[:_LAST_SEQ] + (replacement,)

    assert verify_chain(rewritten).ok is True  # internally consistent chain
    verdict = verify_chain(rewritten, anchor=anchor)
    assert verdict.ok is False
    assert verdict.reason is ChainReason.CHAIN_HEAD_MISMATCH
    assert verdict.at_seq == anchor.upto_seq


def test_verify_chain_anchor_matching_the_last_row_is_ok() -> None:
    rows = _build_chain(_CHAIN_LEN)
    anchor = anchor_for(rows)
    verdict = verify_chain(rows, anchor=anchor)
    assert verdict.ok is True
    assert verdict.reason is ChainReason.OK
    assert verdict.at_seq is None


# --- anchor.upto_seq must be a non-negative index (fail-closed) -------------


def test_verify_chain_negative_anchor_upto_seq_is_anchor_invalid_not_ok() -> None:
    # A forged/corrupted anchor with upto_seq=-1 aliases rows[-1] via Python's
    # negative indexing, so its chain_hash trivially "matches" the real last
    # row's hash unless this is rejected before that lookup.
    rows = _build_chain(5)
    anchor = AnchorRecord(upto_seq=-1, chain_hash=rows[-1].row_hash)
    verdict = verify_chain(rows, anchor=anchor)
    assert verdict.ok is False
    assert verdict.reason is ChainReason.ANCHOR_INVALID
    assert verdict.at_seq == -1


def test_verify_chain_empty_rows_with_negative_anchor_upto_seq_does_not_raise() -> None:
    anchor = AnchorRecord(upto_seq=-1, chain_hash="sha256:whatever")
    verdict = verify_chain((), anchor=anchor)
    assert verdict.ok is False
    assert verdict.reason is ChainReason.ANCHOR_INVALID
    assert verdict.at_seq == -1


@settings(max_examples=25, deadline=None)
@given(upto_seq=st.integers(max_value=-1))
def test_verify_chain_any_negative_anchor_upto_seq_is_anchor_invalid(upto_seq: int) -> None:
    rows = _build_chain(3)
    anchor = AnchorRecord(upto_seq=upto_seq, chain_hash=rows[-1].row_hash)
    verdict = verify_chain(rows, anchor=anchor)
    assert verdict.ok is False
    assert verdict.reason is ChainReason.ANCHOR_INVALID
    assert verdict.at_seq == upto_seq


def test_anchor_for_pins_the_last_row() -> None:
    rows = _build_chain(5)
    assert anchor_for(rows) == AnchorRecord(upto_seq=4, chain_hash=rows[-1].row_hash)


# --- property: any single-row mutation is detected --------------------------


@st.composite
def _chains(draw: st.DrawFn) -> tuple[ChainRow, ...]:
    length = draw(st.integers(min_value=1, max_value=20))
    kinds = st.sampled_from(("seal", "anchor", "receipt"))
    digests = st.text(alphabet="0123456789abcdef", min_size=8, max_size=16)
    rows: list[ChainRow] = []
    for _ in range(length):
        rows.append(append_row(rows, kind=draw(kinds), payload_digest=draw(digests)))
    return tuple(rows)


def _mutate(row: ChainRow, field: str) -> ChainRow:
    if field == "payload_digest":
        return dataclasses.replace(row, payload_digest=row.payload_digest + "-MUTATED")
    if field == "prev_hash":
        return dataclasses.replace(row, prev_hash=f"{row.prev_hash}-MUTATED")
    return dataclasses.replace(row, row_hash=row.row_hash + "-MUTATED")


@settings(max_examples=100, deadline=None)
@given(chain=_chains(), data=st.data())
def test_any_single_field_mutation_is_detected(
    chain: tuple[ChainRow, ...], data: st.DataObject
) -> None:
    index = data.draw(st.integers(min_value=0, max_value=len(chain) - 1))
    field = data.draw(st.sampled_from(("payload_digest", "prev_hash", "row_hash")))
    mutated_row = _mutate(chain[index], field)
    mutated_chain = chain[:index] + (mutated_row,) + chain[index + 1 :]

    assert verify_chain(mutated_chain).ok is False

"""Hash and verify append-only ledger rows with optional external anchors.

Rows bind their sequence, payload digest and predecessor hash. Verification checks
contiguity and hashes; an independently supplied anchor also binds the expected
head and length, preventing an internally consistent replacement from passing."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from underwrite_core.canonical import content_digest


@dataclass(frozen=True)
class ChainRow:
    """One chained row: ``row_hash`` binds ``seq``/``kind``/``payload_digest``/``prev_hash``."""

    seq: int
    kind: str
    payload_digest: str
    prev_hash: str | None
    row_hash: str


def row_hash_for(seq: int, kind: str, payload_digest: str, prev_hash: str | None) -> str:
    """``content_digest`` of the row's chained fields under schema ``chain_row.v1``."""
    return content_digest(
        {
            "schema": "chain_row.v1",
            "seq": seq,
            "kind": kind,
            "payload_digest": payload_digest,
            "prev_hash": prev_hash,
        }
    )


def append_row(rows: Sequence[ChainRow], kind: str, payload_digest: str) -> ChainRow:
    """Build the next `ChainRow` after ``rows`` (``seq`` 0 and ``prev_hash`` `None` if empty)."""
    last = rows[-1] if rows else None
    seq = last.seq + 1 if last is not None else 0
    prev_hash = last.row_hash if last is not None else None
    row_hash = row_hash_for(seq, kind, payload_digest, prev_hash)
    return ChainRow(
        seq=seq, kind=kind, payload_digest=payload_digest, prev_hash=prev_hash, row_hash=row_hash
    )


class ChainReason(Enum):
    """Canonical `verify_chain` outcomes."""

    OK = "OK"
    CHAIN_EDIT = "CHAIN_EDIT"
    CHAIN_GAP = "CHAIN_GAP"
    CHAIN_TRUNCATED = "CHAIN_TRUNCATED"
    CHAIN_HEAD_MISMATCH = "CHAIN_HEAD_MISMATCH"
    ANCHOR_INVALID = "ANCHOR_INVALID"


@dataclass(frozen=True)
class ChainVerdict:
    """The outcome of one `verify_chain` call. ``at_seq`` is `None` only when ``ok``."""

    ok: bool
    reason: ChainReason
    at_seq: int | None


@dataclass(frozen=True)
class AnchorRecord:
    """Pins a chain's length and head hash."""

    upto_seq: int
    chain_hash: str
    schema: str = "anchor.v1"


def anchor_for(rows: Sequence[ChainRow]) -> AnchorRecord:
    """Anchor at the last row of ``rows``. ``rows`` must be non-empty."""
    last = rows[-1]
    return AnchorRecord(upto_seq=last.seq, chain_hash=last.row_hash)


def verify_chain(rows: Sequence[ChainRow], *, anchor: AnchorRecord | None = None) -> ChainVerdict:
    """Verify ``rows`` are a contiguous, correctly hashed chain from ``seq`` 0.

    Walks ``rows`` in order: a ``seq`` that skips ahead or a ``prev_hash``
    that does not equal the previous row's ``row_hash`` is a missing/
    reordered row (`CHAIN_GAP` at the first row where this is detected); a
    ``row_hash`` that does not match `row_hash_for` recomputed from that
    row's own fields is an in-place edit (`CHAIN_EDIT` at that row's
    ``seq``). When ``anchor`` is given, an internally consistent chain that
    does not reach ``anchor.upto_seq`` is a truncated/replaced tail
    (`CHAIN_TRUNCATED`); one that reaches it but whose row there hashes to
    something other than ``anchor.chain_hash`` is a rewritten head
    (`CHAIN_HEAD_MISMATCH`) -- both reported at ``anchor.upto_seq``. A negative
    ``anchor.upto_seq`` is rejected outright as `ANCHOR_INVALID` before any
    indexing, since Python's negative-index wraparound would otherwise alias
    an existing row (or raise `IndexError` on an empty chain).
    """
    expected_prev: str | None = None
    for index, row in enumerate(rows):
        if row.seq != index or row.prev_hash != expected_prev:
            return ChainVerdict(ok=False, reason=ChainReason.CHAIN_GAP, at_seq=index)
        if row_hash_for(row.seq, row.kind, row.payload_digest, row.prev_hash) != row.row_hash:
            return ChainVerdict(ok=False, reason=ChainReason.CHAIN_EDIT, at_seq=row.seq)
        expected_prev = row.row_hash

    if anchor is not None:
        if anchor.upto_seq < 0:
            return ChainVerdict(ok=False, reason=ChainReason.ANCHOR_INVALID, at_seq=anchor.upto_seq)
        last_seq = rows[-1].seq if rows else -1
        if last_seq < anchor.upto_seq:
            return ChainVerdict(
                ok=False, reason=ChainReason.CHAIN_TRUNCATED, at_seq=anchor.upto_seq
            )
        if rows[anchor.upto_seq].row_hash != anchor.chain_hash:
            return ChainVerdict(
                ok=False, reason=ChainReason.CHAIN_HEAD_MISMATCH, at_seq=anchor.upto_seq
            )

    return ChainVerdict(ok=True, reason=ChainReason.OK, at_seq=None)

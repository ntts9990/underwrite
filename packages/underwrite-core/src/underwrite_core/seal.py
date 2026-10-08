"""Bind structured records to canonical bytes and content digests.

All structured hashing passes through canonical serialization. Pure sealing
supplies no clock, file access, signature verification or authenticity claim."""

import datetime
import re
from dataclasses import dataclass

from underwrite_core.canonical import canonical_bytes, digest_bytes

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class SealError(ValueError):
    """Raised when a seal record or a digest cannot be sealed as specified.

    ``args[0]`` is one of: INVALID_SEALED_AT, INVALID_DIGEST.
    """


@dataclass(frozen=True)
class SealRecord:
    """One sealed subject: what was sealed, by whom, when, and its chain link.

    ``prev_ref`` is the immutable ref of the previous seal in the same
    chain, or ``None`` for the first seal. ``sealed_at`` must parse as
    ISO-8601 (validated in ``__post_init__``); it is injected by the shell,
    never read from a clock here.
    """

    subject_digest: str
    role: str
    sealed_at: str
    prev_ref: str | None
    schema: str = "seal_record.v1"

    def __post_init__(self) -> None:
        _check_sealed_at(self)


def _check_sealed_at(record: SealRecord) -> None:
    """Reject a ``sealed_at`` that is not ISO-8601; parses it, never reads a clock.
    """
    try:
        datetime.datetime.fromisoformat(record.sealed_at)
    except ValueError as exc:
        raise SealError("INVALID_SEALED_AT") from exc


def seal_record_bytes(rec: SealRecord) -> bytes:
    """Canonical bytes of ``rec``'s dict form."""
    return canonical_bytes(
        {
            "schema": rec.schema,
            "subject_digest": rec.subject_digest,
            "role": rec.role,
            "sealed_at": rec.sealed_at,
            "prev_ref": rec.prev_ref,
        }
    )


def seal_digest(rec: SealRecord) -> str:
    """``sha256:<hex>`` digest of ``seal_record_bytes(rec)``."""
    return digest_bytes(seal_record_bytes(rec))


def immutable_ref(digest: str) -> str:
    """Immutable object-store ref for a ``sha256:<64 hex>`` digest.

    Returns ``"sha256/<hh>/<hex>"`` where ``hh`` is the digest's first two
    hex characters (the shard directory) and ``hex`` is the full 64-char
    hex digest. Raises `SealError("INVALID_DIGEST")` if ``digest`` is not
    exactly the ``sha256:<64 lowercase hex>`` form.
    """
    if not _DIGEST_RE.fullmatch(digest):
        raise SealError("INVALID_DIGEST")
    hex_digest = digest.removeprefix("sha256:")
    return f"sha256/{hex_digest[:2]}/{hex_digest}"

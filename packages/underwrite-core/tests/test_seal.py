"""Standalone behavior and boundary checks."""

import pytest
from underwrite_core.canonical import canonical_bytes, digest_bytes
from underwrite_core.seal import (
    SealError,
    SealRecord,
    immutable_ref,
    seal_digest,
    seal_record_bytes,
)

_DIGEST_HEX = "ab" * 32  # 64 hex chars
_SUBJECT_DIGEST = f"sha256:{_DIGEST_HEX}"
_SEALED_AT = "2026-09-04T00:00:00+00:00"


def _record(
    *,
    subject_digest: str = _SUBJECT_DIGEST,
    role: str = "underwriter",
    sealed_at: str = _SEALED_AT,
    prev_ref: str | None = None,
) -> SealRecord:
    return SealRecord(
        subject_digest=subject_digest, role=role, sealed_at=sealed_at, prev_ref=prev_ref
    )


def test_seal_record_bytes_are_canonical_and_deterministic() -> None:
    rec = _record()
    expected = canonical_bytes(
        {
            "schema": "seal_record.v1",
            "subject_digest": _SUBJECT_DIGEST,
            "role": "underwriter",
            "sealed_at": _SEALED_AT,
            "prev_ref": None,
        }
    )
    assert seal_record_bytes(rec) == expected
    assert seal_record_bytes(rec) == seal_record_bytes(rec)


def test_seal_record_bytes_reflect_prev_ref_when_chained() -> None:
    rec = _record(prev_ref=f"sha256/{_DIGEST_HEX[:2]}/{_DIGEST_HEX}")
    assert f'"prev_ref":"sha256/{_DIGEST_HEX[:2]}/{_DIGEST_HEX}"'.encode() in seal_record_bytes(rec)


def test_seal_digest_is_stable_and_matches_canonical_digest_bytes() -> None:
    rec = _record()
    assert seal_digest(rec) == seal_digest(rec)
    assert seal_digest(rec) == digest_bytes(seal_record_bytes(rec))


def test_seal_digest_changes_when_any_field_changes() -> None:
    base = _record()
    changed = _record(role="reviewer")
    assert seal_digest(base) != seal_digest(changed)


def test_immutable_ref_format() -> None:
    assert immutable_ref(_SUBJECT_DIGEST) == f"sha256/{_DIGEST_HEX[:2]}/{_DIGEST_HEX}"


@pytest.mark.parametrize(
    "bad_digest",
    [
        "sha256:short",
        "sha256:" + "g" * 64,  # non-hex character
        "sha256:" + "A" * 64,  # uppercase hex not accepted
        "md5:" + "a" * 64,  # wrong algorithm prefix
        "a" * 64,  # missing "sha256:" prefix entirely
        "",
    ],
)
def test_immutable_ref_rejects_malformed_digest(bad_digest: str) -> None:
    with pytest.raises(SealError, match=r"^INVALID_DIGEST$"):
        immutable_ref(bad_digest)


def test_seal_record_rejects_invalid_sealed_at() -> None:
    with pytest.raises(SealError, match=r"^INVALID_SEALED_AT$"):
        _record(sealed_at="not-a-timestamp")


def test_seal_record_accepts_date_only_iso8601() -> None:
    # datetime.fromisoformat accepts a bare date; seal.py validates with it verbatim.
    rec = _record(sealed_at="2026-09-04")
    assert rec.sealed_at == "2026-09-04"

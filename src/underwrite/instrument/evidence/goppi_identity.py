"""Card: baseline-event | Paper: ADR-020 | Stage: producer-bridge.

Goppi UTF-8 decimal-length-framed identities, distinct from underwrite tags.
"""

from collections.abc import Iterable

from underwrite_core.canonical import digest_bytes


def length_prefixed_digest(parts: Iterable[str]) -> str:
    """Hash each ordered UTF-8 part as its decimal byte length, colon, then bytes."""
    framed = b"".join(
        str(len(encoded)).encode("ascii") + b":" + encoded
        for encoded in (part.encode("utf-8") for part in parts)
    )
    return digest_bytes(framed)

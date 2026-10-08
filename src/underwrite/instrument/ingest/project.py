"""One deterministic projection for every trusted codec and transport."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from typing import TypeGuard

from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes


class ProjectionError(ValueError):
    """Codec output or source metadata cannot form a canonical observation."""


class SourceError(ProjectionError):
    """Supplied source identity is invalid; never silently rewrite it."""


@dataclass(frozen=True)
class Source:
    """Stable identity; required ref=None explicitly requests raw content identity.

    A content identifier is not a resolvable original URI or producer provenance.
    locator is an optional stable source retrieval hint, not the transport path.
    """

    format: str
    format_version: str
    ref: str | None
    locator: str | None = None


@dataclass(frozen=True)
class Projection:
    """The format-specific facts extracted by a trusted codec."""

    kind: str
    payload: dict[str, object]


def _is_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _normalize(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if _is_list(value):
        return [_normalize(item) for item in value]
    if _is_dict(value):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProjectionError("NON_STRING_KEY")
            normalized = unicodedata.normalize("NFC", key)
            if normalized in result:
                raise ProjectionError("NFC_KEY_COLLISION")
            result[normalized] = _normalize(item)
        return result
    return value


def _reference(value: object) -> None:
    if not isinstance(value, str):
        raise SourceError("INVALID_PROJECTION") from TypeError("REFERENCE_MUST_BE_TEXT")
    if not value.strip():
        raise SourceError("EMPTY_REFERENCE")
    if not unicodedata.is_normalized("NFC", value):
        raise SourceError("NON_NFC_REFERENCE")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise SourceError("INVALID_REFERENCE_UNICODE") from exc


def validate_source(source: Source) -> None:
    """Validate source identity without reading or projecting an artifact."""
    for reference in (source.format, source.format_version):
        _reference(reference)
    for reference in (source.ref, source.locator):
        if reference is not None:
            _reference(reference)


def project(projection: Projection, source: Source, raw_bytes: bytes) -> dict[str, object]:
    """Build canonicalizable facts, preserving the hash of received bytes.

    The ingest boundary validates this result once against observation.v1.
    Source identities are checked rather than rewritten as different URIs.
    """
    try:
        validate_source(source)
        raw_hash = digest_bytes(raw_bytes)
        raw: dict[str, object] = {
            "ref": raw_hash if source.ref is None else source.ref,
            "hash": raw_hash,
        }
        if source.locator is not None:
            raw["locator"] = source.locator
        observation: dict[str, object] = {
            "schema": "observation.v1",
            "source": {"format": source.format, "format_version": source.format_version},
            "kind": projection.kind,
            "payload": _normalize(projection.payload),
            "raw": raw,
            "normalization": {"nfc": True},
        }
        json.dumps(observation, allow_nan=False)
        canonical_bytes(observation)
    except ProjectionError:
        raise
    except (CanonicalizationError, UnicodeError, TypeError, ValueError, RecursionError) as exc:
        raise ProjectionError("INVALID_PROJECTION") from exc
    return observation

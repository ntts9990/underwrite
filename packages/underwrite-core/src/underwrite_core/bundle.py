"""Assemble content-addressed bundles and verify their complete layout.

The caller fixes required roles independently of the supplied index. Verification
checks each blob hash, manifest entry and descriptor, refusing missing roles and
unexpected content. The returned file mapping is pure; archive I/O belongs to the
caller. Content integrity does not establish issuer authenticity."""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeGuard

from underwrite_core.canonical import canonical_bytes, digest_bytes

_INDEX_PATH = "index.json"
_MANIFEST_PATH = "manifest-sha256.txt"
_BLOB_PATH_RE = re.compile(r"^blobs/sha256/([0-9a-f]{64})$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class BundleError(ValueError):
    """Raised when planning fails; ``args[0]`` is one of: DUPLICATE_ROLE."""


@dataclass(frozen=True)
class Blob:
    """One artifact to place in the bundle, content-addressed by its `data`."""

    role: str
    media_type: str
    data: bytes


@dataclass(frozen=True)
class Descriptor:
    """One `index.json` entry: the role, media type, digest, and size it claims."""

    role: str
    media_type: str
    digest: str
    size: int


@dataclass(frozen=True)
class ExpectedSet:
    """Required-role policy fixed by the caller/verifier, never read from index.json."""

    required_roles: frozenset[str]


class BundleReason(Enum):
    """One `verify_layout` finding kind, paired with a path/role in `BundleVerdict.reasons`."""

    OK = "OK"
    MISSING_INDEX = "MISSING_INDEX"
    MISSING_MANIFEST = "MISSING_MANIFEST"
    INDEX_INVALID = "INDEX_INVALID"
    DIGEST_MISMATCH = "DIGEST_MISMATCH"
    MANIFEST_MISMATCH = "MANIFEST_MISMATCH"
    MISSING_ROLE = "MISSING_ROLE"
    EXTRA_BLOB = "EXTRA_BLOB"


@dataclass(frozen=True)
class BundleVerdict:
    """`ok` iff `reasons` is empty; each reason is a `(kind, path_or_role)` pair."""

    ok: bool
    reasons: tuple[tuple[BundleReason, str], ...]


def plan_layout(
    blobs: Sequence[Blob], *, extra_files: Mapping[str, bytes] = {}
) -> dict[str, bytes]:
    """Lay `blobs` out content-addressed with a manifest and index.json.

    Returns `blobs/sha256/<hex>` and `extra_files` paths, `manifest-sha256.txt`
    (BagIt ``"<hex>  <path>\n"`` lines sorted by path, excluding itself and
    index.json), and `index.json` (canonical bytes, of ``{"schema":
    "bundle_index.v1", "descriptors": [...sorted by role]}``). Raises
    `BundleError("DUPLICATE_ROLE:<role>")` on a repeated role."""
    seen_roles: set[str] = set()
    descriptors: list[Descriptor] = []
    files: dict[str, bytes] = {}
    for blob in blobs:
        if blob.role in seen_roles:
            raise BundleError(f"DUPLICATE_ROLE:{blob.role}")
        seen_roles.add(blob.role)
        digest = digest_bytes(blob.data)
        files[f"blobs/sha256/{digest.removeprefix('sha256:')}"] = blob.data
        size = len(blob.data)
        descriptors.append(Descriptor(blob.role, blob.media_type, digest, size))
    files.update(extra_files)

    manifest_text = "".join(
        f"{digest_bytes(data).removeprefix('sha256:')}  {path}\n"
        for path, data in sorted(files.items())
    )
    files[_MANIFEST_PATH] = manifest_text.encode("utf-8")
    files[_INDEX_PATH] = canonical_bytes(
        {
            "schema": "bundle_index.v1",
            "descriptors": [
                {"role": d.role, "mediaType": d.media_type, "digest": d.digest, "size": d.size}
                for d in sorted(descriptors, key=lambda item: item.role)
            ],
        }
    )
    return files


def verify_layout(files: Mapping[str, bytes], expected: ExpectedSet) -> BundleVerdict:
    """Independently re-verify a layout; each check re-derives its own evidence
    from `files` and all run regardless of the others' findings."""
    reasons = [*_check_digests(files), *_check_manifest(files), *_check_index(files, expected)]
    return BundleVerdict(ok=not reasons, reasons=tuple(reasons))


def _check_digests(files: Mapping[str, bytes]) -> list[tuple[BundleReason, str]]:
    """Every `blobs/sha256/<hex>` path's content must hash to its own name."""
    return [
        (BundleReason.DIGEST_MISMATCH, path)
        for path in sorted(files)
        if (match := _BLOB_PATH_RE.match(path))
        and digest_bytes(files[path]).removeprefix("sha256:") != match.group(1)
    ]


def _check_manifest(files: Mapping[str, bytes]) -> list[tuple[BundleReason, str]]:
    """Every file but index.json/the manifest itself must be listed, digest-correct."""
    manifest_data = files.get(_MANIFEST_PATH)
    if manifest_data is None:
        return [(BundleReason.MISSING_MANIFEST, _MANIFEST_PATH)]
    try:
        text = manifest_data.decode("utf-8")
    except UnicodeDecodeError:
        return [(BundleReason.MANIFEST_MISMATCH, _MANIFEST_PATH)]
    lines = (ln.partition("  ") for ln in text.splitlines() if ln)
    declared = {path: hexd for hexd, _, path in lines}
    actual = {
        path: digest_bytes(data).removeprefix("sha256:")
        for path, data in files.items()
        if path not in (_INDEX_PATH, _MANIFEST_PATH)
    }
    return [
        (BundleReason.MANIFEST_MISMATCH, path)
        for path in sorted(actual.keys() | declared.keys())
        if actual.get(path) != declared.get(path)
    ]


def _check_index(
    files: Mapping[str, bytes], expected: ExpectedSet
) -> list[tuple[BundleReason, str]]:
    """MISSING_ROLE/EXTRA_BLOB/INDEX_INVALID; required roles come only from
    `expected` (never index.json) -- this is what defeats the drop attack."""
    index_data = files.get(_INDEX_PATH)
    if index_data is None:
        return [(BundleReason.MISSING_INDEX, _INDEX_PATH)]

    descriptors, findings = _parse_index(index_data)
    roles_present: set[str] = set()
    referenced: set[str] = set()
    for descriptor in descriptors:
        role, digest = descriptor.role, descriptor.digest
        roles_present.add(role)
        hex_digest = digest.removeprefix("sha256:")
        path = f"blobs/sha256/{hex_digest}"
        blob_data = files.get(path)
        if (
            blob_data is None
            or digest_bytes(blob_data) != digest
            or len(blob_data) != descriptor.size
        ):
            findings.append((BundleReason.INDEX_INVALID, role))
        else:
            referenced.add(path)

    missing_roles = expected.required_roles - roles_present
    findings += [(BundleReason.MISSING_ROLE, role) for role in sorted(missing_roles)]
    findings += [
        (BundleReason.EXTRA_BLOB, path)
        for path in sorted(files)
        if _BLOB_PATH_RE.match(path) and path not in referenced
    ]
    return findings


def _is_dict(value: object) -> TypeGuard[dict[str, Any]]:
    """`isinstance(value, dict)`, narrowed to `dict[str, Any]` (not
    `dict[Unknown, Unknown]`) so callers type-check with no `typing.cast`."""
    return isinstance(value, dict)


def _is_list(value: object) -> TypeGuard[list[Any]]:
    """`isinstance(value, list)`, narrowed to `list[Any]` for the same
    reason as `_is_dict` above."""
    return isinstance(value, list)


def _as_str(value: object) -> str | None:
    """Narrow a string-valued index field; malformed types remain absent.

    No coercion or static assertion can turn malformed input into a string."""
    return value if isinstance(value, str) else None


def _index_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject ambiguous JSON objects before last-key-wins can discard a field."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate index key")
        result[key] = value
    return result


def _parse_index(data: bytes) -> tuple[list[Descriptor], list[tuple[BundleReason, str]]]:
    """Parse the closed bundle_index.v1 structure; any structural
    problem (bad JSON/schema/descriptor) is INDEX_INVALID, and that role then
    also reads as MISSING_ROLE below if required."""
    try:
        raw: Any = json.loads(data, object_pairs_hook=_index_object)
    except ValueError:
        return [], [(BundleReason.INDEX_INVALID, _INDEX_PATH)]
    if not _is_dict(raw):
        return [], [(BundleReason.INDEX_INVALID, _INDEX_PATH)]
    if set(raw) != {"schema", "descriptors"} or raw.get("schema") != "bundle_index.v1":
        return [], [(BundleReason.INDEX_INVALID, _INDEX_PATH)]
    raw_descriptors = raw.get("descriptors")
    if not _is_list(raw_descriptors):
        return [], [(BundleReason.INDEX_INVALID, _INDEX_PATH)]

    descriptors: list[Descriptor] = []
    findings: list[tuple[BundleReason, str]] = []
    roles: set[str] = set()
    for idx, entry in enumerate(raw_descriptors):
        fields = entry if _is_dict(entry) else None
        role = _as_str(fields.get("role")) if fields is not None else None
        digest = _as_str(fields.get("digest")) if fields is not None else None
        media_type = _as_str(fields.get("mediaType")) if fields is not None else None
        size = fields.get("size") if fields is not None else None
        if (
            fields is not None
            and set(fields) == {"role", "mediaType", "digest", "size"}
            and role
            and role not in roles
            and media_type
            and digest is not None
            and _DIGEST_RE.fullmatch(digest) is not None
            and isinstance(size, int)
            and not isinstance(size, bool)
            and size >= 0
        ):
            roles.add(role)
            descriptors.append(Descriptor(role, media_type, digest, size))
        else:
            findings.append((BundleReason.INDEX_INVALID, f"descriptors[{idx}]"))
    return descriptors, findings

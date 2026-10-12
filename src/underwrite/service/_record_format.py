"""Strict, dependency-free validation for local record bundle documents."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from typing import Final, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes

MAX_FILES: Final = 64
MAX_LINKS: Final = 16
MAX_SELECTION_EXCLUDED: Final = 32
MAX_FILE_BYTES: Final = 128 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 512 * 1024 * 1024
MAX_MANIFEST_BYTES: Final = 256 * 1024
MAX_RECORDS: Final = 1000
MAX_METADATA: Final = 16
MAX_SOURCE_PATH: Final = 4096

_RECORD_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}")
_KIND = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_LOGICAL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*")
_MEDIA_TYPE = re.compile(r"[!-~]{1,128}")
_UTC_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z")
_ROLES = frozenset(
    {"source", "input", "policy", "result", "verification", "code", "log", "environment"}
)


class FormatError(ValueError):
    """A record document violates its closed runtime contract."""


def _object(value: object, keys: frozenset[str], required: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict:
        raise FormatError("OBJECT_REQUIRED")
    result = cast(dict[object, object], value)
    if any(type(key) is not str for key in result):
        raise FormatError("STRING_KEYS_REQUIRED")
    typed = cast(dict[str, object], result)
    if set(typed) - keys or not required.issubset(typed):
        raise FormatError("OBJECT_SHAPE_INVALID")
    return typed


def _array(value: object, maximum: int, minimum: int = 0) -> list[object]:
    if type(value) is not list:
        raise FormatError("ARRAY_REQUIRED")
    items = cast(list[object], value)
    if not minimum <= len(items) <= maximum:
        raise FormatError("ARRAY_LENGTH_INVALID")
    return items


def _text(value: object, maximum: int, *, nonblank: bool = True) -> str:
    if type(value) is not str:
        raise FormatError("STRING_REQUIRED")
    text = value
    if (
        not text
        or len(text) > maximum
        or (nonblank and not any(not char.isspace() for char in text))
    ):
        raise FormatError("STRING_INVALID")
    if unicodedata.normalize("NFC", text) != text:
        raise FormatError("NON_NFC_STRING")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise FormatError("NON_UTF8_STRING") from exc
    return text


def record_id(value: object) -> str:
    text = _text(value, 96)
    if _RECORD_ID.fullmatch(text) is None:
        raise FormatError("RECORD_ID_INVALID")
    return text


def logical_name(value: object) -> str:
    text = _text(value, 256)
    if _LOGICAL_NAME.fullmatch(text) is None or "\\" in text:
        raise FormatError("LOGICAL_NAME_INVALID")
    return text


def _source_path(value: object) -> str:
    """Validate an execution-only filesystem path without normalizing its bytes."""
    if type(value) is not str:
        raise FormatError("STRING_REQUIRED")
    if (
        not value
        or len(value) > MAX_SOURCE_PATH
        or not any(not character.isspace() for character in value)
        or "\x00" in value
    ):
        raise FormatError("SOURCE_PATH_INVALID")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise FormatError("NON_UTF8_STRING") from exc
    return value


def digest(value: object) -> str:
    text = _text(value, 71)
    if _DIGEST.fullmatch(text) is None:
        raise FormatError("DIGEST_INVALID")
    return text


def _metadata(value: object) -> dict[str, str]:
    if type(value) is not dict:
        raise FormatError("OBJECT_REQUIRED")
    raw = cast(dict[object, object], value)
    if any(type(key) is not str for key in raw):
        raise FormatError("STRING_KEYS_REQUIRED")
    obj = cast(dict[str, object], raw)
    if len(obj) > MAX_METADATA:
        raise FormatError("METADATA_TOO_LARGE")
    result: dict[str, str] = {}
    for key, item in obj.items():
        result[_text(key, 64)] = _text(item, 512)
    return result


def _artifact_ref(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise FormatError("ARTIFACT_REF_INVALID")
    raw = cast(dict[object, object], value)
    scope = raw.get("scope")
    if scope == "this_record":
        obj = _object(raw, frozenset({"scope", "name"}), frozenset({"scope", "name"}))
        return {"scope": "this_record", "name": logical_name(obj["name"])}
    if scope == "linked_record":
        obj = _object(
            raw,
            frozenset({"scope", "record_id", "manifest_digest", "name"}),
            frozenset({"scope", "record_id", "manifest_digest", "name"}),
        )
        return {
            "scope": "linked_record",
            "record_id": record_id(obj["record_id"]),
            "manifest_digest": digest(obj["manifest_digest"]),
            "name": logical_name(obj["name"]),
        }
    raise FormatError("ARTIFACT_REF_INVALID")


def _selection(value: object) -> dict[str, object]:
    obj = _object(value, frozenset({"scope", "excluded"}), frozenset({"scope", "excluded"}))
    if obj["scope"] != "explicit_files":
        raise FormatError("SELECTION_INVALID")
    excluded_value = obj["excluded"]
    if excluded_value is None:
        excluded: list[str] | None = None
    else:
        excluded = [_text(item, 256) for item in _array(excluded_value, MAX_SELECTION_EXCLUDED)]
        if len(set(excluded)) != len(excluded):
            raise FormatError("SELECTION_INVALID")
    return {"scope": "explicit_files", "excluded": excluded}


def _rerun(value: object) -> dict[str, object]:
    obj = _object(value, frozenset({"status", "reference"}), frozenset({"status", "reference"}))
    status = obj["status"]
    if status == "not_performed" and obj["reference"] is None:
        return {"status": "not_performed", "reference": None}
    if status == "referenced":
        return {"status": "referenced", "reference": _artifact_ref(obj["reference"])}
    raise FormatError("RERUN_VERIFICATION_INVALID")


def _provenance(value: object) -> dict[str, object]:
    fields = frozenset({"verified", "attestation_ref"})
    obj = _object(value, fields, fields)
    if obj["verified"] is not False:
        raise FormatError("PROVENANCE_INVALID")
    reference = obj["attestation_ref"]
    return {
        "verified": False,
        "attestation_ref": None if reference is None else _artifact_ref(reference),
    }


def _link(value: object) -> dict[str, object]:
    obj = _object(
        value,
        frozenset({"relation", "record_id", "manifest_digest"}),
        frozenset({"relation", "record_id", "manifest_digest"}),
    )
    if obj["relation"] not in {"input", "related"}:
        raise FormatError("LINK_INVALID")
    return {
        "relation": obj["relation"],
        "record_id": record_id(obj["record_id"]),
        "manifest_digest": digest(obj["manifest_digest"]),
    }


def _check_names(files: list[dict[str, object]]) -> None:
    folded = sorted(cast(str, item["name"]).casefold() for item in files)
    if len(folded) != len(set(folded)):
        raise FormatError("DUPLICATE_LOGICAL_NAME")
    for index, name in enumerate(folded[:-1]):
        if folded[index + 1].startswith(name + "/"):
            raise FormatError("LOGICAL_NAME_PREFIX_CONFLICT")


def _file_spec(value: object, *, path_required: bool) -> dict[str, object]:
    keys = frozenset({"name", "role", "path", "media_type", "source_locator"})
    required = frozenset({"name", "role", "path"} if path_required else {"name", "role"})
    obj = _object(
        value,
        keys if path_required else keys - {"path"},
        required,
    )
    role = obj["role"]
    if type(role) is not str or role not in _ROLES:
        raise FormatError("ROLE_INVALID")
    media_type = obj.get("media_type", "application/octet-stream")
    if type(media_type) is not str or _MEDIA_TYPE.fullmatch(media_type) is None:
        raise FormatError("MEDIA_TYPE_INVALID")
    locator_value = obj.get("source_locator")
    locator = None if locator_value is None else _text(locator_value, 512)
    result: dict[str, object] = {
        "name": logical_name(obj["name"]),
        "role": role,
        "media_type": media_type,
        "source_locator": locator,
    }
    if path_required:
        result["path"] = _source_path(obj["path"])
    return result


def _validate_spec(value: object, *, captured: bool) -> dict[str, object]:
    keys = frozenset(
        {
            "schema",
            "record_id",
            "kind",
            "label",
            "metadata",
            "selection",
            "rerun_verification",
            "provenance",
            "links",
            "files",
        }
    )
    required = keys - {"metadata", "links"}
    obj = _object(value, keys, required)
    if obj["schema"] != "record_spec.v1":
        raise FormatError("SPEC_SCHEMA_INVALID")
    kind = _text(obj["kind"], 64)
    if _KIND.fullmatch(kind) is None:
        raise FormatError("KIND_INVALID")
    links = [_link(item) for item in _array(obj.get("links", []), MAX_LINKS)]
    link_ids = [cast(str, link["record_id"]) for link in links]
    if len(link_ids) != len(set(link_ids)):
        raise FormatError("DUPLICATE_LINK_RECORD")
    files = [
        _file_spec(item, path_required=not captured) for item in _array(obj["files"], MAX_FILES, 1)
    ]
    _check_names(files)
    result: dict[str, object] = {
        "schema": "record_spec.v1",
        "record_id": record_id(obj["record_id"]),
        "kind": kind,
        "label": _text(obj["label"], 256),
        "metadata": _metadata(obj.get("metadata", {})),
        "selection": _selection(obj["selection"]),
        "rerun_verification": _rerun(obj["rerun_verification"]),
        "provenance": _provenance(obj["provenance"]),
        "links": links,
        "files": files,
    }
    canonical_bytes(
        {
            **result,
            "files": [{key: item for key, item in file.items() if key != "path"} for file in files],
        }
    )
    return result


def validate_spec(value: object) -> dict[str, object]:
    """Validate a path-backed record specification."""
    return _validate_spec(value, captured=False)


def validate_captured_spec(value: object) -> dict[str, object]:
    """Validate a bytes-backed record specification with no source paths."""
    return _validate_spec(value, captured=True)


def _stored_file(value: object) -> dict[str, object]:
    obj = _object(
        value,
        frozenset({"name", "role", "sha256", "bytes", "media_type", "source_locator"}),
        frozenset({"name", "role", "sha256", "bytes", "media_type", "source_locator"}),
    )
    role = obj["role"]
    size = obj["bytes"]
    media_type = obj["media_type"]
    if (
        type(role) is not str
        or role not in _ROLES
        or type(size) is not int
        or not 0 <= size <= MAX_FILE_BYTES
    ):
        raise FormatError("STORED_FILE_INVALID")
    if type(media_type) is not str or _MEDIA_TYPE.fullmatch(media_type) is None:
        raise FormatError("STORED_FILE_INVALID")
    locator_value = obj["source_locator"]
    return {
        "name": logical_name(obj["name"]),
        "role": role,
        "sha256": digest(obj["sha256"]),
        "bytes": size,
        "media_type": media_type,
        "source_locator": None if locator_value is None else _text(locator_value, 512),
    }


def validate_manifest(value: object) -> dict[str, object]:
    keys = frozenset(
        {
            "schema",
            "record_id",
            "kind",
            "label",
            "metadata",
            "selection",
            "rerun_verification",
            "provenance",
            "links",
            "captured_at",
            "files",
        }
    )
    obj = _object(value, keys, keys)
    if obj["schema"] != "result_record.v1":
        raise FormatError("MANIFEST_SCHEMA_INVALID")
    kind = _text(obj["kind"], 64)
    captured_at = _text(obj["captured_at"], 40)
    if _KIND.fullmatch(kind) is None or _UTC_TIME.fullmatch(captured_at) is None:
        raise FormatError("MANIFEST_VALUE_INVALID")
    try:
        datetime.fromisoformat(captured_at.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise FormatError("MANIFEST_VALUE_INVALID") from exc
    links = [_link(item) for item in _array(obj["links"], MAX_LINKS)]
    if len({cast(str, item["record_id"]) for item in links}) != len(links):
        raise FormatError("DUPLICATE_LINK_RECORD")
    files = [_stored_file(item) for item in _array(obj["files"], MAX_FILES, 1)]
    _check_names(files)
    if sum(cast(int, item["bytes"]) for item in files) > MAX_TOTAL_BYTES:
        raise FormatError("TOTAL_BYTES_INVALID")
    result: dict[str, object] = {
        "schema": "result_record.v1",
        "record_id": record_id(obj["record_id"]),
        "kind": kind,
        "label": _text(obj["label"], 256),
        "metadata": _metadata(obj["metadata"]),
        "selection": _selection(obj["selection"]),
        "rerun_verification": _rerun(obj["rerun_verification"]),
        "provenance": _provenance(obj["provenance"]),
        "links": links,
        "captured_at": captured_at,
        "files": files,
    }
    _validate_artifact_refs(result)
    canonical_bytes(result)
    return result


def _validate_artifact_refs(record: dict[str, object]) -> None:
    record_files = cast(list[dict[str, object]], record["files"])
    record_links = cast(list[dict[str, object]], record["links"])
    files = {cast(str, item["name"]): item for item in record_files}
    links = {cast(str, item["record_id"]): item for item in record_links}
    refs = [
        cast(dict[str, object], record["rerun_verification"])["reference"],
        cast(dict[str, object], record["provenance"])["attestation_ref"],
    ]
    for index, reference in enumerate(refs):
        if reference is None:
            continue
        ref = cast(dict[str, object], reference)
        if ref["scope"] == "this_record":
            target = files.get(cast(str, ref["name"]))
        else:
            link = links.get(cast(str, ref["record_id"]))
            if link is None or link["manifest_digest"] != ref["manifest_digest"]:
                raise FormatError("ARTIFACT_REF_LINK_INVALID")
            target = None
        if ref["scope"] == "this_record" and target is None:
            raise FormatError("ARTIFACT_REF_FILE_INVALID")
        if (
            index == 0
            and ref["scope"] == "this_record"
            and target is not None
            and target["role"] != "verification"
        ):
            raise FormatError("RERUN_ROLE_INVALID")


def strict_manifest(raw: bytes) -> dict[str, object]:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise FormatError("MANIFEST_TOO_LARGE")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise FormatError("DUPLICATE_KEY")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(FormatError("NONFINITE_NUMBER")),
        )
    except FormatError:
        raise
    except (ValueError, RecursionError) as exc:
        raise FormatError("INVALID_MANIFEST_JSON") from exc

    try:
        manifest = validate_manifest(value)
        if canonical_bytes(manifest) != raw:
            raise FormatError("NONCANONICAL_MANIFEST")
        return manifest
    except (CanonicalizationError, RecursionError) as exc:
        raise FormatError("INVALID_MANIFEST_JSON") from exc

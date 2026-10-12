"""Capture the exact experimental goppi P7 consumer handoff without executing it."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import cast

from underwrite.instrument.evidence.goppi_p7_handoff import (
    COMPLETION_MEMBERS,
    MAPPING_HANDOFF,
    MAPPING_INPUT,
    MAPPING_MANIFEST,
    MAPPING_MEMBERS,
    MAPPING_POLICY,
    MAPPING_PROVENANCE,
    MAX_RECEIPTS,
    MAX_ROWS,
    ORIGINAL_PLAN,
    ORIGINAL_PUBLIC,
    ORIGINAL_RESULT,
    ORIGINAL_SCOPE,
    REGRADE_COMPLETION,
    REGRADE_RECEIPTS,
    REGRADE_ROWS,
    REGRADE_SOURCE,
    ArtifactIdentity,
    GoppiP7Documents,
)
from underwrite.instrument.ingest.local_capture import (
    CapturedBytes,
    CaptureError,
    CapturePolicy,
    RootReader,
    decode_captured_object,
)

_MAPPING_FILES = frozenset(name.removeprefix("mapping/") for name in MAPPING_MEMBERS)
_COMPLETION_FILES = frozenset(name.removeprefix("regrade/") for name in COMPLETION_MEMBERS)
_ENTRIES = frozenset(
    {
        "paired_binary_input",
        "analysis_policy",
        "derivation_provenance",
        "source_regrade_completion",
        "source_original_run_result",
    }
)
_RECEIPT = re.compile(r"private/protocol-receipts/[0-9a-f]{64}\.json\Z")
_PROTOCOL_RECEIPT_PREFIX = REGRADE_RECEIPTS.removesuffix("-manifest.json") + "/"


@dataclass(frozen=True)
class HandoffCapture:
    documents: GoppiP7Documents
    artifacts: tuple[CapturedBytes, ...]
    artifact_by_name: Mapping[str, CapturedBytes]


class HandoffInputError(ValueError):
    def __init__(self, code: str, reason: str, location: str) -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def _fail(reason: str, location: str, code: str = "INVALID_INPUT") -> HandoffInputError:
    return HandoffInputError(code, reason, location)


def _obj(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(k, str) for k in cast(dict[object, object], value)
    ):
        raise _fail("HANDOFF_OBJECT_REQUIRED", location)
    return cast(dict[str, object], value)


def _path(value: object, location: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise _fail("HANDOFF_PATH_INVALID", location)
    parts = value[1:].split("/") if value.startswith("/") else value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise _fail("HANDOFF_PATH_INVALID", location)
    return value


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _relative(path: Path, root: Path, location: str) -> str:
    _path(str(path), location)
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        raise _fail("HANDOFF_PATH_OUTSIDE_ROOT", location, "SOURCE_READ_FAILED") from None


class _Capture:
    def __init__(self, reader: RootReader, root: Path, policy: CapturePolicy) -> None:
        self.reader, self.root, self.policy = reader, root, policy
        self.artifacts: dict[str, CapturedBytes] = {}

    def read(self, path: Path, name: str, claim: dict[str, object] | None = None) -> CapturedBytes:
        location = "/" + name
        if claim is not None and "bytes" in claim:
            size = claim["bytes"]
            if type(size) is not int or size < 0:
                raise _fail("HANDOFF_MANIFEST_INVALID", location)
            if size > self.policy.max_file_bytes or size > self.policy.max_total_bytes:
                raise _fail("HANDOFF_CLAIM_LIMIT_EXCEEDED", location, "INPUT_LIMIT_EXCEEDED")
        item = self.reader.capture_relative(
            _relative(path, self.root, location),
            logical_name=name,
            max_bytes=self.policy.max_file_bytes,
            location=location,
        )
        if claim is not None:
            if claim.get("sha256") != item.sha256:
                raise _fail("HANDOFF_DIGEST_MISMATCH", location)
            if "bytes" in claim and claim["bytes"] != item.size:
                raise _fail("HANDOFF_SIZE_MISMATCH", location)
        self.artifacts[name] = item
        return item

    def obj(self, name: str) -> dict[str, object]:
        return decode_captured_object(self.artifacts[name], max_depth=32, location="/" + name)

    def manifest(
        self, doc: dict[str, object], allowed: frozenset[str] | None, base: Path, prefix: str
    ) -> None:
        entries = _manifest_entries(doc, allowed, prefix)
        claims: dict[str, dict[str, object]] = {}
        total = 0
        for raw in entries:
            entry = _obj(raw, "/" + prefix)
            path = _path(entry.get("path"), "/" + prefix)
            if set(entry) != {"path", "sha256", "bytes"} or path in claims:
                raise _fail("HANDOFF_MANIFEST_INVALID", "/" + prefix)
            if (allowed is not None and path not in allowed) or (
                allowed is None and _RECEIPT.fullmatch(path) is None
            ):
                raise _fail("HANDOFF_MANIFEST_MEMBERSHIP_INVALID", "/" + prefix)
            size = entry["bytes"]
            if type(size) is not int or size < 0:
                raise _fail("HANDOFF_MANIFEST_INVALID", "/" + prefix)
            total += size
            if size > self.policy.max_file_bytes or total > self.policy.max_total_bytes:
                raise _fail("HANDOFF_CLAIM_LIMIT_EXCEEDED", "/" + prefix, "INPUT_LIMIT_EXCEEDED")
            claims[path] = entry
        if allowed is not None and claims.keys() != allowed:
            raise _fail("HANDOFF_MANIFEST_MEMBERSHIP_INVALID", "/" + prefix)
        if allowed is None and (type(doc.get("count")) is not int or doc["count"] != len(claims)):
            raise _fail("HANDOFF_MANIFEST_COUNT_MISMATCH", "/" + prefix)
        self._check_claim_budget(claims, prefix)
        for path, entry in claims.items():
            self.read(base / path, prefix + "/" + path, entry)

    def _check_claim_budget(self, claims: dict[str, dict[str, object]], prefix: str) -> None:
        new_claims = [
            entry for path, entry in claims.items() if prefix + "/" + path not in self.artifacts
        ]
        if (
            self.reader.file_count + len(new_claims) > self.policy.max_files
            or self.reader.total_bytes + sum(cast(int, entry["bytes"]) for entry in new_claims)
            > self.policy.max_total_bytes
        ):
            raise _fail("HANDOFF_CLAIM_LIMIT_EXCEEDED", "/" + prefix, "INPUT_LIMIT_EXCEEDED")


def capture_goppi_p7_handoff(
    handoff_dir: Path,
    *,
    source_root: Path | None,
    policy: CapturePolicy,
) -> HandoffCapture:
    """Read only declared profile artifacts beneath the caller's explicit root."""
    handoff = _absolute(handoff_dir)
    root = _absolute(source_root) if source_root is not None else handoff
    _relative(handoff, root, "/handoff")
    try:
        # Caller paths may contain ..; validate their original directory walks before
        # using lexical coordinates. Producer pointers retain strict component rules.
        with RootReader.open(handoff_dir, policy):
            pass
        with RootReader.open(
            source_root if source_root is not None else handoff_dir, policy
        ) as reader:
            return _capture(_Capture(reader, root, policy), handoff)
    except CaptureError as exc:
        raise HandoffInputError(exc.code, exc.reason, exc.location) from None


def _capture(capture: _Capture, handoff_dir: Path) -> HandoffCapture:
    capture.read(handoff_dir / "consumer-handoff.json", MAPPING_HANDOFF)
    capture.read(handoff_dir / "manifest.json", MAPPING_MANIFEST)
    handoff, manifest = (
        capture.obj(MAPPING_HANDOFF),
        capture.obj(MAPPING_MANIFEST),
    )
    if (
        handoff.get("schema") != "goppi.p7_regrade_consumer_handoff.v1"
        or handoff.get("status") != "ready_for_descriptive_analysis"
        or manifest.get("schema") != "goppi.p7_regrade_mapping_bundle_manifest.v1"
        or manifest.get("status") != "complete"
    ):
        raise _fail("HANDOFF_PROFILE_UNSUPPORTED", "/mapping", "UNSUPPORTED_PROFILE")
    capture.manifest(manifest, _MAPPING_FILES, handoff_dir, "mapping")
    entries = _obj(handoff.get("entry_files"), "/handoff/entry_files")
    if entries.keys() != _ENTRIES:
        raise _fail("HANDOFF_ENTRY_SET_INVALID", "/handoff/entry_files")
    for key, filename, logical_name in (
        ("paired_binary_input", "paired-input.json", MAPPING_INPUT),
        ("analysis_policy", "analysis-policy.json", MAPPING_POLICY),
        ("derivation_provenance", "derivation-provenance.json", MAPPING_PROVENANCE),
    ):
        entry = _obj(entries[key], "/handoff/entry_files")
        if entry.get("path") != filename:
            raise _fail("HANDOFF_ENTRY_PATH_INVALID", "/handoff/entry_files")
        capture.read(handoff_dir / filename, logical_name, entry)
    completion_entry = _obj(entries["source_regrade_completion"], "/handoff/completion")
    completion_path = _completion_path(capture.root, completion_entry)
    capture.read(completion_path, REGRADE_COMPLETION, completion_entry)
    completion = capture.obj(REGRADE_COMPLETION)
    if (
        completion.get("schema") != "goppi.p7_offline_verifier_regrade_completion.v1"
        or completion.get("status") != "complete"
        or completion.get("original_outcomes_rewritten") is not False
    ):
        raise _fail("HANDOFF_COMPLETION_INVALID", "/regrade/completion")
    capture.manifest(completion, _COMPLETION_FILES, completion_path.parent, "regrade")
    receipts = capture.obj(REGRADE_RECEIPTS)
    # This producer profile declares receipt paths relative to the completion bundle.
    capture.manifest(receipts, None, completion_path.parent, "regrade")
    source = capture.obj(REGRADE_SOURCE)
    original = _obj(source.get("original"), "/regrade/source/original")
    _validate_original_paths(original)
    for key, logical_name in (
        ("result", ORIGINAL_RESULT),
        ("plan", ORIGINAL_PLAN),
        ("execution_scope", ORIGINAL_SCOPE),
    ):
        pointer = Path(_path(original.get(key + "_path"), "/original/" + key))
        if not pointer.is_absolute():
            pointer = capture.root / pointer
        capture.read(pointer, logical_name, {"sha256": original.get(key + "_file_sha256")})
    original_entry = _obj(entries["source_original_run_result"], "/handoff/original")
    _validate_original_pointer(original_entry.get("path"), original, "result")
    pointer = Path(_path(original_entry.get("path"), "/handoff/original"))
    if not pointer.is_absolute():
        pointer = capture.root / pointer
    capture.read(pointer, ORIGINAL_RESULT, original_entry)
    # Exact study layout, never an upward search or an expansion of the trusted root.
    capture.read(handoff_dir.parent / "public-summary.json", ORIGINAL_PUBLIC)
    rows = _rows(capture.artifacts[REGRADE_ROWS])
    documents = GoppiP7Documents(
        handoff=handoff,
        mapping_manifest=manifest,
        paired_input=capture.obj(MAPPING_INPUT),
        analysis_policy=capture.obj(MAPPING_POLICY),
        derivation_provenance=capture.obj(MAPPING_PROVENANCE),
        completion_manifest=completion,
        regrade_source=source,
        regrade_rows=tuple(rows),
        receipt_manifest=receipts,
        protocol_receipts={
            name: capture.obj(name)
            for name in capture.artifacts
            if name.startswith(_PROTOCOL_RECEIPT_PREFIX)
        },
        original_result=capture.obj(ORIGINAL_RESULT),
        original_scope=capture.obj(ORIGINAL_SCOPE),
        original_plan=capture.obj(ORIGINAL_PLAN),
        original_public_summary=capture.obj(ORIGINAL_PUBLIC),
        byte_identities={
            name: ArtifactIdentity(item.sha256, item.size)
            for name, item in capture.artifacts.items()
        },
    )
    return HandoffCapture(
        documents, tuple(capture.artifacts.values()), MappingProxyType(capture.artifacts)
    )


def _rows(rows_item: CapturedBytes) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, line in enumerate(rows_item.data.splitlines()):
        if not line.strip():
            continue
        if len(rows) >= MAX_ROWS:
            raise _fail("HANDOFF_ROW_LIMIT_EXCEEDED", "/regrade/rows", "INPUT_LIMIT_EXCEEDED")
        if len(line) > 2 * 1024 * 1024:
            raise _fail("HANDOFF_ROW_LIMIT_EXCEEDED", "/regrade/rows", "INPUT_LIMIT_EXCEEDED")
        rows.append(
            decode_captured_object(
                CapturedBytes(rows_item.logical_name, line, rows_item.sha256, len(line)),
                max_depth=32,
                location=f"/regrade/rows/{index}",
            )
        )
    return rows


def _validate_original_paths(original: dict[str, object]) -> None:
    for key in ("result", "plan", "execution_scope"):
        _validate_original_pointer(original.get(key + "_path"), original, key)


def _validate_original_pointer(value: object, original: dict[str, object], key: str) -> None:
    location = "/original/" + key
    path = _path(value, location)
    run = original.get("run_id")
    plan = original.get("plan_digest")
    alias = original.get("run_alias")
    if (
        not isinstance(run, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", run) is None
        or not isinstance(plan, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", plan) is None
        or not isinstance(alias, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", alias) is None
    ):
        raise _fail("HANDOFF_ORIGINAL_IDENTITY_INVALID", location)
    suffix = {
        "result": ".goppi/runs/" + run[7:] + "/local-result.json",
        "plan": ".goppi/native-plans/" + plan[7:] + ".json",
        "execution_scope": ".goppi/execution-scopes/" + alias + ".json",
    }[key]
    if path != suffix and not path.endswith("/" + suffix):
        raise _fail("HANDOFF_ORIGINAL_PATH_INVALID", location)


def _completion_path(root: Path, completion_entry: dict[str, object]) -> Path:
    completion_path = Path(_path(completion_entry.get("path"), "/handoff/completion"))
    if not completion_path.is_absolute():
        completion_path = root / completion_path
    _relative(completion_path, root, "/regrade/completion-manifest.json")
    if completion_path.parts[-2:] != ("offline-verifier-regrade", "completion-manifest.json"):
        raise _fail("HANDOFF_COMPLETION_PATH_INVALID", "/handoff/completion")
    return completion_path


def _manifest_entries(
    doc: dict[str, object], allowed: frozenset[str] | None, prefix: str
) -> list[object]:
    entries = doc.get("files")
    if not isinstance(entries, list):
        raise _fail("HANDOFF_MANIFEST_INVALID", "/" + prefix)
    if allowed is None and (
        type(doc.get("count")) is not int or not 0 <= cast(int, doc["count"]) <= MAX_RECEIPTS
    ):
        raise _fail("HANDOFF_CLAIM_LIMIT_EXCEEDED", "/" + prefix, "INPUT_LIMIT_EXCEEDED")
    maximum = MAX_RECEIPTS if allowed is None else len(allowed)
    if len(cast(list[object], entries)) > maximum:
        raise _fail("HANDOFF_CLAIM_LIMIT_EXCEEDED", "/" + prefix, "INPUT_LIMIT_EXCEEDED")
    return cast(list[object], entries)

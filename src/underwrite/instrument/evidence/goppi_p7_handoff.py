"""Pure verification of one exact goppi P7 regrade consumer handoff."""

from __future__ import annotations

import base64
import binascii
import json
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from typing import cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

MAX_CASES = 128
MAX_SLOTS = 4096
MAX_OBSERVATIONS = 8192
MAX_ROWS = 4096
MAX_RECEIPTS = 4096
MAX_FILES = 64
MAX_METADATA = 16
SAFE_INTEGER = 2**53 - 1
SHA256_DIGEST_LENGTH = 71

MAPPING_HANDOFF = "mapping/consumer-handoff.json"
MAPPING_MANIFEST = "mapping/manifest.json"
MAPPING_INPUT = "mapping/paired-input.json"
MAPPING_POLICY = "mapping/analysis-policy.json"
MAPPING_PROVENANCE = "mapping/derivation-provenance.json"
REGRADE_COMPLETION = "regrade/completion-manifest.json"
REGRADE_SOURCE = "regrade/private/source.json"
REGRADE_ROWS = "regrade/private/rows.jsonl"
REGRADE_RECEIPTS = "regrade/private/protocol-receipts-manifest.json"
ORIGINAL_RESULT = "original/result.json"
ORIGINAL_SCOPE = "original/execution-scope.json"
ORIGINAL_PLAN = "original/plan.json"
ORIGINAL_PUBLIC = "original/public-summary.json"


@dataclass(frozen=True)
class ArtifactIdentity:
    sha256: str
    bytes: int


@dataclass(frozen=True)
class GoppiP7Documents:
    handoff: dict[str, object]
    mapping_manifest: dict[str, object]
    paired_input: dict[str, object]
    analysis_policy: dict[str, object]
    derivation_provenance: dict[str, object]
    completion_manifest: dict[str, object]
    regrade_source: dict[str, object]
    regrade_rows: tuple[dict[str, object], ...]
    receipt_manifest: dict[str, object]
    protocol_receipts: dict[str, dict[str, object]]
    original_result: dict[str, object]
    original_scope: dict[str, object]
    original_plan: dict[str, object]
    original_public_summary: dict[str, object]
    byte_identities: dict[str, ArtifactIdentity]


@dataclass(frozen=True)
class HandoffFinding:
    code: str
    source: str


@dataclass(frozen=True)
class VerifiedHandoff:
    input_doc: dict[str, object]
    policy_doc: dict[str, object]
    context_doc: dict[str, object]
    scope: dict[str, object]
    findings: tuple[HandoffFinding, ...]
    context_provenance: dict[str, object]
    used_artifact_names: tuple[str, ...]


@dataclass(frozen=True)
class _RowContext:
    documents: GoppiP7Documents
    expected: set[tuple[str, str, int]]
    actual: dict[tuple[str, str, int], dict[str, object]]
    observed: dict[tuple[str, str, str], dict[str, object]]
    original: dict[str, object]
    metadata: dict[str, object]
    logical_receipts: set[str]


@dataclass(frozen=True)
class _ParsedRow:
    row: dict[str, object]
    original: dict[str, object]
    regrade: dict[str, object]
    provenance: dict[str, object]
    slot: tuple[str, str, int]


class HandoffMappingError(ValueError):
    """A finite pure mapping error without producer-controlled diagnostics."""

    def __init__(self, code: str, reason: str, location: str) -> None:
        self.code = code
        self.reason = reason
        self.location = location
        super().__init__(reason)


def _fail(reason: str, location: str, code: str = "INVALID_INPUT") -> HandoffMappingError:
    return HandoffMappingError(code, reason, location)


def _object(
    value: object,
    required: frozenset[str],
    location: str,
    *,
    allowed: frozenset[str] | None = None,
) -> dict[str, object]:
    if type(value) is not dict:
        raise _fail("HANDOFF_OBJECT_REQUIRED", location)
    raw = cast(dict[object, object], value)
    permitted = required if allowed is None else allowed
    if not len(required) <= len(raw) <= len(permitted):
        raise _fail("HANDOFF_OBJECT_SHAPE_INVALID", location)
    if any(type(key) is not str for key in raw):
        raise _fail("HANDOFF_STRING_KEYS_REQUIRED", location)
    result = cast(dict[str, object], raw)
    if not required <= result.keys() or not result.keys() <= permitted:
        raise _fail("HANDOFF_OBJECT_SHAPE_INVALID", location)
    return result


def _list(value: object, maximum: int, location: str, *, minimum: int = 0) -> list[object]:
    if type(value) is not list:
        raise _fail("HANDOFF_ARRAY_REQUIRED", location)
    result = cast(list[object], value)
    if not minimum <= len(result) <= maximum:
        raise _fail("HANDOFF_LIMIT_EXCEEDED", location, "INPUT_LIMIT_EXCEEDED")
    return result


def _tuple(value: object, maximum: int, location: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise _fail("HANDOFF_TUPLE_REQUIRED", location)
    result = cast(tuple[object, ...], value)
    if len(result) > maximum:
        raise _fail("HANDOFF_LIMIT_EXCEEDED", location, "INPUT_LIMIT_EXCEEDED")
    return result


def _text(value: object, maximum: int, location: str) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise _fail("HANDOFF_TEXT_INVALID", location)
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise _fail("HANDOFF_TEXT_INVALID", location) from exc
    if not unicodedata.is_normalized("NFC", value):
        raise _fail("HANDOFF_TEXT_INVALID", location)
    return value


def _digest(value: object, location: str) -> str:
    text = _text(value, SHA256_DIGEST_LENGTH, location)
    if (
        len(text) != SHA256_DIGEST_LENGTH
        or not text.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in text[7:])
    ):
        raise _fail("HANDOFF_DIGEST_INVALID", location)
    return text


def _integer(value: object, maximum: int, location: str, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _fail("HANDOFF_INTEGER_INVALID", location)
    return value


def _boolean(value: object, expected: bool, location: str) -> bool:
    if type(value) is not bool or value is not expected:
        raise _fail("HANDOFF_BOOLEAN_INVALID", location)
    return value


def _bool_value(value: object, location: str) -> bool:
    if type(value) is not bool:
        raise _fail("HANDOFF_BOOLEAN_INVALID", location)
    return value


def _digest_fields(value: dict[str, object], keys: tuple[str, ...], location: str) -> None:
    for key in keys:
        _digest(value[key], f"{location}/{key}")


def _integer_fields(
    value: dict[str, object], keys: tuple[str, ...], maximum: int, location: str
) -> None:
    for key in keys:
        _integer(value[key], maximum, f"{location}/{key}")


def _false_fields(value: dict[str, object], keys: tuple[str, ...], location: str) -> None:
    for key in keys:
        _boolean(value[key], False, f"{location}/{key}")


def _literal(value: object, expected: object, location: str) -> None:
    if type(value) is not type(expected) or value != expected:
        raise _fail("HANDOFF_PROFILE_UNSUPPORTED", location, "UNSUPPORTED_PROFILE")


def _identity(documents: GoppiP7Documents, name: str, location: str) -> ArtifactIdentity:
    identity = documents.byte_identities.get(name)
    if type(identity) is not ArtifactIdentity:
        raise _fail("HANDOFF_ARTIFACT_IDENTITY_MISSING", location)
    _digest(identity.sha256, location)
    _integer(identity.bytes, SAFE_INTEGER, location)
    return identity


def _manifest_files(
    value: object,
    maximum: int,
    location: str,
) -> dict[str, tuple[str, int]]:
    result: dict[str, tuple[str, int]] = {}
    for index, item in enumerate(_list(value, maximum, location)):
        item_location = f"{location}/{index}"
        entry = _object(item, frozenset({"path", "sha256", "bytes"}), item_location)
        path = _text(entry["path"], 512, f"{item_location}/path")
        if path in result:
            raise _fail("HANDOFF_DUPLICATE_ARTIFACT", item_location)
        result[path] = (
            _digest(entry["sha256"], f"{item_location}/sha256"),
            _integer(entry["bytes"], SAFE_INTEGER, f"{item_location}/bytes"),
        )
    return result


def _verify_identity_claim(
    documents: GoppiP7Documents,
    name: str,
    claimed_sha: object,
    location: str,
    claimed_bytes: object | None = None,
) -> None:
    identity = _identity(documents, name, location)
    if identity.sha256 != _digest(claimed_sha, location):
        raise _fail("HANDOFF_ARTIFACT_DIGEST_MISMATCH", location)
    if claimed_bytes is not None and identity.bytes != _integer(
        claimed_bytes, SAFE_INTEGER, location
    ):
        raise _fail("HANDOFF_ARTIFACT_SIZE_MISMATCH", location)


def _verify_mapping_bundle(
    documents: GoppiP7Documents,
) -> tuple[list[HandoffFinding], set[str]]:
    used = {
        MAPPING_HANDOFF,
        MAPPING_MANIFEST,
        MAPPING_INPUT,
        MAPPING_POLICY,
        MAPPING_PROVENANCE,
        REGRADE_COMPLETION,
        ORIGINAL_RESULT,
    }
    manifest = _object(
        documents.mapping_manifest,
        frozenset(
            {
                "schema",
                "status",
                "files",
                "policy_included",
                "analysis_included",
                "original_outcomes_rewritten",
            }
        ),
        "/mapping_manifest",
    )
    _literal(
        manifest["schema"],
        "goppi.p7_regrade_mapping_bundle_manifest.v1",
        "/mapping_manifest/schema",
    )
    _literal(manifest["status"], "complete", "/mapping_manifest/status")
    _boolean(manifest["analysis_included"], False, "/mapping_manifest/analysis_included")
    _boolean(
        manifest["original_outcomes_rewritten"],
        False,
        "/mapping_manifest/original_outcomes_rewritten",
    )
    expected_files = {
        "paired-input.json": MAPPING_INPUT,
        "analysis-policy.json": MAPPING_POLICY,
        "derivation-provenance.json": MAPPING_PROVENANCE,
        "consumer-handoff.json": MAPPING_HANDOFF,
    }
    claims = _manifest_files(manifest["files"], 4, "/mapping_manifest/files")
    if set(claims) != set(expected_files):
        raise _fail("HANDOFF_MAPPING_FILE_SET_INVALID", "/mapping_manifest/files")
    for relative, logical in expected_files.items():
        sha, size = claims[relative]
        _verify_identity_claim(documents, logical, sha, f"/mapping_manifest/files/{relative}", size)

    handoff = _object(
        documents.handoff, frozenset({"schema", "status", "entry_files", "limits"}), "/handoff"
    )
    _literal(handoff["schema"], "goppi.p7_regrade_consumer_handoff.v1", "/handoff/schema")
    _literal(handoff["status"], "ready_for_descriptive_analysis", "/handoff/status")
    entries = _object(
        handoff["entry_files"],
        frozenset(
            {
                "paired_binary_input",
                "analysis_policy",
                "derivation_provenance",
                "source_regrade_completion",
                "source_original_run_result",
            }
        ),
        "/handoff/entry_files",
    )
    entry_names = {
        "paired_binary_input": MAPPING_INPUT,
        "analysis_policy": MAPPING_POLICY,
        "derivation_provenance": MAPPING_PROVENANCE,
        "source_regrade_completion": REGRADE_COMPLETION,
        "source_original_run_result": ORIGINAL_RESULT,
    }
    for key, logical in entry_names.items():
        entry = _object(entries[key], frozenset({"path", "sha256"}), f"/handoff/entry_files/{key}")
        _text(entry["path"], 4096, f"/handoff/entry_files/{key}/path")
        _verify_identity_claim(
            documents,
            logical,
            entry["sha256"],
            f"/handoff/entry_files/{key}/sha256",
        )
    findings = (
        [
            HandoffFinding(
                "PRODUCER_POLICY_INCLUDED_FLAG_CONTRADICTION",
                MAPPING_MANIFEST,
            )
        ]
        if not _bool_value(manifest["policy_included"], "/mapping_manifest/policy_included")
        else []
    )
    return findings, used


def _verify_completion(documents: GoppiP7Documents, used: set[str]) -> tuple[set[str], set[str]]:
    completion = _object(
        documents.completion_manifest,
        frozenset(
            {
                "schema",
                "status",
                "regrade_id",
                "content_digest",
                "authentication_tag",
                "files",
                "protocol_receipts",
                "original_outcomes_rewritten",
            }
        ),
        "/completion",
        allowed=frozenset(
            {
                "schema",
                "status",
                "regrade_id",
                "content_digest",
                "authentication_tag",
                "files",
                "protocol_receipts",
                "original_outcomes_rewritten",
                "model_calls",
                "network_calls",
                "output_root",
                "producer_authentication",
                "source_preservation",
            }
        ),
    )
    _literal(
        completion["schema"],
        "goppi.p7_offline_verifier_regrade_completion.v1",
        "/completion/schema",
    )
    _literal(completion["status"], "complete", "/completion/status")
    _boolean(
        completion["original_outcomes_rewritten"],
        False,
        "/completion/original_outcomes_rewritten",
    )
    files = _manifest_files(completion["files"], MAX_FILES, "/completion/files")
    for relative, (sha, size) in files.items():
        logical = f"regrade/{relative}"
        _verify_identity_claim(documents, logical, sha, f"/completion/files/{relative}", size)
        used.add(logical)
    required = {
        "private/source.json": REGRADE_SOURCE,
        "private/rows.jsonl": REGRADE_ROWS,
        "private/protocol-receipts-manifest.json": REGRADE_RECEIPTS,
        "private/schema.json": "regrade/private/schema.json",
        "private/controls.json": "regrade/private/controls.json",
        "public-summary.json": "regrade/public-summary.json",
    }
    if not set(required) <= set(files):
        raise _fail("HANDOFF_COMPLETION_FILE_SET_INVALID", "/completion/files")
    receipt_claim = _object(
        completion["protocol_receipts"],
        frozenset({"count", "manifest_path", "manifest_sha256"}),
        "/completion/protocol_receipts",
    )
    _literal(
        receipt_claim["manifest_path"],
        "private/protocol-receipts-manifest.json",
        "/completion/protocol_receipts/manifest_path",
    )
    _verify_identity_claim(
        documents,
        REGRADE_RECEIPTS,
        receipt_claim["manifest_sha256"],
        "/completion/protocol_receipts/manifest_sha256",
    )

    receipt_manifest = _object(
        documents.receipt_manifest,
        frozenset(
            {"schema", "regrade_id", "count", "files", "content_digest", "authentication_tag"}
        ),
        "/receipt_manifest",
    )
    _literal(
        receipt_manifest["schema"],
        "goppi.p7_offline_regrade_receipts_manifest.v1",
        "/receipt_manifest/schema",
    )
    receipt_files = _manifest_files(
        receipt_manifest["files"], MAX_RECEIPTS, "/receipt_manifest/files"
    )
    if _integer(receipt_manifest["count"], MAX_RECEIPTS, "/receipt_manifest/count") != len(
        receipt_files
    ):
        raise _fail("HANDOFF_RECEIPT_COUNT_MISMATCH", "/receipt_manifest/count")
    if _integer(receipt_claim["count"], MAX_RECEIPTS, "/completion/protocol_receipts/count") != len(
        receipt_files
    ):
        raise _fail("HANDOFF_RECEIPT_COUNT_MISMATCH", "/completion/protocol_receipts/count")
    logical_receipts: set[str] = set()
    for relative, (sha, size) in receipt_files.items():
        logical = f"regrade/{relative}"
        if logical not in documents.protocol_receipts:
            raise _fail("HANDOFF_RECEIPT_MISSING", f"/receipt_manifest/files/{relative}")
        _verify_identity_claim(documents, logical, sha, f"/receipt_manifest/files/{relative}", size)
        logical_receipts.add(logical)
        used.add(logical)
    if set(documents.protocol_receipts) != logical_receipts:
        raise _fail("HANDOFF_RECEIPT_SET_MISMATCH", "/protocol_receipts")
    return logical_receipts, set(files)


def _input_parts(documents: GoppiP7Documents) -> tuple[dict[str, object], dict[str, object]]:
    input_doc = _object(
        documents.paired_input,
        frozenset(
            {
                "schema",
                "source",
                "outcome_definition",
                "metadata",
                "primary_case_ids",
                "planned_slots",
                "slot_records",
            }
        ),
        "/paired_input",
    )
    _literal(input_doc["schema"], "paired_binary_input.v1", "/paired_input/schema")
    for key, maximum in (
        ("primary_case_ids", MAX_CASES),
        ("planned_slots", MAX_SLOTS),
        ("slot_records", MAX_SLOTS),
    ):
        _list(input_doc[key], maximum, f"/paired_input/{key}")
    source = _object(
        input_doc["source"], frozenset({"producer", "version", "run_id"}), "/paired_input/source"
    )
    _literal(
        source["producer"],
        "goppi-offline-verifier-regrade",
        "/paired_input/source/producer",
    )
    _literal(
        source["version"],
        "goppi.p7_offline_verifier_regrade_completion.v1",
        "/paired_input/source/version",
    )
    _digest(source["run_id"], "/paired_input/source/run_id")
    outcome_definition = _object(
        input_doc["outcome_definition"],
        frozenset({"name", "positive_means"}),
        "/paired_input/outcome_definition",
    )
    _literal(
        outcome_definition["name"],
        "goppi_offline_regrade_binary_task_success",
        "/paired_input/outcome_definition/name",
    )
    _literal(
        outcome_definition["positive_means"],
        "persisted answer passed the same verifier core under corrected protocol invocation",
        "/paired_input/outcome_definition/positive_means",
    )
    policy = _object(
        documents.analysis_policy,
        frozenset(
            {
                "schema",
                "seed",
                "permutation_iterations",
                "bootstrap_iterations",
                "confidence",
                "max_work",
                "assumptions",
            }
        ),
        "/analysis_policy",
    )
    _literal(policy["schema"], "paired_binary_policy.v1", "/analysis_policy/schema")
    _integer(policy["seed"], SAFE_INTEGER, "/analysis_policy/seed")
    _integer(
        policy["permutation_iterations"],
        20_000,
        "/analysis_policy/permutation_iterations",
        minimum=10_000,
    )
    _integer(
        policy["bootstrap_iterations"],
        10_000,
        "/analysis_policy/bootstrap_iterations",
        minimum=1_000,
    )
    confidence = policy["confidence"]
    if type(confidence) is not float or not 0.0 < confidence < 1.0:
        raise _fail("HANDOFF_POLICY_INVALID", "/analysis_policy/confidence")
    _integer(policy["max_work"], 2_000_000, "/analysis_policy/max_work", minimum=1)
    assumptions = _object(
        policy["assumptions"],
        frozenset({"independent_units", "sign_flip_invariance", "fixed_sample"}),
        "/analysis_policy/assumptions",
    )
    _boolean(
        assumptions["independent_units"],
        False,
        "/analysis_policy/assumptions/independent_units",
    )
    _boolean(
        assumptions["sign_flip_invariance"],
        False,
        "/analysis_policy/assumptions/sign_flip_invariance",
    )
    _boolean(
        assumptions["fixed_sample"],
        True,
        "/analysis_policy/assumptions/fixed_sample",
    )
    return input_doc, policy


def _canonical_digest(value: object, location: str) -> str:
    try:
        return digest_bytes(canonical_bytes(value))
    except (CanonicalizationError, RecursionError):
        raise _fail("HANDOFF_CANONICALIZATION_FAILED", location) from None


def _verify_source_and_provenance(
    documents: GoppiP7Documents,
    input_doc: dict[str, object],
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    provenance = _object(
        documents.derivation_provenance,
        frozenset({"schema", "status", "original_run", "offline_regrade", "mapping", "claims"}),
        "/provenance",
    )
    _literal(provenance["schema"], "goppi.p7_regrade_mapping_provenance.v1", "/provenance/schema")
    _literal(provenance["status"], "mapped_not_analyzed", "/provenance/status")
    mapping = _object(
        provenance["mapping"],
        frozenset(
            {
                "spec_sha256",
                "policy_sha256",
                "paired_input_digest",
                "case_count",
                "planned_slot_count",
                "observed_slot_count",
                "missing_slot_count",
                "transitions",
            }
        ),
        "/provenance/mapping",
    )
    _digest(mapping["spec_sha256"], "/provenance/mapping/spec_sha256")
    if (
        mapping["policy_sha256"]
        != _identity(documents, MAPPING_POLICY, "/provenance/mapping/policy_sha256").sha256
    ):
        raise _fail("HANDOFF_POLICY_DIGEST_MISMATCH", "/provenance/mapping/policy_sha256")
    original_run = _object(
        provenance["original_run"],
        frozenset(
            {
                "run_id",
                "original_planned_slots",
                "original_missing_slots",
                "original_outcome_status",
                "original_outcomes_rewritten",
            }
        ),
        "/provenance/original_run",
        allowed=frozenset(
            {
                "run_id",
                "run_alias",
                "authenticated_result_sha256",
                "original_planned_slots",
                "original_missing_slots",
                "original_outcome_status",
                "original_outcomes_rewritten",
            }
        ),
    )
    _boolean(
        original_run["original_outcomes_rewritten"],
        False,
        "/provenance/original_run/original_outcomes_rewritten",
    )
    _literal(
        original_run["original_outcome_status"],
        "infra_missing",
        "/provenance/original_run/original_outcome_status",
    )
    _integer_fields(
        original_run,
        ("original_planned_slots", "original_missing_slots"),
        MAX_SLOTS,
        "/provenance/original_run",
    )
    claims = _object(
        provenance["claims"],
        frozenset(
            {
                "new_model_run",
                "original_sealed_outcomes_replaced",
                "source_claims_independently_verified",
            }
        ),
        "/provenance/claims",
        allowed=frozenset(
            {
                "new_model_run",
                "original_sealed_outcomes_replaced",
                "offline_evaluator_recovery",
                "analysis_performed",
                "source_claims_independently_verified",
                "approval_or_deployment_authority",
            }
        ),
    )
    _false_fields(
        claims,
        (
            "new_model_run",
            "original_sealed_outcomes_replaced",
            "source_claims_independently_verified",
        ),
        "/provenance/claims",
    )
    offline = _object(
        provenance["offline_regrade"],
        frozenset(
            {
                "regrade_id",
                "completion_sha256",
                "completion_content_digest",
                "evaluator_identity_digest",
                "original_failed_invocation_digest",
                "corrected_invocation_digest",
            }
        ),
        "/provenance/offline_regrade",
        allowed=frozenset(
            {
                "regrade_id",
                "completion_sha256",
                "completion_content_digest",
                "evaluator_identity_digest",
                "original_failed_invocation_digest",
                "corrected_invocation_digest",
                "producer_authentication_verified",
                "key_identity_digest",
                "verifier_core_sha256",
                "model_calls",
                "network_calls",
            }
        ),
    )
    _digest_fields(
        offline,
        (
            "regrade_id",
            "completion_sha256",
            "completion_content_digest",
            "evaluator_identity_digest",
            "original_failed_invocation_digest",
            "corrected_invocation_digest",
        ),
        "/provenance/offline_regrade",
    )
    if (
        offline["completion_sha256"]
        != _identity(
            documents, REGRADE_COMPLETION, "/provenance/offline_regrade/completion_sha256"
        ).sha256
    ):
        raise _fail(
            "HANDOFF_COMPLETION_DIGEST_MISMATCH", "/provenance/offline_regrade/completion_sha256"
        )
    completion = documents.completion_manifest
    if offline["completion_content_digest"] != completion.get("content_digest"):
        raise _fail(
            "HANDOFF_COMPLETION_DIGEST_MISMATCH",
            "/provenance/offline_regrade/completion_content_digest",
        )

    source = _object(
        documents.regrade_source,
        frozenset(
            {
                "schema",
                "regrade_id",
                "original",
                "runtime",
                "private_material",
                "verifier",
                "content_digest",
                "authentication_tag",
            }
        ),
        "/regrade_source",
        allowed=frozenset(
            {
                "schema",
                "regrade_id",
                "original",
                "runtime",
                "private_material",
                "verifier",
                "content_digest",
                "authentication_tag",
                "classification",
                "producer_authentication",
                "source_preservation",
            }
        ),
    )
    _literal(
        source["schema"], "goppi.p7_offline_verifier_regrade_source.v1", "/regrade_source/schema"
    )
    original = _object(
        source["original"],
        frozenset(
            {
                "run_id",
                "run_alias",
                "evalset_digest",
                "assignment_digest",
                "plan_digest",
                "execution_scope_digest",
                "plan_file_sha256",
                "execution_scope_file_sha256",
                "result_file_sha256",
                "plan_path",
                "execution_scope_path",
                "result_path",
                "baseline_snapshot_digest",
                "candidate_snapshot_digest",
                "project_config_digest",
                "split_policy_digest",
                "original_planned_slots",
                "original_missing_slots",
                "original_outcome_status",
                "original_outcomes_rewritten",
            }
        ),
        "/regrade_source/original",
        allowed=frozenset(
            {
                "run_id",
                "run_alias",
                "draft_digest",
                "evalset_digest",
                "assignment_digest",
                "split_policy_digest",
                "execution_scope_digest",
                "plan_digest",
                "project_config_digest",
                "baseline_snapshot_digest",
                "candidate_snapshot_digest",
                "execution_scope_authentication_verified",
                "execution_scope_file_sha256",
                "execution_scope_path",
                "plan_file_sha256",
                "plan_path",
                "result_authentication_verified",
                "result_file_sha256",
                "result_path",
                "original_planned_slots",
                "original_missing_slots",
                "original_outcome_status",
                "original_outcomes_rewritten",
            }
        ),
    )
    _text(original["run_alias"], 128, "/regrade_source/original/run_alias")
    _digest_fields(
        original,
        (
            "run_id",
            "evalset_digest",
            "assignment_digest",
            "plan_digest",
            "execution_scope_digest",
            "plan_file_sha256",
            "execution_scope_file_sha256",
            "result_file_sha256",
            "baseline_snapshot_digest",
            "candidate_snapshot_digest",
            "project_config_digest",
            "split_policy_digest",
        ),
        "/regrade_source/original",
    )
    for key in ("plan_path", "execution_scope_path", "result_path"):
        _text(original[key], 4096, f"/regrade_source/original/{key}")
    _literal(
        original["original_outcome_status"],
        "infra_missing",
        "/regrade_source/original/original_outcome_status",
    )
    _boolean(
        original["original_outcomes_rewritten"],
        False,
        "/regrade_source/original/original_outcomes_rewritten",
    )
    _integer_fields(
        original,
        ("original_planned_slots", "original_missing_slots"),
        MAX_SLOTS,
        "/regrade_source/original",
    )
    runtime = _object(
        source["runtime"],
        frozenset({"evaluator_identity_digest"}),
        "/regrade_source/runtime",
        allowed=frozenset(
            {
                "answer_handling",
                "architecture",
                "evaluator_identity_digest",
                "evaluator_script_path",
                "evaluator_script_sha256",
                "model_calls",
                "network_calls",
                "node_version",
                "platform",
            }
        ),
    )
    _digest(
        runtime["evaluator_identity_digest"],
        "/regrade_source/runtime/evaluator_identity_digest",
    )
    private_material = _object(
        source["private_material"],
        frozenset({"path"}),
        "/regrade_source/private_material",
        allowed=frozenset(
            {
                "path",
                "case_count",
                "content_digest",
                "evalset_digest",
                "file_sha256",
                "truth_exported",
            }
        ),
    )
    material_path = _text(private_material["path"], 4096, "/regrade_source/private_material/path")
    if not material_path.endswith("/explore.json"):
        raise _fail(
            "HANDOFF_PRIVATE_MATERIAL_SCOPE_MISMATCH",
            "/regrade_source/private_material/path",
        )
    verifier = _object(
        source["verifier"],
        frozenset({"corrected", "original"}),
        "/regrade_source/verifier",
        allowed=frozenset(
            {
                "corrected",
                "original",
                "core_program_file_sha256",
                "core_program_path",
                "verifier_id",
            }
        ),
    )
    corrected = _object(
        verifier["corrected"],
        frozenset({"invocation_digest"}),
        "/regrade_source/verifier/corrected",
        allowed=frozenset(
            {
                "invocation_digest",
                "argv",
                "executable_path",
                "executable_sha256",
                "program_source_sha256",
                "protocol",
            }
        ),
    )
    failed = _object(
        verifier["original"],
        frozenset({"failed_invocation_digest"}),
        "/regrade_source/verifier/original",
        allowed=frozenset(
            {
                "failed_invocation_digest",
                "argv",
                "executable_path",
                "executable_sha256",
                "observed_exit_code_zero",
                "observed_receipts",
                "observed_stderr_bytes_zero",
                "observed_stdout_bytes_zero",
                "outcome_status",
                "profile_identity_digest",
                "protocol",
            }
        ),
    )
    meta = _object(
        input_doc["metadata"],
        frozenset(
            {
                "baseline_context_id",
                "candidate_context_id",
                "evaluator_id",
                "source_completion_digest",
                "regrade_id",
                "corrected_invocation_digest",
                "original_failed_invocation_digest",
            }
        ),
        "/paired_input/metadata",
    )
    for key, value in meta.items():
        _digest(value, f"/paired_input/metadata/{key}")
    source_claim = _object(
        input_doc["source"], frozenset({"producer", "version", "run_id"}), "/paired_input/source"
    )
    result_cycle = _object(
        documents.original_result.get("cycle"),
        frozenset({"run_id"}),
        "/original_result/cycle",
        allowed=frozenset(
            {
                "run_id",
                "schema",
                "classification",
                "content_digest",
                "human_review",
                "lifecycle_state",
                "max_iterations",
                "merge_authorized",
                "revert_authorized",
                "stage_artifacts",
                "status",
            }
        ),
    )
    joins = (
        (
            source_claim["run_id"],
            original["run_id"],
            original_run["run_id"],
            result_cycle["run_id"],
        ),
        (
            original_run["original_planned_slots"],
            original["original_planned_slots"],
        ),
        (
            original_run["original_missing_slots"],
            original["original_missing_slots"],
        ),
        (
            meta["evaluator_id"],
            runtime["evaluator_identity_digest"],
            offline["evaluator_identity_digest"],
        ),
        (meta["source_completion_digest"], documents.completion_manifest.get("content_digest")),
        (
            meta["regrade_id"],
            documents.completion_manifest.get("regrade_id"),
            source["regrade_id"],
            offline["regrade_id"],
        ),
        (
            meta["corrected_invocation_digest"],
            offline["corrected_invocation_digest"],
            corrected["invocation_digest"],
        ),
        (
            meta["original_failed_invocation_digest"],
            offline["original_failed_invocation_digest"],
            failed["failed_invocation_digest"],
        ),
    )
    if any(len(set(values)) != 1 for values in joins):
        raise _fail("HANDOFF_IDENTITY_JOIN_MISMATCH", "/paired_input/metadata")
    return original, meta, mapping


def _explore_cases(public: dict[str, object]) -> set[str]:
    cases: set[str] = set()
    for index, item in enumerate(
        _list(public["cases"], MAX_CASES, "/original_public_summary/cases")
    ):
        case = _object(
            item,
            frozenset({"case_id", "split"}),
            f"/original_public_summary/cases/{index}",
            allowed=frozenset({"case_id", "family", "source_group_id", "split", "verifier_digest"}),
        )
        if case["split"] == "explore":
            identifier = _text(
                case["case_id"], 128, f"/original_public_summary/cases/{index}/case_id"
            )
            if identifier in cases:
                raise _fail("HANDOFF_DUPLICATE_CASE", "/original_public_summary/cases")
            cases.add(identifier)
    if not cases:
        raise _fail("HANDOFF_EMPTY_PRIMARY_POPULATION", "/original_public_summary/cases")
    return cases


def _scope_and_cases(
    documents: GoppiP7Documents,
    original: dict[str, object],
    input_doc: dict[str, object],
) -> tuple[dict[str, object], set[str], set[tuple[str, str, int]]]:
    scope = _object(
        documents.original_scope,
        frozenset(
            {
                "alias",
                "evalset_digest",
                "assignment_digest",
                "plan_digest",
                "content_digest",
                "pause_after_stage",
            }
        ),
        "/original_scope",
        allowed=frozenset(
            {
                "schema",
                "alias",
                "approved_draft_digest",
                "assignment_digest",
                "authentication_tag",
                "backend",
                "base_execution_identity_digest",
                "content_digest",
                "evalset_digest",
                "max_fresh_runner_attempts",
                "pause_after_stage",
                "plan_digest",
                "scoped_execution_identity_digest",
            }
        ),
    )
    plan = _object(
        documents.original_plan,
        frozenset({"content_digest", "repeats"}),
        "/original_plan",
        allowed=frozenset(
            {
                "schema",
                "approved_draft",
                "backend",
                "content_digest",
                "gate_policy",
                "gate_policy_digest",
                "measurement_policy",
                "measurement_policy_digest",
                "plan_id",
                "proposal",
                "repeats",
                "resources",
                "runner",
                "verifier",
            }
        ),
    )
    public = _object(
        documents.original_public_summary,
        frozenset({"evalset_digest", "cases"}),
        "/original_public_summary",
        allowed=frozenset(
            {
                "schema",
                "assignment_digest",
                "cases",
                "counts",
                "draft_digest",
                "evalset_digest",
                "evalset_id",
                "paths",
                "private_confirm_digest",
                "private_explore_digest",
                "project_root",
                "split_policy_digest",
                "verifier",
            }
        ),
    )
    if any(
        scope[key] != original[key]
        for key in ("evalset_digest", "assignment_digest", "plan_digest")
    ):
        raise _fail("HANDOFF_SCOPE_JOIN_MISMATCH", "/original_scope")
    if scope["content_digest"] != original["execution_scope_digest"]:
        raise _fail("HANDOFF_SCOPE_JOIN_MISMATCH", "/original_scope/content_digest")
    if plan["content_digest"] != original["plan_digest"]:
        raise _fail("HANDOFF_PLAN_JOIN_MISMATCH", "/original_plan/content_digest")
    if public["evalset_digest"] != original["evalset_digest"]:
        raise _fail("HANDOFF_EVALSET_JOIN_MISMATCH", "/original_public_summary/evalset_digest")
    if scope["alias"] != original["run_alias"]:
        raise _fail("HANDOFF_SCOPE_JOIN_MISMATCH", "/original_scope/alias")
    _literal(scope["pause_after_stage"], "same_batch", "/original_scope/pause_after_stage")
    _literal(plan["repeats"], 1, "/original_plan/repeats")
    cases = _explore_cases(public)
    primary = _list(input_doc["primary_case_ids"], MAX_CASES, "/paired_input/primary_case_ids")
    primary_ids = {_text(item, 128, "/paired_input/primary_case_ids") for item in primary}
    if len(primary_ids) != len(primary) or primary_ids != cases:
        raise _fail("HANDOFF_PRIMARY_MEMBERSHIP_MISMATCH", "/paired_input/primary_case_ids")
    expected = {(case_id, arm, 0) for case_id in cases for arm in ("baseline", "candidate")}
    return (
        {
            "stage": "same_batch",
            "split": "explore",
            "repeat": 0,
            "cases": len(cases),
            "planned_slots": len(expected),
        },
        cases,
        expected,
    )


def _slot_key(value: object, location: str, *, record: bool = False) -> tuple[str, str, int]:
    slot = _object(
        value,
        frozenset({"case_id", "arm", "repeat"}),
        location,
        allowed=(
            frozenset({"case_id", "arm", "repeat", "outcome", "missing_reason", "source_reason"})
            if record
            else None
        ),
    )
    case_id = _text(slot["case_id"], 128, f"{location}/case_id")
    arm = slot["arm"]
    if type(arm) is not str or arm not in ("baseline", "candidate"):
        raise _fail("HANDOFF_ARM_INVALID", f"{location}/arm")
    return case_id, arm, _integer(slot["repeat"], 1024, f"{location}/repeat")


def _input_slots(
    input_doc: dict[str, object], expected: set[tuple[str, str, int]]
) -> dict[tuple[str, str, int], dict[str, object]]:
    planned: set[tuple[str, str, int]] = set()
    for index, item in enumerate(
        _list(input_doc["planned_slots"], MAX_SLOTS, "/paired_input/planned_slots")
    ):
        key = _slot_key(item, f"/paired_input/planned_slots/{index}")
        if key in planned:
            raise _fail("HANDOFF_DUPLICATE_SLOT", "/paired_input/planned_slots")
        planned.add(key)
    actual: dict[tuple[str, str, int], dict[str, object]] = {}
    for index, item in enumerate(
        _list(input_doc["slot_records"], MAX_SLOTS, "/paired_input/slot_records")
    ):
        key = _slot_key(item, f"/paired_input/slot_records/{index}", record=True)
        if key in actual:
            raise _fail("HANDOFF_DUPLICATE_SLOT", "/paired_input/slot_records")
        record = cast(dict[str, object], item)
        outcome = record.get("outcome")
        if type(outcome) is not int or outcome not in (0, 1) or "missing_reason" in record:
            raise _fail("HANDOFF_SLOT_OUTCOME_INVALID", f"/paired_input/slot_records/{index}")
        _text(record.get("source_reason"), 256, f"/paired_input/slot_records/{index}/source_reason")
        actual[key] = record
    if planned != expected or set(actual) != expected:
        raise _fail("HANDOFF_SLOT_MEMBERSHIP_MISMATCH", "/paired_input")
    return actual


def _observations(
    documents: GoppiP7Documents,
    expected: set[tuple[str, str, int]],
) -> dict[tuple[str, str, str], dict[str, object]]:
    result = _object(
        documents.original_result,
        frozenset({"cycle", "observations"}),
        "/original_result",
        allowed=frozenset(
            {
                "schema",
                "authentication_tag",
                "backend",
                "cycle",
                "observations",
                "profile",
                "reservation",
            }
        ),
    )
    observed: dict[tuple[str, str, str], dict[str, object]] = {}
    for index, item in enumerate(
        _list(result["observations"], MAX_OBSERVATIONS, "/original_result/observations")
    ):
        location = f"/original_result/observations/{index}"
        observation = _object(
            item,
            frozenset({"case_id", "variant", "operation", "stage", "receipt"}),
            location,
            allowed=frozenset({"case_id", "operation", "receipt", "stage", "usage", "variant"}),
        )
        case_id = _text(observation["case_id"], 128, f"{location}/case_id")
        arm = observation["variant"]
        operation = observation["operation"]
        if type(arm) is not str or arm not in ("baseline", "candidate"):
            raise _fail("HANDOFF_ARM_INVALID", f"{location}/variant")
        if type(operation) is not str or operation not in ("runner", "verifier"):
            raise _fail("HANDOFF_OPERATION_INVALID", f"{location}/operation")
        _literal(observation["stage"], "same_batch", f"{location}/stage")
        if (case_id, arm, 0) not in expected:
            raise _fail("HANDOFF_OBSERVATION_OUT_OF_SCOPE", location)
        key = (case_id, arm, operation)
        if key in observed:
            raise _fail("HANDOFF_DUPLICATE_OBSERVATION", location)
        receipt = _object(
            observation["receipt"],
            frozenset({"content_digest", "request_digest"}),
            f"{location}/receipt",
            allowed=frozenset(
                {
                    "schema",
                    "architecture",
                    "attempt_id",
                    "backend",
                    "content_digest",
                    "duration_ms",
                    "evidence_class",
                    "executable_digest",
                    "executable_source",
                    "exit_code",
                    "exit_signal",
                    "observed_finished_at",
                    "observed_pgid",
                    "observed_pid",
                    "observed_start_token",
                    "observed_started_at",
                    "output_available",
                    "platform",
                    "process_instance_id",
                    "profile_identity_digest",
                    "request_digest",
                    "snapshot_digest",
                    "stderr",
                    "stdout",
                    "termination_reason",
                }
            ),
        )
        _digest(receipt["content_digest"], f"{location}/receipt/content_digest")
        _digest(receipt["request_digest"], f"{location}/receipt/request_digest")
        observed[key] = receipt
    expected_observations = {
        (case_id, arm, operation)
        for case_id, arm, _ in expected
        for operation in ("runner", "verifier")
    }
    if set(observed) != expected_observations:
        raise _fail("HANDOFF_OBSERVATION_MEMBERSHIP_MISMATCH", "/original_result/observations")
    return observed


def _strict_json(data: bytes, location: str) -> object:
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise _fail("HANDOFF_PROTOCOL_JSON_INVALID", location)
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        del value
        raise _fail("HANDOFF_PROTOCOL_JSON_INVALID", location)

    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, HandoffMappingError):
            raise
        raise _fail("HANDOFF_PROTOCOL_JSON_INVALID", location) from exc


def _decode_stream(
    value: object, expected_bytes: object, expected_sha: object, location: str
) -> bytes:
    if type(value) is not str or len(value) > 4 * 1024 * 1024:
        raise _fail("HANDOFF_PROTOCOL_BASE64_INVALID", location)
    text = value
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise _fail("HANDOFF_PROTOCOL_BASE64_INVALID", location) from exc
    if len(raw) != _integer(expected_bytes, 4 * 1024 * 1024, location):
        raise _fail("HANDOFF_PROTOCOL_BYTES_MISMATCH", location)
    if digest_bytes(raw) != _digest(expected_sha, location):
        raise _fail("HANDOFF_PROTOCOL_DIGEST_MISMATCH", location)
    return raw


def _row_parts(value: object, location: str) -> _ParsedRow:
    row_keys = frozenset(
        {
            "schema",
            "regrade_id",
            "slot_id",
            "original",
            "regrade",
            "provenance",
            "content_digest",
            "authentication_tag",
        }
    )
    row = _object(value, row_keys, location)
    _literal(row["schema"], "goppi.p7_offline_verifier_regrade_row.v1", f"{location}/schema")
    old = _object(
        row["original"],
        frozenset(
            {
                "run_id",
                "run_alias",
                "stage",
                "repeat_index",
                "repeat_run_id",
                "arm",
                "case_id",
                "runner_attempt_id",
                "config_digest",
                "arm_context_digest",
                "answer_manifest_path",
                "answer_manifest_file_sha256",
                "answer_manifest_content_digest",
                "answer_object_path",
                "answer_object_file_sha256",
                "answer_digest",
                "runner_receipt_digest",
                "runner_request_digest",
                "verifier_receipt_digest",
                "verifier_request_digest",
                "verifier_digest",
                "original_verifier_runtime_digest",
                "original_verifier_infra_status",
            }
        ),
        f"{location}/original",
    )
    regrade = _object(
        row["regrade"],
        frozenset(
            {
                "classification",
                "verifier_id",
                "verifier_digest",
                "verifier_runtime_digest",
                "request_sha256",
                "response_path",
                "response_file_sha256",
                "protocol_exit_code",
                "protocol_stdout_sha256",
                "protocol_stdout_bytes",
                "protocol_stderr_sha256",
                "protocol_stderr_bytes",
                "outcome",
            }
        ),
        f"{location}/regrade",
    )
    provenance = _object(
        row["provenance"],
        frozenset(
            {
                "draft_digest",
                "evalset_digest",
                "split_policy_digest",
                "assignment_digest",
                "execution_scope_digest",
                "private_material_artifact_digest",
                "private_material_case_digest",
                "corrected_invocation_digest",
                "evaluator_identity_digest",
            }
        ),
        f"{location}/provenance",
    )
    for key in ("regrade_id", "slot_id", "content_digest", "authentication_tag"):
        _digest(row[key], f"{location}/{key}")
    for key in (
        "run_id",
        "repeat_run_id",
        "config_digest",
        "arm_context_digest",
        "answer_manifest_file_sha256",
        "answer_manifest_content_digest",
        "answer_object_file_sha256",
        "answer_digest",
        "runner_receipt_digest",
        "runner_request_digest",
        "verifier_receipt_digest",
        "verifier_request_digest",
        "verifier_digest",
        "original_verifier_runtime_digest",
    ):
        _digest(old[key], f"{location}/original/{key}")
    for key in (
        "verifier_digest",
        "verifier_runtime_digest",
        "request_sha256",
        "response_file_sha256",
        "protocol_stdout_sha256",
        "protocol_stderr_sha256",
    ):
        _digest(regrade[key], f"{location}/regrade/{key}")
    for key in (
        "draft_digest",
        "evalset_digest",
        "split_policy_digest",
        "assignment_digest",
        "execution_scope_digest",
        "private_material_artifact_digest",
        "private_material_case_digest",
        "corrected_invocation_digest",
        "evaluator_identity_digest",
    ):
        _digest(provenance[key], f"{location}/provenance/{key}")
    _text(regrade["classification"], 64, f"{location}/regrade/classification")
    _text(regrade["verifier_id"], 128, f"{location}/regrade/verifier_id")
    for key in ("protocol_exit_code", "protocol_stdout_bytes", "protocol_stderr_bytes"):
        _integer(regrade[key], 4 * 1024 * 1024, f"{location}/regrade/{key}")
    _literal(old["stage"], "same_batch", f"{location}/original/stage")
    _text(old["run_alias"], 128, f"{location}/original/run_alias")
    slot = (
        _text(old["case_id"], 128, f"{location}/original/case_id"),
        _text(old["arm"], 32, f"{location}/original/arm"),
        _integer(old["repeat_index"], 1024, f"{location}/original/repeat_index"),
    )
    return _ParsedRow(row, old, regrade, provenance, slot)


def _verify_row_source_joins(
    context: _RowContext,
    row: dict[str, object],
    old: dict[str, object],
    provenance: dict[str, object],
    slot: tuple[str, str, int],
    location: str,
) -> None:
    if slot not in context.expected or slot[1] not in ("baseline", "candidate"):
        raise _fail("HANDOFF_ROW_SLOT_MISMATCH", location)
    joins = (
        (old["run_id"], old["repeat_run_id"], context.original["run_id"]),
        (old["run_alias"], context.original["run_alias"]),
        (
            old["arm_context_digest"],
            context.metadata[f"{slot[1]}_context_id"],
            context.original[f"{slot[1]}_snapshot_digest"],
        ),
        (row["regrade_id"], context.metadata["regrade_id"]),
        (provenance["evaluator_identity_digest"], context.metadata["evaluator_id"]),
        (
            provenance["corrected_invocation_digest"],
            context.metadata["corrected_invocation_digest"],
        ),
    )
    if any(len(set(values)) != 1 for values in joins):
        raise _fail("HANDOFF_ROW_IDENTITY_MISMATCH", location)
    for key in (
        "evalset_digest",
        "assignment_digest",
        "split_policy_digest",
        "execution_scope_digest",
    ):
        if provenance[key] != context.original[key]:
            raise _fail("HANDOFF_ROW_PROVENANCE_MISMATCH", f"{location}/provenance/{key}")
    for operation in ("runner", "verifier"):
        source_receipt = context.observed[(slot[0], slot[1], operation)]
        if source_receipt["content_digest"] != old[f"{operation}_receipt_digest"]:
            raise _fail("HANDOFF_ORIGINAL_RECEIPT_MISMATCH", location)
        if source_receipt["request_digest"] != old[f"{operation}_request_digest"]:
            raise _fail("HANDOFF_ORIGINAL_REQUEST_MISMATCH", location)
    _literal(
        old["original_verifier_infra_status"],
        "missing",
        f"{location}/original/original_verifier_infra_status",
    )


def _receipt_parts(
    value: object, location: str
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    receipt = _object(
        value,
        frozenset(
            {
                "schema",
                "regrade_id",
                "slot_id",
                "request_sha256",
                "response",
                "process",
                "content_digest",
                "authentication_tag",
            }
        ),
        f"{location}/receipt",
    )
    response = _object(
        receipt["response"],
        frozenset(
            {
                "schema",
                "case_id",
                "run_id",
                "variant_id",
                "config_hash",
                "trace_digest",
                "verifier",
                "verifier_runtime_digest",
                "outcome",
            }
        ),
        f"{location}/receipt/response",
    )
    process = _object(
        receipt["process"],
        frozenset(
            {
                "exit_code",
                "stdout_base64",
                "stdout_bytes",
                "stdout_sha256",
                "stderr_base64",
                "stderr_bytes",
                "stderr_sha256",
            }
        ),
        f"{location}/receipt/process",
    )
    return receipt, response, process


def _verify_protocol_receipt(
    context: _RowContext,
    parsed: _ParsedRow,
    used_receipts: set[str],
    location: str,
) -> bool:
    row, old, regrade, slot = (
        parsed.row,
        parsed.original,
        parsed.regrade,
        parsed.slot,
    )
    relative = _text(regrade["response_path"], 512, f"{location}/regrade/response_path")
    logical = f"regrade/{relative}"
    if logical not in context.logical_receipts or logical in used_receipts:
        raise _fail("HANDOFF_RECEIPT_SET_MISMATCH", f"{location}/regrade/response_path")
    used_receipts.add(logical)
    _verify_identity_claim(
        context.documents,
        logical,
        regrade["response_file_sha256"],
        f"{location}/regrade/response_file_sha256",
    )
    receipt, response, process = _receipt_parts(
        context.documents.protocol_receipts[logical], location
    )
    _literal(
        receipt["schema"],
        "goppi.p7_offline_regrade_protocol_receipt.v1",
        f"{location}/receipt/schema",
    )
    _literal(
        response["schema"],
        "goppi.verifier_response.v2",
        f"{location}/receipt/response/schema",
    )
    for key in (
        "regrade_id",
        "slot_id",
        "request_sha256",
        "content_digest",
        "authentication_tag",
    ):
        _digest(receipt[key], f"{location}/receipt/{key}")
    for key in (
        "run_id",
        "config_hash",
        "trace_digest",
        "verifier_runtime_digest",
    ):
        _digest(response[key], f"{location}/receipt/response/{key}")
    if receipt["slot_id"] != row["slot_id"] or receipt["regrade_id"] != row["regrade_id"]:
        raise _fail("HANDOFF_RECEIPT_IDENTITY_MISMATCH", location)
    if receipt["request_sha256"] != regrade["request_sha256"]:
        raise _fail("HANDOFF_RECEIPT_REQUEST_MISMATCH", location)
    stdout = _decode_stream(
        process["stdout_base64"],
        process["stdout_bytes"],
        process["stdout_sha256"],
        f"{location}/receipt/process/stdout",
    )
    stderr = _decode_stream(
        process["stderr_base64"],
        process["stderr_bytes"],
        process["stderr_sha256"],
        f"{location}/receipt/process/stderr",
    )
    if _strict_json(stdout, f"{location}/receipt/process/stdout") != response or stderr != b"":
        raise _fail("HANDOFF_PROTOCOL_STREAM_MISMATCH", location)
    process_exit = _integer(
        process["exit_code"], SAFE_INTEGER, f"{location}/receipt/process/exit_code"
    )
    declared_exit = _integer(
        regrade["protocol_exit_code"],
        SAFE_INTEGER,
        f"{location}/regrade/protocol_exit_code",
    )
    if process_exit != declared_exit or process_exit != 0:
        raise _fail("HANDOFF_PROTOCOL_EXIT_MISMATCH", location)
    stream_joins = (
        (process["stdout_bytes"], regrade["protocol_stdout_bytes"]),
        (process["stdout_sha256"], regrade["protocol_stdout_sha256"]),
        (process["stderr_bytes"], regrade["protocol_stderr_bytes"]),
        (process["stderr_sha256"], regrade["protocol_stderr_sha256"]),
    )
    if any(len(set(values)) != 1 for values in stream_joins):
        raise _fail("HANDOFF_PROTOCOL_STREAM_MISMATCH", location)
    return _verify_response(context, old, regrade, response, slot, location)


def _verify_response(
    context: _RowContext,
    old: dict[str, object],
    regrade: dict[str, object],
    response: dict[str, object],
    slot: tuple[str, str, int],
    location: str,
) -> bool:
    outcome = _object(
        response["outcome"], frozenset({"kind", "value"}), f"{location}/receipt/response/outcome"
    )
    verifier = _object(
        response["verifier"],
        frozenset({"verifier_id", "verifier_digest"}),
        f"{location}/receipt/response/verifier",
    )
    _text(verifier["verifier_id"], 128, f"{location}/receipt/response/verifier/verifier_id")
    _digest(verifier["verifier_digest"], f"{location}/receipt/response/verifier/verifier_digest")
    if outcome["kind"] != "binary" or type(outcome["value"]) is not bool:
        raise _fail("HANDOFF_RESPONSE_OUTCOME_INVALID", location)
    if response["case_id"] != slot[0] or response["variant_id"] != slot[1]:
        raise _fail("HANDOFF_RESPONSE_SLOT_MISMATCH", location)
    trace_parts = (
        "goppi:p7-offline-regrade-trace:v1",
        cast(str, old["runner_receipt_digest"]),
        cast(str, old["answer_manifest_content_digest"]),
    )
    framed = b"".join(
        str(len(encoded)).encode("ascii") + b":" + encoded
        for encoded in (part.encode("utf-8") for part in trace_parts)
    )
    if response["trace_digest"] != digest_bytes(framed):
        raise _fail("HANDOFF_RESPONSE_TRACE_MISMATCH", location)
    joins = (
        (response["run_id"], old["run_id"]),
        (response["config_hash"], old["config_digest"], context.original["project_config_digest"]),
        (verifier["verifier_digest"], old["verifier_digest"], regrade["verifier_digest"]),
        (verifier["verifier_id"], regrade["verifier_id"]),
        (response["verifier_runtime_digest"], regrade["verifier_runtime_digest"]),
    )
    if any(len(set(values)) != 1 for values in joins) or regrade["outcome"] != outcome:
        raise _fail("HANDOFF_RESPONSE_IDENTITY_MISMATCH", location)
    result = outcome["value"]
    classification = "observed_true" if result else "observed_false"
    if regrade["classification"] != classification:
        raise _fail("HANDOFF_RESPONSE_OUTCOME_MISMATCH", location)
    input_record = context.actual[slot]
    if input_record["outcome"] != int(result):
        raise _fail("HANDOFF_INPUT_OUTCOME_MISMATCH", location)
    if input_record["source_reason"] != f"offline_regrade_{classification}":
        raise _fail("HANDOFF_SOURCE_REASON_MISMATCH", location)
    return result


def _transition_counts(
    actual: dict[tuple[str, str, int], dict[str, object]],
    expected: set[tuple[str, str, int]],
) -> dict[str, int]:
    counts = {
        "zero_to_zero": 0,
        "zero_to_one": 0,
        "one_to_zero": 0,
        "one_to_one": 0,
        "incomplete_pair_count": 0,
    }
    for case_id in {case_id for case_id, _, _ in expected}:
        left = cast(int, actual[(case_id, "baseline", 0)]["outcome"])
        right = cast(int, actual[(case_id, "candidate", 0)]["outcome"])
        counts[f"{'one' if left else 'zero'}_to_{'one' if right else 'zero'}"] += 1
    return counts


def _verify_rows(context: _RowContext) -> dict[str, int]:
    rows = _tuple(context.documents.regrade_rows, MAX_ROWS, "/regrade_rows")
    if len(rows) != len(context.expected):
        raise _fail("HANDOFF_ROW_COUNT_MISMATCH", "/regrade_rows")
    seen: set[tuple[str, str, int]] = set()
    used_receipts: set[str] = set()
    for index, item in enumerate(rows):
        location = f"/regrade_rows/{index}"
        parsed = _row_parts(item, location)
        if parsed.slot in seen:
            raise _fail("HANDOFF_DUPLICATE_ROW", location)
        seen.add(parsed.slot)
        _verify_row_source_joins(
            context,
            parsed.row,
            parsed.original,
            parsed.provenance,
            parsed.slot,
            location,
        )
        _verify_protocol_receipt(context, parsed, used_receipts, location)
    if seen != context.expected or used_receipts != context.logical_receipts:
        raise _fail("HANDOFF_ROW_MEMBERSHIP_MISMATCH", "/regrade_rows")
    return _transition_counts(context.actual, context.expected)


def _verify_declared_limits(documents: GoppiP7Documents, scope: dict[str, object]) -> None:
    limit_keys = frozenset(
        {
            "stage",
            "split",
            "repeat",
            "cases",
            "planned_slots",
            "descriptive_policy_only",
            "expected_analysis_status",
            "expected_analysis_reason",
            "confirm_or_later_stage_claim",
            "representative_population_claim",
        }
    )
    limits = _object(documents.handoff["limits"], limit_keys, "/handoff/limits")
    for key, expected in (
        ("stage", scope["stage"]),
        ("split", scope["split"]),
        ("expected_analysis_status", "not_measured"),
        ("expected_analysis_reason", "ASSUMPTIONS_NOT_DECLARED"),
    ):
        _literal(limits[key], expected, f"/handoff/limits/{key}")
    for key, expected in (
        ("repeat", scope["repeat"]),
        ("cases", scope["cases"]),
        ("planned_slots", scope["planned_slots"]),
    ):
        if _integer(limits[key], MAX_SLOTS, f"/handoff/limits/{key}") != expected:
            raise _fail("HANDOFF_LIMITS_MISMATCH", f"/handoff/limits/{key}")
    for key, expected in (
        ("descriptive_policy_only", True),
        ("confirm_or_later_stage_claim", False),
        ("representative_population_claim", False),
    ):
        _boolean(limits[key], expected, f"/handoff/limits/{key}")


def _verify_count_and_transitions(
    scope: dict[str, object],
    mapping: dict[str, object],
    transitions: dict[str, int],
    original: dict[str, object],
) -> None:
    for key, expected in (
        ("case_count", scope["cases"]),
        ("planned_slot_count", scope["planned_slots"]),
        ("observed_slot_count", scope["planned_slots"]),
        ("missing_slot_count", 0),
    ):
        if _integer(mapping[key], MAX_SLOTS, f"/provenance/mapping/{key}") != expected:
            raise _fail("HANDOFF_MAPPING_COUNT_MISMATCH", f"/provenance/mapping/{key}")
    for key in ("original_planned_slots", "original_missing_slots"):
        if (
            _integer(original[key], MAX_SLOTS, f"/regrade_source/original/{key}")
            != scope["planned_slots"]
        ):
            raise _fail("HANDOFF_ORIGINAL_COUNT_MISMATCH", f"/regrade_source/original/{key}")
    declared = _object(
        mapping["transitions"], frozenset(transitions), "/provenance/mapping/transitions"
    )
    for key, expected in transitions.items():
        if _integer(declared[key], MAX_CASES, f"/provenance/mapping/transitions/{key}") != expected:
            raise _fail("HANDOFF_TRANSITION_MISMATCH", f"/provenance/mapping/transitions/{key}")


def _verify_limits_and_transitions(
    documents: GoppiP7Documents,
    scope: dict[str, object],
    mapping: dict[str, object],
    transitions: dict[str, int],
    original: dict[str, object],
) -> None:
    _verify_declared_limits(documents, scope)
    _verify_count_and_transitions(scope, mapping, transitions, original)


def verify_goppi_p7_mapping(documents: GoppiP7Documents) -> VerifiedHandoff:
    """Verify exact P7 joins and return bounded paired inputs without claiming provenance."""
    if type(documents) is not GoppiP7Documents:
        raise _fail("HANDOFF_DOCUMENTS_REQUIRED", "/")
    if type(documents.byte_identities) is not dict or len(documents.byte_identities) > MAX_FILES:
        raise _fail("HANDOFF_LIMIT_EXCEEDED", "/byte_identities", "INPUT_LIMIT_EXCEEDED")
    findings, used = _verify_mapping_bundle(documents)
    logical_receipts, _ = _verify_completion(documents, used)
    input_doc, policy_doc = _input_parts(documents)
    original, metadata, mapping = _verify_source_and_provenance(documents, input_doc)
    for name, claim in (
        (ORIGINAL_RESULT, original["result_file_sha256"]),
        (ORIGINAL_SCOPE, original["execution_scope_file_sha256"]),
        (ORIGINAL_PLAN, original["plan_file_sha256"]),
    ):
        _verify_identity_claim(documents, name, claim, f"/regrade_source/original/{name}")
        used.add(name)
    _identity(documents, ORIGINAL_PUBLIC, "/original_public_summary")
    used.add(ORIGINAL_PUBLIC)
    scope, _, expected = _scope_and_cases(documents, original, input_doc)
    actual = _input_slots(input_doc, expected)
    if mapping["paired_input_digest"] != _canonical_digest(input_doc, "/paired_input"):
        raise _fail("HANDOFF_INPUT_DIGEST_MISMATCH", "/provenance/mapping/paired_input_digest")
    observed = _observations(documents, expected)
    transitions = _verify_rows(
        _RowContext(
            documents,
            expected,
            actual,
            observed,
            original,
            metadata,
            logical_receipts,
        )
    )
    _verify_limits_and_transitions(documents, scope, mapping, transitions, original)
    if set(documents.byte_identities) != used:
        raise _fail("HANDOFF_ARTIFACT_SET_MISMATCH", "/byte_identities")
    try:
        context: dict[str, object] = {
            "schema": "paired_review_context.v1",
            "binding": {
                "input_digest": digest_bytes(canonical_bytes(input_doc)),
                "policy_digest": digest_bytes(canonical_bytes(policy_doc)),
            },
            "conditions": cast(list[object], []),
        }
        canonical_bytes(context)
    except (CanonicalizationError, RecursionError) as exc:
        raise _fail("HANDOFF_CANONICALIZATION_FAILED", "/context") from exc
    findings.extend(
        (
            HandoffFinding("CONDITIONS_NOT_SUPPLIED", MAPPING_HANDOFF),
            HandoffFinding(
                "SOURCE_AUTHENTICATION_NOT_INDEPENDENTLY_VERIFIED",
                REGRADE_SOURCE,
            ),
        )
    )
    return VerifiedHandoff(
        input_doc=deepcopy(input_doc),
        policy_doc=deepcopy(policy_doc),
        context_doc=context,
        scope=scope,
        findings=tuple(findings),
        context_provenance={
            "source": "consumer_derived_empty",
            "conditions_supplied_by_producer": False,
            "semantic_completeness_verified": False,
            "source_authentication_independently_verified": False,
        },
        used_artifact_names=tuple(sorted(used)),
    )

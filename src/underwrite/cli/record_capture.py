"""Card: record-reuse | Paper: langfuse_record.v1 | Stage: storage-capture

Shared response admission, rendering, and storage of captured producer evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from underwrite_core.canonical import canonical_bytes

from underwrite.cli.evidence import EvidenceError, output_line_size
from underwrite.cli.local_json import Validator, validate
from underwrite.cli.records import record_error_candidates, record_error_payload
from underwrite.service.records import (
    CapturedRecordSpec,
    RecordError,
    save_captured_record,
    verify_record,
)

STORAGE_FAILURE_EXIT = 3


def saved_record(identifier: str, digest: str, count: int) -> dict[str, object]:
    return {
        "state": "saved",
        "record_id": identifier,
        "manifest_digest": digest,
        "verification": {
            "schema": "record_verification.v1",
            "record_id": identifier,
            "manifest_digest": digest,
            "valid": True,
            "files_checked": count,
            "links_checked": 0,
            "findings": [],
            "verification_scope": "stored_bytes_and_direct_links",
        },
    }


def store_captured_record(
    root: Path,
    spec: CapturedRecordSpec,
    verification_validator: Validator,
    record_error_validator: Validator,
) -> dict[str, Any]:
    known: dict[str, object] | None = None
    try:
        snapshot = save_captured_record(root, spec)
        known = {"record_id": spec.record_id, "manifest_digest": snapshot.digest}
        verification = verify_record(root, spec.record_id)
        validate(verification_validator, verification, EvidenceError, "INTERNAL_ERROR")
        expected = saved_record(spec.record_id, snapshot.digest, len(spec.files))
        if verification != expected["verification"]:
            raise RecordError("STORE_IO_ERROR")
        return expected
    except RecordError as exc:
        error = record_error_payload(exc)
    except Exception:
        error = record_error_payload(RecordError("INTERNAL_ERROR"))
    validate(record_error_validator, error, EvidenceError, "INTERNAL_ERROR")
    return {"state": "error", "error": error, "published_record": known}


def _storage_text(storage: dict[str, Any]) -> str:
    if storage["state"] == "saved":
        return f"Saved and verified record: {storage['record_id']} ({storage['manifest_digest']})."
    error = storage["error"]
    text = f"Storage failed: {error['code']} ({error['reason']}); next: {error['next_action']}."
    known = storage["published_record"]
    if known is not None:
        return (
            text + f" Returned record identity: {known['record_id']} ({known['manifest_digest']})."
        )
    return text + " No record identity returned; a record may still exist."


def render_record_response(report: dict[str, object], *, analysis_text: str) -> str:
    return (
        analysis_text
        + "\n"
        + _storage_text(cast(dict[str, Any], report["storage"]))
        + f"\nExit: {report['exit_code']}."
    )


def admit_record_response(  # noqa: PLR0913 - explicit producer contracts and output bounds
    envelope: dict[str, Any],
    *,
    aggregate_validator: Validator,
    storage_validator: Validator,
    identifier: str,
    count: int,
    analysis_text: str,
    as_json: bool,
    max_bytes: int,
) -> None:
    digest = "sha256:" + "0" * 64
    representative = envelope.get("storage") or saved_record(identifier, digest, count)
    envelope["storage"] = representative
    validate(aggregate_validator, envelope, EvidenceError, "INTERNAL_ERROR")
    base_size = output_line_size(canonical_bytes(envelope))
    storage_size = len(canonical_bytes(representative))
    maximum = base_size
    human_prefix = analysis_text + "\n"
    human_maximum = output_line_size(render_record_response(envelope, analysis_text=analysis_text))
    identity = {"record_id": identifier, "manifest_digest": digest}
    for error in record_error_candidates():
        for published in (None, identity):
            candidate = {"state": "error", "error": error, "published_record": published}
            validate(storage_validator, candidate, EvidenceError, "INTERNAL_ERROR")
            maximum = max(maximum, base_size - storage_size + len(canonical_bytes(candidate)))
            human_maximum = max(
                human_maximum,
                output_line_size(
                    human_prefix + _storage_text(candidate) + f"\nExit: {STORAGE_FAILURE_EXIT}."
                ),
            )
    if (maximum if as_json else human_maximum) > max_bytes:
        raise EvidenceError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")

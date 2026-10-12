"""Card: record-reuse | Paper: langfuse_record.v1 | Stage: storage-capture

Shared storage and verification of already captured producer evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from underwrite.cli.evidence import EvidenceError
from underwrite.cli.local_json import Validator, validate
from underwrite.cli.records import record_error_payload
from underwrite.service.records import (
    CapturedRecordSpec,
    RecordError,
    save_captured_record,
    verify_record,
)


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

"""Composed output contracts stay standalone and generated from their owners."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


def test_promptfoo_source_versions_match_codec_and_composed_contract() -> None:
    from jsonschema.exceptions import ValidationError

    from underwrite.cli.local_json import Validator
    from underwrite.instrument.ingest.accounting import audit_promptfoo_file
    from underwrite.instrument.ingest.codecs.promptfoo import SUPPORTED_VERSIONS

    schema = json.loads((ROOT / "contracts/promptfoo_accounting.v1.schema.json").read_bytes())
    record = json.loads((ROOT / "contracts/promptfoo_record.v1.schema.json").read_bytes())
    selector = schema["properties"]["source"]["properties"]["format_version"]
    assert selector == {"enum": list(SUPPORTED_VERSIONS)}
    embedded = record["$defs"]["promptfoo_accounting"]
    assert embedded["properties"]["source"]["properties"]["format_version"] == selector
    validator = cast(Validator, Draft202012Validator(schema))
    for version, directory in (("0.123.0", "promptfoo"), ("0.124.1", "promptfoo-0.124.1")):
        report = audit_promptfoo_file(
            ROOT / "fixtures/golden/external" / directory / "results.json", version=version
        )
        validator.validate(report)
        with pytest.raises(ValidationError):
            validator.validate(
                {
                    **report,
                    "source": {"format": "promptfoo.eval-output", "format_version": "0.124.2"},
                }
            )


@pytest.mark.parametrize(
    "name",
    [
        "paired_handoff_review",
        "promptfoo_record",
        "langfuse_record",
        "deepeval_record",
        "deepeval_record_v2",
    ],
)
def test_generated_handoff_contract_matches_owners(name: str) -> None:
    result = subprocess.run(
        [sys.executable, f"scripts/build_{name}.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "name,version",
    [
        ("paired_handoff_review", "v1"),
        ("promptfoo_record", "v1"),
        ("langfuse_record", "v1"),
        ("deepeval_record", "v1"),
        ("deepeval_record", "v2"),
    ],
)
def test_composite_contract_has_only_resolvable_local_references(name: str, version: str) -> None:
    document = json.loads((ROOT / f"contracts/{name}.{version}.schema.json").read_bytes())
    Draft202012Validator.check_schema(document)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            mapping = cast(dict[str, Any], value)
            if "$ref" in mapping:
                reference = cast(str, mapping["$ref"])
                assert reference.startswith("#/")
                target = document
                for part in reference[2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            for child in mapping.values():
                walk(child)
        elif isinstance(value, list):
            for child in cast(list[object], value):
                walk(child)

    walk(document)


@pytest.mark.parametrize(
    "producer,version", [("promptfoo", "v1"), ("deepeval", "v1"), ("deepeval", "v2")]
)
@pytest.mark.parametrize("analysis_error", [False, True])
@pytest.mark.parametrize("storage_error", [False, True])
def test_single_source_record_exit_and_capture_states_are_bound(
    producer: str,
    version: str,
    analysis_error: bool,
    storage_error: bool,
) -> None:
    from jsonschema.exceptions import ValidationError

    from underwrite.cli.evidence import EvidenceError
    from underwrite.cli.local_json import Validator
    from underwrite.cli.records import record_error_payload
    from underwrite.service.records import RecordError

    schema = json.loads((ROOT / f"contracts/{producer}_record.{version}.schema.json").read_bytes())
    validator = cast(Validator, Draft202012Validator(schema))
    if producer == "promptfoo":
        report = json.loads(
            (ROOT / "fixtures/examples/evidence/baseline-accounting.json").read_bytes()
        )
    else:
        from underwrite.instrument.ingest.deepeval_accounting import audit_deepeval_file

        report = audit_deepeval_file(
            ROOT
            / "fixtures/golden/external"
            / (
                "deepeval-4.2.8/score_without_verdict.json"
                if version == "v2"
                else "deepeval/test_run_20260914_132634.json"
            ),
            version="4.2.8" if version == "v2" else "4.1.1",
        )
    identity = {"record_id": "capture", "manifest_digest": "sha256:" + "0" * 64}
    storage: dict[str, Any] = (
        {
            "state": "error",
            "error": record_error_payload(RecordError("STORE_IO_ERROR")),
            "published_record": None,
        }
        if storage_error
        else {
            "state": "saved",
            **identity,
            "verification": {
                "schema": "record_verification.v1",
                **identity,
                "valid": True,
                "files_checked": 3,
                "links_checked": 0,
                "findings": [],
                "verification_scope": "stored_bytes_and_direct_links",
            },
        }
    )
    analysis: dict[str, Any] = (
        {
            "state": "error",
            "phase": "analysis",
            "error": EvidenceError("INVALID_INPUT", "INVALID_INPUT").payload,
        }
        if analysis_error
        else {"state": "result", "report": report}
    )
    exit_code = 3 if storage_error else 2 if analysis_error else 0
    envelope: dict[str, Any] = {
        "schema": f"{producer}_record.{version}",
        "command": f"records audit-{producer}",
        "source": {"state": "captured", "raw_hash": report["raw_hash"], "bytes": 1},
        "analysis": analysis,
        "storage": storage,
        "exit_code": exit_code,
    }
    if version == "v2":
        envelope["source_format_version"] = "4.2.8"
    validator.validate(envelope)
    if version == "v2":
        with pytest.raises(ValidationError):
            validator.validate({**envelope, "source_format_version": "4.1.1"})
        without_version = {
            key: value for key, value in envelope.items() if key != "source_format_version"
        }
        with pytest.raises(ValidationError):
            validator.validate(without_version)
    with pytest.raises(ValidationError):
        validator.validate({**envelope, "unexpected": True})
    with pytest.raises(ValidationError):
        validator.validate({**envelope, "analysis": {**analysis, "unexpected": True}})
    if not analysis_error:
        with pytest.raises(ValidationError):
            validator.validate(
                {
                    **envelope,
                    "analysis": {**analysis, "report": {**report, "schema": "other_accounting.v1"}},
                }
            )
    if not storage_error:
        wrong_storage = {**storage, "verification": {**storage["verification"], "files_checked": 2}}
        with pytest.raises(ValidationError):
            validator.validate({**envelope, "storage": wrong_storage})
    for wrong in {0, 2, 3} - {exit_code}:
        with pytest.raises(ValidationError):
            validator.validate({**envelope, "exit_code": wrong})
    with pytest.raises(ValidationError):
        validator.validate({**envelope, "source": {"state": "unavailable"}})
    if analysis_error:
        acquisition = {
            **envelope,
            "source": {"state": "unavailable"},
            "analysis": {**analysis, "phase": "acquisition"},
        }
        if not storage_error:
            with pytest.raises(ValidationError):
                validator.validate(acquisition)
            acquisition["storage"] = {
                **storage,
                "verification": {**storage["verification"], "files_checked": 2},
            }
        validator.validate(acquisition)
        with pytest.raises(ValidationError):
            validator.validate({**acquisition, "source": envelope["source"]})


@pytest.mark.parametrize("observations", [False, True])
@pytest.mark.parametrize("scores", [False, True])
@pytest.mark.parametrize("storage_error", [False, True])
def test_langfuse_record_binds_sources_capture_count_phase_and_exit(
    observations: bool, scores: bool, storage_error: bool
) -> None:
    from jsonschema.exceptions import ValidationError

    from underwrite.cli.evidence import EvidenceError
    from underwrite.cli.local_json import Validator
    from underwrite.cli.record_capture import saved_record
    from underwrite.cli.records import record_error_payload
    from underwrite.service.records import RecordError

    validator = cast(
        Validator,
        Draft202012Validator(
            json.loads((ROOT / "contracts/langfuse_record.v1.schema.json").read_bytes())
        ),
    )
    error = EvidenceError("INVALID_INPUT", "INVALID_INPUT").payload
    captured = {"state": "captured", "raw_hash": "sha256:" + "a" * 64, "bytes": 1}
    sources = {
        role: captured if available else {"state": "unavailable", "error": error}
        for role, available in (("observations", observations), ("scores", scores))
    }
    count = 2 + observations + scores
    storage = (
        {
            "state": "error",
            "error": record_error_payload(RecordError("STORE_IO_ERROR")),
            "published_record": None,
        }
        if storage_error
        else saved_record("capture", "sha256:" + "0" * 64, count)
    )
    phase = "analysis" if observations and scores else "acquisition"
    analysis = {"state": "error", "phase": phase, "error": error}
    envelope: dict[str, Any] = {
        "schema": "langfuse_record.v1",
        "command": "records audit-langfuse",
        "sources": sources,
        "analysis": analysis,
        "storage": storage,
        "exit_code": 3 if storage_error else 2,
    }
    validator.validate(envelope)
    for wrong in {0, 2, 3} - {envelope["exit_code"]}:
        with pytest.raises(ValidationError):
            validator.validate({**envelope, "exit_code": wrong})
    with pytest.raises(ValidationError):
        validator.validate(
            {
                **envelope,
                "analysis": {
                    **analysis,
                    "phase": ("acquisition" if phase == "analysis" else "analysis"),
                },
            }
        )
    if not storage_error:
        for wrong_count in {2, 3, 4} - {count}:
            with pytest.raises(ValidationError):
                validator.validate(
                    {
                        **envelope,
                        "storage": saved_record("capture", "sha256:" + "0" * 64, wrong_count),
                    }
                )
    for role, available in (("observations", observations), ("scores", scores)):
        if not available:
            with pytest.raises(ValidationError):
                validator.validate(
                    {**envelope, "sources": {**sources, role: {"state": "unavailable"}}}
                )


def test_langfuse_record_result_requires_both_complete_sources() -> None:
    from jsonschema.exceptions import ValidationError

    from underwrite.cli.evidence import EvidenceError
    from underwrite.cli.local_json import Validator
    from underwrite.cli.record_capture import saved_record
    from underwrite.instrument.ingest.langfuse_accounting import audit_langfuse_files

    golden = ROOT / "fixtures/golden/external/langfuse"
    report = audit_langfuse_files(
        golden / "observations_v2-2026-09-14T06-40-00.json",
        golden / "scores-2026-09-14T06-40-00.json",
        profile="blob-4.35.0",
    )
    validator = cast(
        Validator,
        Draft202012Validator(
            json.loads((ROOT / "contracts/langfuse_record.v1.schema.json").read_bytes())
        ),
    )
    captured = {"state": "captured", "raw_hash": "sha256:" + "a" * 64, "bytes": 1}
    envelope = {
        "schema": "langfuse_record.v1",
        "command": "records audit-langfuse",
        "sources": {"observations": captured, "scores": captured},
        "analysis": {"state": "result", "report": report},
        "storage": saved_record("capture", "sha256:" + "0" * 64, 4),
        "exit_code": 0,
    }
    validator.validate(envelope)
    for role in ("observations", "scores"):
        with pytest.raises(ValidationError):
            validator.validate(
                {
                    **envelope,
                    "sources": {
                        "observations": captured,
                        "scores": captured,
                        role: {
                            "state": "unavailable",
                            "error": EvidenceError("INVALID_INPUT", "INVALID_INPUT").payload,
                        },
                    },
                }
            )

"""Record the same four buffers used by strict cross-artifact review."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.cli.main import main
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).resolve().parents[3]
C55 = ROOT / "fixtures/conformance/goppi-c55-common"
CANARY = "PRIVATE_CASE_AND_ANSWER_CANARY"
ANALYSIS_EXIT = 2
STORAGE_EXIT = 3
FILE_COUNT = 6


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    assessment = tmp_path / "private-assessment.json"
    result = tmp_path / "private-result.json"
    assessment.write_bytes((C55 / "assessment.json").read_bytes())
    result.write_bytes((C55 / "evidence-result.json").read_bytes())
    cases = json.loads(assessment.read_bytes())["cases"]
    rows = [
        {
            "success": False,
            "failureReason": 1,
            "testCase": {"metadata": {"case_id": cases[0]["case_id"], "slot_kind": "control"}},
            "prompt": {"raw": CANARY},
        }
    ]
    for index, case in enumerate(cases):
        success = index in (0, 1)
        rows.append(
            {
                "success": success,
                "failureReason": 0 if success else 1,
                "testCase": {"metadata": {"case_id": case["case_id"], "slot_kind": "subject"}},
                "response": {"output": CANARY},
            }
        )
    promptfoo = tmp_path / "private-promptfoo.json"
    promptfoo.write_bytes(
        canonical_bytes(
            {
                "results": {
                    "version": 3,
                    "results": rows,
                    "stats": {"successes": 2, "failures": 6, "errors": 0},
                },
                "metadata": {"promptfooVersion": "0.123.0"},
            }
        )
    )
    declaration = tmp_path / "private-correspondence.json"
    declaration.write_bytes(
        canonical_bytes(
            {
                "schema": "cross_artifact_correspondence.v1",
                "scope": "owner_declared_case_mapping",
                "declared_by": CANARY,
                "coverage": "complete",
                "sources": {
                    "goppi_assessment_sha256": digest_bytes(assessment.read_bytes()),
                    "goppi_result_sha256": digest_bytes(result.read_bytes()),
                    "promptfoo_export_sha256": digest_bytes(promptfoo.read_bytes()),
                },
                "pairs": [
                    {"case_index": i, "row_index": i + 1, "case_id": case["case_id"]}
                    for i, case in enumerate(cases)
                ],
            }
        )
    )
    return assessment, result, promptfoo, declaration


def _args(paths: tuple[Path, Path, Path, Path], root: Path, identifier: str) -> list[str]:
    assessment, result, promptfoo, declaration = paths
    return [
        "records",
        "review-artifacts",
        "--goppi-assessment",
        str(assessment),
        "--goppi-result",
        str(result),
        "--promptfoo",
        str(promptfoo),
        "--correspondence",
        str(declaration),
        "--root",
        str(root),
        "--id",
        identifier,
        "--json",
    ]


def _run(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any], str]:
    code = main(args)
    streams = capsys.readouterr()
    return code, json.loads(streams.out or streams.err), streams.out + streams.err


def test_mapped_success_and_verified_readback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _inputs(tmp_path)
    root = tmp_path / "records"
    code, report, output = _run(_args(paths, root, "mapped-1"), capsys)
    assert code == 0
    packaged_validator("cross_artifact_record.v1").validate(report)  # pyright: ignore[reportUnknownMemberType]
    assert report["analysis"]["report"]["relation"] == "declared_case_mapping_descriptive"
    assert report["storage"]["verification"]["files_checked"] == FILE_COUNT
    assert [member["name"] for member in report["source"]["members"]] == [
        "goppi-assessment.json",
        "goppi-result.json",
        "promptfoo.json",
        "correspondence.json",
    ]
    assert CANARY not in output and str(paths[0]) not in output
    code, selected, _ = _run(
        [
            "records",
            "read-json",
            "--root",
            str(root),
            "--id",
            "mapped-1",
            "--name",
            "analysis/review.json",
        ],
        capsys,
    )
    assert code == 0 and selected["content"] == report["analysis"]["report"]
    code, invocation, _ = _run(
        [
            "records",
            "read-json",
            "--root",
            str(root),
            "--id",
            "mapped-1",
            "--name",
            "invocation.json",
        ],
        capsys,
    )
    assert code == 0
    assert invocation["content"]["command"] == "records review-artifacts"
    assert CANARY not in str(invocation["content"])
    assert all(str(path) not in str(invocation["content"]) for path in paths)
    for name, path in zip(
        ("goppi-assessment.json", "goppi-result.json", "promptfoo.json", "correspondence.json"),
        paths,
        strict=True,
    ):
        code, selected, _ = _run(
            [
                "records",
                "read-json",
                "--root",
                str(root),
                "--id",
                "mapped-1",
                "--name",
                "source/" + name,
            ],
            capsys,
        )
        assert code == 0 and selected["sha256"] == digest_bytes(path.read_bytes())


def test_complete_bad_source_saves_diagnostic_but_acquisition_does_not(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _inputs(tmp_path)
    root = tmp_path / "records"
    paths[0].write_bytes(canonical_bytes({"schema": "wrong", "private": CANARY}))
    code, report, output = _run(_args(paths, root, "bad-source"), capsys)
    assert code == ANALYSIS_EXIT and report["analysis"]["state"] == "error"
    assert report["storage"]["state"] == "saved"
    assert CANARY not in output
    paths[0].unlink()
    code, error, output = _run(_args(paths, root, "missing-source"), capsys)
    assert code == ANALYSIS_EXIT and error["schema"] == "evidence_error.v1"
    assert not (root / "records" / "missing-source").exists()
    assert str(paths[0]) not in output


def test_bad_map_is_diagnostic_and_duplicate_never_overwrites(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _inputs(tmp_path)
    root = tmp_path / "records"
    mapping = json.loads(paths[3].read_bytes())
    mapping["sources"]["promptfoo_export_sha256"] = "sha256:" + "0" * 64
    paths[3].write_bytes(canonical_bytes(mapping))
    code, diagnostic, output = _run(_args(paths, root, "same-id"), capsys)
    assert (
        code == ANALYSIS_EXIT
        and diagnostic["analysis"]["error"]["code"] == "INPUT_BINDING_MISMATCH"
    )
    assert diagnostic["storage"]["state"] == "saved" and CANARY not in output
    prior = diagnostic["storage"]["manifest_digest"]
    paths = _inputs(tmp_path)
    code, duplicate, _ = _run(_args(paths, root, "same-id"), capsys)
    assert code == STORAGE_EXIT and duplicate["analysis"]["state"] == "result"
    assert duplicate["storage"]["state"] == "error"
    code, manifest, _ = _run(
        ["records", "show", "--root", str(root), "--id", "same-id", "--json"], capsys
    )
    assert code == 0 and manifest["manifest_digest"] == prior


def test_changed_paths_after_capture_do_not_change_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from underwrite.cli import record_cross_artifact

    paths = _inputs(tmp_path)
    original = record_cross_artifact.capture_mapped_review_inputs

    def changed(*args: Path) -> Any:
        captured = original(*args)
        for path in paths:
            path.write_bytes(b"CHANGED AFTER CAPTURE")
        return captured

    monkeypatch.setattr(record_cross_artifact, "capture_mapped_review_inputs", changed)
    root = tmp_path / "records"
    code, report, _ = _run(_args(paths, root, "captured-once"), capsys)
    assert code == 0
    assert report["source"]["members"][0]["raw_hash"] != digest_bytes(paths[0].read_bytes())
    code, selected, _ = _run(
        [
            "records",
            "read-json",
            "--root",
            str(root),
            "--id",
            "captured-once",
            "--name",
            "source/goppi-assessment.json",
        ],
        capsys,
    )
    assert code == 0 and selected["sha256"] == report["source"]["members"][0]["raw_hash"]


def test_map_required_usage_does_not_create_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _inputs(tmp_path)
    root = tmp_path / "records"
    args = _args(paths, root, "no-map")
    index = args.index("--correspondence")
    del args[index : index + 2]
    code, error, output = _run(args, capsys)
    assert code == ANALYSIS_EXIT and error["code"] == "USAGE_ERROR"
    assert not root.exists() and CANARY not in output


def test_postpublication_verification_failure_keeps_analysis_and_known_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from underwrite.cli import record_capture
    from underwrite.service.records import RecordError

    paths = _inputs(tmp_path)
    root = tmp_path / "records"

    def failed_verification(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        raise RecordError("STORE_IO_ERROR")

    monkeypatch.setattr(record_capture, "verify_record", failed_verification)
    code, report, output = _run(_args(paths, root, "postpublication"), capsys)
    assert code == STORAGE_EXIT
    assert report["analysis"]["state"] == "result"
    assert report["storage"]["state"] == "error"
    assert report["storage"]["published_record"]["record_id"] == "postpublication"
    assert (root / "records" / "postpublication").is_dir()
    assert CANARY not in output


def test_output_admission_failure_does_not_consume_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from underwrite.cli import record_cross_artifact

    paths = _inputs(tmp_path)
    root = tmp_path / "records"
    monkeypatch.setattr(record_cross_artifact, "OUTPUT_MAX_BYTES", 1)
    code, error, output = _run(_args(paths, root, "too-small"), capsys)
    assert code == ANALYSIS_EXIT
    assert error["code"] == "OUTPUT_LIMIT_EXCEEDED"
    assert not root.exists() and CANARY not in output


def test_capture_hash_inconsistency_is_refused_before_storage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from underwrite.cli import record_cross_artifact

    paths = _inputs(tmp_path)
    root = tmp_path / "records"
    original = record_cross_artifact.capture_mapped_review_inputs

    def false_identity(*args: Path) -> Any:
        captured = original(*args)
        hashes = {**captured.sources.hashes, "promptfoo_export_sha256": "sha256:" + "0" * 64}
        return replace(captured, sources=replace(captured.sources, hashes=hashes))

    monkeypatch.setattr(record_cross_artifact, "capture_mapped_review_inputs", false_identity)
    code, error, output = _run(_args(paths, root, "false-hash"), capsys)
    assert code == ANALYSIS_EXIT and error["code"] == "INPUT_BINDING_MISMATCH"
    assert not root.exists() and CANARY not in output

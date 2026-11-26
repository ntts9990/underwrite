"""A frozen pre-change v2 analysis record stays exact and readable without reanalysis."""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, cast

from underwrite.cli.local_json import Validator
from underwrite.cli.record_trajectory import (
    LAYOUT_V2,
    READ_SCHEMA_V3,
    run_read_trajectory,
    run_read_trajectory_summary,
)
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/trajectory-record-v2"
ARCHIVE_SHA256 = "86e130374dec609587617e546d615d17657420b9c90a943bbfe4e724a648863d"


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_prechange_v2_record_is_frozen_and_reopens_with_its_original_analysis(
    tmp_path: Path,
) -> None:
    ledger = json.loads((FIXTURE / "byte-ledger.json").read_text())
    archive_path = FIXTURE / ledger["archive"]["name"]
    assert _sha256(archive_path) == ledger["archive"]["sha256"] == "sha256:" + ARCHIVE_SHA256
    assert archive_path.stat().st_size == ledger["archive"]["bytes"]
    raw = archive_path.read_bytes()
    assert raw[:3] == b"\x1f\x8b\x08" and raw[4:8] == b"\x00" * 4

    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        assert [member.name for member in members] == sorted(ledger["tar_members"])
        for member in members:
            relative = PurePosixPath(member.name)
            assert not relative.is_absolute() and ".." not in relative.parts
            assert ledger["tar_members"][member.name] == (
                "directory" if member.isdir() else "file"
            )
            assert member.mode == (0o700 if member.isdir() else 0o600)
            assert member.uid == member.gid == member.mtime == 0
        archive.extractall(tmp_path, members=members, filter="data")
        for member in members:
            (tmp_path / member.name).chmod(member.mode)

    for name, expected in ledger["contracts"].items():
        assert _sha256(ROOT / "contracts" / f"{name}.schema.json") == expected

    for identifier, record in ledger["records"].items():
        directory = tmp_path / "records" / identifier
        stored = {
            path.relative_to(directory).as_posix(): path
            for path in directory.rglob("*")
            if path.is_file()
        }
        assert set(stored) == set(record["files"])
        for name, expected in record["files"].items():
            assert stored[name].stat().st_size == expected["bytes"]
            assert _sha256(stored[name]) == expected["sha256"]
        analysis = json.loads(stored["files/analysis/trajectory.json"].read_bytes())
        assert analysis["schema"] == record["analysis_schema"] == "goppi_trajectory_review.v2"

        reopened = cast(dict[str, Any], run_read_trajectory(tmp_path, identifier))
        cast(Validator, packaged_validator(READ_SCHEMA_V3)).validate(reopened)
        assert reopened["schema"] == READ_SCHEMA_V3
        assert reopened["record_layout"] == record["record_layout"] == LAYOUT_V2
        # The original v2 analysis is returned as stored, without relabeling or new fields.
        assert reopened["analysis"] == {"state": "result", "report": analysis}
        assert "cycle" not in reopened["analysis"]["report"]
        assert reopened["analysis_limitations"] == [
            "retained_analysis_omits_cycle_status_stage_verdicts_and_runner"
        ]
        assert reopened["verifier_report_history"]["verifier_report_state"] == (
            record["verifier_report_state"]
        )
        assert reopened["verification_interpretation"]["state"] == "current_report_only"
        assert reopened["native_verification_performed_now"] is False

        summary = cast(dict[str, Any], run_read_trajectory_summary(tmp_path, identifier))
        cast(Validator, packaged_validator(summary["schema"])).validate(summary)
        assert summary["schema"] == "trajectory_record_read_summary.v1"
        assert summary["record_layout"] == LAYOUT_V2
        assert "source_format" not in summary
        assert "analysis_limitations" not in summary
        assert summary["populations"] == analysis["populations"]

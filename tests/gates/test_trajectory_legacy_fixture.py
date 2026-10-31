"""Frozen pre-migration v1 records remain exact and readable as historical data."""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, cast

from underwrite.cli.record_trajectory import run_read_trajectory

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/trajectory-record-v1"
ARCHIVE_SHA256 = "2abac1d0eba265f291ae395a8f995751ffb403e8cdbada68f4a452fcf9ddc272"


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_prechange_trajectory_records_and_contracts_are_frozen(tmp_path: Path) -> None:
    ledger = json.loads((FIXTURE / "byte-ledger.json").read_text())
    archive_path = FIXTURE / ledger["archive"]["name"]
    assert _sha256(archive_path) == ledger["archive"]["sha256"]
    assert _sha256(archive_path) == "sha256:" + ARCHIVE_SHA256
    assert archive_path.stat().st_size == ledger["archive"]["bytes"]

    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        actual = {
            member.name: (
                "directory" if member.isdir() else "file" if member.isfile() else "rejected"
            )
            for member in members
        }
        assert actual == ledger["tar_members"]
        for member in members:
            relative = PurePosixPath(member.name)
            assert not relative.is_absolute() and ".." not in relative.parts
            assert member.isdir() or member.isfile()
            assert member.mode == (0o700 if member.isdir() else 0o600)
        archive.extractall(tmp_path, members=members, filter="data")
        for member in members:
            (tmp_path / member.name).chmod(member.mode)

    for identifier, record in ledger["records"].items():
        directory = tmp_path / "records" / identifier
        actual_files = {
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*")
            if path.is_file()
        }
        assert actual_files == set(record["files"])
        for name, expected in record["files"].items():
            path = directory / name
            assert path.stat().st_size == expected["bytes"]
            assert _sha256(path) == expected["sha256"]
        reopened = cast(dict[str, Any], run_read_trajectory(tmp_path, identifier))
        assert reopened["schema"] == "trajectory_record_read.v1"
        assert reopened["analysis"]["state"] == record["analysis_state"]
        assert reopened["native_verification_history"]["state"] == record["history_state"]
        assert reopened["native_verification_performed_now"] is False

    for name, expected in ledger["contracts"].items():
        assert _sha256(ROOT / "contracts" / f"{name}.schema.json") == expected

"""Exact Pi 1.5 producer bytes and descriptive consumer compatibility."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, cast

from drivers.goppi_trajectory_verifier_stub import verifier_response

from underwrite.instrument.ingest.goppi_trajectory import audit_goppi_trajectory

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/goppi-trajectory-pi15"
ARCHIVE_SHA256 = "91a87b103d5de4d974a2646f929ed0f7deda7bc0650a55360e140b8657253a87"
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
PAIRED_ROWS = 144
CASE_STAGE_REPEAT_PAIRS = 72
MEMBERS = (
    "paired/execution.json",
    "paired/manifest.json",
    "paired/rows.jsonl",
    "paired/schema.json",
    "trajectory/manifest.json",
    "trajectory/schema.json",
    "trajectory/trajectory.json",
)


class ResponseStub:
    """Protocol fixture only; does not verify native Goppi content closure."""

    def verify(self, snapshot: Path) -> bytes:
        return json.dumps(verifier_response(snapshot)).encode()


def test_frozen_pi15_exchange_and_consumer_semantics(tmp_path: Path) -> None:
    pins: dict[str, Any] = json.loads((FIXTURE / "pins.json").read_bytes())
    archive = (FIXTURE / "exchange.tar.gz").read_bytes()
    digest = "sha256:" + hashlib.sha256(archive).hexdigest()
    assert digest == pins["archive"]["sha256"] == "sha256:" + ARCHIVE_SHA256
    assert len(archive) == pins["archive"]["bytes"]
    assert pins["archive"]["format"] == "ustar_gzip_mtime_zero"
    assert archive[:3] == b"\x1f\x8b\x08" and archive[4:8] == b"\x00" * 4
    assert gzip.decompress(archive)[257:263] == b"ustar\x00"
    assert set(pins["members"]) == set(MEMBERS)

    exchange = tmp_path / "exchange"
    exchange.mkdir(mode=DIRECTORY_MODE)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
        entries = source.getmembers()
        assert [entry.name.rstrip("/") for entry in entries] == [
            "paired",
            "trajectory",
            *MEMBERS,
        ]
        for entry in entries:
            relative = PurePosixPath(entry.name)
            assert not relative.is_absolute() and ".." not in relative.parts
            assert entry.uid == entry.gid == entry.mtime == 0
            assert entry.uname == entry.gname == ""
            target = exchange.joinpath(*relative.parts)
            if entry.isdir():
                assert entry.mode == DIRECTORY_MODE and entry.name.rstrip("/") in {
                    "paired",
                    "trajectory",
                }
                target.mkdir(mode=DIRECTORY_MODE)
                continue
            assert entry.isfile() and entry.mode == FILE_MODE
            selected = source.extractfile(entry)
            assert selected is not None
            data = selected.read()
            claim = pins["members"][entry.name]
            assert len(data) == claim["bytes"]
            assert "sha256:" + hashlib.sha256(data).hexdigest() == claim["sha256"]
            target.write_bytes(data)
            target.chmod(FILE_MODE)

    execution = json.loads((exchange / "paired/execution.json").read_bytes())
    trajectory = json.loads((exchange / "trajectory/trajectory.json").read_bytes())
    assert execution["source"]["runner"]["profile"] == "pi_1_5_0"
    assert trajectory["profile"] == "native_pi_1_5_lifecycle.v1"
    assert trajectory["call_relationships"] == "not_captured"
    assert trajectory["availability_counts"]["retained"] == PAIRED_ROWS

    report = cast(dict[str, Any], audit_goppi_trajectory(exchange, verifier=ResponseStub()))
    assert report["schema"] == "goppi_trajectory_review.v2"
    assert report["state"] == "analyzed"
    assert report["populations"]["paired_rows"] == PAIRED_ROWS
    assert report["populations"]["case_stage_repeat_pairs"] == CASE_STAGE_REPEAT_PAIRS
    assert report["capture_counts"]["retained"] == PAIRED_ROWS
    assert report["trust_scope"]["verifier_source"] == "caller_supplied_python_port"
    assert report["trust_scope"]["verifier_identity_verified"] is False
    assert report["trust_scope"]["source_authentication_independently_verified"] is False
    assert report["authority"]["execution_authorized"] is False
    assert "causality_and_retry_effects_not_established" in report["interpretation_limits"]

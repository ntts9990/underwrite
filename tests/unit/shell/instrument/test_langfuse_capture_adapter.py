"""Byte accounting preserves the file entry point's identities and limits."""

from pathlib import Path

import pytest

from underwrite.instrument.ingest import langfuse_accounting as adapter

GOLDEN = Path(__file__).resolve().parents[4] / "fixtures/golden/external/langfuse"
OBSERVATIONS = GOLDEN / "observations_v2-2026-09-14T06-40-00.json"
SCORES = GOLDEN / "scores-2026-09-14T06-40-00.json"


def test_bytes_match_existing_file_accounting() -> None:
    assert adapter.audit_langfuse_bytes(OBSERVATIONS.read_bytes(), SCORES.read_bytes()) == (
        adapter.audit_langfuse_files(OBSERVATIONS, SCORES)
    )


@pytest.mark.parametrize("role", ["observations", "scores"])
def test_bytes_retain_per_source_limits(role: str, monkeypatch: pytest.MonkeyPatch) -> None:
    buffers = {"observations": b"[]", "scores": b"[]"}
    buffers[role] = b"[ ]"
    monkeypatch.setattr(adapter, "DEFAULT_MAX_BYTES", 2)
    with pytest.raises(adapter.LangfuseFileError) as caught:
        adapter.audit_langfuse_bytes(buffers["observations"], buffers["scores"])
    assert caught.value.code == "INPUT_LIMIT_EXCEEDED"
    assert caught.value.location == "/" + role


def test_unknown_role_refused_before_read(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown Langfuse source role"):
        adapter.read_langfuse_file(tmp_path / "missing", "unknown")

"""Small local inspection fixtures shared by library and CLI tests."""

import json
from pathlib import Path

import pytest
from _trusted_contracts import trust_contracts


@pytest.fixture
def inspection_contracts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit trusted data for library tests, not a package-installation claim."""
    trust_contracts(tmp_path, monkeypatch, "observation.v1", "artifact_case.v1")


def case(
    tmp_path: Path,
    body: bytes = (
        b'{"schema_version":"underwrite.evidence-bundle.v1",'
        b'"manifest":{"run_id":"local"},'
        b'"sources":[],"readings":[],"availability":[]}'
    ),
) -> Path:
    (tmp_path / "artifact.json").write_bytes(body)
    path = tmp_path / "case.json"
    path.write_text(
        json.dumps(
            {
                "schema": "artifact_case.v1",
                "artifact": {"path": "artifact.json"},
                "source": {"format": "underwrite.evidence-bundle", "format_version": "v1"},
            }
        )
    )
    return path


def update(path: Path, field: str, changes: dict[str, object]) -> None:
    value = json.loads(path.read_bytes())
    value[field].update(changes)
    path.write_text(json.dumps(value))

"""Explicit trusted contracts for tests that run where package resources are absent."""

from pathlib import Path

import pytest
from _repo_paths import repo_root

from underwrite.instrument.ingest import application, schema


def trust_contracts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Deploy the packaged bytes of each ``<name>.schema.json`` as the only trusted contracts.

    The copy reads the packaged source resource, so dropping a contract fails the
    caller. Wheel installation is covered separately by the packaging tests.
    """
    root = repo_root(Path(__file__).resolve())
    deployed = tmp_path / "trusted"
    (deployed / "_contracts").mkdir(parents=True)
    for name in names:
        filename = f"{name}.schema.json"
        (deployed / "_contracts" / filename).write_bytes(
            (root / "src/underwrite/_contracts" / filename).read_bytes()
        )

    def resources(name: str) -> Path:
        assert name == "underwrite"
        return deployed

    monkeypatch.setattr(application, "files", resources)
    monkeypatch.setattr(schema, "files", resources)

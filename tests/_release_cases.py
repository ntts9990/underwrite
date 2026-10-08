"""Explicit release test contracts and small real codec inputs."""

import json
import urllib.request
from pathlib import Path

import pytest
from _repo_paths import repo_root
from _trusted_contracts import trust_contracts


@pytest.fixture
def release_contracts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trust_contracts(
        tmp_path,
        monkeypatch,
        "observation.v1",
        "artifact_case.v1",
        "release_case.v1",
        "release_inspection.v1",
    )

    def deny_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("No network permitted during release tests")

    monkeypatch.setattr(urllib.request, "urlopen", deny_network)


def release_case(tmp_path: Path) -> Path:
    root = repo_root(Path(__file__).resolve())
    subject = dict(
        target="demo",
        version="example",
        environment="underwrite-capture",
        evalset="set",
        evalset_version="v1",
        conditions="offline",
    )
    requirements: list[dict[str, str]] = []
    evidence: list[dict[str, object]] = []
    for name, fmt, version, filename in (
        ("eval", "deepeval.test-run", "4.1.1", "deepeval/test_run_20260914_132634.json"),
        (
            "trace",
            "langfuse.observations-v2",
            "4.35.0",
            "langfuse/observations_v2-2026-09-14T06-40-00.json",
        ),
    ):
        (tmp_path / f"{name}.json").write_bytes(
            (root / "fixtures/golden/external" / filename).read_bytes()
        )
        requirements.append(dict(id=name, format=fmt, format_version=version))
        evidence.append(
            dict(
                id=name,
                requirement=name,
                declared=subject.copy(),
                case={
                    "schema": "artifact_case.v1",
                    "artifact": {"path": f"{name}.json"},
                    "source": {"format": fmt, "format_version": version},
                },
            )
        )
    path = tmp_path / "release.json"
    path.write_text(
        json.dumps(
            dict(
                schema="release_case.v1",
                subject=subject,
                requirements=requirements,
                evidence=evidence,
            )
        )
    )
    return path

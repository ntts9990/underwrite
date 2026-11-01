"""Adversarial boundaries for the optional installed-pair acceptance script."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

import pytest
from _script_loader import load_module_from_path

ROOT = Path(__file__).resolve().parents[2]
check = cast(
    Any,
    load_module_from_path(
        "check_goppi_installed_pair", ROOT / "scripts/check_goppi_installed_pair.py"
    ),
)


def test_artifact_snapshots_survive_source_replacement(tmp_path: Path) -> None:
    inputs: dict[str, Path] = {}
    originals: dict[str, bytes] = {}
    for index, role in enumerate(check.ARTIFACT_ROLES):
        source = tmp_path / f"source-{index}"
        originals[role] = f"original-{role}".encode()
        source.write_bytes(originals[role])
        inputs[role] = source

    snapshots, identities = check.snapshot_artifacts(inputs, tmp_path / "private")
    for role, source in inputs.items():
        replacement = source.with_suffix(".replacement")
        replacement.write_bytes(b"replacement")
        os.replace(replacement, source)
        assert snapshots[role].read_bytes() == originals[role]
        assert identities[role]["sha256"] == check.digest(snapshots[role])


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("flood", "PROCESS_OUTPUT_LIMIT"), ("timeout", "PROCESS_TIMEOUT")],
)
def test_process_failure_kills_descendants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected: str,
) -> None:
    marker = tmp_path / "escaped"
    child = (
        f"import pathlib,time;time.sleep(.5);pathlib.Path({str(marker)!r}).write_text('escaped')"
    )
    action = "import os;os.write(1,b'x'*4096);os.write(2,b'y'*4096)" if mode == "flood" else "pass"
    parent = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}]);"
        f"{action};time.sleep(10)"
    )
    monkeypatch.setattr(check, "STDOUT_MAX_BYTES", 64 if mode == "flood" else 4096)
    monkeypatch.setattr(check, "STDERR_MAX_BYTES", 64 if mode == "flood" else 4096)
    monkeypatch.setattr(check, "DEADLINE_SECONDS", 0.1 if mode == "timeout" else 2.0)

    with pytest.raises(check.Failure) as caught:
        check.run([sys.executable, "-c", parent], tmp_path)
    assert caught.value.reason == expected
    time.sleep(0.7)
    assert not marker.exists()


def test_runtime_environment_does_not_forward_provider_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "private")
    node = tmp_path / "artifacts/node"
    node.parent.mkdir()
    node.touch()
    env = check.isolated_env(tmp_path, node)

    assert "OPENAI_API_KEY" not in env
    assert env["UV_PYTHON_DOWNLOADS"] == "never"
    assert env["NPM_CONFIG_USERCONFIG"].startswith(str(tmp_path))
    assert env["UV_CACHE_DIR"].startswith(str(tmp_path))

    fake = tmp_path / "fake-goppi.py"
    fake.write_text(
        "import json,os;print(json.dumps({'credential':os.getenv('OPENAI_API_KEY')}))",
        encoding="utf-8",
    )
    result = check.native(Path(sys.executable), fake, tmp_path, tmp_path, env)
    assert json.loads(result.stdout) == {"credential": None}


def _positive_review() -> dict[str, Any]:
    stable = {
        "difference": {"outcome_changed": True},
        "baseline": {
            "outcome": {"status": "observed", "scored_value": False},
            "tool_status_counts": [{"tool_name": "read", "status": "error"}],
        },
        "candidate": {
            "outcome": {"status": "observed", "scored_value": True},
            "tool_status_counts": [{"tool_name": "read", "status": "ok"}],
        },
    }
    return {
        "schema": check.CONTRACTS["underwrite_review"],
        "state": "analyzed",
        "trust_scope": dict(check.REVIEW_TRUST),
        "authority": dict(check.REVIEW_AUTHORITY),
        "populations": dict(check.POPULATIONS),
        "comparisons": [copy.deepcopy(stable) for _ in range(72)],
    }


@pytest.mark.parametrize("mutation", ["population", "stable_change"])
def test_positive_review_rejects_population_or_semantic_drift(mutation: str) -> None:
    review = _positive_review()
    check.validate_review(review)
    if mutation == "population":
        review["populations"]["paired_rows"] = 143
    else:
        for comparison in review["comparisons"]:
            comparison["difference"]["outcome_changed"] = False
    with pytest.raises(check.Failure, match="UNDERWRITE_POSITIVE_CONTRACT_DRIFT"):
        check.validate_review(review)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("native_verification", "local_goppi_same_byte_snapshot"),
        ("verifier_identity_verified", True),
        ("content_closure", "self_consistent_content_closure"),
    ],
)
def test_positive_review_rejects_native_or_elevated_underwrite_trust(
    field: str, value: object
) -> None:
    review = _positive_review()
    review["trust_scope"][field] = value
    with pytest.raises(check.Failure, match="UNDERWRITE_POSITIVE_CONTRACT_DRIFT"):
        check.validate_review(review)


def test_native_missing_trajectory_reason_is_exact() -> None:
    native = subprocess.CompletedProcess(
        [],
        3,
        json.dumps({"data": {"storage_code": "WRONG_REASON"}}).encode(),
        b"",
    )
    audit = subprocess.CompletedProcess(
        [],
        2,
        b"",
        json.dumps({"reason": "TRAJECTORY_COMPLETE_HANDOFF_REQUIRED"}).encode(),
    )
    with pytest.raises(check.Failure, match="NATIVE_MISSING_TRAJECTORY_REASON_DRIFT"):
        check.validate_missing(native, audit)


@pytest.mark.parametrize("mutation", ["contract", "npm_resolution", "python_resolution"])
def test_success_summary_requires_contract_and_resolution_identities(mutation: str) -> None:
    summary = {
        "contracts": dict(check.CONTRACTS),
        "trust_limits": dict(check.TRUST_LIMITS),
        "npm_resolution": {"package_count": 1, "sha256": "sha256:" + "a" * 64},
        "python_resolution": {"package_count": 1, "sha256": "sha256:" + "b" * 64},
    }
    check.validate_success_summary(summary)
    if mutation == "contract":
        summary["contracts"]["native_verification"] = "wrong"
    else:
        del summary[mutation]
    with pytest.raises(check.Failure):
        check.validate_success_summary(summary)


def test_failure_summary_is_structured_and_path_free(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "private-artifact"
    code = check.main(
        [
            "--goppi-tarball",
            str(missing),
            "--underwrite-wheel",
            str(missing),
            "--underwrite-core-wheel",
            str(missing),
            "--node",
            str(missing),
        ]
    )
    streams = capsys.readouterr()
    report = json.loads(streams.err)

    assert code == 1 and streams.out == ""
    assert report["failure"] == {
        "phase": "snapshot",
        "reason": "INPUT_ARTIFACT_UNAVAILABLE",
        "location": "/goppi_tarball",
        "next_action": "CORRECT_ARGUMENTS",
    }
    assert str(tmp_path) not in streams.err

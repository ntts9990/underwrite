"""Baseline projection CLI composition and source/privacy boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from _baseline_exchange import build_baseline_exchange
from _repo_paths import repo_root
from underwrite_core.canonical import canonical_bytes

from underwrite.cli.main import main
from underwrite.instrument.evidence.goppi_baseline import project_baseline_bundle
from underwrite.instrument.ingest.goppi_baseline import capture_baseline_documents
from underwrite.measurement_contract.semantics import parse_semantics, semantic_digest

ERROR_EXIT = 2
ROOT = repo_root(Path(__file__).resolve())


def _write(root: Path, files: dict[str, bytes]) -> None:
    for name, raw in files.items():
        (root / name).write_bytes(raw)


def _args(root: Path, direction: str = "higher") -> list[str]:
    return ["project-baseline", "--exchange", str(root), "--direction", direction, "--json"]


def _error(capsys: pytest.CaptureFixture[str], code: str) -> dict[str, Any]:
    captured = capsys.readouterr()
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["schema"] == "evidence_error.v1"
    assert error["code"] == code
    assert error["exit_code"] == ERROR_EXIT
    assert error["retryable"] is False
    assert "PRIVATE_SENTINEL" not in captured.err
    return error


def test_generated_bundle_identity_and_private_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exchange = build_baseline_exchange()
    exchange.export["include_answers"] = True
    for row in exchange.rows:
        row["answer"] = {
            "included": True,
            "availability": "included_private",
            "value": "PRIVATE_SENTINEL",
        }
        row["outcome"]["reason"] = "PRIVATE_SENTINEL"
    _write(tmp_path, exchange.files())
    assert main(_args(tmp_path)) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "PRIVATE_SENTINEL" not in captured.out
    assert str(tmp_path) not in captured.out
    observation = json.loads(captured.out)
    assert observation["schema"] == "observation.v1"
    bundle = project_baseline_bundle(capture_baseline_documents(tmp_path), direction="higher")
    assert (
        observation["raw"]["hash"]
        == "sha256:" + hashlib.sha256(canonical_bytes(bundle)).hexdigest()
    )
    assert observation["payload"] == bundle


@pytest.mark.parametrize(
    "args", [[], ["--exchange", "PRIVATE_SENTINEL"], ["--direction", "higher"]]
)
def test_requires_exchange_and_explicit_direction(
    args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["project-baseline", *args, "--json"]) == ERROR_EXIT
    _error(capsys, "USAGE_ERROR")


def test_missing_file_is_sanitized(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(_args(tmp_path / "PRIVATE_SENTINEL")) == ERROR_EXIT
    _error(capsys, "SOURCE_READ_FAILED")


def test_changed_bytes_refuse_without_partial_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, build_baseline_exchange().files())
    with (tmp_path / "rows.jsonl").open("ab") as stream:
        stream.write(b" ")
    assert main(_args(tmp_path)) == ERROR_EXIT
    _error(capsys, "INVALID_INPUT")


@pytest.mark.parametrize("hint", ["absent", "tampered", "changed_semantics"])
@pytest.mark.parametrize("mutation", ["subset", "reorder", "superset", "exact"])
def test_projected_observation_composes_with_native_measurement(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mutation: str, hint: str
) -> None:
    _write(tmp_path, build_baseline_exchange().files())
    assert main(_args(tmp_path)) == 0
    observation = json.loads(capsys.readouterr().out)
    payload = observation["payload"]
    policy = json.loads((ROOT / "fixtures/examples/event-measurement/policy.json").read_bytes())
    semantics = payload["measurement_semantics"]
    policy["semantic_digest"] = semantic_digest(parse_semantics(semantics))
    if hint == "absent":
        payload.pop("semantic_digest", None)
    else:
        payload["semantic_digest"] = (
            policy["semantic_digest"] if hint == "changed_semantics" else "sha256:" + "0" * 64
        )
    if hint == "changed_semantics":
        semantics["desirable_direction"] = "lower"
    reading = payload["readings"][0]
    roster = [row["case_id"] for row in reading["rows"]]
    policy["expected_case_ids"] = (
        roster[:-1]
        if mutation == "subset"
        else list(reversed(roster))
        if mutation == "reorder"
        else [*roster, "unreported-extra-case"]
        if mutation == "superset"
        else roster
    )
    policy["assumptions"] = {key: "unknown" for key in policy["assumptions"]}
    observation_path = tmp_path / "observation.json"
    policy_path = tmp_path / "policy.json"
    observation_path.write_text(json.dumps(observation))
    policy_path.write_text(json.dumps(policy))
    result = main(
        ["measure", "--observation", str(observation_path), "--policy", str(policy_path), "--json"]
    )
    streams = capsys.readouterr()
    if hint == "changed_semantics":
        assert result == ERROR_EXIT
        assert streams.out == ""
        assert json.loads(streams.err)["reason"] == "SEMANTIC_DIGEST_MISMATCH"
    elif mutation in {"subset", "reorder"}:
        assert result == ERROR_EXIT
        assert streams.out == ""
        assert json.loads(streams.err)["code"] == "INVALID_OBSERVATION"
    else:
        assert result == 0, streams.err
        report = json.loads(streams.out)
        assert report["schema"] == "read.v2"
        assert report["directional"]["availability"] == "not_measured"
        assert report["decision"]["status"] == "withheld"
        assert report["diagnostics"]["event_rate"]["value"] == pytest.approx(0.5)
        assert report["diagnostics"]["brier"]["value"] is None
        assert report["diagnostics"]["ece"]["value"] is None
        assert report["population"]["predictions"]["observed_count"] is None
        assert report["population"]["expected"]["count"] == len(policy["expected_case_ids"])
        if mutation == "superset":
            assert "unreported-extra-case" in report["population"]["outcomes"]["missing_ids"]


@pytest.mark.parametrize("direction", [None, "PRIVATE_SENTINEL"])
def test_invalid_direction_fails_before_capture(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    direction: str | None,
) -> None:
    from underwrite.cli import project_baseline

    def unexpected_capture(path: Path) -> None:
        pytest.fail("invalid arguments must not capture source files")

    monkeypatch.setattr(project_baseline, "capture_baseline_documents", unexpected_capture)
    args = ["project-baseline", "--exchange", str(tmp_path), "--json"]
    if direction is not None:
        args.extend(["--direction", direction])
    assert main(args) == ERROR_EXIT
    _error(capsys, "USAGE_ERROR")


def test_opposite_direction_changes_interpretation_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, build_baseline_exchange().files())
    observations: list[dict[str, Any]] = []
    for direction in ("higher", "lower"):
        assert main(_args(tmp_path, direction)) == 0
        observations.append(json.loads(capsys.readouterr().out))
    higher, lower = [item["payload"] for item in observations]
    assert higher["readings"] == lower["readings"]
    assert higher["sources"] == lower["sources"]
    assert higher["availability"] == lower["availability"]
    assert higher["measurement_semantics"]["desirable_direction"] == "higher"
    assert lower["measurement_semantics"]["desirable_direction"] == "lower"
    assert semantic_digest(parse_semantics(higher["measurement_semantics"])) != semantic_digest(
        parse_semantics(lower["measurement_semantics"])
    )


@pytest.mark.parametrize("mutation", ["unknown", "path", "unicode"])
def test_untrusted_invalid_values_never_leak_into_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mutation: str
) -> None:
    exchange = build_baseline_exchange()
    if mutation == "unknown":
        exchange.rows[0]["PRIVATE_SENTINEL"] = "PRIVATE_SENTINEL"
    elif mutation == "path":
        exchange.rows[0]["runner"]["profile"] = "/PRIVATE_SENTINEL/private/model"
    files = exchange.files()
    if mutation == "unicode":
        files["manifest.json"] = b'{"PRIVATE_SENTINEL":"\\ud800"}'
    _write(tmp_path, files)
    assert main(_args(tmp_path)) == ERROR_EXIT
    _error(capsys, "INVALID_INPUT")

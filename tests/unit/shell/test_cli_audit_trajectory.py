"""CLI wiring uses a declared verifier stub; native acceptance is a separate task."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from _repo_paths import repo_root
from drivers.goppi_trajectory_verifier_stub import verifier_response

from underwrite.cli import audit_trajectory
from underwrite.cli.main import main
from underwrite.instrument.ingest.goppi_verifier import VerifierError

EXCHANGE = repo_root(Path(__file__).resolve()) / "fixtures/conformance/goppi-trajectory/exchange"
ERROR_EXIT = 2


class StubVerifier:
    def __init__(self) -> None:
        self.snapshots: list[Path] = []

    def verify(self, snapshot: Path) -> bytes:
        self.snapshots.append(snapshot)
        assert snapshot != EXCHANGE
        return json.dumps(verifier_response(snapshot)).encode()


def test_entry_point_discovers_and_invokes_selected_verifier(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stub = StubVerifier()
    choices: list[Path | None] = []

    def factory(path: Path | None) -> StubVerifier:
        choices.append(path)
        return stub

    monkeypatch.setattr(audit_trajectory, "GoppiCliVerifier", factory)
    assert main(["audit-trajectory", "--exchange", str(EXCHANGE), "--json"]) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    result: dict[str, Any] = json.loads(streams.out)
    assert result["schema"] == audit_trajectory.RESULT_SCHEMA
    assert result["trust_scope"]["native_verification"] == "local_goppi_same_byte_snapshot"
    assert result["authority"]["execution_authorized"] is False
    assert choices == [None] and len(stub.snapshots) == 1
    assert not stub.snapshots[0].exists()
    assert main(["capabilities", "--json"]) == 0
    commands = json.loads(capsys.readouterr().out)["commands"]
    descriptor = next(item for item in commands if item["command"] == "audit-trajectory")
    assert descriptor["profiles"][0]["output_schemas"] == [audit_trajectory.RESULT_SCHEMA]


def test_explicit_executable_and_refusal_do_not_become_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    selected = tmp_path / "trusted verifier"

    def refusal(path: Path | None) -> StubVerifier:
        assert path == selected
        raise VerifierError("INVALID_INPUT", "GOPPI_VERIFIER_REFUSED")

    monkeypatch.setattr(audit_trajectory, "GoppiCliVerifier", refusal)
    assert (
        main(
            [
                "audit-trajectory",
                "--exchange",
                str(EXCHANGE),
                "--goppi-executable",
                str(selected),
                "--json",
            ]
        )
        == ERROR_EXIT
    )
    streams = capsys.readouterr()
    assert streams.out == "" and str(tmp_path) not in streams.err
    assert json.loads(streams.err)["reason"] == "GOPPI_VERIFIER_REFUSED"


def test_unknown_version_option_is_not_silently_accepted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(path: Path | None) -> StubVerifier:
        pytest.fail("invalid invocation must not construct a verifier")

    monkeypatch.setattr(audit_trajectory, "GoppiCliVerifier", forbidden)
    assert (
        main(
            [
                "audit-trajectory",
                "--exchange",
                str(EXCHANGE),
                "--version",
                "999",
                "--json",
            ]
        )
        == ERROR_EXIT
    )
    streams = capsys.readouterr()
    assert streams.out == ""
    assert json.loads(streams.err)["code"] == "USAGE_ERROR"


@pytest.mark.parametrize("layout", ["bare_paired", "missing_companion", "extra_entry"])
def test_incomplete_handoff_has_actionable_error_without_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], layout: str
) -> None:
    import shutil

    exchange = Path(shutil.copytree(EXCHANGE, tmp_path / "exchange"))
    selected = exchange
    if layout == "bare_paired":
        selected = exchange / "paired"
    elif layout == "missing_companion":
        shutil.rmtree(exchange / "trajectory")
    else:
        (exchange / "extra").write_text("private")
    stub = StubVerifier()

    def selected_verifier(path: Path | None) -> StubVerifier:
        return stub

    monkeypatch.setattr(audit_trajectory, "GoppiCliVerifier", selected_verifier)
    assert main(["audit-trajectory", "--exchange", str(selected), "--json"]) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == ""
    error = json.loads(streams.err)
    assert error["code"] == "INVALID_INPUT"
    assert error["reason"] == "TRAJECTORY_COMPLETE_HANDOFF_REQUIRED"
    assert error["location"] == "/exchange"
    assert error["next_action"] == "CORRECT_INPUT"
    assert str(tmp_path) not in streams.err
    assert stub.snapshots == []

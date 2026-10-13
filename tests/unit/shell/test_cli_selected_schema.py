"""Parsed allowlisted choices select one output contract inside the CLI boundary."""

from __future__ import annotations

import argparse
import json
from typing import Any

import pytest

from underwrite.cli import evidence
from underwrite.instrument.ingest.schema import SchemaConfigurationError

ERROR_EXIT = 2


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--version", choices=("old", "new"), default="old")
    parser.add_argument("--json", action="store_true")


@pytest.mark.parametrize("version", ["old", "new"])
def test_selected_schema_resolved_once_before_produce(
    version: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events: list[str] = []

    def select(args: argparse.Namespace) -> str:
        events.append("select:" + args.version)
        return "selected." + args.version

    def produce(args: argparse.Namespace) -> dict[str, object]:
        events.append("produce")
        return {"schema": "selected." + args.version}

    class Validator:
        def validate(self, value: object) -> None:
            assert value == {"schema": "selected." + version}
            events.append("validate")

    def packaged(name: str) -> Any:
        events.append("load:" + name)
        return Validator()

    monkeypatch.setattr(evidence, "packaged_validator", packaged)
    code = evidence.run_cli(
        "selected",
        ["--version", version, "--json"],
        configure,
        produce,
        evidence.ReportOutput(select),
    )
    assert code == 0
    assert events == ["select:" + version, "produce", "load:selected." + version, "validate"]
    assert json.loads(capsys.readouterr().out) == {"schema": "selected." + version}


def test_selector_configuration_failure_prevents_produce(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def select(_: argparse.Namespace) -> str:
        raise SchemaConfigurationError("private details")

    def forbidden(_: argparse.Namespace) -> dict[str, object]:
        pytest.fail("produce called after failed selection")

    code = evidence.run_cli(
        "selected", ["--json"], configure, forbidden, evidence.ReportOutput(select)
    )
    output = capsys.readouterr()
    assert code == ERROR_EXIT and not output.out
    assert json.loads(output.err)["code"] == "SCHEMA_CONFIGURATION_ERROR"
    assert "private details" not in output.err


def test_static_schema_remains_supported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = evidence.EvidenceError("INVALID_INPUT", "INVALID_INPUT").payload

    def produce(_: argparse.Namespace) -> dict[str, object]:
        return report

    code = evidence.run_cli(
        "static", ["--json"], configure, produce, evidence.ReportOutput("evidence_error.v1")
    )
    assert code == 0 and json.loads(capsys.readouterr().out) == report

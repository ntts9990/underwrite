"""Closed output choices load trusted contracts before producing or emitting anything."""

from __future__ import annotations

import argparse
import json

import pytest
from jsonschema import Draft202012Validator

from underwrite.cli import evidence
from underwrite.instrument.ingest.schema import SchemaConfigurationError

ERROR_EXIT = 2


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true")


@pytest.mark.parametrize("selected", ["first.v1", "second.v1"])
def test_closed_output_set_is_loaded_before_produce(
    selected: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    loaded: list[str] = []

    def validator(name: str) -> Draft202012Validator:
        loaded.append(name)
        return Draft202012Validator(
            {"type": "object", "properties": {"schema": {"const": name}}, "required": ["schema"]}
        )

    def produce(args: argparse.Namespace) -> dict[str, object]:
        del args
        assert loaded == ["first.v1", "second.v1"]
        return {"schema": selected}

    monkeypatch.setattr(evidence, "packaged_validator", validator)
    assert (
        evidence.run_cli(
            "test",
            ["--json"],
            _configure,
            produce,
            evidence.ReportOutput(("first.v1", "second.v1")),
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {"schema": selected}
    assert loaded == ["first.v1", "second.v1"]


@pytest.mark.parametrize("selected", [None, [], "../../PRIVATE_CANARY", "unknown.v1"])
def test_report_cannot_choose_an_unlisted_schema(
    selected: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    loaded: list[str] = []

    def validator(name: str) -> Draft202012Validator:
        loaded.append(name)
        return Draft202012Validator({})

    monkeypatch.setattr(evidence, "packaged_validator", validator)
    assert (
        evidence.run_cli(
            "test",
            ["--json"],
            _configure,
            lambda _: {"schema": selected},
            evidence.ReportOutput(("first.v1", "second.v1")),
        )
        == ERROR_EXIT
    )
    captured = capsys.readouterr()
    assert captured.out == "" and "PRIVATE_CANARY" not in captured.err
    assert json.loads(captured.err)["code"] == "INTERNAL_ERROR"
    assert loaded == ["first.v1", "second.v1"]


def test_missing_trusted_output_contract_prevents_produce(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    produced: list[bool] = []

    def validator(name: str) -> Draft202012Validator:
        if name == "second.v1":
            raise SchemaConfigurationError("PRIVATE_CANARY")
        return Draft202012Validator({})

    def produce(args: argparse.Namespace) -> dict[str, object]:
        del args
        produced.append(True)
        return {"schema": "first.v1"}

    monkeypatch.setattr(evidence, "packaged_validator", validator)
    assert (
        evidence.run_cli(
            "test",
            ["--json"],
            _configure,
            produce,
            evidence.ReportOutput(("first.v1", "second.v1")),
        )
        == ERROR_EXIT
    )
    captured = capsys.readouterr()
    assert produced == [] and captured.out == "" and "PRIVATE_CANARY" not in captured.err
    assert json.loads(captured.err)["code"] == "SCHEMA_CONFIGURATION_ERROR"


def test_closed_output_still_counts_final_newline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def validator(name: str) -> Draft202012Validator:
        del name
        return Draft202012Validator({})

    monkeypatch.setattr(evidence, "packaged_validator", validator)
    payload: dict[str, object] = {"schema": "first.v1"}
    exact = len(b'{"schema":"first.v1"}\n')
    for limit, expected in ((exact, 0), (exact - 1, 2)):
        assert (
            evidence.run_cli(
                "test",
                ["--json"],
                _configure,
                lambda _: payload,
                evidence.ReportOutput(("first.v1", "second.v1"), max_bytes=limit),
            )
            == expected
        )
        captured = capsys.readouterr()
        if expected:
            assert captured.out == ""
            assert json.loads(captured.err)["code"] == "OUTPUT_LIMIT_EXCEEDED"
        else:
            assert captured.err == "" and len(captured.out.encode()) == exact


@pytest.mark.parametrize("choices", [(), ("first.v1", "first.v1"), ("",)])
def test_invalid_closed_configuration_prevents_produce(
    choices: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    def produce(args: argparse.Namespace) -> dict[str, object]:
        del args
        pytest.fail("invalid output configuration reached produce")

    assert (
        evidence.run_cli("test", ["--json"], _configure, produce, evidence.ReportOutput(choices))
        == ERROR_EXIT
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["code"] == "SCHEMA_CONFIGURATION_ERROR"

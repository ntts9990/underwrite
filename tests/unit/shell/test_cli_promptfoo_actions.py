"""Promptfoo action view retains existing accounting while naming safe checks."""

from __future__ import annotations

import json
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import digest_bytes

from underwrite.cli import main
from underwrite.instrument.evidence.promptfoo_compat import ACTIONS_SCHEMA
from underwrite.instrument.ingest.accounting import (
    audit_promptfoo_actions_bytes,
    audit_promptfoo_actions_file,
    audit_promptfoo_file,
)

ROOT = Path(__file__).resolve().parents[3]
MISMATCH = ROOT / "fixtures/examples/evidence/synthetic-promptfoo-count-mismatch.json"
CONSISTENT = ROOT / "fixtures/golden/external/promptfoo-0.124.1/results.json"
ERROR_EXIT = 2


def _report(path: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert main.main(["audit-promptfoo", str(path), "--view", "actions", "--json"]) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    return cast(dict[str, Any], json.loads(streams.out))


def test_actions_view_is_a_checked_projection_of_the_same_selected_bytes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = _report(MISMATCH, capsys)
    baseline = audit_promptfoo_file(MISMATCH)
    assert report == audit_promptfoo_actions_file(MISMATCH)
    assert report == audit_promptfoo_actions_bytes(MISMATCH.read_bytes())
    assert report["schema"] == ACTIONS_SCHEMA
    assert report["raw_hash"] == baseline["raw_hash"] == digest_bytes(MISMATCH.read_bytes())
    facts = {key: value for key, value in report.items() if key not in ("schema", "next_actions")}
    assert facts == {
        key: value for key, value in baseline.items() if key != "schema"
    }
    assert report["next_actions"] == ["RECONCILE_SOURCE_DECLARED_COUNTS"]
    assert report["declared_relation"] == "inconsistent"
    assert report["source"]["claims_verified"] is False
    resource = files("underwrite").joinpath("_contracts", ACTIONS_SCHEMA + ".schema.json")
    schema = json.loads(resource.read_bytes())
    Draft202012Validator(schema).validate(report)  # pyright: ignore[reportUnknownMemberType]
    for actions in ([], ["INSPECT_SOURCE_FLAG_DISAGREEMENTS"], ["PRIVATE_ACTION"]):
        invalid = {**report, "next_actions": actions}
        with pytest.raises(ValidationError):
            Draft202012Validator(schema).validate(invalid)  # pyright: ignore[reportUnknownMemberType]


def test_consistent_and_source_flag_disagreement_actions_are_distinct(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _report(CONSISTENT, capsys)["next_actions"] == []
    payload = cast(dict[str, Any], json.loads(CONSISTENT.read_bytes()))
    row = cast(list[dict[str, Any]], payload["results"]["results"])[0]
    row["failureReason"] = 2
    row["prompt"] = "PRIVATE_PROMPTFOO_CANARY"
    source = tmp_path / "PRIVATE_PROMPTFOO_CANARY.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    report = _report(source, capsys)
    assert report["next_actions"] == ["INSPECT_SOURCE_FLAG_DISAGREEMENTS"]
    assert report["findings"][0]["code"] == "SOURCE_FLAGS_DISAGREE"
    assert "PRIVATE_PROMPTFOO_CANARY" not in json.dumps(report)
    report["next_actions"].append("PRIVATE_PROMPTFOO_CANARY")
    assert _report(source, capsys)["next_actions"] == ["INSPECT_SOURCE_FLAG_DISAGREEMENTS"]

    both = deepcopy(payload)
    both["results"]["stats"]["failures"] += 1
    source.write_text(json.dumps(both), encoding="utf-8")
    assert _report(source, capsys)["next_actions"] == [
        "RECONCILE_SOURCE_DECLARED_COUNTS",
        "INSPECT_SOURCE_FLAG_DISAGREEMENTS",
    ]


def test_action_view_preserves_typed_refusal_and_default_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main.main(["audit-promptfoo", str(MISMATCH), "--json"]) == 0
    baseline = json.loads(capsys.readouterr().out)
    assert baseline == audit_promptfoo_file(MISMATCH)
    assert baseline["schema"] == "promptfoo_accounting.v2"
    bad = tmp_path / "PRIVATE_PROMPTFOO_CANARY.json"
    bad.write_bytes(b'{"results":1,"results":2}')
    assert main.main(["audit-promptfoo", str(bad), "--view", "actions", "--json"]) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == ""
    assert json.loads(streams.err)["code"] == "INVALID_INPUT"
    assert "PRIVATE_PROMPTFOO_CANARY" not in streams.err


def test_actions_file_reads_once_and_unknown_view_refuses_before_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from underwrite.instrument.ingest import accounting as adapter

    source = tmp_path / "source.json"
    source.write_bytes(MISMATCH.read_bytes())
    original = adapter.read_local_file
    reads: list[Path] = []

    def counted_read(path: Path, limit: int) -> bytes:
        reads.append(path)
        return original(path, limit)

    monkeypatch.setattr(adapter, "read_local_file", counted_read)
    assert adapter.audit_promptfoo_actions_file(source)["next_actions"] == [
        "RECONCILE_SOURCE_DECLARED_COUNTS"
    ]
    assert reads == [source]
    assert main.main(["audit-promptfoo", str(source), "--view", "private", "--json"]) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == "" and json.loads(streams.err)["code"] == "USAGE_ERROR"
    assert reads == [source]


def test_actions_view_is_discoverable_in_capabilities() -> None:
    from underwrite.cli.capabilities import report

    commands = cast(list[dict[str, Any]], report(main.COMMAND_DESCRIPTORS)["commands"])
    command = next(item for item in commands if item["command"] == "audit-promptfoo")
    profiles = cast(list[dict[str, Any]], command["profiles"])
    actions = next(
        item for item in profiles if item["name"] == "promptfoo.eval-output|auto|actions"
    )
    assert actions["required_inputs"] == ["file", "view"]
    assert actions["output_schemas"] == [ACTIONS_SCHEMA]

"""Automatic common-result selection preserves v1 while screening v2 declarations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import digest_bytes

from underwrite.cli import evidence, inspect_result
from underwrite.cli.main import main
from underwrite.instrument.ingest.deepeval_binding import audit_deepeval_bound_file
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator

SOURCE = Path("fixtures/examples/deepeval-auto-4.3.1/ordinary.json")
PACK = Path("fixtures/examples/evidence/synthetic-deepeval.bindings.v2.json")
CANARY = "PRIVATE_CANARY"
ERROR_EXIT = 2


def _v2() -> dict[str, Any]:
    return cast(dict[str, Any], audit_deepeval_bound_file(SOURCE, PACK))


@pytest.mark.parametrize("view", ["summary", "actions"])
def test_v2_api_cli_and_advertised_output_are_content_minimized(
    view: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = _v2()
    for item in result["items"]:
        if item["metric"]["semantic_binding"] == "bound":
            item["metric"]["namespace"] = CANARY
    path = tmp_path / f"{CANARY}.json"
    path.write_text(json.dumps(result))
    api = inspect_result.inspect_result_file(path, view=cast(inspect_result.View, view))
    assert api["selected_result"] == {
        "format": "evidence_result.v2",
        "raw_hash": digest_bytes(path.read_bytes()),
    }
    assert api["availability_counts"] == {
        "observed": 2,
        "missing": 0,
        "error": 1,
        "abstained": 0,
        "total": 3,
    }
    if view == "actions":
        assert api["declared_semantic_binding_counts"] == {"unbound": 2, "bound": 1}
    assert main(["inspect-result", str(path), "--view", view, "--json"]) == 0
    output = capsys.readouterr()
    assert output.err == "" and CANARY not in output.out
    assert json.loads(output.out) == api
    assert main(["inspect-result", str(path), "--view", view]) == 0
    human = capsys.readouterr()
    assert human.err == "" and CANARY not in human.out
    assert "remain unchecked" in human.out
    assert main(["capabilities", "--json"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    entry = next(row for row in catalog["commands"] if row["command"] == "inspect-result")
    name = "evidence_result.v2" + ("|actions" if view == "actions" else "")
    selected = next(row for row in entry["profiles"] if row["name"] == name)
    assert selected["input_schemas"] == ["evidence_result.v2"]
    assert selected["output_schemas"] == [api["schema"]]
    packaged_validator(cast(str, api["schema"])).validate(api)  # pyright: ignore[reportUnknownMemberType]


@pytest.mark.parametrize("schema", [None, [], 1, "../../PRIVATE_CANARY", "evidence_result.v999"])
def test_untrusted_schema_never_becomes_a_resource_path(
    schema: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"schema": schema}))

    def unexpected(name: str) -> Any:
        pytest.fail(f"untrusted input selected a contract: {name}")

    monkeypatch.setattr(inspect_result, "packaged_validator", unexpected)
    with pytest.raises(evidence.EvidenceError) as caught:
        inspect_result.inspect_result_file(path)
    assert caught.value.payload["location"] == "/result/schema"
    assert CANARY not in str(caught.value.payload)


def test_v1_cli_preloads_the_declared_v2_output_before_acquisition(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real = evidence.packaged_validator

    def missing(name: str) -> Any:
        if name == "common_result_inspection.v2":
            raise SchemaConfigurationError(CANARY)
        return real(name)

    def unexpected(*args: object, **kwargs: object) -> bytes:
        pytest.fail("input read before the closed output contracts were available")

    monkeypatch.setattr(evidence, "packaged_validator", missing)
    monkeypatch.setattr(inspect_result, "read_source", unexpected)
    assert main(["inspect-result", "missing", "--json"]) == ERROR_EXIT
    captured = capsys.readouterr()
    assert captured.out == "" and CANARY not in captured.err
    assert json.loads(captured.err)["code"] == "SCHEMA_CONFIGURATION_ERROR"


def test_v2_selection_and_analysis_use_the_same_read_buffer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "input.json"
    path.write_text(json.dumps(_v2()))
    original = path.read_bytes()
    read = inspect_result.read_source
    calls: list[Path] = []

    def replace(selected: Path, *args: Any, **kwargs: Any) -> bytes:
        calls.append(selected)
        raw = read(selected, *args, **kwargs)
        path.write_bytes(b"invalid replacement")
        return raw

    monkeypatch.setattr(inspect_result, "read_source", replace)
    report = inspect_result.inspect_result_file(path)
    assert calls == [path]
    assert report["selected_result"] == {
        "format": "evidence_result.v2",
        "raw_hash": digest_bytes(original),
    }

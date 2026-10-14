"""External common-result screening keeps the selected file's content private."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import content_digest, digest_bytes

from underwrite.cli import inspect_result
from underwrite.cli.evidence import EvidenceError
from underwrite.cli.main import main
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator

FIXTURE = Path("fixtures/examples/evidence/common-result-c55.json")
CANARY = "PRIVATE_CANARY_123"
ERROR_EXIT = 2
FIXTURE_SHA256 = "5475c11c8e5ca0d6eb7f2134383fbffa3e47a6b4c96e952354c47a81a6bff16f"


def _selected(tmp_path: Path, document: object | None = None) -> Path:
    path = tmp_path / f"{CANARY}.json"
    path.write_bytes(FIXTURE.read_bytes() if document is None else json.dumps(document).encode())
    return path


def _document() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_bytes()))


def _cli(path: Path, *, as_json: bool = True) -> int:
    args = ["inspect-result", str(path)]
    return main([*args, "--json"] if as_json else args)


def test_c55_file_api_cli_and_capability(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _selected(tmp_path)
    assert digest_bytes(path.read_bytes()) == f"sha256:{FIXTURE_SHA256}"
    api = inspect_result.inspect_result_file(path)
    assert api["selected_result"] == {
        "format": "evidence_result.v1",
        "raw_hash": digest_bytes(path.read_bytes()),
    }
    assert api["declared_population"] == {"unit": "case", "observed_count": 7, "planned_count": 7}
    assert api["availability_counts"] == {
        "observed": 2, "missing": 2, "error": 1, "abstained": 2, "total": 7
    }
    assert api["verification_entry_count"] == 0
    assert api["checks"] == {
        "contract_shape": "valid", "item_invariants": "valid", "source_bytes": "not_checked",
        "producer_authentication": "not_checked", "verification_refs": "not_reperformed",
    }
    packaged_validator("common_result_inspection.v1").validate(api)  # pyright: ignore[reportUnknownMemberType]
    assert _cli(path) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    assert json.loads(streams.out) == api
    assert str(path) not in streams.out
    assert "goppi.operator_answer_assessment" not in streams.out
    assert _cli(path, as_json=False) == 0
    human = capsys.readouterr()
    assert human.err == "" and "Availability:" in human.out
    assert str(path) not in human.out
    assert main(["capabilities", "--json"]) == 0
    capabilities = json.loads(capsys.readouterr().out)
    entry = next(item for item in capabilities["commands"] if item["command"] == "inspect-result")
    assert entry["profiles"][0]["output_schemas"] == ["common_result_inspection.v1"]


@pytest.mark.parametrize("state,reason,action", [
    ("insufficient", "MISSING_INPUT", "supply_input"),
    ("unsupported", "UNSUPPORTED_FORMAT", "provide_supported_export"),
])
def test_non_analyzed_is_valid_inspection_without_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], state: str, reason: str, action: str
) -> None:
    document = _document()
    document.update({"processing": {"state": state, "reason": reason, "next_action": action},
                     "population": None, "items": []})
    if reason == "MISSING_INPUT":
        document["source"] = None
        document["verification"] = []
    path = _selected(tmp_path, document)
    assert _cli(path) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    output = json.loads(streams.out)
    assert output["declared_processing"] == document["processing"]
    assert output["declared_population"] is None
    assert output["availability_counts"] is None
    assert output["checks"]["item_invariants"] == "not_applicable"


def test_success_does_not_project_source_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document = _document()
    document["source"]["format"] = CANARY
    document["items"][0]["metric"] = {
        "semantic_binding": "bound", "namespace": "synthetic", "id": CANARY,
        "version": "v1", "semantic_digest": "sha256:" + "0" * 64,
    }
    document["verification"] = [{"target": "source_bytes", "method": "sha256",
                                 "scope": "artifact", "evidence_ref": CANARY}]
    path = _selected(tmp_path, document)
    assert _cli(path) == 0
    json_output = capsys.readouterr().out
    assert CANARY not in json_output
    assert json.loads(json_output)["verification_entry_count"] == 1
    assert _cli(path, as_json=False) == 0
    assert CANARY not in capsys.readouterr().out


@pytest.mark.parametrize("mutation,reason,location", [
    ("duplicate_coordinate", "RESULT_COORDINATE_DUPLICATE", "/items/1/coordinate"),
    ("count_mismatch", "RESULT_ITEM_COUNT_MISMATCH", "/items"),
])
def test_invariant_refusal_is_finite_and_private(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mutation: str, reason: str, location: str
) -> None:
    document = _document()
    if mutation == "duplicate_coordinate":
        document["items"][1]["coordinate"] = document["items"][0]["coordinate"]
    else:
        document["population"]["observed_count"] = 8
    path = _selected(tmp_path, document)
    with pytest.raises(EvidenceError) as caught:
        inspect_result.inspect_result_file(path)
    assert (caught.value.payload["code"], caught.value.payload["reason"],
            caught.value.payload["location"]) == ("INVALID_INPUT", reason, location)
    assert _cli(path) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == "" and CANARY not in streams.err
    error = json.loads(streams.err)
    assert (error["code"], error["reason"], error["location"]) == (
        "INVALID_INPUT", reason, location
    )


@pytest.mark.parametrize("raw,code,reason", [
    (b'{"schema":1,"schema":2}', "INVALID_INPUT", "INVALID_JSON_INPUT"),
    (b"\xff", "INVALID_INPUT", "INVALID_JSON_INPUT"),
    (b"{}", "INVALID_INPUT", "SCHEMA_VALIDATION_FAILED"),
    (b"[" * 65 + b"0" + b"]" * 65, "INPUT_LIMIT_EXCEEDED", "DEPTH_LIMIT_EXCEEDED"),
    (b" " * (inspect_result.INPUT_MAX_BYTES + 1), "INPUT_LIMIT_EXCEEDED", "BYTE_LIMIT_EXCEEDED"),
])
def test_malformed_refusal_has_no_partial_output_or_private_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], raw: bytes, code: str, reason: str
) -> None:
    path = tmp_path / f"{CANARY}.json"
    path.write_bytes(raw)
    assert _cli(path) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == "" and CANARY not in streams.err
    error = json.loads(streams.err)
    assert (error["code"], error["reason"]) == (code, reason)
    assert error["location"] == "/result"


def test_selected_bytes_are_read_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _selected(tmp_path)
    expected = digest_bytes(path.read_bytes())
    original = inspect_result.read_source
    reads = 0

    def captured(*args: Any, **kwargs: Any) -> bytes:
        nonlocal reads
        reads += 1
        raw = original(*args, **kwargs)
        path.write_bytes(b"replaced after capture")
        return raw

    monkeypatch.setattr(inspect_result, "read_source", captured)
    assert inspect_result.inspect_result_file(path)["selected_result"] == {
        "format": "evidence_result.v1", "raw_hash": expected
    }
    assert reads == 1


def test_selected_hash_identifies_raw_bytes_not_canonical_document(tmp_path: Path) -> None:
    document = _document()
    path = tmp_path / "formatted.json"
    raw = json.dumps(document, indent=2).encode()
    path.write_bytes(raw)
    report = inspect_result.inspect_result_file(path)
    selected = cast(dict[str, object], report["selected_result"])
    assert selected["raw_hash"] == digest_bytes(raw)
    assert selected["raw_hash"] != content_digest(document)


@pytest.mark.parametrize("broken_contract", ["evidence_result.v1", "common_result_inspection.v1"])
def test_packaged_contract_fault_is_configuration_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    broken_contract: str,
) -> None:
    path = _selected(tmp_path)
    original = inspect_result.packaged_validator

    def broken(name: str) -> Any:
        if name == broken_contract:
            raise SchemaConfigurationError("MISSING_PACKAGED_CONTRACT")
        return original(name)

    monkeypatch.setattr(inspect_result, "packaged_validator", broken)
    with pytest.raises(SchemaConfigurationError):
        inspect_result.inspect_result_file(path)
    assert _cli(path) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == "" and CANARY not in streams.err
    error = json.loads(streams.err)
    assert (error["code"], error["reason"], error["location"]) == (
        "SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT", ""
    )

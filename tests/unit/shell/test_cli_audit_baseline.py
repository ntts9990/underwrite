"""Read-once common-result audit of synthetic baseline exchanges."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
from _baseline_exchange import SyntheticBaselineExchange, build_baseline_exchange

from underwrite.cli.main import main
from underwrite.instrument.evidence.goppi_baseline import BaselineProjectionError
from underwrite.instrument.ingest.goppi_baseline import audit_baseline_exchange
from underwrite.instrument.ingest.local_capture import CapturedBytes, CaptureError, RootReader
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator

STATUSES = ("observed_true", "observed_false", "observed_false", "missing")
PRIVATE = "PRIVATE_SENTINEL"
ERROR_EXIT = 2


def _write(root: Path, files: dict[str, bytes]) -> None:
    for name, raw in files.items():
        (root / name).write_bytes(raw)


def _args(root: Path) -> list[str]:
    return ["audit-baseline", "--exchange", str(root), "--json"]


def _refusal(capsys: pytest.CaptureFixture[str], code: str) -> dict[str, object]:
    streams = capsys.readouterr()
    assert streams.out == ""
    assert PRIVATE not in streams.err
    error = json.loads(streams.err)
    assert error["schema"] == "evidence_error.v1"
    assert error["code"] == code
    assert error["exit_code"] == ERROR_EXIT
    assert error["retryable"] is False
    assert error["location"] == "" or str(error["location"]).startswith("/")
    assert error["next_action"]
    packaged_validator("evidence_error.v1").validate(error)  # pyright: ignore[reportUnknownMemberType]
    return error


def test_api_and_cli_emit_one_descriptive_result_without_private_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exchange = build_baseline_exchange(STATUSES)
    exchange.export["include_answers"] = True
    for row in exchange.rows:
        row["answer"] = {
            "included": True,
            "availability": "included_private",
            "value": PRIVATE,
        }
        row["outcome"]["reason"] = PRIVATE
    files = exchange.files()
    _write(tmp_path, files)
    before = {name: (tmp_path / name).read_bytes() for name in files}

    api = audit_baseline_exchange(tmp_path)
    assert main(_args(tmp_path)) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    assert PRIVATE not in streams.out
    assert str(tmp_path) not in streams.out
    for row in exchange.rows:
        assert row["case_id"] not in streams.out
    assert "records" not in api
    assert api == json.loads(streams.out)
    packaged_validator("evidence_result.v2").validate(api)  # pyright: ignore[reportUnknownMemberType]
    assert api["schema"] == "evidence_result.v2"
    assert api["processing"] == {"state": "analyzed", "reason": None, "next_action": "none"}
    assert api["population"] == {"unit": "case", "planned_count": 4, "observed_count": 4}
    assert api["verification"] == []
    source = cast(dict[str, Any], api["source"])
    identity = source["identity"]
    assert identity == {
        "kind": "bound_bundle",
        "manifest_raw_hash": "sha256:" + hashlib.sha256(files["manifest.json"]).hexdigest(),
        "members": {
            name: "sha256:" + hashlib.sha256(raw).hexdigest()
            for name, raw in files.items()
        },
    }
    items = cast(list[dict[str, Any]], api["items"])
    assert [item["coordinate"] for item in items] == [
        {"kind": "case", "index": index} for index in range(4)
    ]
    assert [item["value"] for item in items] == [True, False, False, None]
    assert [item["availability"] for item in items] == [
        "observed", "observed", "observed", "missing"
    ]
    assert all(item["origin"] == "source_claim" for item in items)
    assert all(item["permitted_effect"] == "descriptive" for item in items)
    assert all(item["metric"]["semantic_binding"] == "unbound" for item in items)
    assert items[-1]["reason"] == "SOURCE_MISSING"
    assert items[-1]["next_action"] == "inspect_source"
    assert before == {name: (tmp_path / name).read_bytes() for name in files}
    assert {path.name for path in tmp_path.iterdir()} == set(files)


def test_capture_once_per_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = build_baseline_exchange(STATUSES).files()
    _write(tmp_path, files)
    captured: list[str] = []
    original = RootReader.capture_relative

    def capture(
        self: RootReader, relative_path: str, *, logical_name: str, max_bytes: int, location: str
    ) -> CapturedBytes:
        captured.append(relative_path)
        return original(
            self, relative_path, logical_name=logical_name, max_bytes=max_bytes, location=location
        )

    monkeypatch.setattr(RootReader, "capture_relative", capture)
    audit_baseline_exchange(tmp_path)
    assert len(captured) == len(set(captured)) == len(files)
    assert set(captured) == set(files)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("missing", "SOURCE_READ_FAILED"),
        ("symlink", "SOURCE_READ_FAILED"),
        ("nonregular", "SOURCE_READ_FAILED"),
        ("tampered_member", "INVALID_INPUT"),
        ("tampered_manifest", "INVALID_INPUT"),
        ("duplicate_key", "INVALID_INPUT"),
        ("malformed_json", "INVALID_INPUT"),
        ("row_order", "INVALID_INPUT"),
        ("row_set", "INVALID_INPUT"),
        ("unsupported_profile", "INVALID_INPUT"),
    ],
)
def test_invalid_exchange_refuses_without_output_or_private_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mutation: str,
    code: str,
) -> None:
    exchange = build_baseline_exchange(STATUSES)
    _write(tmp_path, _invalid_files(exchange, mutation))
    _mutate_member(tmp_path, mutation)

    assert main(_args(tmp_path)) == ERROR_EXIT
    error = _refusal(capsys, code)
    assert str(tmp_path) not in json.dumps(error)
    with pytest.raises((CaptureError, BaselineProjectionError)) as caught:
        audit_baseline_exchange(tmp_path)
    assert caught.value.code == code
    assert PRIVATE not in str(caught.value)


def _invalid_files(exchange: SyntheticBaselineExchange, mutation: str) -> dict[str, bytes]:
    if mutation == "row_order":
        exchange.rows.reverse()
    elif mutation == "row_set":
        exchange.rows[-1]["case_id"] = exchange.rows[0]["case_id"]
    elif mutation == "unsupported_profile":
        exchange.rows[0]["runner"]["profile"] = PRIVATE
    files: dict[str, bytes] = exchange.files()
    if mutation == "tampered_member":
        files["rows.jsonl"] += b" "
    elif mutation == "tampered_manifest":
        files["manifest.json"] = files["manifest.json"].replace(b'"complete"', b'"incomplete"')
    elif mutation == "duplicate_key":
        files["export.json"] = b'{"schema":1,"schema":2}'
    elif mutation == "malformed_json":
        files["rows.jsonl"] = b'{"broken":'
    return files


def _mutate_member(root: Path, mutation: str) -> None:
    if mutation not in {"missing", "symlink", "nonregular"}:
        return
    target = root / "scope.json"
    target.unlink()
    if mutation == "symlink":
        target.symlink_to(root / "export.json")
    elif mutation == "nonregular":
        target.mkdir()


def test_usage_refuses_before_capture_and_command_is_discoverable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from underwrite.cli import audit_baseline

    def unexpected_capture(exchange: Path) -> dict[str, Any]:
        pytest.fail("invalid arguments must not capture source files")

    monkeypatch.setattr(audit_baseline, "audit_baseline_exchange", unexpected_capture)
    args = ["audit-baseline", "--exchange", str(tmp_path), "--direction", "higher", "--json"]
    assert main(args) == ERROR_EXIT
    _refusal(capsys, "USAGE_ERROR")
    assert main(["capabilities", "--json"]) == 0
    capability = json.loads(capsys.readouterr().out)
    command = next(item for item in capability["commands"] if item["command"] == "audit-baseline")
    assert command["profiles"] == [
        {
            "name": "goppi-baseline-exchange-v2",
            "required_inputs": ["exchange"],
            "input_schemas": [],
            "output_schemas": ["evidence_result.v2"],
        }
    ]


@pytest.mark.parametrize(
    ("failure", "code", "reason"),
    [
        ("invalid_result", "INTERNAL_ERROR", "BASELINE_RESULT_INVALID"),
        ("invalid_schema", "SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT"),
    ],
)
def test_generated_contract_faults_are_finite(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    code: str,
    reason: str,
) -> None:
    from underwrite.instrument.ingest import goppi_baseline

    _write(tmp_path, build_baseline_exchange(STATUSES).files())
    if failure == "invalid_result":

        def invalid_result(documents: object) -> dict[str, object]:
            del documents
            return {"private": PRIVATE}

        monkeypatch.setattr(goppi_baseline, "project_baseline_result", invalid_result)
    else:

        def invalid_schema(name: str) -> object:
            del name
            raise SchemaConfigurationError(PRIVATE)

        monkeypatch.setattr(goppi_baseline, "packaged_validator", invalid_schema)

    with pytest.raises(BaselineProjectionError) as caught:
        audit_baseline_exchange(tmp_path)
    assert (caught.value.code, caught.value.reason, caught.value.location) == (
        code,
        reason,
        "/projection",
    )
    assert PRIVATE not in str(caught.value)
    assert main(_args(tmp_path)) == ERROR_EXIT
    error = _refusal(capsys, code)
    assert error["reason"] == reason


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("usage_extra", "BASELINE_SHAPE_INVALID"),
        ("usage_status", "BASELINE_VALUE_INVALID"),
        ("usage_authority", "BASELINE_PROFILE_INVALID"),
        ("aggregate_cost_shape", "BASELINE_SHAPE_INVALID"),
        ("available_duration", "BASELINE_VALUE_INVALID"),
    ],
)
def test_invalid_accounting_fields_refuse_at_api_and_cli_boundaries(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mutation: str,
    reason: str,
) -> None:
    exchange = build_baseline_exchange(STATUSES)
    _invalid_accounting_field(exchange, mutation)
    _write(tmp_path, exchange.files())

    with pytest.raises(BaselineProjectionError) as caught:
        audit_baseline_exchange(tmp_path)
    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.reason == reason
    assert PRIVATE not in str(caught.value)
    assert main(_args(tmp_path)) == ERROR_EXIT
    error = _refusal(capsys, "INVALID_INPUT")
    assert error["reason"] == reason
    assert str(tmp_path) not in json.dumps(error)


def _invalid_accounting_field(exchange: SyntheticBaselineExchange, mutation: str) -> None:
    usage = exchange.rows[0]["usage"]
    if mutation == "usage_extra":
        usage["private"] = PRIVATE
    elif mutation == "usage_status":
        usage["status"] = PRIVATE
    elif mutation == "usage_authority":
        usage["provider_authentication_verified"] = True
    elif mutation == "aggregate_cost_shape":
        exchange.export["aggregate_cost"]["private"] = PRIVATE
    elif mutation == "available_duration":
        attempt = exchange.rows[0]["attempt"]
        attempt.update(
            status="available",
            attempt_id="attempt-1",
            request_digest=exchange.export["source"]["run_id"],
            receipt_digest=exchange.export["source"]["result_digest"],
            termination_reason="completed",
            duration_ms=PRIVATE,
            local_receipt_content_digest_checked=True,
        )

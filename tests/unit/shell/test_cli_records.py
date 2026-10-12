"""Records CLI emits only validated bounded reports or one finite atomic error."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from _repo_paths import repo_root
from jsonschema import Draft202012Validator

from underwrite.cli import records
from underwrite.instrument.ingest.schema import SchemaConfigurationError
from underwrite.service.records import RecordError

ROOT = repo_root(Path(__file__).resolve())
FIXTURES = ROOT / "fixtures/examples/records"
ERROR_EXIT = 2


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def _validator(name: str) -> Validator:
    schema = json.loads((ROOT / "contracts" / f"{name}.schema.json").read_bytes())
    Draft202012Validator.check_schema(schema)
    return cast("Validator", Draft202012Validator(schema))


def _spec(record_id: str = "synthetic.capture-001") -> dict[str, Any]:
    value = json.loads((FIXTURES / "record-spec.json").read_bytes())
    value["record_id"] = record_id
    return value


def _inputs(tmp_path: Path, spec: dict[str, Any] | None = None) -> tuple[Path, Path, Path]:
    source = tmp_path / "input"
    source.mkdir()
    (source / "note.txt").write_bytes((FIXTURES / "note.txt").read_bytes())
    spec_path = source / "record-spec.json"
    spec_path.write_text(json.dumps(spec or _spec()))
    return tmp_path / "store", spec_path, source / "note.txt"


def _invoke(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, Any], str]:
    code = records.main(argv)
    streams = capsys.readouterr()
    payload = json.loads(streams.out if code == 0 else streams.err)
    return code, payload, streams.err if code == 0 else streams.out


def _error(capsys: pytest.CaptureFixture[str], argv: list[str], expected: str) -> dict[str, Any]:
    code, payload, other = _invoke(capsys, argv)
    assert code == ERROR_EXIT and other == ""
    _validator("record_error.v1").validate(payload)
    assert payload["code"] == expected and payload["exit_code"] == ERROR_EXIT
    return payload


def _save_args(root: Path, spec: Path, *, as_json: bool = True) -> list[str]:
    args = ["save", "--root", str(root), "--spec", str(spec)]
    return [*args, "--json"] if as_json else args


def test_save_show_list_and_verify_emit_their_exact_contracts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, spec_path, source = _inputs(tmp_path)
    source_before = source.read_bytes()
    code, saved, other = _invoke(capsys, _save_args(root, spec_path))
    assert code == 0 and other == ""
    _validator("record_view.v1").validate(saved)
    assert saved["record"]["record_id"] == "synthetic.capture-001"
    assert "path" not in saved["record"]["files"][0]
    assert saved["record"]["files"][0]["source_locator"] == "synthetic://records/note"
    assert source.read_bytes() == source_before

    code, shown, other = _invoke(
        capsys,
        ["show", "--root", str(root), "--id", "synthetic.capture-001", "--json"],
    )
    assert code == 0 and other == "" and shown == saved
    _validator("record_view.v1").validate(shown)

    code, catalog, other = _invoke(capsys, ["list", "--root", str(root), "--json"])
    assert code == 0 and other == ""
    _validator("record_catalog.v1").validate(catalog)
    assert catalog["records"][0]["state"] == "complete"

    code, verification, other = _invoke(
        capsys,
        ["verify", "--root", str(root), "--id", "synthetic.capture-001", "--json"],
    )
    assert code == 0 and other == ""
    _validator("record_verification.v1").validate(verification)
    assert verification["valid"] is True and verification["findings"] == []


def test_catalog_distinguishes_unpublished_and_show_verify_refuse_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, spec_path, _ = _inputs(tmp_path)
    assert records.main(_save_args(root, spec_path)) == 0
    capsys.readouterr()
    pending = root / "records/synthetic.pending-001"
    pending.mkdir(mode=0o700)
    code, catalog, _ = _invoke(capsys, ["list", "--root", str(root), "--json"])
    assert code == 0
    assert catalog["records"][-1] == {
        "record_id": "synthetic.pending-001",
        "state": "unpublished",
    }
    for command in ("show", "verify"):
        error = _error(
            capsys,
            [command, "--root", str(root), "--id", "synthetic.pending-001", "--json"],
            "RECORD_NOT_PUBLISHED",
        )
        assert error["reason"] == "RECORD_NOT_PUBLISHED"


def test_existing_id_collision_is_atomic_and_source_is_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, spec_path, source = _inputs(tmp_path)
    before = source.read_bytes()
    assert records.main(_save_args(root, spec_path)) == 0
    first = json.loads(capsys.readouterr().out)
    error = _error(capsys, _save_args(root, spec_path), "RECORD_EXISTS")
    assert error["reason"] == "RECORD_ALREADY_EXISTS"
    assert source.read_bytes() == before
    code, shown, _ = _invoke(
        capsys,
        ["show", "--root", str(root), "--id", "synthetic.capture-001", "--json"],
    )
    assert code == 0 and shown == first


def test_corruption_is_valid_false_data_with_exit_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, spec_path, _ = _inputs(tmp_path)
    assert records.main(_save_args(root, spec_path)) == 0
    capsys.readouterr()
    stored = root / "records/synthetic.capture-001/files/source/note.txt"
    stored.write_text("changed")
    code, report, other = _invoke(
        capsys,
        ["verify", "--root", str(root), "--id", "synthetic.capture-001", "--json"],
    )
    assert code == 0 and other == ""
    _validator("record_verification.v1").validate(report)
    assert report["valid"] is False
    assert report["findings"][0]["code"] in {"FILE_SIZE_MISMATCH", "FILE_DIGEST_MISMATCH"}


def test_direct_parent_link_and_locator_mapping_are_retained_without_source_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, first_spec, _ = _inputs(tmp_path)
    _, parent, _ = _invoke(capsys, _save_args(root, first_spec))
    child_spec = _spec("synthetic.child-001")
    child_spec["links"] = [
        {
            "relation": "input",
            "record_id": "synthetic.capture-001",
            "manifest_digest": parent["manifest_digest"],
        }
    ]
    child_spec["provenance"] = {
        "verified": False,
        "attestation_ref": {
            "scope": "linked_record",
            "record_id": "synthetic.capture-001",
            "manifest_digest": parent["manifest_digest"],
            "name": "source/note.txt",
        },
    }
    child_path = first_spec.parent / "child-spec.json"
    child_path.write_text(json.dumps(child_spec))
    code, child, other = _invoke(capsys, _save_args(root, child_path))
    assert code == 0 and other == ""
    assert child["record"]["links"] == child_spec["links"]
    assert child["record"]["provenance"] == child_spec["provenance"]
    assert child["record"]["files"][0]["source_locator"] == "synthetic://records/note"
    assert "path" not in child["record"]["files"][0]


@pytest.mark.parametrize("fault", ["malformed", "invalid", "oversized"])
def test_bad_specs_are_atomic_typed_and_do_not_echo_source_text(
    fault: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, spec_path, _ = _inputs(tmp_path)
    if fault == "malformed":
        spec_path.write_text('{"private-secret":')
        expected = "INVALID_SPEC"
    elif fault == "invalid":
        spec = _spec()
        spec["files"][0]["name"] = "../private-secret"
        spec_path.write_text(json.dumps(spec))
        expected = "INVALID_SPEC"
    else:
        spec_path.write_text('{"private-secret":"' + "x" * records.SPEC_MAX_BYTES + '"}')
        expected = "INPUT_LIMIT_EXCEEDED"
    error = _error(capsys, _save_args(root, spec_path), expected)
    assert error["location"] == "/spec"
    assert "private-secret" not in json.dumps(error)
    assert not root.exists()


@pytest.mark.parametrize(
    "argv",
    [[], ["save"], ["list"], ["show", "--root", "x"], ["verify", "--root", "x"]],
)
def test_usage_errors_are_atomic(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    error = _error(capsys, [*argv, "--json"], "USAGE_ERROR")
    assert error["reason"] == "INVALID_ARGUMENTS_SEE_HELP"


def test_human_output_escapes_record_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = _spec()
    spec["label"] = "line\n\x1b[31m"
    root, spec_path, _ = _inputs(tmp_path, spec)
    assert records.main(_save_args(root, spec_path, as_json=False)) == 0
    streams = capsys.readouterr()
    assert streams.err == "" and "storage integrity is not" in streams.out
    assert "\\n\\u001b[31m" in streams.out and "\x1b" not in streams.out


def test_output_limit_and_unexpected_failures_remain_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, spec_path, _ = _inputs(tmp_path)
    assert records.main(_save_args(root, spec_path)) == 0
    capsys.readouterr()
    monkeypatch.setattr(records, "OUTPUT_MAX_BYTES", 1)
    error = _error(capsys, ["list", "--root", str(root), "--json"], "OUTPUT_LIMIT_EXCEEDED")
    assert error["location"] == "/output"
    monkeypatch.setattr(records, "OUTPUT_MAX_BYTES", 2 * 1024 * 1024)

    def broken(root: Path) -> dict[str, object]:
        del root
        raise RuntimeError("private-secret")

    monkeypatch.setattr(records, "list_records", broken)
    error = _error(capsys, ["list", "--root", str(root), "--json"], "INTERNAL_ERROR")
    assert "private-secret" not in json.dumps(error)

    def unsafe_service_error(root: Path) -> dict[str, object]:
        del root
        raise RecordError("STORE_IO_ERROR", "/private/source")

    monkeypatch.setattr(records, "list_records", unsafe_service_error)
    error = _error(capsys, ["list", "--root", str(root), "--json"], "INTERNAL_ERROR")
    assert error["location"] == ""


def test_schema_configuration_failure_is_typed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken(name: str) -> object:
        del name
        raise SchemaConfigurationError("private-secret")

    def empty_catalog(root: Path) -> dict[str, object]:
        del root
        return {"schema": "record_catalog.v1", "record_count": 0, "records": []}

    monkeypatch.setattr(records, "list_records", empty_catalog)
    monkeypatch.setattr(records, "packaged_validator", broken)
    error = _error(capsys, ["list", "--root", "unused", "--json"], "SCHEMA_CONFIGURATION_ERROR")
    assert error["reason"] == "INVALID_PACKAGED_CONTRACT"


def test_cli_error_mapping_matches_every_contract_branch() -> None:
    compiled = _validator("record_error.v1")
    contract = json.loads((ROOT / "contracts/record_error.v1.schema.json").read_bytes())
    error_map = records._ERRORS  # pyright: ignore[reportPrivateUsage]
    assert set(error_map) == set(contract["$defs"])
    for code in error_map:
        error = records._CliError(code)  # pyright: ignore[reportPrivateUsage]
        compiled.validate(error.payload)


# Card: record-reuse | Paper: record_document.v1 | Stage: storage-read


def _read_json_args(tmp_path: Path, raw: bytes) -> tuple[list[str], Path]:
    from underwrite.service.records import save_record

    root, spec_path, source = _inputs(tmp_path)
    source.write_bytes(raw)
    manifest = save_record(root, _spec(), spec_path.parent).manifest
    name = cast(list[dict[str, str]], manifest["files"])[0]["name"]
    source.unlink()
    return ["read-json", "--root", str(root), "--id", "synthetic.capture-001", "--name", name], root


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        (b' { "status": "failed", "n": 2 } \n', {"status": "failed", "n": 2}),
        (b"[1,true,null]", [1, True, None]),
        (b'"string"', "string"),
        (b"17", 17),
        (b"true", True),
        (b"null", None),
        (b"1e-400", 0),
        (b"1.00000000000000000000000001", 1),
    ],
)
def test_read_json_accepts_all_admitted_values_and_always_emits_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], raw: bytes, value: object
) -> None:
    from underwrite_core.canonical import canonical_bytes, digest_bytes

    from underwrite.service.records import read_record

    args, root = _read_json_args(tmp_path, raw)
    assert records.main(args) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    output = json.loads(streams.out)
    assert output["content"] == value
    assert output["bytes"] == len(raw) and output["sha256"] == digest_bytes(raw)
    assert output["manifest_digest"] == read_record(root, "synthetic.capture-001").digest
    assert output["verification_scope"] == "selected_file_against_manifest"
    assert output["content_representation"] == "parsed_json"
    assert "valid" not in output and "source_locator" not in output
    _validator("record_document.v1").validate(output)
    assert streams.out.encode() == canonical_bytes(output) + b"\n"
    assert records.main([*args, "--json"]) == 0
    repeated = capsys.readouterr()
    assert repeated == streams


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"binary\x00\xff",
        b'{"a":1,"a":2}',
        b"NaN",
        b"1e400",
        b'"\\ud800"',
        b'"e\\u0301"',
        b"9007199254740993",
        b'{"secret":"PRIVATE-MARKER"',
    ],
)
def test_read_json_refuses_unsupported_content_with_atomic_finite_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], raw: bytes
) -> None:
    args, _ = _read_json_args(tmp_path, raw)
    error = _error(capsys, args, "UNSUPPORTED_RECORD_CONTENT")
    assert error["next_action"] == "SELECT_SUPPORTED_RECORD_FILE"
    assert "PRIVATE-MARKER" not in json.dumps(error)
    assert str(tmp_path) not in json.dumps(error)


@pytest.mark.parametrize("depth", [32, 33])
def test_read_json_nesting_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], depth: int
) -> None:
    args, _ = _read_json_args(tmp_path, b"[" * depth + b"0" + b"]" * depth)
    if depth == records.DOCUMENT_MAX_DEPTH:
        code, _, other = _invoke(capsys, args)
        assert code == 0 and other == ""
    else:
        _error(capsys, args, "INPUT_LIMIT_EXCEEDED")


def test_read_json_checks_full_output_budget_including_newline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _ = _read_json_args(tmp_path, b'{"text":"ok"}')
    assert records.main(args) == 0
    expected = capsys.readouterr().out
    limit = len(expected.encode())
    monkeypatch.setattr(records, "OUTPUT_MAX_BYTES", limit)
    assert records.main(args) == 0
    assert capsys.readouterr().out == expected
    monkeypatch.setattr(records, "OUTPUT_MAX_BYTES", limit - 1)
    _error(capsys, args, "OUTPUT_LIMIT_EXCEEDED")


def test_read_json_checks_integrity_before_content_and_distinguishes_unknown_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args, root = _read_json_args(tmp_path, b"binary")
    selected = root / "records/synthetic.capture-001/files" / args[-1]
    selected.write_bytes(b"broken")
    _error(capsys, args, "RECORD_FILE_INTEGRITY_MISMATCH")
    _error(capsys, [*args[:-1], "unknown.json"], "RECORD_FILE_NOT_FOUND")
    _error(capsys, [*args[:-1], "../escape"], "INVALID_SPEC")


@pytest.mark.parametrize("extra", [[], ["--json"]])
def test_read_json_usage_errors_are_json_without_json_flag(
    capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    _error(capsys, ["read-json", *extra], "USAGE_ERROR")


def test_show_preserves_abbreviated_json_option(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, root = _read_json_args(tmp_path, b"null")
    code, shown, other = _invoke(
        capsys, ["show", "--root", str(root), "--id", "synthetic.capture-001", "--j"]
    )
    assert code == 0 and other == ""
    _validator("record_view.v1").validate(shown)

"""Actual instrument subprocess acceptance, outside mutation-selected ingest tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from _repo_paths import repo_root
from jsonschema import Draft202012Validator
from underwrite_core.canonical import SAFE_INT_MAX, canonical_bytes, content_digest, digest_bytes

from underwrite.cli import instrument as instrument_cli
from underwrite.instrument.ingest.formats import (
    CodecConfigurationError,
    native_codecs,
    trusted_codecs,
)

ROOT = repo_root(Path(__file__).resolve())
CLI = ROOT / ".venv/bin/underwrite"
GOLDEN = ROOT / "fixtures/examples/measurement/bundle.json"
DEEPEVAL = ROOT / "fixtures/golden/external/deepeval/test_run_20260914_132634.json"


def _run(*arguments: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLI), *arguments], cwd=cwd, capture_output=True, text=True, check=False
    )


def _arguments(command: str = "ingest", path: Path = GOLDEN) -> list[str]:
    return [
        command,
        "--format",
        "underwrite.evidence-bundle",
        "--version",
        "v1",
        "--file",
        str(path),
    ]


@pytest.mark.parametrize("command", ["ingest", "project"])
def test_actual_console_emits_one_canonical_observation(command: str) -> None:
    result = _run(*_arguments(command), "--json")
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    observation: dict[str, Any] = json.loads(result.stdout)
    assert result.stdout.encode() == canonical_bytes(observation) + b"\n"
    assert observation["schema"] == "observation.v1"
    assert observation["raw"] == {
        "ref": digest_bytes(GOLDEN.read_bytes()),
        "hash": digest_bytes(GOLDEN.read_bytes()),
    }


def _error(
    result: subprocess.CompletedProcess[str], code: str, reason: str, exit_code: int = 1
) -> None:
    assert result.returncode == exit_code
    assert result.stdout == ""
    actual = json.loads(result.stderr)
    schema = json.loads((ROOT / "contracts/instrument_error.v1.schema.json").read_text())
    Draft202012Validator(schema).validate(actual)  # pyright: ignore[reportUnknownMemberType]
    assert actual == {
        "schema": "instrument_error.v1",
        "command": result.args[1],
        "code": code,
        "reason": reason,
        "exit_code": exit_code,
    }


def test_all_trusted_captures_preserve_explicit_identity_across_aliases_and_routes() -> None:
    covered: set[tuple[str, str]] = set()
    observation_schema = json.loads((ROOT / "contracts/observation.v1.schema.json").read_text())
    manifests = sorted((ROOT / "fixtures/golden/external").glob("*/observations.json"))
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text())
        capture_root = manifest_path.parent
        source_manifest: dict[str, Any] = {"files": {}}
        if "source_manifest" in manifest:
            source_path = manifest_path.parent / manifest["source_manifest"]
            source_manifest = json.loads(source_path.read_text())
            capture_root = source_path.parent
        for name, entry in manifest["files"].items():
            if "source" not in entry:
                entry = source_manifest["files"][name] | entry
            source = entry["source"]
            covered.add((source["format"], source["format_version"]))
            outputs: list[str] = []
            for command in ("ingest", "project"):
                for mode in ("--file", "--http-body-file"):
                    arguments = [
                        command,
                        "--format",
                        source["format"],
                        "--version",
                        source["format_version"],
                        mode,
                        str(capture_root / name),
                        "--source-ref",
                        entry["ref"],
                        "--json",
                    ]
                    if mode == "--http-body-file":
                        arguments += ["--transport-locator", "https://not-fetched.invalid/body"]
                    result = _run(*arguments)
                    assert result.returncode == 0, result.stderr
                    assert result.stderr == ""
                    observation = json.loads(result.stdout)
                    Draft202012Validator(observation_schema).validate(observation)  # pyright: ignore[reportUnknownMemberType]
                    assert observation["raw"] == {"ref": entry["ref"], "hash": entry["raw_hash"]}
                    assert content_digest(observation) == entry["observation_digest"]
                    outputs.append(result.stdout)
            assert len(set(outputs)) == 1
    assert covered == set(trusted_codecs()) - set(native_codecs())


def test_relocation_and_hostile_cwd_cannot_change_identity(tmp_path: Path) -> None:
    moved = tmp_path / "renamed.body"
    moved.write_bytes(GOLDEN.read_bytes())
    hostile = tmp_path / "contracts"
    hostile.mkdir()
    (hostile / "observation.v1.schema.json").write_text("false")
    baseline = _run(*_arguments(), "--json")
    relocated = _run(*_arguments(path=moved), "--json", cwd=tmp_path)
    assert relocated.returncode == 0 and relocated.stderr == ""
    assert baseline.stdout == relocated.stdout
    located = _run(*_arguments(path=moved), "--source-locator", "archive:one", "--json")
    assert located.returncode == 0
    assert json.loads(located.stdout)["raw"]["locator"] == "archive:one"
    assert located.stdout != baseline.stdout


@pytest.mark.parametrize(
    ("format_name", "version"),
    [
        ("unknown", "v1"),
        ("Underwrite.evidence-bundle", "v1"),
        ("underwrite.evidence-bundle", "V1"),
        ("underwrite.evidence-bundle", "v999"),
        ("sample.metrics-table", "v1"),
    ],
)
def test_unsupported_selector_wins_before_missing_file(format_name: str, version: str) -> None:
    for mode in ("--file", "--http-body-file"):
        arguments = [
            "project",
            "--format",
            format_name,
            "--version",
            version,
            mode,
            "/not/an/input",
            "--json",
        ]
        if mode == "--http-body-file":
            arguments += ["--transport-locator", "label"]
        _error(_run(*arguments), "UNSUPPORTED_FORMAT", "UNSUPPORTED_FORMAT")


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--format", "underwrite.evidence-bundle"],
        ["--version", "v1"],
        ["--format", "underwrite.evidence-bundle", "--version", "v1"],
        ["--file", "one", "--http-body-file", "two"],
        ["--unknown", "secret-payload"],
        ["--max-bytes", "0"],
        ["--max-depth", "-1"],
        ["--max-depth", "1.5"],
        ["--max-depth", "bad"],
    ],
)
def test_instrument_usage_has_machine_contract(arguments: list[str]) -> None:
    _error(_run("ingest", "--json", *arguments), "USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP", 2)


def test_body_mode_requires_label_and_file_mode_rejects_it() -> None:
    base = ["ingest", "--format", "underwrite.evidence-bundle", "--version", "v1"]
    for label in ([], ["--transport-locator", "  "]):
        _error(
            _run(*base, "--http-body-file", str(GOLDEN), *label, "--json"),
            "USAGE_ERROR",
            "BODY_TRANSPORT_LOCATOR_REQUIRED",
            2,
        )
    _error(
        _run(*_arguments(), "--transport-locator", "label", "--json"),
        "USAGE_ERROR",
        "TRANSPORT_LOCATOR_REQUIRES_BODY",
        2,
    )


@pytest.mark.parametrize("option", ["--source-ref", "--source-locator"])
@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("", "EMPTY_REFERENCE"),
        (" \t", "EMPTY_REFERENCE"),
        ("cafe\u0301", "NON_NFC_REFERENCE"),
    ],
)
def test_invalid_source_is_not_normalized(option: str, value: str, reason: str) -> None:
    _error(_run(*_arguments(), option, value, "--json"), "INVALID_SOURCE", reason)


@pytest.mark.parametrize(
    ("body", "code", "reason"),
    [
        (b"", "INVALID_INPUT", "INVALID_JSON_INPUT"),
        (b"\xff", "INVALID_INPUT", "INVALID_JSON_INPUT"),
        (b"\xef\xbb\xbf{}", "INVALID_INPUT", "INVALID_JSON_INPUT"),
        (b"{} {}", "INVALID_INPUT", "INVALID_JSON_INPUT"),
        (b'{"nested":{"x":0,"x":1}}', "INVALID_INPUT", "DUPLICATE_JSON_KEY"),
        (b'{"n":NaN}', "INVALID_INPUT", "NON_FINITE_JSON_NUMBER"),
        (b'{"n":Infinity}', "INVALID_INPUT", "NON_FINITE_JSON_NUMBER"),
        (b'{"n":-Infinity}', "INVALID_INPUT", "NON_FINITE_JSON_NUMBER"),
        (b'{"n":1e999}', "INVALID_INPUT", "NON_FINITE_JSON_NUMBER"),
        (b'{"n":"\\ud800"}', "INVALID_INPUT", "INVALID_JSON_INPUT"),
        (b"[]", "INVALID_INPUT", "MALFORMED_EVIDENCE_BUNDLE_PAYLOAD"),
        (b"{}", "UNSUPPORTED_FORMAT", "UNSUPPORTED_FORMAT"),
        (
            b'{"schema_version":"underwrite.evidence-bundle.v1"}',
            "INVALID_INPUT",
            "MALFORMED_EVIDENCE_BUNDLE_PAYLOAD",
        ),
        (b'{"schema":"run.v2","run_id":"id"}', "UNSUPPORTED_FORMAT", "UNSUPPORTED_FORMAT"),
    ],
)
def test_bad_input_has_bounded_typed_diagnostics(
    tmp_path: Path, body: bytes, code: str, reason: str
) -> None:
    path = tmp_path / "negative.json"
    path.write_bytes(body)
    _error(_run(*_arguments(path=path), "--json"), code, reason)


def test_canonical_number_and_nfc_collision_are_not_decode_or_measurement_absence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "negative.json"
    payload = json.loads(GOLDEN.read_bytes())
    payload["diagnostic"] = SAFE_INT_MAX + 1
    path.write_text(json.dumps(payload))
    _error(
        _run(*_arguments(path=path), "--json"),
        "CANONICAL_NUMBER_INCOMPATIBLE",
        "INT_OUT_OF_SAFE_RANGE",
    )
    payload.pop("diagnostic")
    payload.update({"é": 1, "e\u0301": 2})
    path.write_text(json.dumps(payload))
    _error(_run(*_arguments(path=path), "--json"), "INVALID_INPUT", "NFC_KEY_COLLISION")


def test_byte_and_depth_limits_are_processing_failures() -> None:
    size = len(GOLDEN.read_bytes())
    for limit in (size, size + 1):
        result = _run(*_arguments(), "--max-bytes", str(limit), "--json")
        assert result.returncode == 0 and result.stderr == ""
    _error(
        _run(*_arguments(), "--max-bytes", str(size - 1), "--json"),
        "INPUT_LIMIT_EXCEEDED",
        "BYTE_LIMIT_EXCEEDED",
    )
    _error(
        _run(*_arguments(), "--max-depth", "1", "--json"),
        "INPUT_LIMIT_EXCEEDED",
        "DEPTH_LIMIT_EXCEEDED",
    )


def test_missing_and_directory_inputs_are_read_failures(tmp_path: Path) -> None:
    for path, reason in (
        (tmp_path, "SOURCE_NOT_REGULAR_FILE"),
        (tmp_path / "absent", "SOURCE_READ_FAILED"),
    ):
        _error(_run(*_arguments(path=path), "--json"), "SOURCE_READ_FAILED", reason, 2)


def test_human_refs_are_escaped_bounded_but_json_identity_is_unchanged() -> None:
    reference = "urn:controls:\n\r\t\x1b[31m" + "long" * 100
    result = _run(*_arguments(), "--source-ref", reference)
    assert result.returncode == 0 and result.stderr == ""
    assert "Projection only; source claims not verified" in result.stdout
    assert "[truncated]" in result.stdout
    assert "\\n\\r\\t\\u001b" in result.stdout
    assert "\x1b" not in result.stdout
    assert "long" * 100 not in result.stdout
    stored = _run(*_arguments(), "--source-ref", reference, "--json")
    assert json.loads(stored.stdout)["raw"]["ref"] == reference


@pytest.mark.parametrize("command", ["ingest", "project"])
def test_json_help_is_still_ordinary_successful_help(command: str) -> None:
    result = _run(command, "--json", "--help")
    assert result.returncode == 0 and result.stderr == ""
    assert "usage:" in result.stdout and "alias" in result.stdout
    assert "--schema" not in result.stdout
    assert "Supported format/version pairs (exact matches):" in result.stdout
    for format_name, version in sorted(trusted_codecs()):
        assert f"  {format_name} / {version}\n" in result.stdout
    assert "source claims are preserved, not verified" in result.stdout


def test_help_registry_failure_keeps_typed_diagnostic(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken_registry() -> None:
        raise CodecConfigurationError("DUPLICATE_CODEC_SELECTOR")

    monkeypatch.setattr(instrument_cli, "trusted_codecs", broken_registry)
    exit_code = instrument_cli.main("ingest", ["--json", "--help"])
    captured = capsys.readouterr()
    assert captured.out == ""
    diagnostic = json.loads(captured.err)
    assert exit_code == diagnostic["exit_code"]
    assert diagnostic == {
        "schema": "instrument_error.v1",
        "command": "ingest",
        "code": "SCHEMA_CONFIGURATION_ERROR",
        "reason": "DUPLICATE_CODEC_SELECTOR",
        "exit_code": 2,
    }


def test_human_error_omits_untrusted_input_and_provides_help() -> None:
    result = _run("ingest", "--unknown", "\x1b[31msecret-payload")
    assert result.returncode != 0 and result.stdout == ""
    assert "USAGE_ERROR" in result.stderr and "see --help" in result.stderr
    assert "secret-payload" not in result.stderr and "\x1b" not in result.stderr


def test_nonidentity_nfd_is_normalized_but_original_hash_is_not_erased(tmp_path: Path) -> None:
    payload = json.loads(GOLDEN.read_bytes())
    outputs: list[dict[str, Any]] = []
    for name in ("café", "cafe\u0301"):
        payload["extra"] = name
        path = tmp_path / "normalization.json"
        path.write_text(json.dumps(payload, ensure_ascii=False))
        result = _run(*_arguments(path=path), "--source-ref", "source:stable", "--json")
        assert result.returncode == 0 and result.stderr == ""
        outputs.append(json.loads(result.stdout))
    first, second = outputs
    assert first["payload"] == second["payload"]
    assert first["raw"]["hash"] != second["raw"]["hash"]
    assert first["raw"]["ref"] == second["raw"]["ref"] == "source:stable"


def test_source_claims_and_payload_urls_are_opaque() -> None:
    path = ROOT / "fixtures/examples/measurement/bundle.json"
    result = _run(
        "ingest",
        "--format",
        "underwrite.evidence-bundle",
        "--version",
        "v1",
        "--file",
        str(path),
        "--json",
    )
    assert result.returncode == 0 and result.stderr == ""
    observation = json.loads(result.stdout)
    assert observation["payload"] == json.loads(path.read_bytes())
    assert set(observation) == {"schema", "source", "kind", "payload", "raw", "normalization"}


def test_external_deepeval_capture_is_dispatched_only_at_its_pinned_version(
    tmp_path: Path,
) -> None:
    selector = ["ingest", "--format", "deepeval.test-run", "--file", str(DEEPEVAL), "--json"]
    result = _run(*selector, "--version", "4.1.1")
    assert result.returncode == 0, result.stderr
    observation: dict[str, Any] = json.loads(result.stdout)
    assert observation["payload"] == json.loads(DEEPEVAL.read_bytes())
    assert observation["source"] == {"format": "deepeval.test-run", "format_version": "4.1.1"}
    drifted = tmp_path / "drifted.json"
    drifted.write_text('{"testCases": [], "schemaVersion": 2}', encoding="utf-8")
    result = _run(
        "ingest", "--format", "deepeval.test-run", "--version", "4.1.1", "--file", str(drifted)
    )
    assert result.returncode == 1
    assert result.stderr == "ingest: INVALID_INPUT (MALFORMED_DEEPEVAL_PAYLOAD); see --help\n"
    result = _run(*selector, "--version", "4.2.2")
    assert result.returncode == 1
    assert json.loads(result.stderr)["code"] == "UNSUPPORTED_FORMAT"


@pytest.mark.parametrize("field", ["sources", "readings", "availability"])
def test_native_bundle_refuses_scalar_entries(tmp_path: Path, field: str) -> None:
    payload = json.loads(GOLDEN.read_bytes())
    payload[field] = ["unstructured"]
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(payload))
    _error(
        _run(*_arguments(path=path), "--json"), "INVALID_INPUT", "MALFORMED_EVIDENCE_BUNDLE_PAYLOAD"
    )


def test_native_bundle_refuses_blank_run_id(tmp_path: Path) -> None:
    payload = json.loads(GOLDEN.read_bytes())
    payload["manifest"]["run_id"] = " \t"
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(payload))
    _error(
        _run(*_arguments(path=path), "--json"), "INVALID_INPUT", "MALFORMED_EVIDENCE_BUNDLE_PAYLOAD"
    )


def test_native_bundle_refuses_unsupported_payload_profile(tmp_path: Path) -> None:
    payload = json.loads(GOLDEN.read_bytes())
    payload["schema_version"] = "underwrite.evidence-bundle.v2"
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(payload))
    _error(_run(*_arguments(path=path), "--json"), "UNSUPPORTED_FORMAT", "UNSUPPORTED_FORMAT")


def test_native_bundle_preserves_opaque_source_claims(tmp_path: Path) -> None:
    payload = json.loads(GOLDEN.read_bytes())
    payload["vendor_claim"] = {"approved": True, "details": [None, {"score": 99}]}
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(payload))
    result = _run(*_arguments(path=path), "--json")
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    observation = json.loads(result.stdout)
    assert observation["payload"] == payload
    assert observation["kind"] == "eval_run"
    assert "verdict" not in observation

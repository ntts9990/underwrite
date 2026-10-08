"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI_RESULT_SCHEMA_PATH = REPO_ROOT / "contracts" / "cli_result.v1.schema.json"
ALLOWLIST_SCHEMA_PATH = REPO_ROOT / "contracts" / "boundary_allowlist.v1.schema.json"
ALLOWLIST_PATH = REPO_ROOT / "rules" / "boundary-allowlist.json"
_UNCONFIGURED_EXIT = 2


def _validate_result(payload: dict[str, Any]) -> None:
    # jsonschema has no py.typed marker; isolate its untyped method boundary.
    validator = Draft202012Validator(json.loads(CLI_RESULT_SCHEMA_PATH.read_text()))
    validator.validate(payload)  # pyright: ignore[reportUnknownMemberType]


@pytest.mark.parametrize("suffix", ["json", "jsonl"])
@pytest.mark.parametrize(
    "document",
    [
        '{"decision":"approve","decision":"neutral"}',
        '{"nested":[{"decision":"approve","decision":"neutral"}]}',
        '{"decision":"neutral","decis\\u0069on":"neutral"}',
    ],
)
def test_duplicate_keys_cannot_hide_from_scan(tmp_path: Path, suffix: str, document: str) -> None:
    target = tmp_path / f"doc.{suffix}"
    target.write_text(document + "\n", encoding="utf-8")
    result = _run_cli(target.name, "--json", cwd=tmp_path)
    assert result.returncode == _UNCONFIGURED_EXIT
    payload = _payload(result)
    assert payload["state"] == "UNCONFIGURED"
    assert payload["details"]["reason"] == "INVALID_JSON_INPUT"
    assert payload["details"]["invalid"] == [target.name]
    assert result.stderr == ""
    _validate_result(payload)


@pytest.mark.parametrize("contents", [b"{", b"\xff", b'{"entries": [], "entries": []}', b"[]"])
def test_invalid_allowlist_cannot_silently_pass(tmp_path: Path, contents: bytes) -> None:
    (tmp_path / "doc.json").write_text('{"safe":true}', encoding="utf-8")
    (tmp_path / "allowlist.json").write_bytes(contents)
    result = _run_cli("doc.json", "--allowlist", "allowlist.json", "--json", cwd=tmp_path)
    assert result.returncode == _UNCONFIGURED_EXIT
    payload = _payload(result)
    assert payload["details"] == {"invalid": ["allowlist.json"], "reason": "INVALID_JSON_INPUT"}
    _validate_result(payload)
    assert result.stderr == ""


@pytest.mark.parametrize("contents", [b"{", b"\xff"])
def test_invalid_scan_input_has_structured_failure(tmp_path: Path, contents: bytes) -> None:
    (tmp_path / "doc.json").write_bytes(contents)
    result = _run_cli("doc.json", "--json", cwd=tmp_path)
    assert result.returncode == _UNCONFIGURED_EXIT
    payload = _payload(result)
    assert payload["details"] == {"invalid": ["doc.json"], "reason": "INVALID_JSON_INPUT"}
    _validate_result(payload)
    assert result.stderr == ""


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "underwrite.cli.main", "boundary", "scan", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _payload(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return json.loads(result.stdout)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _write_allowlist(path: Path, *, path_glob: str, term: str, expiry: str) -> None:
    _write_json(
        path,
        {
            "schema": "boundary_allowlist.v1",
            "entries": [
                {
                    "path_glob": path_glob,
                    "term": term,
                    "reason": "test fixture",
                    "expiry": expiry,
                    "adr": "example-rule",
                }
            ],
        },
    )


# --- 1: a gate-vocabulary leak exits 1, row names the JSONPath -------------


def test_gate_leak_exits_1_with_jsonpath(tmp_path: Path) -> None:
    _write_json(tmp_path / "doc.json", {"note": "게이트 판정 promote와 관련된 문서"})

    result = _run_cli("doc.json", "--json", cwd=tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    payload = _payload(result)
    assert payload["state"] == "LEAK"
    leaks = payload["details"]["leaks"]
    assert any(row["path"] == "$.note" for row in leaks)
    assert {row["family"] for row in leaks} == {"gate_vocab"}


# --- 2: near-miss English compounds are not leaks ---------------------------


def test_threshold_and_holdout_are_not_leaks(tmp_path: Path) -> None:
    _write_json(tmp_path / "doc.json", {"note": "the threshold and holdout figures differ"})

    result = _run_cli("doc.json", "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = _payload(result)
    assert payload["state"] == "PASS"
    assert payload["details"]["leaks"] == []


# --- 3: JSONL leak on line 3 (0-based line[2]) prefix ------------------------


def test_jsonl_leak_on_line_3_has_line_2_prefix(tmp_path: Path) -> None:
    target = tmp_path / "doc.jsonl"
    lines = [
        json.dumps({"a": "safe"}),
        json.dumps({"b": "also safe"}),
        json.dumps({"c": "certify this run"}),
    ]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = _run_cli("doc.jsonl", "--json", cwd=tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    payload = _payload(result)
    assert payload["state"] == "LEAK"
    leaks = payload["details"]["leaks"]
    assert any(row["path"].startswith("line[2]") for row in leaks)


# --- 4/5/6: allowlist matching ----------------------------------------------


def test_allowlisted_leak_before_expiry_exits_0_with_allowed_row(tmp_path: Path) -> None:
    _write_json(tmp_path / "doc.json", {"note": "certify this"})
    _write_allowlist(
        tmp_path / "allowlist.json", path_glob="doc.json", term="certify", expiry="2099-01-01"
    )

    result = _run_cli(
        "doc.json", "--allowlist", "allowlist.json", "--now", "2026-09-04", "--json", cwd=tmp_path
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = _payload(result)
    assert payload["state"] == "PASS"
    row = payload["details"]["leaks"][0]
    assert row["allowed"] is True
    assert row["reason"] == "test fixture"


def test_allowlisted_leak_after_expiry_stays_leak_exit_1(tmp_path: Path) -> None:
    _write_json(tmp_path / "doc.json", {"note": "certify this"})
    _write_allowlist(
        tmp_path / "allowlist.json", path_glob="doc.json", term="certify", expiry="2020-01-01"
    )

    result = _run_cli(
        "doc.json", "--allowlist", "allowlist.json", "--now", "2026-09-04", "--json", cwd=tmp_path
    )

    assert result.returncode == 1, result.stdout + result.stderr
    payload = _payload(result)
    assert payload["state"] == "LEAK"
    row = payload["details"]["leaks"][0]
    assert row["allowed"] is False
    assert row["reason"] == "allow_expired"


def test_allowlist_present_without_now_is_now_required_exit_1(tmp_path: Path) -> None:
    _write_json(tmp_path / "doc.json", {"note": "certify this"})
    _write_allowlist(
        tmp_path / "allowlist.json", path_glob="doc.json", term="certify", expiry="2099-01-01"
    )

    result = _run_cli("doc.json", "--allowlist", "allowlist.json", "--json", cwd=tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    payload = _payload(result)
    assert payload["state"] == "NOW_REQUIRED"


# --- 7: missing path is UNCONFIGURED exit 2 ---------------------------------


def test_missing_path_is_unconfigured_exit_2(tmp_path: Path) -> None:
    result = _run_cli("does-not-exist.json", "--json", cwd=tmp_path)

    assert result.returncode == _UNCONFIGURED_EXIT, result.stdout + result.stderr
    payload = _payload(result)
    assert payload["state"] == "UNCONFIGURED"


# --- 8: directory with no JSON is EMPTY_POPULATION exit 1 ------------------


def test_directory_with_no_json_is_empty_population_exit_1(tmp_path: Path) -> None:
    (tmp_path / "no-json").mkdir()
    (tmp_path / "no-json" / "notes.txt").write_text("no json here", encoding="utf-8")

    result = _run_cli("no-json", "--json", cwd=tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    payload = _payload(result)
    assert payload["state"] == "EMPTY_POPULATION"


# --- 9: a *.schema.json inside the directory is skipped --------------------


def test_schema_json_file_is_skipped(tmp_path: Path) -> None:
    _write_json(tmp_path / "contracts-like" / "thing.schema.json", {"decision": "approve"})

    result = _run_cli("contracts-like", "--json", cwd=tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    payload = _payload(result)
    assert payload["state"] == "EMPTY_POPULATION"


# --- 10: --json validates against contracts/cli_result.v1.schema.json ------


def test_json_payload_validates_against_cli_result_schema(tmp_path: Path) -> None:
    _write_json(tmp_path / "doc.json", {"note": "nothing to see here"})

    result = _run_cli("doc.json", "--json", cwd=tmp_path)

    schema = json.loads(CLI_RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_payload(result))  # pyright: ignore[reportUnknownMemberType]


# --- 11: rules/boundary-allowlist.json validates against its own schema ----


def test_real_allowlist_validates_against_its_schema() -> None:
    schema = json.loads(ALLOWLIST_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    data: dict[str, Any] = {"schema": "boundary_allowlist.v1", "entries": []}

    Draft202012Validator(schema).validate(data)  # pyright: ignore[reportUnknownMemberType]

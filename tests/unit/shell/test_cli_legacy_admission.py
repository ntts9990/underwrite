"""Legacy CLI inputs refuse ambiguity and invalid dates before interpretation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((ROOT / "contracts/cli_result.v1.schema.json").read_text())
REFUSAL_EXIT = 2
CONFIG_LIMIT = 1024 * 1024
SCAN_LIMIT = 4 * 1024 * 1024
ENTRY_LIMIT = 1024


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "underwrite.cli.main", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _refusal(result: subprocess.CompletedProcess[str], command: str) -> dict[str, Any]:
    assert result.returncode == REFUSAL_EXIT, result.stdout + result.stderr
    assert result.stderr == ""
    payload: dict[str, Any] = json.loads(result.stdout)
    assert payload["command"] == command and payload["state"] == "UNCONFIGURED"
    Draft202012Validator(SCHEMA).validate(payload)  # pyright: ignore[reportUnknownMemberType]
    return payload


def test_duplicate_exemption_cannot_choose_later_future_expiry(tmp_path: Path) -> None:
    selected = tmp_path / "exemptions.json"
    selected.write_text(
        '{"schema":"exemptions.v1","exemptions":{'
        '"k":{"reason":"old","expiry":"2020-01-01"},'
        '"k":{"reason":"new","expiry":"2099-01-01"}}}',
        encoding="utf-8",
    )
    payload = _refusal(
        _run(
            "absence",
            "--root",
            str(tmp_path),
            "--glob",
            "*.missing",
            "--min",
            "1",
            "--exemptions",
            str(selected),
            "--exemption-key",
            "k",
            "--now",
            "2026-10-15",
            "--json",
        ),
        "absence",
    )
    assert payload["details"]["reason"] == "INVALID_JSON_INPUT"


def test_boundary_nonfinite_document_cannot_pass(tmp_path: Path) -> None:
    selected = tmp_path / "nonfinite.json"
    selected.write_bytes(b'{"score":NaN}')
    payload = _refusal(_run("boundary", "scan", str(selected), "--json"), "boundary scan")
    assert payload["details"] == {"invalid": [str(selected)], "reason": "INVALID_JSON_INPUT"}


def test_boundary_invalid_matched_expiry_is_finite(tmp_path: Path) -> None:
    selected = tmp_path / "leak.json"
    selected.write_text('{"note":"certify this"}', encoding="utf-8")
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(
        json.dumps(
            {
                "schema": "boundary_allowlist.v1",
                "entries": [
                    {
                        "path_glob": str(selected),
                        "term": "certify",
                        "reason": "test",
                        "expiry": "2026-02-30",
                        "adr": "example",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    payload = _refusal(
        _run(
            "boundary",
            "scan",
            str(selected),
            "--allowlist",
            str(allowlist),
            "--now",
            "2026-10-15",
            "--json",
        ),
        "boundary scan",
    )
    assert payload["details"]["reason"] == "INVALID_DATE"


def test_absence_supplied_invalid_now_refuses_even_when_threshold_passes(tmp_path: Path) -> None:
    selected = tmp_path / "present.txt"
    selected.write_text("present", encoding="utf-8")
    payload = _refusal(
        _run(
            "absence",
            "--root",
            str(tmp_path),
            "--glob",
            "*.txt",
            "--min",
            "1",
            "--now",
            "2026-02-30",
            "--json",
        ),
        "absence",
    )
    assert payload["details"]["reason"] == "INVALID_DATE"


def _absence(tmp_path: Path, config: Path, *, now: str = "2026-10-15") -> dict[str, Any]:
    return _refusal(
        _run(
            "absence",
            "--root",
            str(tmp_path),
            "--glob",
            "*.missing",
            "--min",
            "1",
            "--exemptions",
            str(config),
            "--exemption-key",
            "k",
            "--now",
            now,
            "--json",
        ),
        "absence",
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema":"exemptions.v1","exemptions":{"k":{},"\\u006b":{}}}',
        b'{"schema":"exemptions.v1","exemptions":{"k":{"reason":NaN}}}',
        b'{"schema":"exemptions.v1","exemptions":{"k":{"reason":"\\ud800"}}}',
        b"\xff",
    ],
)
def test_exemption_config_strict_json_refusal(tmp_path: Path, raw: bytes) -> None:
    selected = tmp_path / "exemptions.json"
    selected.write_bytes(raw)
    assert _absence(tmp_path, selected)["details"] == {
        "reason": "INVALID_JSON_INPUT",
        "location": "/exemptions",
    }


@pytest.mark.parametrize(
    "entry",
    [
        {"reason": "", "expiry": "2099-01-01"},
        {"reason": 5, "expiry": "2099-01-01"},
        {"reason": "valid", "expiry": "2099-01-01", "adr": ""},
    ],
)
def test_unselected_bad_exemption_refuses_even_when_absence_would_pass(
    tmp_path: Path, entry: dict[str, object]
) -> None:
    (tmp_path / "present.txt").write_text("present", encoding="utf-8")
    selected = tmp_path / "exemptions.json"
    selected.write_text(json.dumps({"schema": "exemptions.v1", "exemptions": {"unused": entry}}))
    payload = _refusal(
        _run(
            "absence",
            "--root",
            str(tmp_path),
            "--glob",
            "*.txt",
            "--min",
            "1",
            "--exemptions",
            str(selected),
            "--json",
        ),
        "absence",
    )
    assert payload["details"] == {"reason": "INVALID_EXEMPTION", "location": "/exemptions"}


def test_unselected_bad_exemption_date_refuses(tmp_path: Path) -> None:
    selected = tmp_path / "exemptions.json"
    selected.write_text(
        json.dumps(
            {
                "schema": "exemptions.v1",
                "exemptions": {
                    "unused": {"reason": "test", "expiry": "2026-02-30"},
                },
            }
        )
    )
    assert _absence(tmp_path, selected)["details"] == {
        "reason": "INVALID_DATE",
        "location": "/exemptions",
    }


@pytest.mark.parametrize(
    "kind,raw,limit",
    [
        ("bytes", b" " * (CONFIG_LIMIT + 1), CONFIG_LIMIT),
        ("depth", b"[" * 65 + b"0" + b"]" * 65, 64),
    ],
    ids=["bytes", "depth"],
)
def test_exemption_config_limits_are_finite(
    tmp_path: Path, kind: str, raw: bytes, limit: int
) -> None:
    selected = tmp_path / "exemptions.json"
    selected.write_bytes(raw)
    assert _absence(tmp_path, selected)["details"] == {
        "reason": "INPUT_LIMIT_EXCEEDED",
        "location": "/exemptions",
        "limit_kind": kind,
        "limit": limit,
    }


def test_exemption_entry_limit_is_per_config(tmp_path: Path) -> None:
    selected = tmp_path / "exemptions.json"
    entries = {
        f"entry-{i}": {"reason": "test", "expiry": "2099-01-01"} for i in range(ENTRY_LIMIT + 1)
    }
    selected.write_text(json.dumps({"schema": "exemptions.v1", "exemptions": entries}))
    assert _absence(tmp_path, selected)["details"] == {
        "reason": "INPUT_LIMIT_EXCEEDED",
        "location": "/exemptions",
        "limit_kind": "entries",
        "limit": ENTRY_LIMIT,
    }


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_exemption_source_failure_is_finite(tmp_path: Path, kind: str) -> None:
    selected = tmp_path / "exemptions.json"
    if kind == "directory":
        selected.mkdir()
    assert _absence(tmp_path, selected)["details"] == {
        "reason": "SOURCE_READ_FAILED",
        "location": "/exemptions",
    }


def _boundary(tmp_path: Path, allowlist: Path, *, now: str | None = None) -> dict[str, Any]:
    selected = tmp_path / "safe.json"
    selected.write_text('{"safe":true}', encoding="utf-8")
    args = ["boundary", "scan", str(selected), "--allowlist", str(allowlist)]
    if now is not None:
        args.extend(["--now", now])
    return _refusal(_run(*args, "--json"), "boundary scan")


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema":"boundary_allowlist.v1","entries":NaN}',
        b'{"schema":"boundary_allowlist.v1","entries":Infinity}',
        b'{"schema":"boundary_allowlist.v1","entries":"\\ud800"}',
    ],
)
def test_allowlist_nonstandard_json_uses_existing_invalid_json_detail(
    tmp_path: Path, raw: bytes
) -> None:
    selected = tmp_path / "allowlist.json"
    selected.write_bytes(raw)
    assert _boundary(tmp_path, selected)["details"] == {
        "invalid": [str(selected)],
        "reason": "INVALID_JSON_INPUT",
    }


def test_unused_invalid_allowlist_rule_refuses(tmp_path: Path) -> None:
    selected = tmp_path / "allowlist.json"
    selected.write_text(
        json.dumps(
            {
                "schema": "boundary_allowlist.v1",
                "entries": [
                    {
                        "path_glob": "nomatch",
                        "term": "certify",
                        "reason": "test",
                        "expiry": "2099-01-01",
                        "adr": "",
                    }
                ],
            }
        )
    )
    assert _boundary(tmp_path, selected)["details"] == {
        "reason": "INVALID_ALLOWLIST",
        "location": "/allowlist",
    }


def test_unused_invalid_allowlist_date_refuses(tmp_path: Path) -> None:
    selected = tmp_path / "allowlist.json"
    selected.write_text(
        json.dumps(
            {
                "schema": "boundary_allowlist.v1",
                "entries": [
                    {
                        "path_glob": "nomatch",
                        "term": "certify",
                        "reason": "test",
                        "expiry": "2026-02-30",
                        "adr": "example",
                    }
                ],
            }
        )
    )
    assert _boundary(tmp_path, selected)["details"] == {
        "reason": "INVALID_DATE",
        "location": "/allowlist",
    }


def test_allowlist_entry_limit_refuses(tmp_path: Path) -> None:
    selected = tmp_path / "allowlist.json"
    entry = {
        "path_glob": "nomatch",
        "term": "certify",
        "reason": "test",
        "expiry": "2099-01-01",
        "adr": "example",
    }
    selected.write_text(
        json.dumps({"schema": "boundary_allowlist.v1", "entries": [entry] * (ENTRY_LIMIT + 1)})
    )
    assert _boundary(tmp_path, selected)["details"] == {
        "reason": "INPUT_LIMIT_EXCEEDED",
        "location": "/allowlist",
        "limit_kind": "entries",
        "limit": ENTRY_LIMIT,
    }


@pytest.mark.parametrize(
    "raw,kind,limit",
    [
        (b" " * (CONFIG_LIMIT + 1), "bytes", CONFIG_LIMIT),
        (b"[" * 65 + b"0" + b"]" * 65, "depth", 64),
    ],
    ids=["bytes", "depth"],
)
def test_allowlist_config_limit_refuses(tmp_path: Path, raw: bytes, kind: str, limit: int) -> None:
    selected = tmp_path / "allowlist.json"
    selected.write_bytes(raw)
    assert _boundary(tmp_path, selected)["details"] == {
        "reason": "INPUT_LIMIT_EXCEEDED",
        "location": "/allowlist",
        "limit_kind": kind,
        "limit": limit,
    }


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_allowlist_source_failure_is_finite(tmp_path: Path, kind: str) -> None:
    selected = tmp_path / "allowlist.json"
    if kind == "directory":
        selected.mkdir()
    assert _boundary(tmp_path, selected)["details"] == {
        "reason": "SOURCE_READ_FAILED",
        "location": "/allowlist",
    }


@pytest.mark.parametrize(
    "raw,kind,limit",
    [
        (b" " * (SCAN_LIMIT + 1), "bytes", SCAN_LIMIT),
        (b"[" * 65 + b"0" + b"]" * 65, "depth", 64),
    ],
    ids=["bytes", "depth"],
)
def test_scanned_file_per_file_limit_refuses(
    tmp_path: Path, raw: bytes, kind: str, limit: int
) -> None:
    selected = tmp_path / "scan.json"
    selected.write_bytes(raw)
    payload = _refusal(_run("boundary", "scan", str(selected), "--json"), "boundary scan")
    assert payload["details"] == {
        "reason": "INPUT_LIMIT_EXCEEDED",
        "location": "/files",
        "limit_kind": kind,
        "limit": limit,
    }


def test_jsonl_blank_line_keeps_physical_coordinate(tmp_path: Path) -> None:
    selected = tmp_path / "lines.jsonl"
    selected.write_bytes(b'{"safe":true}\n\n{"note":"certify this"}\n')
    result = _run("boundary", "scan", str(selected), "--json")
    assert result.returncode == 1 and result.stderr == ""
    payload: dict[str, Any] = json.loads(result.stdout)
    assert payload["details"]["leaks"][0]["path"].startswith("line[2]")


@pytest.mark.parametrize(
    "raw",
    [
        b"42",
        b"true",
        b'[1,2,{"safe":true}]',
        ('{"decomposed":"e\u0301","large":' + str(10**1000) + "}").encode(),
    ],
)
def test_scanner_preserves_arbitrary_valid_json_roots(tmp_path: Path, raw: bytes) -> None:
    selected = tmp_path / "valid.json"
    selected.write_bytes(raw)
    result = _run("boundary", "scan", str(selected), "--json")
    assert result.returncode == 0 and result.stderr == ""
    payload: dict[str, Any] = json.loads(result.stdout)
    assert payload["state"] == "PASS"


@pytest.mark.parametrize("now", ["20260904", "2026-W36-5", "2026-02-30", "２０２６-09-04"])
@pytest.mark.parametrize("command", ["boundary", "drift"])
def test_invalid_supplied_now_refuses_for_other_legacy_commands(
    tmp_path: Path, command: str, now: str
) -> None:
    if command == "boundary":
        selected = tmp_path / "safe.json"
        selected.write_text('{"safe":true}', encoding="utf-8")
        result = _run("boundary", "scan", str(selected), "--now", now, "--json")
        expected = "boundary scan"
    else:
        selected = tmp_path / "pins"
        selected.mkdir()
        result = _run(
            "drift",
            "check",
            "--pins-root",
            str(selected),
            "--siblings-root",
            str(tmp_path),
            "--now",
            now,
            "--json",
        )
        expected = "drift check"
    assert _refusal(result, expected)["details"] == {"reason": "INVALID_DATE", "location": "/now"}


def test_drift_exemption_config_is_eagerly_validated(tmp_path: Path) -> None:
    root = tmp_path / "pins"
    root.mkdir()
    selected = tmp_path / "exemptions.json"
    selected.write_text(
        '{"schema":"exemptions.v1","exemptions":{"unused":{"reason":"a",'
        '"expiry":"2099-01-01"},"unused":{"reason":"b","expiry":"2099-01-01"}}}',
        encoding="utf-8",
    )
    payload = _refusal(
        _run(
            "drift",
            "check",
            "--pins-root",
            str(root),
            "--siblings-root",
            str(tmp_path),
            "--exemptions",
            str(selected),
            "--json",
        ),
        "drift check",
    )
    assert payload["details"] == {"reason": "INVALID_JSON_INPUT", "location": "/exemptions"}


def test_authored_exemption_contract_allows_nonempty_optional_adr(tmp_path: Path) -> None:
    selected = tmp_path / "exemptions.json"
    selected.write_text(
        json.dumps(
            {
                "schema": "exemptions.v1",
                "exemptions": {
                    "unused": {"reason": "valid", "expiry": "2099-01-01", "adr": " "},
                },
            }
        )
    )
    (tmp_path / "present.txt").write_text("present", encoding="utf-8")
    result = _run(
        "absence",
        "--root",
        str(tmp_path),
        "--glob",
        "*.txt",
        "--min",
        "1",
        "--exemptions",
        str(selected),
        "--json",
    )
    assert result.returncode == 0 and result.stderr == ""
    assert json.loads(result.stdout)["state"] == "PASS"


def test_authored_allowlist_contract_allows_nonempty_whitespace_fields(tmp_path: Path) -> None:
    selected = tmp_path / "allowlist.json"
    selected.write_text(
        json.dumps(
            {
                "schema": "boundary_allowlist.v1",
                "entries": [
                    {
                        "path_glob": " ",
                        "term": " ",
                        "reason": " ",
                        "expiry": "2099-01-01",
                        "adr": " ",
                    }
                ],
            }
        )
    )
    safe = tmp_path / "safe.json"
    safe.write_text('{"safe":true}', encoding="utf-8")
    result = _run("boundary", "scan", str(safe), "--allowlist", str(selected), "--json")
    assert result.returncode == 0 and result.stderr == ""
    assert json.loads(result.stdout)["state"] == "PASS"


@pytest.mark.parametrize("policy", ["exemptions", "allowlist"])
def test_authored_policy_contract_rejects_extra_fields(tmp_path: Path, policy: str) -> None:
    selected = tmp_path / f"{policy}.json"
    if policy == "exemptions":
        selected.write_text(
            json.dumps(
                {
                    "schema": "exemptions.v1",
                    "exemptions": {
                        "k": {"reason": "test", "expiry": "2099-01-01", "extra": "unowned"},
                    },
                }
            )
        )
        details = _absence(tmp_path, selected)["details"]
        expected = {"reason": "INVALID_EXEMPTION", "location": "/exemptions"}
    else:
        selected.write_text(
            json.dumps(
                {
                    "schema": "boundary_allowlist.v1",
                    "entries": [
                        {
                            "path_glob": "safe.json",
                            "term": "certify",
                            "reason": "test",
                            "expiry": "2099-01-01",
                            "adr": "example",
                            "extra": "unowned",
                        }
                    ],
                }
            )
        )
        details = _boundary(tmp_path, selected)["details"]
        expected = {"reason": "INVALID_ALLOWLIST", "location": "/allowlist"}
    assert details == expected

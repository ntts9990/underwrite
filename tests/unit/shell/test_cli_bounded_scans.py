"""Bounded legacy scans refuse excess without publishing partial results."""

from __future__ import annotations

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from underwrite.cli import boundary, commands, render
from underwrite.cli.main import main

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((ROOT / "contracts/cli_result.v1.schema.json").read_text())
REFUSAL_EXIT = 2
TWO_FILES = 2
FOUR_FILES = 4
HUMAN_BUDGET = 500


def _invoke(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    code = main(args)
    output = capsys.readouterr()
    assert output.err == ""
    payload: dict[str, Any] = json.loads(output.out)
    assert output.out.count('"schema": "cli_result.v1"') == 1
    Draft202012Validator(SCHEMA).validate(payload)  # pyright: ignore[reportUnknownMemberType]
    return code, payload


def _limit(args: list[str], capsys: pytest.CaptureFixture[str], kind: str, limit: int) -> None:
    code, payload = _invoke(args, capsys)
    assert code == REFUSAL_EXIT
    assert payload["state"] == "UNCONFIGURED" and payload["exit_code"] == REFUSAL_EXIT
    assert payload["details"]["reason"] == "INPUT_LIMIT_EXCEEDED"
    assert payload["details"]["limit_kind"] == kind
    assert payload["details"]["limit"] == limit


def test_selected_file_cap_refuses_before_a_partial_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.json").write_text('{"safe":true}')
    (tmp_path / "b.json").write_text('{"safe":true}')
    monkeypatch.setattr(boundary, "SELECTED_FILES_MAX", 1, raising=False)
    _limit(["boundary", "scan", str(tmp_path), "--json"], capsys, "selected_files", 1)


def test_visited_entry_cap_counts_ignored_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "notes.txt").write_text("ignored")
    (tmp_path / "safe.json").write_text('{"safe":true}')
    monkeypatch.setattr(boundary, "VISITED_ENTRIES_MAX", 1, raising=False)
    _limit(["boundary", "scan", str(tmp_path), "--json"], capsys, "directory_entries", 1)


def test_cumulative_capture_cap_counts_files_and_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "first.json").write_bytes(b"{}")
    (tmp_path / "second.json").write_bytes(b"{}")
    config = tmp_path / "allowlist.txt"
    config.write_bytes(b'{"schema":"boundary_allowlist.v1","entries":[]}')
    monkeypatch.setattr(boundary, "CAPTURED_MAX_BYTES", config.stat().st_size + 3, raising=False)
    original = boundary.read_bytes
    budgets: list[int] = []

    def tracked(path: Path, max_bytes: int, location: str) -> bytes:
        budgets.append(max_bytes)
        return original(path, max_bytes, location)

    monkeypatch.setattr(boundary, "read_bytes", tracked)
    _limit(
        ["boundary", "scan", str(tmp_path), "--allowlist", str(config), "--json"],
        capsys,
        "captured_bytes",
        config.stat().st_size + 3,
    )
    assert budgets == [boundary.CONFIG_MAX_BYTES, 3, 1]


def test_jsonl_line_and_physical_line_caps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    selected = tmp_path / "lines.jsonl"
    selected.write_bytes(b"{}\n\n{}\n")
    monkeypatch.setattr(boundary, "JSONL_LINES_MAX", 2, raising=False)
    _limit(["boundary", "scan", str(selected), "--json"], capsys, "lines", 2)
    monkeypatch.setattr(boundary, "JSONL_LINES_MAX", 100_000, raising=False)
    monkeypatch.setattr(boundary, "JSONL_LINE_MAX_BYTES", 2, raising=False)
    selected.write_bytes(b'{"safe":true}\n')
    _limit(["boundary", "scan", str(selected), "--json"], capsys, "line_bytes", 2)


def test_leak_cap_includes_allowed_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    selected = tmp_path / "leak.json"
    selected.write_text('{"note":"promote promote"}')
    monkeypatch.setattr(boundary, "LEAKS_MAX", 1, raising=False)
    _limit(["boundary", "scan", str(selected), "--json"], capsys, "leaks", 1)


def test_selected_render_budget_changes_actual_main_exit_and_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    selected = tmp_path / "leak.json"
    selected.write_text('{"note":"certify this"}')
    monkeypatch.setattr(render, "OUTPUT_MAX_BYTES", 400, raising=False)
    _limit(["boundary", "scan", str(selected), "--json"], capsys, "output_bytes", 400)


def test_expired_adr_is_not_charged_to_human_output_lower_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    selected = tmp_path / "leak.json"
    selected.write_text('{"note":"certify this"}')
    allowlist = tmp_path / "allowlist.txt"
    allowlist.write_text(
        json.dumps(
            {
                "schema": "boundary_allowlist.v1",
                "entries": [
                    {
                        "path_glob": str(selected),
                        "term": "certify",
                        "reason": "test",
                        "expiry": "2020-01-01",
                        "adr": "a" * 1000,
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(boundary, "OUTPUT_MAX_BYTES", HUMAN_BUDGET)
    monkeypatch.setattr(render, "OUTPUT_MAX_BYTES", HUMAN_BUDGET)
    code = main(
        ["boundary", "scan", str(selected), "--allowlist", str(allowlist), "--now", "2026-10-15"]
    )
    output = capsys.readouterr()
    assert code == 1 and output.err == ""
    assert "allow_expired" in output.out and "a" * 1000 not in output.out
    assert len(output.out.encode()) <= HUMAN_BUDGET


def test_absence_matched_file_cap_refuses_without_false_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    monkeypatch.setattr(commands, "SELECTED_FILES_MAX", 1, raising=False)
    _limit(
        ["absence", "--root", str(tmp_path), "--glob", "*.txt", "--min", "1", "--json"],
        capsys,
        "selected_files",
        1,
    )


def test_path_object_order_and_duplicate_root_arguments_are_preserved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "a"
    directory.mkdir()
    (directory / "file.json").write_text('{"note":"promote"}')
    (tmp_path / "a.json").write_text('{"note":"reject"}')
    code, payload = _invoke(["boundary", "scan", str(tmp_path), str(tmp_path), "--json"], capsys)
    assert code == 1
    assert payload["details"]["files_scanned"] == FOUR_FILES
    assert [row["file"] for row in payload["details"]["leaks"]] == [
        str(directory / "file.json"),
        str(tmp_path / "a.json"),
        str(directory / "file.json"),
        str(tmp_path / "a.json"),
    ]


def test_explicit_directory_link_and_regular_file_link_but_no_child_directory_link(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    selected = tmp_path / "real"
    selected.mkdir()
    source = selected / "source.json"
    source.write_text('{"note":"promote"}')
    (selected / "copy.json").symlink_to(source)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hidden.json").write_text('{"note":"reject"}')
    (selected / "child-link").symlink_to(outside, target_is_directory=True)
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(selected, target_is_directory=True)

    code, payload = _invoke(["boundary", "scan", str(linked_root), "--json"], capsys)

    assert code == 1 and payload["details"]["files_scanned"] == TWO_FILES
    assert {row["file"] for row in payload["details"]["leaks"]} == {
        str(linked_root / "source.json"),
        str(linked_root / "copy.json"),
    }


def test_walk_error_is_finite_without_empty_population(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "safe.json").write_text('{"safe":true}')

    def denied(_path: Path) -> object:
        raise PermissionError("private path and host text")

    monkeypatch.setattr(boundary.os, "scandir", denied)
    code, payload = _invoke(["boundary", "scan", str(tmp_path), "--json"], capsys)
    assert code == REFUSAL_EXIT
    assert payload["details"] == {"reason": "SOURCE_READ_FAILED", "location": "/files"}


@pytest.mark.parametrize(
    "separator",
    [
        "\r\n",
        "\n",
        "\r",
        "\v",
        "\f",
        "\x1c",
        "\x1d",
        "\x1e",
        "\x85",
        "\u2028",
        "\u2029",
    ],
)
def test_jsonl_separator_matches_splitlines_and_keeps_physical_index(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], separator: str
) -> None:
    selected = tmp_path / "separators.jsonl"
    selected.write_text("{}" + separator + separator + '{"note":"certify"}')
    code, payload = _invoke(["boundary", "scan", str(selected), "--json"], capsys)
    assert code == 1
    assert payload["details"]["leaks"][0]["path"] == "line[2].note"


def test_jsonl_delimiter_bytes_count_at_and_above_line_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    selected = tmp_path / "line.jsonl"
    selected.write_bytes(b"{}\r\n")
    monkeypatch.setattr(boundary, "JSONL_LINE_MAX_BYTES", 4)
    code, payload = _invoke(["boundary", "scan", str(selected), "--json"], capsys)
    assert code == 0 and payload["state"] == "PASS"
    monkeypatch.setattr(boundary, "JSONL_LINE_MAX_BYTES", 3)
    _limit(["boundary", "scan", str(selected), "--json"], capsys, "line_bytes", 3)


def test_cumulative_exact_limit_and_empty_next_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "one.json").write_bytes(b"{}")
    (tmp_path / "two.json").write_bytes(b"{}")
    monkeypatch.setattr(boundary, "CAPTURED_MAX_BYTES", 4)
    code, payload = _invoke(["boundary", "scan", str(tmp_path), "--json"], capsys)
    assert code == 0 and payload["details"]["files_scanned"] == TWO_FILES
    (tmp_path / "two.json").write_bytes(b"")
    monkeypatch.setattr(boundary, "CAPTURED_MAX_BYTES", 2)
    # An empty captured file is still malformed JSON, not a false PASS or a budget error.
    code, payload = _invoke(["boundary", "scan", str(tmp_path), "--json"], capsys)
    assert code == REFUSAL_EXIT
    assert payload["details"] == {
        "invalid": [str(tmp_path / "two.json")],
        "reason": "INVALID_JSON_INPUT",
    }


def test_exact_render_budget_preserves_original_bytes_then_refuses_one_less(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    selected = tmp_path / "leak.json"
    selected.write_text('{"note":"certify"}')
    _, report = boundary.cmd_boundary_scan(Namespace(paths=[selected], allowlist=None, now=None))
    expected = json.dumps(report, indent=2) + "\n"
    size = len(expected.encode())
    monkeypatch.setattr(render, "OUTPUT_MAX_BYTES", size)
    code = main(["boundary", "scan", str(selected), "--json"])
    output = capsys.readouterr()
    assert code == 1 and output.out == expected and output.err == ""
    monkeypatch.setattr(render, "OUTPUT_MAX_BYTES", size - 1)
    _limit(["boundary", "scan", str(selected), "--json"], capsys, "output_bytes", size - 1)


def test_exact_selected_and_visited_caps_allow_complete_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / "b.json").write_text("{}")
    monkeypatch.setattr(boundary, "SELECTED_FILES_MAX", 2)
    monkeypatch.setattr(boundary, "VISITED_ENTRIES_MAX", 2)
    code, payload = _invoke(["boundary", "scan", str(tmp_path), "--json"], capsys)
    assert code == 0 and payload["details"]["files_scanned"] == TWO_FILES


def test_malformed_later_jsonl_line_refuses_without_partial_leaks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    selected = tmp_path / "mixed.jsonl"
    selected.write_bytes(b'{"note":"certify"}\n{"bad":NaN}\n')
    code, payload = _invoke(["boundary", "scan", str(selected), "--json"], capsys)
    assert code == REFUSAL_EXIT
    assert payload["details"] == {"invalid": [str(selected)], "reason": "INVALID_JSON_INPUT"}


def test_leak_cap_counts_allowed_matches_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    selected = tmp_path / "allowed.json"
    selected.write_text('{"note":"certify certify"}')
    config = tmp_path / "allowlist.txt"
    config.write_text(
        json.dumps(
            {
                "schema": "boundary_allowlist.v1",
                "entries": [
                    {
                        "path_glob": str(selected),
                        "term": "certify",
                        "reason": "test",
                        "expiry": "2099-01-01",
                        "adr": "example",
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(boundary, "LEAKS_MAX", 1)
    _limit(
        [
            "boundary",
            "scan",
            str(selected),
            "--allowlist",
            str(config),
            "--now",
            "2026-10-15",
            "--json",
        ],
        capsys,
        "leaks",
        1,
    )


@pytest.mark.parametrize("pattern", ["*.txt", "**/*.txt", "**", "sub/", "**/**"])
def test_absence_keeps_pathlib_glob_count_semantics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], pattern: str
) -> None:
    (tmp_path / "visible.txt").write_text("v")
    (tmp_path / ".hidden.txt").write_text("h")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("n")
    (tmp_path / "sub-link").symlink_to(sub, target_is_directory=True)
    expected = sum(1 for path in tmp_path.glob(pattern) if path.is_file())
    code, payload = _invoke(
        ["absence", "--root", str(tmp_path), "--glob", pattern, "--min", "-1", "--json"],
        capsys,
    )
    assert code == 0 and payload["details"]["count"] == expected


@pytest.mark.parametrize("pattern", ["**x", "/absolute/*"])
def test_observable_invalid_glob_is_finite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], pattern: str
) -> None:
    code, payload = _invoke(
        ["absence", "--root", str(tmp_path), "--glob", pattern, "--min", "1", "--json"],
        capsys,
    )
    assert code == REFUSAL_EXIT
    assert payload["details"] == {"reason": "INVALID_GLOB", "location": "/glob"}


def test_observable_glob_io_error_is_finite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing_glob(_self: Path, _pattern: str) -> object:
        raise OSError("private host path")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "glob", failing_glob)
        code, payload = _invoke(
            ["absence", "--root", str(tmp_path), "--glob", "*.txt", "--min", "1", "--json"],
            capsys,
        )
    assert code == REFUSAL_EXIT
    assert payload["details"] == {"reason": "SOURCE_READ_FAILED", "location": "/root"}


def test_default_cli_refuses_large_repeated_path_before_partial_stdout(tmp_path: Path) -> None:
    selected = tmp_path / "long-key.json"
    selected.write_text(json.dumps({"x" * 1_100_000 + " promote": "safe"}))
    result = subprocess.run(
        [sys.executable, "-m", "underwrite.cli.main", "boundary", "scan", str(selected), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == REFUSAL_EXIT and result.stderr == ""
    payload: dict[str, Any] = json.loads(result.stdout)
    Draft202012Validator(SCHEMA).validate(payload)  # pyright: ignore[reportUnknownMemberType]
    assert payload["details"]["limit_kind"] == "output_bytes"
    assert payload["details"]["limit"] == 1024 * 1024

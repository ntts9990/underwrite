"""`underwrite boundary scan PATH...` -- walks JSON/JSONL files under the given
paths and runs underwrite_core.boundary.scan_strings (core, pure) over every
document, classifying the result against an optional dated allowlist
(rules/boundary-allowlist.json, contracts/boundary_allowlist.v1.schema.json).
The core module already recurses dict/list structures and matches both
vocabularies (`gate_vocab`/`authority_vocab` families); this module owns
only the file walk, the JSONL `line[<n>]` path prefix, allowlist matching,
and state classification.

*.json -> one document (`scan_strings(doc)`, path rooted at `$`). *.jsonl ->
one document per non-empty line, `scan_strings(doc, path=f"line[{n}]")` (`n`
0-based, the line's physical position -- blank lines are skipped but still
counted). Any other extension is ignored; a `*.schema.json` file is never
scanned even inside a walked directory, because adjudication vocabulary is
legitimate in a contract that defines it.

Allowlist matching (rules/boundary-allowlist.json entries): `fnmatch` on the
scanned file's path exactly as given on the command line (repo-relative when
invoked the documented way, from the repo root) AND an exact `term` match --
no glob on `term`. A match still needs `--now` to evaluate its `expiry`
(NOW_REQUIRED if missing); an expired match does NOT allow -- unlike
underwrite_core.absence's EXEMPTION_EXPIRED, an expired allowlist entry does
not turn the whole command into a distinct failure state, it simply fails to
apply, so the leak stays state LEAK and its row's `reason` is
`"allow_expired"` (evaluated per leak, not once for the whole command).

State vocabulary (a subset of underwrite.cli.commands.STATES, plus the one
new value LEAK it contributes): PASS (0 leaks) -- exit 0; LEAK (>=1
non-allowlisted leak) -- exit 1; EMPTY_POPULATION (no scannable file under
any given path -- absence is not consent, RFC-001 Article 1) -- exit 1;
NOW_REQUIRED (an allowlist entry matches a found leak but `--now` is
missing) -- exit 1; UNCONFIGURED (a path is missing or its JSON input is
invalid/ambiguous, including duplicate keys) -- exit 2.
SKIPPED_WITH_REASON/EXEMPTION_EXPIRED are not part of this command's
vocabulary (there is no `--exemptions`/`--exemption-key` pair here, only
`--allowlist`/`--now`).

Shell module: file I/O only (no subprocess, no network). Shared CLI admission
owns bounded JSON acquisition; the pure core owns date and vocabulary meaning."""

from __future__ import annotations

import argparse
import fnmatch
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

from underwrite_core.boundary import Leak, scan_strings

from underwrite.cli.legacy_input import (
    CONFIG_MAX_BYTES,
    SCAN_MAX_BYTES,
    AdmissionError,
    decode_bytes,
    limit_policy_entries,
    read_bytes,
    read_json,
    require_date,
    validate_date,
    validate_policy,
)

_SCHEMA_SUFFIX = ".schema.json"
_SCANNABLE_SUFFIXES = (".json", ".jsonl")


@dataclass(frozen=True)
class AllowlistEntry:
    """One rules/boundary-allowlist.json entry (contracts/boundary_allowlist.v1.schema.json)."""

    path_glob: str
    term: str
    reason: str
    expiry: str
    expiry_date: date
    adr: str


def load_allowlist(path: Path) -> list[AllowlistEntry]:
    """Admit every entry against the installed shape and strict date contract."""
    raw = read_json(path, CONFIG_MAX_BYTES, "/allowlist")
    if not isinstance(raw, dict):
        raise AdmissionError("INVALID_JSON_INPUT", "INVALID_JSON_INPUT", "/allowlist")
    limit_policy_entries(cast(object, raw), "entries", "/allowlist")
    validate_policy(cast(object, raw), "boundary_allowlist.v1", "INVALID_ALLOWLIST", "/allowlist")
    document = cast(dict[str, Any], raw)
    selected_entries = cast(list[dict[str, str]], document["entries"])

    entries: list[AllowlistEntry] = []
    for item in selected_entries:
        path_glob, term = item["path_glob"], item["term"]
        reason, expiry, adr = item["reason"], item["expiry"], item["adr"]
        expiry_date = require_date(expiry, "/allowlist")
        entries.append(AllowlistEntry(path_glob, term, reason, expiry, expiry_date, adr))
    return entries


def _is_scannable(path: Path) -> bool:
    return path.suffix in _SCANNABLE_SUFFIXES and not path.name.endswith(_SCHEMA_SUFFIX)


def _scannable_files(paths: list[Path]) -> list[Path]:
    """Every *.json/*.jsonl file under `paths` (files kept as-is, directories walked
    recursively), *.schema.json excluded, in deterministic order."""
    files: list[Path] = []
    for root in paths:
        if root.is_file():
            if _is_scannable(root):
                files.append(root)
        else:
            files.extend(p for p in sorted(root.rglob("*")) if p.is_file() and _is_scannable(p))
    return files


def _scan_file(path: Path) -> list[Leak]:
    raw = read_bytes(path, SCAN_MAX_BYTES, "/files")
    if path.suffix == ".jsonl":
        leaks: list[Leak] = []
        for line_no, raw_line in enumerate(raw.splitlines()):
            if not raw_line.strip():
                continue
            leaks.extend(scan_strings(decode_bytes(raw_line, "/files"), path=f"line[{line_no}]"))
        return leaks
    return scan_strings(decode_bytes(raw, "/files"))


def _match(entries: list[AllowlistEntry], relpath: str, term: str) -> AllowlistEntry | None:
    for entry in entries:
        if entry.term == term and fnmatch.fnmatch(relpath, entry.path_glob):
            return entry
    return None


def _row(
    file: str,
    leak: Leak,
    *,
    allowed: bool,
    reason: str | None = None,
    entry: AllowlistEntry | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "file": file,
        "path": leak.path,
        "term": leak.term,
        "family": leak.family,
        "allowed": allowed,
    }
    if reason is not None:
        row["reason"] = reason
    elif entry is not None:
        row["reason"] = entry.reason
    if entry is not None:
        row["expiry"] = entry.expiry
        row["adr"] = entry.adr
    return row


def _result(state: str, exit_code: int, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "cli_result.v1",
        "command": "boundary scan",
        "state": state,
        "exit_code": exit_code,
        "details": details,
    }


def _admission_refusal(
    exc: AdmissionError, invalid_path: Path | None = None
) -> tuple[int, dict[str, Any]]:
    if exc.reason == "INVALID_JSON_INPUT" and invalid_path is not None:
        return 2, _result(
            "UNCONFIGURED",
            2,
            {"invalid": [str(invalid_path)], "reason": "INVALID_JSON_INPUT"},
        )
    return 2, _result("UNCONFIGURED", 2, exc.details())


def cmd_boundary_scan(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """`underwrite boundary scan` -- see the module docstring for the full state vocabulary."""
    paths: list[Path] = list(args.paths)
    try:
        now_date = validate_date(args.now)
        entries = load_allowlist(args.allowlist) if args.allowlist is not None else []
    except AdmissionError as exc:
        return _admission_refusal(exc, args.allowlist)
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        return 2, _result("UNCONFIGURED", 2, {"missing": missing})

    files = _scannable_files(paths)
    if not files:
        empty_details: dict[str, Any] = {
            "files_scanned": 0,
            "leaks": [],
            "allowlist_entries": len(entries),
        }
        return 1, _result("EMPTY_POPULATION", 1, empty_details)

    rows: list[dict[str, Any]] = []
    for file in files:
        relpath = str(file)
        try:
            leaks = _scan_file(file)
        except AdmissionError as exc:
            return _admission_refusal(exc, file)
        for leak in leaks:
            entry = _match(entries, relpath, leak.term)
            if entry is None:
                rows.append(_row(relpath, leak, allowed=False))
                continue
            if now_date is None:
                nr_details: dict[str, Any] = {"file": relpath, "path": leak.path, "term": leak.term}
                return 1, _result("NOW_REQUIRED", 1, nr_details)
            if entry.expiry_date > now_date:
                rows.append(_row(relpath, leak, allowed=True, entry=entry))
            else:
                rows.append(_row(relpath, leak, allowed=False, reason="allow_expired", entry=entry))

    has_leak = any(not row["allowed"] for row in rows)
    state = "LEAK" if has_leak else "PASS"
    exit_code = 1 if has_leak else 0
    details: dict[str, Any] = {
        "files_scanned": len(files),
        "leaks": rows,
        "allowlist_entries": len(entries),
    }
    return exit_code, _result(state, exit_code, details)

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

Shell module: file I/O only (no subprocess, no network). Imports stdlib and
underwrite_core only."""

from __future__ import annotations

import argparse
import fnmatch
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

from underwrite_core.boundary import Leak, scan_strings

_SCHEMA_SUFFIX = ".schema.json"
_SCANNABLE_SUFFIXES = (".json", ".jsonl")


@dataclass(frozen=True)
class AllowlistEntry:
    """One rules/boundary-allowlist.json entry (contracts/boundary_allowlist.v1.schema.json)."""

    path_glob: str
    term: str
    reason: str
    expiry: str
    adr: str


def _get_str(obj: dict[str, Any], key: str) -> str | None:
    value = obj.get(key)
    return value if isinstance(value, str) else None


def load_allowlist(path: Path) -> list[AllowlistEntry]:
    """Parse rules/boundary-allowlist.json with plain shape checks (schema validation is
    a test-time concern, core purity contract, mirrored from commands.load_exemptions)."""
    raw: object = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(raw, dict):
        raise ValueError("ALLOWLIST_OBJECT_REQUIRED")
    raw_entries = cast("dict[str, Any]", raw).get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError(f"{path}: expected a JSON object with an 'entries' array")

    entries: list[AllowlistEntry] = []
    for item in cast("list[Any]", raw_entries):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: entry is not an object: {item!r}")
        item_dict = cast("dict[str, Any]", item)
        path_glob = _get_str(item_dict, "path_glob")
        term = _get_str(item_dict, "term")
        reason = _get_str(item_dict, "reason")
        expiry = _get_str(item_dict, "expiry")
        adr = _get_str(item_dict, "adr")
        if path_glob is None or term is None or reason is None or expiry is None or adr is None:
            raise ValueError(f"{path}: entry missing a required string field: {item!r}")
        entries.append(AllowlistEntry(path_glob, term, reason, expiry, adr))
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


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _scan_file(path: Path) -> list[Leak]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        leaks: list[Leak] = []
        for line_no, raw_line in enumerate(text.splitlines()):
            if not raw_line.strip():
                continue
            leaks.extend(
                scan_strings(
                    json.loads(raw_line, object_pairs_hook=_unique_object), path=f"line[{line_no}]"
                )
            )
        return leaks
    return scan_strings(json.loads(text, object_pairs_hook=_unique_object))


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


def cmd_boundary_scan(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """`underwrite boundary scan` -- see the module docstring for the full state vocabulary."""
    paths: list[Path] = list(args.paths)
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        return 2, _result("UNCONFIGURED", 2, {"missing": missing})

    files = _scannable_files(paths)
    try:
        entries = load_allowlist(args.allowlist) if args.allowlist is not None else []
    except (ValueError, UnicodeError, RecursionError):
        return 2, _result(
            "UNCONFIGURED", 2, {"invalid": [str(args.allowlist)], "reason": "INVALID_JSON_INPUT"}
        )
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
        except (ValueError, UnicodeError, RecursionError):
            return 2, _result(
                "UNCONFIGURED", 2, {"invalid": [relpath], "reason": "INVALID_JSON_INPUT"}
            )
        for leak in leaks:
            entry = _match(entries, relpath, leak.term)
            if entry is None:
                rows.append(_row(relpath, leak, allowed=False))
                continue
            if args.now is None:
                nr_details: dict[str, Any] = {"file": relpath, "path": leak.path, "term": leak.term}
                return 1, _result("NOW_REQUIRED", 1, nr_details)
            if date.fromisoformat(entry.expiry) > date.fromisoformat(args.now):
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

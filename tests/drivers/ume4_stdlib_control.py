"""Independent stdlib control: add case-level descriptive JSON to a validated goppi neutral read.

This is an experiment, not an Underwrite adapter. It trusts a separately run,
pinned goppi neutral consumer for full producer-specific admission, then checks
the exact retained bytes and facts it projects. It imports neither product.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
from pathlib import Path
from typing import Any, NoReturn, cast

FILES = ("manifest.json", "export.json", "rows.jsonl", "schema.json", "scope.json")
MEMBERS = FILES[1:]
MAX_DOCUMENT = 1024 * 1024
MAX_ROWS = 4 * 1024 * 1024


class Refusal(ValueError):
    """Finite refusal for this standalone control."""


def _refuse() -> NoReturn:
    raise Refusal("INPUT_REJECTED")


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _refuse()
        result[key] = value
    return result


def _json(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw, object_pairs_hook=_object_pairs)
    if type(value) is not dict:
        _refuse()
    return cast(dict[str, Any], value)


def _read(path: Path, limit: int) -> bytes:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
        _refuse()
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        _refuse()
    return raw


def _check_members(
    manifest: dict[str, Any], captured: dict[str, bytes], hashes: dict[str, str]
) -> None:
    raw_claims = manifest.get("files")
    if type(raw_claims) is not list:
        _refuse()
    entries = cast(list[object], raw_claims)
    if len(entries) != len(MEMBERS) or any(type(entry) is not dict for entry in entries):
        _refuse()
    claims = cast(list[dict[str, Any]], entries)
    if [entry.get("path") for entry in claims] != list(MEMBERS):
        _refuse()
    if any(
        entry.get("sha256") != hashes[name] or entry.get("bytes") != len(captured[name])
        for name, entry in zip(MEMBERS, claims, strict=True)
    ):
        _refuse()


def _case_statuses(scope: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    raw_selected = scope.get("selection", {}).get("case_ids")
    raw_cases = scope.get("cases")
    if type(raw_selected) is not list or type(raw_cases) is not list:
        _refuse()
    selected_items = cast(list[object], raw_selected)
    case_items = cast(list[object], raw_cases)
    if any(type(item) is not str for item in selected_items) or any(
        type(item) is not dict for item in case_items
    ):
        _refuse()
    selected = cast(list[str], selected_items)
    cases = cast(list[dict[str, Any]], case_items)
    if (
        len(selected) != len(rows)
        or len(cases) != len(rows)
        or len(set(selected)) != len(selected)
        or selected != [item.get("case_id") for item in cases]
        or selected != [row.get("case_id") for row in rows]
    ):
        _refuse()
    statuses = [row.get("outcome", {}).get("primary_status") for row in rows]
    if any(state not in ("observed_true", "observed_false", "missing") for state in statuses):
        _refuse()
    return cast(list[str], statuses)


def project(exchange: Path, neutral_path: Path) -> dict[str, object]:
    """Project only the source-claim fields needed for the prespecified task."""
    if exchange.is_symlink():
        _refuse()
    root = exchange.resolve(strict=True)
    if not root.is_dir() or {path.name for path in root.iterdir()} != set(FILES):
        _refuse()
    captured = {
        name: _read(root / name, MAX_ROWS if name == "rows.jsonl" else MAX_DOCUMENT)
        for name in FILES
    }
    hashes = {name: _sha(raw) for name, raw in captured.items()}
    manifest = _json(captured["manifest.json"])
    exported = _json(captured["export.json"])
    scope = _json(captured["scope.json"])
    neutral = _json(_read(neutral_path, MAX_DOCUMENT))
    row_lines = captured["rows.jsonl"].splitlines(keepends=True)
    if not row_lines or any(not line.endswith(b"\n") for line in row_lines):
        _refuse()
    rows = [_json(line) for line in row_lines]
    if (
        manifest.get("schema") != "goppi.baseline_case_exchange.manifest.v2"
        or exported.get("schema") != "goppi.baseline_case_exchange.export.v2"
        or scope.get("schema") != "goppi.baseline_case_exchange.scope.v1"
        or neutral.get("schema") != "goppi.product_baseline_neutral_interpretation.v1"
        or neutral.get("source_manifest_sha256") != hashes["manifest.json"]
        or neutral.get("source_manifest_digest") != manifest.get("content_digest")
        or neutral.get("scope_content_digest") != scope.get("content_digest")
        or manifest.get("scope_content_digest") != scope.get("content_digest")
        or exported.get("source", {}).get("scope_content_digest") != scope.get("content_digest")
    ):
        _refuse()
    _check_members(manifest, captured, hashes)
    statuses = _case_statuses(scope, rows)
    counts = {
        "planned": len(rows),
        "observed_true": statuses.count("observed_true"),
        "observed_false": statuses.count("observed_false"),
        "missing": statuses.count("missing"),
    }
    if (
        neutral.get("counts") != counts
        or neutral.get("row_count") != len(rows)
        or exported.get("counts") != counts
        or any(value is not False for value in neutral.get("claims", {}).values())
        or neutral.get("claims") == {}
    ):
        _refuse()
    items: list[dict[str, object]] = []
    for index, state in enumerate(statuses):
        missing = state == "missing"
        items.append(
            {
                "coordinate": {"kind": "case", "index": index},
                "availability": "missing" if missing else "observed",
                "value": None if missing else state == "observed_true",
                "reason": "SOURCE_MISSING" if missing else None,
                "origin": "source_claim",
                "permitted_effect": "descriptive",
            }
        )
    return {
        "schema": "ume4.stdlib_control.v1",
        "source_identity": {"manifest_raw_hash": hashes["manifest.json"], "members": hashes},
        "population": {"unit": "case", "planned_count": len(rows), "observed_count": len(rows)},
        "items": items,
        "verification": [],
        "interpretation_limit": (
            "producer claims only; no paired effect, independent truth, or approval"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchange", type=Path, required=True)
    parser.add_argument("--neutral", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = project(args.exchange, args.neutral)
    except Exception:
        print('{"reason":"INPUT_REJECTED","schema":"ume4.control_error.v1"}', file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

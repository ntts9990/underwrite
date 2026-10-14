"""Compare a pinned neutral interpretation with a fresh Underwrite baseline audit.

This test-only driver emits counts, hashes, and named checks, never case IDs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, TypedDict, cast

from underwrite.instrument.evidence.goppi_baseline import FILES
from underwrite.instrument.ingest.goppi_baseline import audit_baseline_exchange

STATUSES = ("observed_true", "observed_false", "missing")


class Comparison(TypedDict):
    schema: str
    state: str
    checks: dict[str, bool]
    counts: dict[str, int]
    member_hashes: dict[str, str]
    interpretation_limit: str


def _hash(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _load(raw: bytes) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(raw)
    return value


def compare_exchange(exchange: Path, neutral_path: Path) -> Comparison:
    """Return a sanitized check result for the exact five captured fixture bytes."""
    captured = {name: (exchange / name).read_bytes() for name in FILES}
    hashes = {name: _hash(raw) for name, raw in captured.items()}
    manifest = _load(captured["manifest.json"])
    exported = _load(captured["export.json"])
    scope = _load(captured["scope.json"])
    rows = [_load(line) for line in captured["rows.jsonl"].splitlines()]
    neutral = _load(neutral_path.read_bytes())
    underwrite = cast(dict[str, Any], audit_baseline_exchange(exchange))
    unchanged = all((exchange / name).read_bytes() == raw for name, raw in captured.items())

    selection = scope["selection"]["case_ids"]
    scope_cases = [case["case_id"] for case in scope["cases"]]
    row_cases = [row["case_id"] for row in rows]
    row_counts = Counter(row["outcome"]["primary_status"] for row in rows)
    source = cast(dict[str, Any], underwrite["source"])
    population = cast(dict[str, Any], underwrite["population"])
    items = cast(list[dict[str, Any]], underwrite["items"])
    identity = cast(dict[str, Any], source["identity"])
    observed = Counter(
        "missing"
        if item["availability"] == "missing"
        else "observed_true"
        if item["value"] is True
        else "observed_false"
        for item in items
    )
    counts = {"planned": len(selection), **{status: row_counts[status] for status in STATUSES}}
    observed_counts = {
        "planned": population["planned_count"],
        **{status: observed[status] for status in STATUSES},
    }
    claimed_members = {
        entry["path"]: (entry["sha256"], entry["bytes"]) for entry in manifest["files"]
    }
    checks: dict[str, bool] = {
        "neutral_result_profile": neutral.get("schema")
        == "goppi.product_baseline_neutral_interpretation.v1",
        "underwrite_result_profile": underwrite.get("schema") == "evidence_result.v1"
        and underwrite.get("processing")
        == {"state": "analyzed", "reason": None, "next_action": "none"},
        "captured_bytes_unchanged": unchanged,
        "ordered_scope_row_roster": selection == scope_cases == row_cases,
        "manifest_member_bytes": set(claimed_members) == set(FILES) - {"manifest.json"}
        and all(
            claimed_members[name] == (hashes[name], len(captured[name]))
            for name in FILES
            if name != "manifest.json"
        ),
        "underwrite_member_hashes": identity.get("members") == hashes
        and identity.get("manifest_raw_hash") == hashes["manifest.json"],
        "neutral_manifest_identity": neutral.get("source_manifest_sha256")
        == hashes["manifest.json"]
        and neutral.get("source_manifest_digest") == manifest["content_digest"],
        "neutral_scope_identity": neutral.get("scope_content_digest") == scope["content_digest"],
        "neutral_counts": neutral.get("counts") == counts and neutral.get("row_count") == len(rows),
        "export_counts": exported.get("counts") == counts,
        "underwrite_counts": observed_counts == counts
        and population.get("observed_count") == len(rows),
        "case_coordinates": population.get("unit") == "case"
        and [item["coordinate"] for item in items]
        == [{"kind": "case", "index": index} for index in range(len(rows))],
        "row_status_order": len(items) == len(rows)
        and all(
            item["value"]
            is (
                True
                if row["outcome"]["primary_status"] == "observed_true"
                else False
                if row["outcome"]["primary_status"] == "observed_false"
                else None
            )
            for item, row in zip(items, rows, strict=True)
        ),
        "descriptive_unbound_boolean": all(
            item["origin"] == "source_claim"
            and item["permitted_effect"] == "descriptive"
            and item["metric"]["semantic_binding"] == "unbound"
            and type(item["value"]) in (bool, type(None))
            for item in items
        ),
        "no_paired_or_approval_authority": neutral.get("claims", {}).get("paired_effect_claimed")
        is False
        and neutral.get("claims", {}).get("source_claims_verified") is False
        and neutral.get("claims", {}).get("real_world_generalization_claimed") is False
        and neutral.get("claims", {}).get("approval_or_deployment_authority") is False
        and neutral.get("claims", {}).get("statistical_inference_performed") is False
        and exported["claims"]["paired_effect_claimed"] is False
        and exported["claims"]["human_approval_claimed"] is False
        and underwrite["verification"] == []
        and all(item["method"] is None and item["policy"] is None for item in items),
    }
    return {
        "schema": "ume4.pinned_exchange_comparison.v1",
        "state": "match" if all(checks.values()) else "mismatch",
        "checks": checks,
        "counts": counts,
        "member_hashes": hashes,
        "interpretation_limit": (
            "byte identity and producer claims; no independent provenance or approval"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchange", type=Path, required=True)
    parser.add_argument("--neutral", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = compare_exchange(args.exchange, args.neutral)
    except Exception as error:
        # Do not print producer-controlled exception messages, IDs, or paths.
        print(
            json.dumps(
                {
                    "schema": "ume4.pinned_exchange_comparison.v1",
                    "state": "error",
                    "error_type": type(error).__name__,
                }
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["state"] == "match" else 1


if __name__ == "__main__":
    raise SystemExit(main())

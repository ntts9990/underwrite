"""Synthetic baseline exchange v2; never copies real producer artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.instrument.evidence.goppi_baseline import (
    EXPORT_SCHEMA,
    FORMAT_SCOPE,
    MANIFEST_SCHEMA,
    ROW_SCHEMA,
    SCOPE_SCHEMA,
    BaselineDocuments,
)
from underwrite.instrument.evidence.goppi_identity import length_prefixed_digest


def _digest(text: str) -> str:
    return digest_bytes(text.encode())


def _tag(value: dict[str, Any], domain: str) -> str:
    payload = {key: item for key, item in value.items() if key != "content_digest"}
    return length_prefixed_digest((domain, canonical_bytes(payload).decode()))


@dataclass
class SyntheticBaselineExchange:
    manifest: dict[str, Any]
    export: dict[str, Any]
    scope: dict[str, Any]
    rows: list[dict[str, Any]]

    def files(self) -> dict[str, bytes]:
        """Rebind scope and byte identities after deliberate fixture mutations."""
        self.scope["content_digest"] = _tag(self.scope, "goppi:baseline-case-exchange-scope:v1")
        self.export["source"]["scope_content_digest"] = self.scope["content_digest"]
        self.manifest["scope_content_digest"] = self.scope["content_digest"]
        result = {
            "export.json": canonical_bytes(self.export) + b"\n",
            "rows.jsonl": b"".join(canonical_bytes(row) + b"\n" for row in self.rows),
            "schema.json": b'{"synthetic_schema":"not_executed"}\n',
            "scope.json": canonical_bytes(self.scope) + b"\n",
        }
        self.manifest["files"] = [
            {"path": name, "sha256": digest_bytes(raw), "bytes": len(raw)}
            for name, raw in result.items()
        ]
        self.manifest["content_digest"] = _tag(
            self.manifest, "goppi:baseline-case-exchange-manifest:v2"
        )
        result["manifest.json"] = canonical_bytes(self.manifest) + b"\n"
        return result

    def documents(self) -> BaselineDocuments:
        files = self.files()
        return BaselineDocuments(
            self.manifest,
            self.export,
            self.scope,
            tuple(self.rows),
            {name: (digest_bytes(raw), len(raw)) for name, raw in files.items()},
        )


def build_baseline_exchange(
    statuses: tuple[str, ...] = ("observed_true", "observed_false", "missing"),
) -> SyntheticBaselineExchange:
    ids = [f"case-{index}" for index in range(len(statuses))]
    runner = {
        "profile": "deterministic_node_conformance",
        "support_scope": "deterministic_conformance",
        "provider_authentication_verified": False,
        "provider_billing_verified": False,
    }
    scope: dict[str, Any] = {
        "schema": SCOPE_SCHEMA,
        "approved": {
            **{
                key: _digest(key)
                for key in (
                    "draft_digest",
                    "evalset_digest",
                    "approval_receipt_digest",
                    "split_policy_digest",
                    "assignment_digest",
                )
            },
            "classification": "synthetic_fixture",
        },
        "selection": {"split": "explore", "case_ids": ids},
        "cases": [
            {
                "case_id": case_id,
                "family": "synthetic",
                "source_kind": "synthetic",
                "case_content_digest": _digest(case_id),
                "verifier_id": "synthetic-verifier",
                "verifier_digest": _digest("verifier"),
                "verifier_outcome": "binary",
            }
            for case_id in ids
        ],
        "exclusions": {
            "benchmark_claims": "prohibited",
            "private_truth_included": False,
            "confirm_case_metadata_included": False,
            "instructions_included": False,
            "source_references_included": False,
            "reviewer_details_included": False,
        },
        "content_digest": _digest("placeholder"),
    }
    source = {
        "alias": "synthetic",
        "run_id": _digest("run"),
        "result_digest": _digest("result"),
        "result_schema": "goppi.baseline_discovery_result.v1",
        "preparation_digest": _digest("prep"),
        "evalset_digest": scope["approved"]["evalset_digest"],
        "scope_content_digest": scope["content_digest"],
        "runner": runner,
        "snapshot_material": {
            "bind_schema": "goppi.native_harness_snapshot_bind.v1",
            "source_digest": _digest("snapshot"),
            "graph_digest": None,
            "materialization_binding_digest": None,
            "data_identity_digest": None,
        },
        "local_authenticated_source_checked": True,
        "authenticity_scope": "same_local_integrity_key_operator",
    }
    export: dict[str, Any] = {
        "schema": EXPORT_SCHEMA,
        "format_scope": FORMAT_SCOPE,
        "status": "complete",
        "generated_at": "2026-01-01T00:00:00Z",
        "include_answers": False,
        "source": source,
        "counts": {
            "planned": len(ids),
            **{key: statuses.count(key) for key in ("observed_true", "observed_false", "missing")},
        },
        "aggregate_cost": {
            "status": "known_zero",
            "value": 0,
            "unit": "USD",
            "source": "deterministic_runner_no_model_cost",
            "lower_bound": False,
            "provider_authentication_verified": False,
            "provider_billing_verified": False,
            "provider_billing_cap": False,
            "missing_values_are_zero": False,
        },
        "claims": dict.fromkeys(
            (
                "model_contacted_by_exporter network_contacted_by_exporter "
                "source_project_modified_by_exporter paired_effect_claimed human_approval_claimed "
                "conditions_comparability_claimed producer_scores_independently_regraded "
                "native_receipt_authentication_checked_by_exporter "
                "provider_authentication_verified "
                "provider_billing_verified"
            ).split(),
            False,
        ),
    }
    rows: list[dict[str, Any]] = []
    for case_id, status in zip(ids, statuses, strict=True):
        rows.append(
            {
                "schema": ROW_SCHEMA,
                "source_result_digest": source["result_digest"],
                "source_result_schema": source["result_schema"],
                "run_id": source["run_id"],
                "case_id": case_id,
                "split": "explore",
                "arm": "baseline",
                "repeat_index": 0,
                "runner": dict(runner),
                "observation_id": None,
                "observation_manifest_digest": None,
                "outcome": {
                    "primary_status": status,
                    "reason": "no_observed_primary_outcome"
                    if status == "missing"
                    else "binary_verifier_outcome",
                    "producer_recorded": True,
                    "independently_regraded": False,
                },
                "attempt": {
                    "status": "missing",
                    "attempt_id": None,
                    "request_digest": None,
                    "receipt_digest": None,
                    "termination_reason": None,
                    "duration_ms": None,
                    "local_receipt_content_digest_checked": False,
                },
                "usage": {
                    "status": "not_stored_per_case",
                    "input_tokens": None,
                    "output_tokens": None,
                    "total_tokens": None,
                    "cache_read_tokens": None,
                    "cache_write_tokens": None,
                    "reported_cost": None,
                    "cost_unit": "USD",
                    "source": "product_result_stores_aggregate_only",
                    "lower_bound": False,
                    "provider_authentication_verified": False,
                    "provider_billing_verified": False,
                    "missing_values_are_zero": False,
                },
                "answer": {"included": False, "availability": "missing"},
            }
        )
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "format_scope": FORMAT_SCOPE,
        "status": "complete",
        "manifest_written_last": True,
        "immutable_fresh_output": True,
        "source_result_digest": source["result_digest"],
        "scope_content_digest": scope["content_digest"],
        "files": [],
        "content_digest": _digest("placeholder"),
    }
    return SyntheticBaselineExchange(manifest, export, scope, rows)

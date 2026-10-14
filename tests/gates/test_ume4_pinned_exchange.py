"""Pinned producer exchange and neutral output agree with a fresh Underwrite audit."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from drivers import ume4_compare
from drivers.ume4_compare import compare_exchange

from underwrite.instrument.evidence.goppi_baseline import FILES
from underwrite.instrument.ingest.goppi_baseline import audit_baseline_exchange

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures/conformance/ume4"
EXCHANGE = FIXTURE / "goppi-v2-exchange"
NEUTRAL = FIXTURE / "neutral-result.json"


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_pinned_fixture_bytes_match_single_pin_record() -> None:
    pins: dict[str, Any] = json.loads((FIXTURE / "pins.json").read_bytes())
    assert pins["schema"] == "ume4.pinned_cross_conformance.v1"
    assert _sha256(ROOT / "tests/drivers/ume4_generate_exchange.mts") == pins[
        "generator_sha256"
    ]
    assert _sha256(ROOT / "tests/drivers/ume4_negative_probes.mts") == pins[
        "negative_probe_driver_sha256"
    ]
    assert _sha256(ROOT / "contracts/evidence_result.v1.schema.json") == pins[
        "source_hashes"
    ]["underwrite/contracts/evidence_result.v1.schema.json"]
    assert _sha256(FIXTURE / "negative-results.json") == pins["negative_results_sha256"]
    assert {capture["directory"] for capture in pins["captures"].values()} == {
        "goppi-v2-exchange",
        "goppi-v2-false-missing",
    }
    for capture in pins["captures"].values():
        exchange = FIXTURE / capture["directory"]
        assert set(capture["members"]) == set(FILES)
        for name, identity in capture["members"].items():
            member = exchange / name
            assert member.stat().st_size == identity["bytes"]
            assert _sha256(member) == identity["sha256"]
        assert _sha256(FIXTURE / capture["neutral_result"]) == capture[
            "neutral_sha256"
        ]


@pytest.mark.parametrize(
    ("exchange_name", "neutral_name"),
    [
        ("goppi-v2-exchange", "neutral-result.json"),
        ("goppi-v2-false-missing", "neutral-result-false-missing.json"),
    ],
)
def test_pinned_exchange_matches_neutral_and_underwrite(
    exchange_name: str, neutral_name: str
) -> None:
    exchange = FIXTURE / exchange_name
    result = compare_exchange(exchange, FIXTURE / neutral_name)
    assert result["state"] == "match", result["checks"]
    assert all(result["checks"].values())
    if exchange_name == "goppi-v2-false-missing":
        assert result["counts"]["observed_false"] > 0
        assert result["counts"]["missing"] > 0
    serialized = json.dumps(result)
    scope = json.loads((exchange / "scope.json").read_bytes())
    for case_id in scope["selection"]["case_ids"]:
        assert case_id not in serialized


def test_mismatched_neutral_counts_and_hashes_are_visible(tmp_path: Path) -> None:
    neutral: dict[str, Any] = json.loads(NEUTRAL.read_bytes())
    neutral["counts"]["observed_true"] += 1
    neutral["source_manifest_sha256"] = "sha256:" + "0" * 64
    altered = tmp_path / "neutral.json"
    altered.write_text(json.dumps(neutral), encoding="utf-8")

    result = compare_exchange(EXCHANGE, altered)
    assert result["state"] == "mismatch"
    assert result["checks"]["neutral_counts"] is False
    assert result["checks"]["neutral_manifest_identity"] is False
    assert result["checks"]["manifest_member_bytes"] is True
    assert result["checks"]["underwrite_counts"] is True


def test_numeric_one_is_not_accepted_as_a_boolean(monkeypatch: pytest.MonkeyPatch) -> None:
    altered = deepcopy(cast(dict[str, Any], audit_baseline_exchange(EXCHANGE)))
    altered["items"][0]["value"] = 1

    def altered_audit(_exchange: Path) -> dict[str, object]:
        return altered

    monkeypatch.setattr(ume4_compare, "audit_baseline_exchange", altered_audit)

    result = compare_exchange(EXCHANGE, NEUTRAL)
    assert result["state"] == "mismatch"
    assert result["checks"]["descriptive_unbound_boolean"] is False
    assert result["checks"]["row_status_order"] is False


def test_neutral_authority_promotion_is_mismatch(tmp_path: Path) -> None:
    neutral: dict[str, Any] = json.loads(NEUTRAL.read_bytes())
    neutral["claims"]["source_claims_verified"] = True
    neutral["claims"]["real_world_generalization_claimed"] = True
    altered = tmp_path / "neutral.json"
    altered.write_text(json.dumps(neutral), encoding="utf-8")

    result = compare_exchange(EXCHANGE, altered)
    assert result["state"] == "mismatch"
    assert result["checks"]["no_paired_or_approval_authority"] is False


def test_wrong_result_profile_is_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    neutral: dict[str, Any] = json.loads(NEUTRAL.read_bytes())
    neutral["schema"] = "other.neutral.v1"
    altered = tmp_path / "neutral.json"
    altered.write_text(json.dumps(neutral), encoding="utf-8")
    result = compare_exchange(EXCHANGE, altered)
    assert result["state"] == "mismatch"
    assert result["checks"]["neutral_result_profile"] is False

    underwrite = deepcopy(cast(dict[str, Any], audit_baseline_exchange(EXCHANGE)))
    underwrite["processing"] = {"state": "unsupported", "reason": "UNSUPPORTED_FORMAT"}

    def altered_audit(_exchange: Path) -> dict[str, object]:
        return underwrite

    monkeypatch.setattr(ume4_compare, "audit_baseline_exchange", altered_audit)
    result = compare_exchange(EXCHANGE, NEUTRAL)
    assert result["state"] == "mismatch"
    assert result["checks"]["underwrite_result_profile"] is False

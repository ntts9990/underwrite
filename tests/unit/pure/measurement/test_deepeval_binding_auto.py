"""A neutral owner pack binds selected ordinary occurrences without a release guess."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import content_digest, digest_bytes

from underwrite.instrument.evidence.common_result import check_result_invariants
from underwrite.instrument.evidence.deepeval_binding import (
    DeepevalBindingError,
    bind_deepeval_accounting_auto,
)
from underwrite.instrument.evidence.deepeval_compat import (
    ACCOUNTING_SCHEMA,
    FLAKY_READER,
    NO_FLAKY_READER,
    account_deepeval_compatible,
)

ROOT = Path("fixtures")
SOURCE = ROOT / "examples/deepeval-auto-4.3.1/ordinary.json"
OLD = ROOT / "golden/external/deepeval/test_run_20260914_132634.json"
PACK = ROOT / "examples/evidence/synthetic-deepeval.bindings.v2.json"


def _accounting(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    return {
        "schema": ACCOUNTING_SCHEMA,
        "raw_hash": digest_bytes(raw),
        **account_deepeval_compatible(json.loads(raw)),
    }


def _pack() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(PACK.read_bytes()))


def test_auto_binding_preserves_source_and_declaration_byte_identities() -> None:
    accounting, pack = _accounting(SOURCE), _pack()
    pack_hash = digest_bytes(b"original private pack bytes")
    result = bind_deepeval_accounting_auto(accounting, pack, pack_raw_hash=pack_hash)
    assert result["schema"] == "evidence_result.v2"
    assert result["source"] == {
        "format": "deepeval.test-run",
        "producer_version": None,
        "producer_version_state": "unknown",
        "reader_profile": FLAKY_READER,
        "identity": {"kind": "single_file", "raw_hash": accounting["raw_hash"]},
    }
    assert result["declaration_source"] == {
        "format": "deepeval_metric_bindings.v2",
        "raw_hash": pack_hash,
        "authority": "owner_asserted_unverified",
    }
    items = cast(list[dict[str, Any]], result["items"])
    assert [item["availability"] for item in items] == ["observed", "observed", "error"]
    assert items[1]["value"] == 0
    assert [item["metric"]["semantic_binding"] for item in items] == ["unbound", "bound", "unbound"]
    check_result_invariants(result)


def test_auto_binding_uses_older_rule_without_claiming_an_older_release() -> None:
    accounting = _accounting(OLD)
    pack = _pack()
    binding = pack["bindings"][0]
    binding["source_metric_name"] = "Exact Match"
    definition = binding["definition"]
    definition["metric_id"] = "reported_exact_match_score"
    semantic = definition["semantic"]
    semantic["construct"] = {
        "id": "source_exact_match_score",
        "definition": "Numeric value of the synthetic source's Exact Match metric.",
    }
    semantic["target"] = {
        "kind": "quantity",
        "id": "reported_exact_match_output",
        "definition": "The serialized value; no independently verified match or task quality.",
    }
    definition["semantic_digest"] = content_digest(semantic)
    result = bind_deepeval_accounting_auto(accounting, pack, pack_raw_hash=digest_bytes(b"pack"))
    assert cast(dict[str, Any], result["source"])["reader_profile"] == NO_FLAKY_READER
    assert cast(dict[str, Any], result["source"])["producer_version"] is None
    check_result_invariants(result)


@pytest.mark.parametrize(
    ("mutation", "reason", "location"),
    [
        ("wrong-accounting", "DEEPEVAL_BINDING_VERSION_UNSUPPORTED", "/source"),
        ("null-reader", "DEEPEVAL_BINDING_READER_UNSUPPORTED", "/source/reader_profile"),
        ("unknown-reader", "DEEPEVAL_BINDING_READER_UNSUPPORTED", "/source/reader_profile"),
        ("v1-pack", "DEEPEVAL_BINDING_PACK_UNSUPPORTED", "/bindings/schema"),
        ("wrong-pack-source", "DEEPEVAL_BINDING_PACK_UNSUPPORTED", "/bindings/source"),
        ("bad-pack-hash", "DEEPEVAL_BINDING_PACK_HASH_INVALID", "/bindings"),
        ("unmatched", "DEEPEVAL_BINDING_NAME_UNMATCHED", "/bindings"),
    ],
)
def test_wrong_reader_pack_or_identity_refuses_finitely(
    mutation: str, reason: str, location: str
) -> None:
    accounting, pack = _accounting(SOURCE), _pack()
    pack_hash = digest_bytes(b"pack")
    if mutation == "wrong-accounting":
        accounting["schema"] = "deepeval_accounting.v2"
    elif mutation == "null-reader":
        accounting["reader_profile"] = None
    elif mutation == "unknown-reader":
        accounting["reader_profile"] = "private-reader-token"
    elif mutation == "v1-pack":
        pack["schema"] = "deepeval_metric_bindings.v1"
    elif mutation == "wrong-pack-source":
        pack["source"] = {"format": "other"}
    elif mutation == "bad-pack-hash":
        pack_hash = "sha256:private"
    else:
        pack["bindings"][0]["source_metric_name"] = "unused private name"
    with pytest.raises(DeepevalBindingError) as caught:
        bind_deepeval_accounting_auto(accounting, pack, pack_raw_hash=pack_hash)
    assert (caught.value.reason, caught.value.location) == (reason, location)
    assert "private" not in str(caught.value)


def test_duplicate_binding_and_digest_checks_still_have_one_owner() -> None:
    accounting, pack = _accounting(SOURCE), _pack()
    pack["bindings"].append(deepcopy(pack["bindings"][0]))
    with pytest.raises(DeepevalBindingError, match="DEEPEVAL_BINDING_NAME_DUPLICATE"):
        bind_deepeval_accounting_auto(accounting, pack, pack_raw_hash=digest_bytes(b"pack"))
    pack["bindings"].pop()
    pack["bindings"][0]["definition"]["semantic_digest"] = "sha256:" + "0" * 64
    with pytest.raises(DeepevalBindingError, match="DEEPEVAL_BINDING_DIGEST_MISMATCH"):
        bind_deepeval_accounting_auto(accounting, pack, pack_raw_hash=digest_bytes(b"pack"))

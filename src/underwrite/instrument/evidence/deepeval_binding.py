"""Pure owner-asserted DeepEval name binding over already-accounted occurrences."""

from __future__ import annotations

import re
from typing import Any, NoReturn, cast

from underwrite_core.canonical import CanonicalizationError, content_digest

from underwrite.instrument.evidence.common_result import (
    ResultInvariantError,
    check_result_invariants,
)
from underwrite.instrument.evidence.deepeval_compat import (
    ACCOUNTING_READERS,
    ACCOUNTING_SCHEMA,
    SOURCE_FORMAT,
)

AUTO_PACK_SCHEMA = "deepeval_metric_bindings.v2"
AUTO_RESULT_SCHEMA = "evidence_result.v2"
DECLARATION_AUTHORITY = "owner_asserted_unverified"
_HASH = re.compile(r"sha256:[a-f0-9]{64}\Z")


class DeepevalBindingError(ValueError):
    """Finite refusal without source names or declaration prose."""

    def __init__(self, code: str, reason: str, location: str) -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def _refuse(reason: str, location: str, code: str = "INVALID_INPUT") -> NoReturn:
    raise DeepevalBindingError(code, reason, location)


def _definitions(pack: dict[str, object]) -> dict[str, dict[str, Any]]:
    bindings = cast(list[dict[str, Any]], pack["bindings"])
    by_name: dict[str, dict[str, Any]] = {}
    by_identity: dict[tuple[str, str, str], dict[str, Any]] = {}
    for index, binding in enumerate(bindings):
        location = f"/bindings/{index}"
        name = cast(str, binding["source_metric_name"])
        definition = cast(dict[str, Any], binding["definition"])
        if name in by_name:
            _refuse("DEEPEVAL_BINDING_NAME_DUPLICATE", location)
        if definition["method"] is not None or definition["policy"] is not None:
            _refuse("DEEPEVAL_BINDING_METHOD_POLICY_UNSUPPORTED", location)
        semantic = cast(dict[str, Any], definition["semantic"])
        try:
            digest = content_digest(semantic)
        except CanonicalizationError:
            _refuse("DEEPEVAL_BINDING_SEMANTIC_INVALID", location)
        if definition["semantic_digest"] != digest:
            _refuse("DEEPEVAL_BINDING_DIGEST_MISMATCH", location)
        if (
            semantic["sampling"]["unit"] != "metric_occurrence"
            or semantic["required_coordinates"] != ["case_index", "metric_index"]
            or semantic["value"]["type"] != "number"
        ):
            _refuse("DEEPEVAL_BINDING_SEMANTIC_UNSUPPORTED", location)
        value = semantic["value"]
        minimum, maximum = value["minimum"], value["maximum"]
        if minimum is not None and maximum is not None and minimum > maximum:
            _refuse("DEEPEVAL_BINDING_BOUNDS_INVALID", location)
        identity = (definition["namespace"], definition["metric_id"], definition["version"])
        previous = by_identity.get(identity)
        if previous is not None and previous != definition:
            _refuse("DEEPEVAL_BINDING_IDENTITY_CONFLICT", location)
        by_name[name] = definition
        by_identity[identity] = definition
    return by_name


def _item(
    case: dict[str, Any], metric: dict[str, Any], definition: dict[str, Any] | None
) -> dict[str, object]:
    if definition is None:
        binding_result: dict[str, object] = {
            "semantic_binding": "unbound",
            "source_metric_index": metric["metric_index"],
        }
    else:
        binding_result = {
            "semantic_binding": "bound",
            "namespace": definition["namespace"],
            "id": definition["metric_id"],
            "version": definition["version"],
            "semantic_digest": definition["semantic_digest"],
        }
    state = metric["state"]
    if state == "scored":
        value = metric["score"]
        if type(value) not in (int, float):
            _refuse("DEEPEVAL_BINDING_SCORE_INVALID", "/source")
        if definition is not None:
            value_semantic = definition["semantic"]["value"]
            minimum, maximum = value_semantic["minimum"], value_semantic["maximum"]
            if (minimum is not None and value < minimum) or (
                maximum is not None and value > maximum
            ):
                _refuse("DEEPEVAL_BINDING_SCORE_OUT_OF_RANGE", "/source")
        availability, reason, next_action = "observed", None, "none"
    elif state == "error":
        value, availability, reason, next_action = (None, "error", "SOURCE_ERROR", "inspect_source")
    else:
        value, availability, reason, next_action = (
            None,
            "missing",
            "SOURCE_MISSING",
            "inspect_source",
        )
    return {
        "coordinate": {
            "kind": "metric_occurrence",
            "case_index": case["case_index"],
            "metric_index": metric["metric_index"],
        },
        "metric": binding_result,
        "applicability": "unknown",
        "availability": availability,
        "value": value,
        "reason": reason,
        "next_action": next_action,
        "origin": "source_claim",
        "method": None,
        "policy": None,
        "permitted_effect": "descriptive",
    }


def _items(accounting: dict[str, object], pack: dict[str, object]) -> list[dict[str, object]]:
    by_name = _definitions(pack)
    used: set[str] = set()
    items: list[dict[str, object]] = []
    for case in cast(list[dict[str, Any]], accounting["cases"]):
        for metric in cast(list[dict[str, Any]], case["metrics"]):
            name = cast(str, metric["name"])
            definition = by_name.get(name)
            if definition is not None:
                used.add(name)
            items.append(_item(case, metric, definition))
    if used != set(by_name):
        _refuse("DEEPEVAL_BINDING_NAME_UNMATCHED", "/bindings")
    return items


def _checked(result: dict[str, object]) -> dict[str, object]:
    try:
        check_result_invariants(result)
    except ResultInvariantError:
        _refuse("DEEPEVAL_BINDING_RESULT_INVARIANT_FAILED", "/result", "INTERNAL_ERROR")
    return result


def bind_deepeval_accounting(
    accounting: dict[str, object], pack: dict[str, object]
) -> dict[str, object]:
    """Map accounted occurrences using a schema-validated pack with checked identities."""
    if accounting.get("schema") != "deepeval_accounting.v2":
        _refuse("DEEPEVAL_BINDING_VERSION_UNSUPPORTED", "/source", "UNSUPPORTED_FORMAT")
    items = _items(accounting, pack)
    result: dict[str, object] = {
        "schema": "evidence_result.v1",
        "source": {
            "format": "deepeval.test-run",
            "format_version": "4.2.8",
            "identity": {"kind": "single_file", "raw_hash": accounting["raw_hash"]},
        },
        "processing": {"state": "analyzed", "reason": None, "next_action": "none"},
        "population": {
            "unit": "metric_occurrence",
            "observed_count": len(items),
            "planned_count": None,
        },
        "items": items,
        "verification": [],
    }
    return _checked(result)


def bind_deepeval_accounting_auto(
    accounting: dict[str, object], pack: dict[str, object], *, pack_raw_hash: str
) -> dict[str, object]:
    """Bind supplied ordinary metric occurrences without inferring a producer release."""
    if accounting.get("schema") != ACCOUNTING_SCHEMA:
        _refuse("DEEPEVAL_BINDING_VERSION_UNSUPPORTED", "/source", "UNSUPPORTED_FORMAT")
    reader = accounting.get("reader_profile")
    if reader not in ACCOUNTING_READERS:
        _refuse(
            "DEEPEVAL_BINDING_READER_UNSUPPORTED", "/source/reader_profile", "UNSUPPORTED_PROFILE"
        )
    if accounting.get("source") != {
        "format": SOURCE_FORMAT,
        "producer_version": None,
        "producer_version_state": "unknown",
        "claims_verified": False,
    }:
        _refuse("DEEPEVAL_BINDING_SOURCE_UNSUPPORTED", "/source", "UNSUPPORTED_FORMAT")
    if pack.get("schema") != AUTO_PACK_SCHEMA:
        _refuse("DEEPEVAL_BINDING_PACK_UNSUPPORTED", "/bindings/schema", "UNSUPPORTED_FORMAT")
    if pack.get("source") != {"format": SOURCE_FORMAT}:
        _refuse("DEEPEVAL_BINDING_PACK_UNSUPPORTED", "/bindings/source", "UNSUPPORTED_FORMAT")
    if type(pack_raw_hash) is not str or _HASH.fullmatch(pack_raw_hash) is None:
        _refuse("DEEPEVAL_BINDING_PACK_HASH_INVALID", "/bindings")
    items = _items(accounting, pack)
    return _checked(
        {
            "schema": AUTO_RESULT_SCHEMA,
            "source": {
                "format": SOURCE_FORMAT,
                "producer_version": None,
                "producer_version_state": "unknown",
                "reader_profile": reader,
                "identity": {"kind": "single_file", "raw_hash": accounting["raw_hash"]},
            },
            "declaration_source": {
                "format": AUTO_PACK_SCHEMA,
                "raw_hash": pack_raw_hash,
                "authority": DECLARATION_AUTHORITY,
            },
            "processing": {"state": "analyzed", "reason": None, "next_action": "none"},
            "population": {
                "unit": "metric_occurrence",
                "observed_count": len(items),
                "planned_count": None,
            },
            "items": items,
            "verification": [],
        }
    )

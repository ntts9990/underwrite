"""Selected DeepEval TestRun meanings without inferring its package release."""

from __future__ import annotations

from types import MappingProxyType
from typing import cast

from underwrite.instrument.evidence.deepeval_accounting import (
    LIMITATIONS,
    LIMITATIONS_V2,
    MAX_CASES,
    MAX_METRICS,
    RULE_PROFILE,
    RULE_PROFILE_V2,
    DeepevalAccountingError,
    account_deepeval,
)
from underwrite.instrument.evidence.deepeval_trace import (
    LIMITATIONS as TRACE_LIMITATIONS,
)
from underwrite.instrument.evidence.deepeval_trace import (
    SOURCE_FORMAT,
    index_deepeval_trace,
)

AUTO_PROFILE = "auto"
ACCOUNTING_SCHEMA = "deepeval_accounting.v3"
TRACE_SCHEMA = "deepeval_trace_index.v2"
NO_FLAKY_READER = "deepeval.metric-no-flaky.selected-fields.v1"
FLAKY_READER = "deepeval.metric-flaky.selected-fields.v1"
TRACE_READER = "deepeval.trace-structure.selected-fields.v1"
ACCOUNTING_READERS = (NO_FLAKY_READER, FLAKY_READER)
ACCOUNTING_SCOPE_EXTRA = MappingProxyType(
    {
        "selected_fields_only": True,
        "serialized_metric_population": "supplied_rows_only",
        "expectation_population": "unknown",
        "dataset_version": "not_analyzed",
    }
)
TRACE_SCOPE_EXTRA = MappingProxyType(
    {"expectations": "not_analyzed", "dataset_version": "not_analyzed"}
)
ACCOUNTING_LIMITATIONS_EXTRA = (
    "SOURCE_VERSION_UNKNOWN",
    "SERIALIZED_METRICS_ONLY",
    "EXPECTATION_POPULATION_UNKNOWN",
    "READER_PROFILE_NOT_PRODUCER_VERSION",
    "DATASET_VERSION_NOT_ANALYZED",
)
EMPTY_LIMITATION = "EMPTY_POPULATION_NO_FORMAT_EVIDENCE"
TRACE_LIMITATIONS_EXTRA = (
    "SOURCE_VERSION_UNKNOWN",
    "EXPECTATIONS_NOT_ANALYZED",
    "SOURCE_CASE_SUCCESS_UNINTERPRETED",
    "DATASET_VERSION_NOT_ANALYZED",
)


def _error(reason: str, location: str, code: str = "INVALID_INPUT") -> DeepevalAccountingError:
    return DeepevalAccountingError(code, reason, location)


def _source() -> dict[str, object]:
    return {
        "format": SOURCE_FORMAT,
        "producer_version": None,
        "producer_version_state": "unknown",
        "claims_verified": False,
    }


def _object(value: object, location: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _error("DEEPEVAL_OBJECT_REQUIRED", location)
    return cast(dict[str, object], value)


def _bounded_array(value: object, maximum: int, location: str) -> list[object]:
    if type(value) is not list:
        raise _error("DEEPEVAL_ARRAY_REQUIRED", location)
    items = cast(list[object], value)
    if len(items) > maximum:
        raise _error("DEEPEVAL_POPULATION_LIMIT_EXCEEDED", location, "INPUT_LIMIT_EXCEEDED")
    return items


def _flaky_presence(value: dict[str, object], location: str) -> bool:
    if "flaky" not in value:
        return False
    if type(value["flaky"]) is not bool:
        raise _error("DEEPEVAL_FLAKY_FLAG_INVALID", location + "/flaky")
    return True


def _reader(payload: dict[str, object]) -> tuple[str, str, bool]:
    cases = _bounded_array(payload.get("testCases"), MAX_CASES, "/testCases")
    seen: bool | None = None
    remaining = MAX_METRICS
    for case_index, value in enumerate(cases):
        location = f"/testCases/{case_index}"
        case = _object(value, location)
        if case.get("expectationsData") is not None:
            raise _error(
                "DEEPEVAL_EXPECTATIONS_UNSUPPORTED",
                location + "/expectationsData",
                "UNSUPPORTED_PROFILE",
            )
        present = _flaky_presence(case, location)
        if seen is not None and seen != present:
            raise _error("DEEPEVAL_FLAKY_FIELDS_MIXED", location + "/flaky", "UNSUPPORTED_PROFILE")
        seen = present if seen is None else seen
        metrics = _bounded_array(case.get("metricsData", []), remaining, location + "/metricsData")
        remaining -= len(metrics)
        for metric_index, item in enumerate(metrics):
            metric_location = f"{location}/metricsData/{metric_index}"
            metric = _object(item, metric_location)
            if metric.get("expectationsData") is not None:
                raise _error(
                    "DEEPEVAL_EXPECTATIONS_UNSUPPORTED",
                    metric_location + "/expectationsData",
                    "UNSUPPORTED_PROFILE",
                )
            metric_present = _flaky_presence(metric, metric_location)
            if seen != metric_present:
                raise _error(
                    "DEEPEVAL_FLAKY_FIELDS_MIXED",
                    metric_location + "/flaky",
                    "UNSUPPORTED_PROFILE",
                )
    if seen:
        return FLAKY_READER, RULE_PROFILE_V2, False
    return NO_FLAKY_READER, RULE_PROFILE, not cases


def account_deepeval_compatible(payload: object) -> dict[str, object]:
    """Reuse one established metric rule after bounded field-era admission."""
    source = _object(payload, "/")
    reader, rule, empty = _reader(source)
    counted = account_deepeval(source, rule_profile=rule)
    limitations = [
        *(LIMITATIONS_V2 if rule == RULE_PROFILE_V2 else LIMITATIONS),
        *ACCOUNTING_LIMITATIONS_EXTRA,
    ]
    if empty:
        limitations.append(EMPTY_LIMITATION)
    return {
        "source": _source(),
        "reader_profile": reader,
        **counted,
        "scope": {**cast(dict[str, object], counted["scope"]), **ACCOUNTING_SCOPE_EXTRA},
        "limitations": limitations,
    }


def index_deepeval_compatible(payload: object) -> dict[str, object]:
    """Reuse selected step structure while explicitly excluding expectation outcomes."""
    counted = index_deepeval_trace(payload)
    return {
        "source": _source(),
        "reader_profile": TRACE_READER,
        **counted,
        "scope": {**cast(dict[str, object], counted["scope"]), **TRACE_SCOPE_EXTRA},
        "limitations": [*TRACE_LIMITATIONS, *TRACE_LIMITATIONS_EXTRA],
    }

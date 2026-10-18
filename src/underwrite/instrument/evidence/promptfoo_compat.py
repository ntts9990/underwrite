"""Selected-field Promptfoo SummaryV3 accounting without a package-version claim."""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import cast

from underwrite.instrument.evidence.accounting import (
    ROW_INDEX_LIMITATIONS,
    AccountingError,
    RowIndex,
    audit_promptfoo,
    audit_promptfoo_with_rows,
)

ACCOUNTING_SCHEMA = "promptfoo_accounting.v2"
ROW_INDEX_SCHEMA = "promptfoo_row_index.v2"
SOURCE_FORMAT = "promptfoo.eval-output"
SUMMARY_VERSION = 3
READER_PROFILE = "promptfoo.summary-v3.selected-fields.v1"
MAX_VERSION_LENGTH = 128
VERSION_STATES = ("unknown", "declared")
_NUMBER = r"(?:0|[1-9][0-9]*)"
_PRERELEASE = r"(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
_BUILD = r"[0-9A-Za-z-]+"
# The final negative lookahead works in Python and JSON Schema regex engines;
# a bare $ would also match just before a trailing newline in Python.
VERSION_PATTERN = (
    rf"^{_NUMBER}\.{_NUMBER}\.{_NUMBER}"
    rf"(?:-{_PRERELEASE}(?:\.{_PRERELEASE})*)?"
    rf"(?:\+{_BUILD}(?:\.{_BUILD})*)?(?![\s\S])"
)
SCOPE = MappingProxyType(
    {
        "population": "supplied_results_rows",
        "selected_fields_only": True,
        "planned_evaluations": "unknown",
        "export_completeness": "unknown",
    }
)
LIMITATIONS = (
    "PLANNED_DENOMINATOR_UNKNOWN",
    "CAUSE_UNKNOWN",
    "SOURCE_CLAIMS_UNVERIFIED",
    "UNCONSUMED_FIELDS_AND_POPULATIONS_NOT_ANALYZED",
    "SOURCE_HASH_AND_VERSION_LINKABLE",
    "NO_APPROVAL_AUTHORITY",
)
ROW_LIMITATIONS = (
    *ROW_INDEX_LIMITATIONS,
    "UNCONSUMED_FIELDS_AND_POPULATIONS_NOT_ANALYZED",
    "SOURCE_VERSION_LINKABLE",
)


class PromptfooCompatibilityError(ValueError):
    """Finite selected-field refusal with a structural source location."""

    def __init__(self, code: str, reason: str, location: str) -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def _version(source: dict[str, object]) -> tuple[str | None, str]:
    metadata = source.get("metadata")
    if metadata is None:
        return None, "unknown"
    if type(metadata) is not dict:
        raise PromptfooCompatibilityError(
            "INVALID_INPUT", "PROMPTFOO_METADATA_OBJECT_REQUIRED", "/metadata"
        )
    selected = cast(dict[str, object], metadata)
    if "promptfooVersion" not in selected:
        return None, "unknown"
    claim = selected["promptfooVersion"]
    if (
        type(claim) is not str
        or len(claim) > MAX_VERSION_LENGTH
        or re.fullmatch(VERSION_PATTERN, claim) is None
    ):
        raise PromptfooCompatibilityError(
            "INVALID_INPUT", "PROMPTFOO_PRODUCER_VERSION_INVALID", "/metadata/promptfooVersion"
        )
    return claim, "declared"


def _accounting_error(error: AccountingError) -> PromptfooCompatibilityError:
    reason = str(error)
    if reason == "ROW_LIMIT_EXCEEDED":
        return PromptfooCompatibilityError("COMPUTATION_LIMIT_EXCEEDED", reason, "/results/results")
    if reason in ("INVALID_COUNT", "COUNT_TOTAL_OVERFLOW"):
        location = "/results/stats"
    elif reason == "INVALID_ROW":
        location = "/results/results"
    else:
        location = "/results"
    return PromptfooCompatibilityError("INVALID_INPUT", reason, location)


def account_promptfoo_compatible(payload: object, *, row_index: bool = False) -> dict[str, object]:
    """Describe supplied V3 rows, with source version only when declared."""
    if type(payload) is not dict:
        raise PromptfooCompatibilityError("INVALID_INPUT", "PROMPTFOO_OUTPUT_OBJECT_REQUIRED", "/")
    source = cast(dict[str, object], payload)
    summary = source.get("results")
    if type(summary) is not dict:
        raise PromptfooCompatibilityError(
            "INVALID_INPUT", "PROMPTFOO_SUMMARY_OBJECT_REQUIRED", "/results"
        )
    selected = cast(dict[str, object], summary)
    if type(selected.get("version")) is not int or selected["version"] != SUMMARY_VERSION:
        raise PromptfooCompatibilityError(
            "UNSUPPORTED_PROFILE", "PROMPTFOO_SUMMARY_VERSION_UNSUPPORTED", "/results/version"
        )
    producer_version, version_state = _version(source)
    rows: list[RowIndex] = []
    try:
        if row_index:
            counted, rows = audit_promptfoo_with_rows(
                selected.get("stats"), selected.get("results")
            )
        else:
            counted = audit_promptfoo(selected.get("stats"), selected.get("results"))
    except AccountingError as exc:
        raise _accounting_error(exc) from None
    scope = dict(SCOPE)
    result: dict[str, object] = {
        "source": {
            "format": SOURCE_FORMAT,
            "summary_version": SUMMARY_VERSION,
            "producer_version": producer_version,
            "producer_version_state": version_state,
            "claims_verified": False,
        },
        "reader_profile": READER_PROFILE,
        "scope": scope,
        **counted,
        "limitations": list(ROW_LIMITATIONS if row_index else LIMITATIONS),
    }
    if row_index:
        scope["values"] = "withheld"
        result["rows"] = rows
        result["verification"] = []
    return result

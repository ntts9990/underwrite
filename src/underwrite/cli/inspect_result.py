"""Inspect one external common-result file without projecting its private items."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Literal, cast

from underwrite_core.canonical import digest_bytes

from underwrite.cli.evidence import EvidenceError, ReportOutput, run_cli
from underwrite.cli.local_json import decode_document, read_source, validate
from underwrite.instrument.evidence.common_result import (
    ResultInvariantError,
    availability_counts,
    check_result_invariants,
    item_histograms,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator

INPUT_MAX_BYTES = 4 * 1024 * 1024
INPUT_MAX_DEPTH = 64
SCHEMA = "common_result_inspection.v1"
ACTIONS_SCHEMA = "common_result_inspection_actions.v1"
SCHEMA_V2 = "common_result_inspection.v2"
ACTIONS_SCHEMA_V2 = "common_result_inspection_actions.v2"
RESULT_SCHEMAS = {
    "evidence_result.v1": {"summary": SCHEMA, "actions": ACTIONS_SCHEMA},
    "evidence_result.v2": {"summary": SCHEMA_V2, "actions": ACTIONS_SCHEMA_V2},
}
View = Literal["summary", "actions"]
LIMITATIONS = [
    "SOURCE_CLAIMS_UNVERIFIED",
    "SOURCE_BYTES_NOT_CHECKED",
    "BUNDLE_BINDING_NOT_CHECKED",
    "VERIFICATION_REFS_NOT_REPERFORMED",
    "ITEM_INVARIANTS_LIMITED",
    "NO_MEASUREMENT_OR_ACCEPTANCE",
    "RAW_HASH_AND_COUNTS_LINKABLE",
]


def _vocabulary(schema: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Read only the two closed item vocabularies from the trusted input contract."""
    try:
        owner = cast(dict[str, object], schema)
        definitions = cast(dict[str, object], owner["$defs"])
        item = cast(dict[str, object], definitions["item"])
        action_field = cast(
            dict[str, object], cast(dict[str, object], item["properties"])["next_action"]
        )
        actions = action_field["enum"]
        binding = cast(dict[str, object], definitions["metric_binding"])
        variants = cast(list[object], binding["oneOf"])
        bindings: list[object] = []
        for variant in variants:
            properties = cast(dict[str, object], cast(dict[str, object], variant)["properties"])
            semantic = cast(dict[str, object], properties["semantic_binding"])
            bindings.append(semantic["const"])
    except (KeyError, TypeError, IndexError, AttributeError):
        raise SchemaConfigurationError("INVALID_COMMON_RESULT_VOCABULARY") from None
    return _closed_names(actions), _closed_names(bindings)


def _closed_names(values: object) -> tuple[str, ...]:
    if not isinstance(values, list) or not values:
        raise SchemaConfigurationError("INVALID_COMMON_RESULT_VOCABULARY")
    entries = cast(list[object], values)
    if any(type(value) is not str or not value for value in entries):
        raise SchemaConfigurationError("INVALID_COMMON_RESULT_VOCABULARY")
    names = cast(list[str], entries)
    if len(names) != len(set(names)):
        raise SchemaConfigurationError("INVALID_COMMON_RESULT_VOCABULARY")
    return tuple(names)


def inspect_result_file(path: Path, *, view: View = "summary") -> dict[str, object]:
    """Screen selected raw bytes against the installed common-result contract.

    This checks structure and analyzed item invariants only. Embedded source and
    verification assertions remain unchecked claims.
    """
    if view not in ("summary", "actions"):
        raise EvidenceError("INVALID_INPUT", "UNSUPPORTED_VIEW", "/view")
    raw = read_source(path, INPUT_MAX_BYTES, "/result", "INVALID_INPUT", EvidenceError)
    document, _ = decode_document(
        raw, INPUT_MAX_BYTES, INPUT_MAX_DEPTH, "/result", "INVALID_INPUT", EvidenceError
    )
    selected_format = document.get("schema")
    if type(selected_format) is not str or selected_format not in RESULT_SCHEMAS:
        raise EvidenceError("INVALID_INPUT", "UNSUPPORTED_RESULT_SCHEMA", "/result/schema")
    # Only exact keys in this code-owned map can select trusted installed contracts.
    input_schema = selected_format
    output_schema = RESULT_SCHEMAS[input_schema][view]
    input_validator = packaged_validator(input_schema)
    validate(
        input_validator,
        document,
        EvidenceError,
        "INVALID_INPUT",
        "/result",
    )
    try:
        check_result_invariants(document)
    except ResultInvariantError as exc:
        raise EvidenceError("INVALID_INPUT", exc.reason, exc.location) from None

    processing = cast(dict[str, Any], document["processing"])
    analyzed = processing["state"] == "analyzed"
    report: dict[str, object] = {
        "schema": output_schema,
        "selected_result": {"format": input_schema, "raw_hash": digest_bytes(raw)},
        "declared_processing": {
            "state": processing["state"],
            "reason": processing["reason"],
            "next_action": processing["next_action"],
        },
        "declared_population": document["population"],
        "availability_counts": availability_counts(document),
        "checks": {
            "contract_shape": "valid",
            "item_invariants": "valid" if analyzed else "not_applicable",
            "source_bytes": "not_checked",
            "producer_authentication": "not_checked",
            "verification_refs": "not_reperformed",
        },
        "verification_entry_count": len(cast(list[object], document["verification"])),
        "limitations": LIMITATIONS.copy(),
    }
    if view == "actions":
        action_names, binding_names = _vocabulary(input_validator.schema)
        action_counts, binding_counts = item_histograms(document, action_names, binding_names)
        report["item_next_action_counts"] = action_counts
        report["declared_semantic_binding_counts"] = binding_counts
    validate(
        packaged_validator(output_schema),
        report,
        EvidenceError,
        "INTERNAL_ERROR",
    )
    return report


def configure(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Screen one local evidence_result.v1 or v2 against its installed shape and analyzed-item "
        "invariants. Exit 0 means the selected document was inspected, even when its producer "
        "declares insufficient or unsupported processing. Source bytes, bundle binding, and "
        "verification references are not checked."
    )
    parser.add_argument("file", type=Path, metavar="FILE")
    parser.add_argument(
        "--view",
        choices=("summary", "actions"),
        default="summary",
        help="Select the default screening or item action and declared-binding counts.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the selected view as JSON.")


def render_human(report: dict[str, object]) -> str:
    processing = cast(dict[str, object], report["declared_processing"])
    counts = report["availability_counts"]
    if counts is None:
        summary = "No analyzed item counts"
    else:
        available = cast(dict[str, int], counts)
        summary = "Availability: " + ", ".join(
            f"{name}={available[name]}"
            for name in ("observed", "missing", "error", "abstained", "total")
        )
    return (
        f"Common result shape valid; declared processing: {processing['state']}. "
        f"{summary}. Source claims and verification references remain unchecked."
    )


def render_selected_human(report: dict[str, object]) -> str:
    summary = render_human(report)
    if report["schema"] in (SCHEMA, SCHEMA_V2):
        return summary
    action_counts = report["item_next_action_counts"]
    binding_counts = report["declared_semantic_binding_counts"]
    if action_counts is None or binding_counts is None:
        return summary + " No analyzed item action or binding counts."
    actions = cast(dict[str, int], action_counts)
    bindings = cast(dict[str, int], binding_counts)
    return (
        summary
        + " Item actions: "
        + ", ".join(f"{name}={count}" for name, count in actions.items())
        + ". Declared semantic bindings: "
        + ", ".join(f"{name}={count}" for name, count in bindings.items())
        + "."
    )


def produce(args: argparse.Namespace) -> dict[str, object]:
    return inspect_result_file(cast(Path, args.file), view=cast(View, args.view))


def main(argv: list[str]) -> int:
    return run_cli(
        "inspect-result",
        argv,
        configure,
        produce,
        ReportOutput(
            lambda args: tuple(views[args.view] for views in RESULT_SCHEMAS.values()),
            human=render_selected_human,
        ),
    )

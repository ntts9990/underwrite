"""Generate current Langfuse views and captures from their direct shape owners."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

if __package__:
    from . import build_langfuse_accounting as blob
    from . import build_langfuse_cli_accounting as cli
    from ._schema_compose import object_schema
    from .build_langfuse_record import build_auto_schema, build_row_schema
    from .build_langfuse_record import build_score_source_schema as build_score_source_record_schema
else:
    import build_langfuse_accounting as blob
    import build_langfuse_cli_accounting as cli
    from _schema_compose import object_schema
    from build_langfuse_record import build_auto_schema, build_row_schema
    from build_langfuse_record import build_score_source_schema as build_score_source_record_schema

from underwrite.cli.record_langfuse import (
    AUTO_RECORD_SCHEMA,
    ROW_RECORD_SCHEMA,
    SCORE_SOURCE_RECORD_SCHEMA,
)
from underwrite.instrument.evidence.langfuse_accounting import MAX_SCORES
from underwrite.instrument.evidence.langfuse_compat import (
    ACCOUNTING_SCHEMA,
    BLOB_READER,
    CLI_READER,
    ROW_INDEX_SCHEMA,
    SCORE_SOURCE_CLASSES,
    SCORE_SOURCE_LIMITATIONS,
    SCORE_SOURCE_NEXT_ACTIONS,
    SCORE_SOURCE_RULE_PROFILE,
    SCORE_SOURCE_SCHEMA,
    SCORE_SOURCE_SCOPE,
    SCORE_SOURCE_UNKNOWN_REASONS,
)

ROOT = Path(__file__).resolve().parents[1]


def _view(
    name: str, blob_branch: dict[str, object], cli_branch: dict[str, object]
) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:" + name,
        "title": name,
        "description": (
            "Selected Langfuse array/blob or CLI-list rows; producer version and source windows "
            "remain unknown. The reader family is an Underwrite policy, not origin proof."
        ),
        "if": {
            "properties": {"reader_profile": {"const": BLOB_READER}},
            "required": ["reader_profile"],
        },
        "then": blob_branch,
        "else": cli_branch,
    }


def build_accounting_schema() -> dict[str, Any]:
    return _view(ACCOUNTING_SCHEMA, blob.build_accounting_branch(), cli.build_accounting_branch())


def build_row_index_schema() -> dict[str, Any]:
    return _view(ROW_INDEX_SCHEMA, blob.build_row_branch(), cli.build_row_branch())


def build_score_source_schema() -> dict[str, Any]:
    count = blob.count_schema(MAX_SCORES)
    report = object_schema(
        {
            "schema": {"const": SCORE_SOURCE_SCHEMA},
            "reader_profile": {"enum": [BLOB_READER, CLI_READER]},
            "source_rule_profile": {"const": SCORE_SOURCE_RULE_PROFILE},
            "sources": {"oneOf": [blob.source_schema(BLOB_READER), blob.source_schema(CLI_READER)]},
            "scope": object_schema(
                {key: {"const": value} for key, value in SCORE_SOURCE_SCOPE.items()}
            ),
            "counts": object_schema(
                {
                    "known": object_schema({name: count for name in SCORE_SOURCE_CLASSES}),
                    "unknown": object_schema(
                        {name: count for name in SCORE_SOURCE_UNKNOWN_REASONS}
                    ),
                }
            ),
            "limitations": {"const": list(SCORE_SOURCE_LIMITATIONS)},
            "next_actions": {
                "oneOf": [
                    {"const": [SCORE_SOURCE_NEXT_ACTIONS[0]]},
                    {"const": list(SCORE_SOURCE_NEXT_ACTIONS)},
                ]
            },
        }
    )
    report.update(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:underwrite:contract:" + SCORE_SOURCE_SCHEMA,
            "title": SCORE_SOURCE_SCHEMA,
            "description": (
                "Aggregate score source classes in independently selected Langfuse files. "
                "Source labels are producer claims; hashes bind selected bytes but do not "
                "authenticate actors, execution, completeness, task verdicts, or approval."
            ),
            "allOf": [
                {
                    "if": {
                        "properties": {"reader_profile": {"const": reader}},
                        "required": ["reader_profile"],
                    },
                    "then": {
                        "properties": {
                            "sources": blob.source_schema(reader),
                        }
                    },
                }
                for reader in (BLOB_READER, CLI_READER)
            ],
        }
    )
    report["allOf"].append(
        {
            "if": {
                "properties": {
                    "counts": {
                        "properties": {
                            "unknown": {
                                "properties": {
                                    name: {"const": 0} for name in SCORE_SOURCE_UNKNOWN_REASONS
                                }
                            }
                        }
                    }
                }
            },
            "then": {
                "properties": {"next_actions": {"const": [SCORE_SOURCE_NEXT_ACTIONS[0]]}}
            },
            "else": {"properties": {"next_actions": {"const": list(SCORE_SOURCE_NEXT_ACTIONS)}}},
        }
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, build in (
        (ACCOUNTING_SCHEMA, build_accounting_schema),
        (ROW_INDEX_SCHEMA, build_row_index_schema),
        (SCORE_SOURCE_SCHEMA, build_score_source_schema),
        (AUTO_RECORD_SCHEMA, build_auto_schema),
        (ROW_RECORD_SCHEMA, build_row_schema),
        (SCORE_SOURCE_RECORD_SCHEMA, build_score_source_record_schema),
    ):
        target = ROOT / f"contracts/{name}.schema.json"
        raw = (json.dumps(build(), ensure_ascii=False, indent=2) + "\n").encode()
        if args.check:
            if not target.exists() or target.read_bytes() != raw:
                print(f"{target.name} is stale")
                return 1
        else:
            target.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

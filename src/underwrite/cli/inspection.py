"""Thin artifact inspection CLI: completion is never deployment permission."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn, cast

from underwrite_core.canonical import canonical_bytes

from underwrite.instrument.ingest.application import NEXT_ACTIONS
from underwrite.instrument.ingest.inspection import InspectionError, inspect_artifact

DISPLAY_LIMIT = 1024


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise InspectionError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP", "", "CORRECT_MANIFEST")


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "case", type=Path, metavar="CASE.json", help="Local single-artifact manifest."
    )
    parser.add_argument(
        "--json", action="store_true", help="Report stdout; invocation errors stderr."
    )


def _display(value: object) -> str:
    text = json.dumps(value, ensure_ascii=True, sort_keys=True)
    return text[:DISPLAY_LIMIT] + (" [truncated]" if len(text) > DISPLAY_LIMIT else "")


def _human(payload: dict[str, object]) -> str:
    projection = payload.get("projection")
    headline = {
        "created": "Observation created (artifact inspection only).",
        "not_created": "Observation not created.",
    }.get(str(projection), "Inspection could not complete.")
    diagnostics = cast(list[dict[str, object]], payload.get("diagnostics", []))
    actions = [item["next_action"] for item in diagnostics]
    if "next_action" in payload:
        actions.append(payload["next_action"])
    checked = "No completed artifact inspection."
    if projection is not None:
        checked = "Artifact bytes hashed; expected hash: " + _display(
            payload["expected_hash_check"]
        )
        if payload["expected_hash_check"] == "not_supplied":
            checked += "; no expected-hash comparison performed."
    action = _display(actions)
    if projection == "created":
        action = "No artifact correction indicated; review the unchecked limitations separately."
    elif "CHECK_EXPECTED_HASH" in actions:
        action += " Check the intended artifact and expected hash before retrying."
    elif "CORRECT_MANIFEST" in actions:
        action += " Correct the case manifest before retrying."
    lines = [
        headline,
        "Artifact inspection only; no release or deployment decision.",
        "Checked: " + checked,
        "Gap: " + _display(diagnostics if projection is not None else payload.get("code")),
        "Not checked: " + _display(payload.get("limitations", "Artifact inspection incomplete.")),
        "Next action: " + action,
        "Details:",
    ]
    # Preserve all structured fields, escaped and bounded, after the human summary.
    lines.extend(f"{key}: {_display(value)}" for key, value in payload.items())
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = _Parser(prog="underwrite inspect", allow_abbrev=False)
    configure(parser)
    try:
        args = parser.parse_args(argv)
        report = inspect_artifact(args.case)
    except InspectionError as exc:
        print(
            canonical_bytes(exc.payload).decode() if "--json" in argv else _human(exc.payload),
            file=sys.stderr,
        )
        return 2
    except Exception:
        # Unexpected defects are invocation failures, never ordinary artifact findings.
        error = InspectionError(
            "INTERNAL_ERROR", "INTERNAL_ERROR", "", NEXT_ACTIONS["INTERNAL_ERROR"]
        )
        print(
            canonical_bytes(error.payload).decode() if "--json" in argv else _human(error.payload),
            file=sys.stderr,
        )
        return 2
    print(canonical_bytes(report).decode() if args.json else _human(report))
    return 0 if report["projection"] == "created" else 1

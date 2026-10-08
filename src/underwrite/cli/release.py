"""One bounded rendering of the validated declared-linkage report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn, cast

from underwrite_core.canonical import canonical_bytes

from underwrite.instrument.ingest.application import NEXT_ACTIONS
from underwrite.instrument.ingest.release import ReleaseError, inspect_release

OUTPUT_MAX_BYTES = 256 * 1024
ERROR_MAX_BYTES = 4096


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ReleaseError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP", "", "CORRECT_MANIFEST")


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "release", type=Path, metavar="RELEASE.json", help="Local declared release case."
    )
    parser.add_argument("--json", action="store_true", help="Structured report; errors on stderr.")


def _display(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _human(report: dict[str, object]) -> str:
    evidence = cast(list[dict[str, object]], report["evidence"])
    requirements = cast(list[dict[str, object]], report["requirements"])
    diagnostics = [
        item
        for row in requirements + evidence
        for item in cast(list[dict[str, object]], row["diagnostics"])
    ]
    lines = [
        "Stated linkage and record presence requirements matched."
        if report["result"] == "requirements_matched"
        else "Release linkage gaps found.",
        "Declared release linkage only; no evaluation or deployment decision.",
        "Gaps: " + _display([item["code"] for item in diagnostics]),
        "Not checked: " + _display(report["limitations"]),
        "Next actions: " + _display(sorted({str(item["next_action"]) for item in diagnostics})),
        "Subject: " + _display(report["subject"]),
    ]
    for requirement in requirements:
        lines.append("Requirement: " + _display(requirement))
        lines.extend(
            "Linked evidence: " + _display(entry)
            for entry in evidence
            if entry["requirement"] == requirement["id"]
        )
    ids = {entry["id"] for entry in requirements}
    lines.extend(
        "Unresolved reference: " + _display(entry)
        for entry in evidence
        if entry["requirement"] not in ids
    )
    lines.append("Manifest hash: " + str(report["manifest_hash"]))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = _Parser(prog="underwrite check", allow_abbrev=False)
    configure(parser)
    try:
        args = parser.parse_args(argv)
        report = inspect_release(args.release)
        output = (canonical_bytes(report) if args.json else _human(report).encode("utf-8")) + b"\n"
        if len(output) > OUTPUT_MAX_BYTES:
            raise ReleaseError(
                "OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED", "", "REDUCE_BATCH_SIZE"
            )
    except Exception as exc:
        internal = ReleaseError(
            "INTERNAL_ERROR", "INTERNAL_ERROR", "", NEXT_ACTIONS["INTERNAL_ERROR"]
        )
        error = exc if isinstance(exc, ReleaseError) else internal
        encoded = canonical_bytes(error.payload) + b"\n"
        if len(encoded) > ERROR_MAX_BYTES:
            encoded = canonical_bytes(internal.payload) + b"\n"
        sys.stderr.write(encoded.decode("utf-8"))
        return 2
    sys.stdout.write(output.decode("utf-8"))
    return 0 if report["result"] == "requirements_matched" else 1

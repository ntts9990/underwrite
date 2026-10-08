"""Wire the underwrite command-line surface to product handlers.

Ingestion, inspection, measurement and acceptance use distinct output contracts.
Population, pin and boundary checks return cli_result.v1 with stable exit codes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from underwrite.cli import (
    accept,
    boundary,
    commands,
    inspection,
    instrument,
    measure,
    release,
    render,
)

# Commands with their own parser and error envelope; build_parser registers them for help.
COMMANDS = {
    "measure": measure.main,
    "check": release.main,
    "inspect": inspection.main,
    "accept": accept.main,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="underwrite")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ingest = subparsers.add_parser(
        "ingest", aliases=["project"], help="Project an artifact (project is an explicit alias)."
    )
    instrument.configure(ingest)
    inspection.configure(subparsers.add_parser("inspect", help="Inspect one local artifact case."))
    release.configure(subparsers.add_parser("check", help="Check declared release linkage."))
    measure.configure(subparsers.add_parser("measure", help="Compute one local measurement read."))
    accept.configure(
        subparsers.add_parser("accept", help="Classify one change candidate's declared evidence.")
    )

    absence = subparsers.add_parser("absence", help="Population-absence gate .")
    absence.add_argument("--root", type=Path, required=True, help="Population directory.")
    absence.add_argument("--glob", required=True, help="Glob pattern (relative to --root).")
    absence.add_argument("--min", type=int, required=True, help="Minimum population size.")
    absence.add_argument("--exemptions", type=Path, default=None, help="exemptions.v1 JSON path.")
    absence.add_argument("--exemption-key", default=None, help="Key into --exemptions.")
    absence.add_argument("--now", default=None, help="ISO8601 date vs. exemption expiry.")
    absence.add_argument("--json", action="store_true", help="Print cli_result.v1 JSON.")
    absence.set_defaults(handler=commands.cmd_absence)

    drift = subparsers.add_parser("drift", help="Contract pin drift guard .")
    drift_sub = drift.add_subparsers(dest="drift_command", required=True)
    check = drift_sub.add_parser("check", help="Verify pins and fold in the population rule.")
    check.add_argument("--pins-root", type=Path, default=commands.DEFAULT_PINS_ROOT)
    check.add_argument("--siblings-root", type=Path, default=commands.default_siblings_root())
    check.add_argument("--exemptions", type=Path, default=None, help="exemptions.v1 JSON path.")
    check.add_argument("--now", default=None, help="ISO8601 date vs. exemption expiry.")
    check.add_argument("--json", action="store_true", help="Print cli_result.v1 JSON.")
    check.set_defaults(handler=commands.cmd_drift_check)

    pins = subparsers.add_parser("pins", help="Contract pin registry (contracts/pins/).")
    pins_sub = pins.add_subparsers(dest="pins_command", required=True)
    refresh = pins_sub.add_parser("refresh", help="Re-pin a repo from its sibling checkout.")
    refresh.add_argument("--repo", required=True, help="Repo name under --pins-root/active/.")
    refresh.add_argument("--pins-root", type=Path, default=commands.DEFAULT_PINS_ROOT)
    refresh.add_argument("--siblings-root", type=Path, default=commands.default_siblings_root())
    refresh.set_defaults(handler=commands.cmd_pins_refresh)

    boundary_parser = subparsers.add_parser(
        "boundary", help="Vocabulary-boundary scanner (measurement vs. adjudication)."
    )
    boundary_sub = boundary_parser.add_subparsers(dest="boundary_command", required=True)
    scan = boundary_sub.add_parser(
        "scan", help="Scan JSON/JSONL files for adjudication-vocabulary leaks."
    )
    scan.add_argument("paths", type=Path, nargs="+", metavar="PATH")
    scan.add_argument("--allowlist", type=Path, default=None, help="boundary_allowlist.v1 JSON.")
    scan.add_argument("--now", default=None, help="ISO8601 date vs. allowlist entry expiry.")
    scan.add_argument("--json", action="store_true", help="Print cli_result.v1 JSON.")
    scan.set_defaults(handler=boundary.cmd_boundary_scan)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in COMMANDS:
        return COMMANDS[argv[0]](argv[1:])
    if argv and argv[0] in instrument.COMMANDS:
        return instrument.main(argv[0], argv[1:])
    args = build_parser().parse_args(argv)
    exit_code, payload = args.handler(args)
    render.render(payload, as_json=bool(getattr(args, "json", False)))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

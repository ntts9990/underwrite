"""Command handlers for the `underwrite` CLI (main.py wires argparse to these).
Each handler takes the parsed `argparse.Namespace` and returns
`(exit_code, payload)`; `payload` is the cli_result.v1 shape defined by its contract.
main.py hands the payload to render.py and returns exit_code. For
absence, `underwrite_core.absence` is the canonical source of state and
decision logic, and `underwrite absence` is its product surface."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, cast

from underwrite_core.absence import AbsenceError, Exemption, check_population

from underwrite.fleet.drift import DriftOutcome, DriftVerdict, drift_check
from underwrite.fleet.pins import PinState, load_manifests, refresh_pins

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PINS_ROOT = _REPO_ROOT / "contracts" / "pins"
SIBLINGS_ROOT_ENV = "UNDERWRITE_SIBLINGS_ROOT"


def default_siblings_root() -> Path:
    """`--siblings-root` default: $UNDERWRITE_SIBLINGS_ROOT when set, else the repo's parent."""
    override = os.environ.get(SIBLINGS_ROOT_ENV)
    return Path(override) if override else _REPO_ROOT.parent


# absence's outcome vocabulary comes from underwrite_core.absence plus the
# UNCONFIGURED root-missing case (a CLI-level shortcut, not a core outcome).
_ABSENCE_EXIT_CODES: dict[str, int] = {
    "PASS": 0,
    "SKIPPED_WITH_REASON": 0,
    "EMPTY_POPULATION": 1,
    "EXEMPTION_EXPIRED": 1,
    "NOW_REQUIRED": 1,
    "UNCONFIGURED": 2,
}

STATES: frozenset[str] = frozenset(_ABSENCE_EXIT_CODES) | {o.value for o in DriftOutcome} | {"LEAK"}


def _result(command: str, state: str, exit_code: int, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "cli_result.v1",
        "command": command,
        "state": state,
        "exit_code": exit_code,
        "details": details,
    }


def _get_str(obj: dict[str, Any], key: str) -> str | None:
    value = obj.get(key)
    return value if isinstance(value, str) else None


def load_exemptions(path: Path) -> dict[str, Exemption]:
    """Parse rules/exemptions.json (exemptions.v1) with plain shape checks.

    Missing or non-string required fields are refused instead of silently
    constructing an exemption.
    """
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    raw_exemptions = raw.get("exemptions")
    if not isinstance(raw_exemptions, dict):
        raise ValueError(f"{path}: expected a JSON object with an 'exemptions' object")

    exemptions: dict[str, Exemption] = {}
    for key, entry in cast("dict[str, Any]", raw_exemptions).items():
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: exemption {key!r} is not an object")
        entry_dict = cast("dict[str, Any]", entry)
        reason = _get_str(entry_dict, "reason")
        expiry = _get_str(entry_dict, "expiry")
        if reason is None or expiry is None:
            raise ValueError(f"{path}: exemption {key!r} missing a string 'reason'/'expiry'")
        exemptions[key] = Exemption(reason=reason, expiry=expiry, adr=_get_str(entry_dict, "adr"))
    return exemptions


def cmd_absence(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """`underwrite absence` -- population = count of files matching --glob under --root."""
    root: Path = args.root
    if not root.is_dir():
        return 2, _result("absence", "UNCONFIGURED", 2, {"root": str(root)})

    count = sum(1 for path in root.glob(args.glob) if path.is_file())
    exemption = None
    if args.exemptions is not None and args.exemption_key is not None:
        exemption = load_exemptions(args.exemptions).get(args.exemption_key)

    try:
        verdict = check_population(count, args.min, exemption=exemption, now=args.now)
    except AbsenceError as exc:
        if exc.args[0] != "NOW_REQUIRED":
            raise
        return 1, _result("absence", "NOW_REQUIRED", 1, {"count": count, "min": args.min})

    state = verdict.outcome.value
    details: dict[str, Any] = {"count": count, "min": args.min}
    if verdict.reason is not None:
        details["reason"] = verdict.reason
    if verdict.expiry is not None:
        details["expiry"] = verdict.expiry
    exit_code = _ABSENCE_EXIT_CODES[state]
    return exit_code, _result("absence", state, exit_code, details)


def _repo_table(
    pins_root: Path, verdict: DriftVerdict
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Return sorted active-repository details and retired pin counts.

    Manifest metadata is re-derived from the user-supplied registry. Retired
    repositories remain visible but do not count in the active population.
    """
    repos: dict[str, dict[str, Any]] = {}
    retired: dict[str, int] = {}
    for manifest in load_manifests(pins_root):
        pins = len(manifest.files)
        if manifest.retired:
            retired[manifest.repo] = pins
            continue
        repo_results = [r for r in verdict.pin_report.results if r.repo == manifest.repo]
        if any(r.state is PinState.DRIFT for r in repo_results):
            state = "DRIFT"
        elif any(r.state is PinState.UNCONFIGURED for r in repo_results):
            state = "UNCONFIGURED"
        else:
            state = "PASS"
        repos[manifest.repo] = {
            "state": state,
            "pins": pins,
            "source_commit": manifest.source_commit,
            "drifted": [r.relpath for r in repo_results if r.state is PinState.DRIFT],
        }
    return dict(sorted(repos.items())), dict(sorted(retired.items()))


def cmd_drift_check(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """`underwrite drift check` -- underwrite.fleet.drift.drift_check over pins + siblings."""
    exemptions = load_exemptions(args.exemptions) if args.exemptions is not None else {}
    verdict = drift_check(args.pins_root, args.siblings_root, exemptions=exemptions, now=args.now)
    repos, retired = _repo_table(args.pins_root, verdict)

    details: dict[str, Any] = {
        "active_population": verdict.pin_report.active_population,
        "retired_population": verdict.pin_report.retired_population,
        "drifted": [f"{r.repo}/{r.relpath}" for r in verdict.pin_report.failures()],
        "unconfigured_repos": sorted({r.repo for r in verdict.pin_report.unconfigured()}),
        "repos": repos,
        "retired": retired,
    }
    if verdict.reason is not None:
        details["reason"] = verdict.reason
    if verdict.expiry is not None:
        details["expiry"] = verdict.expiry
    if verdict.adr is not None:
        details["adr"] = verdict.adr

    exit_code = verdict.exit_code
    return exit_code, _result("drift check", verdict.outcome.value, exit_code, details)


def cmd_pins_refresh(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """`underwrite pins refresh` -- re-copy one repo's pinned bytes from its sibling checkout."""
    manifest = refresh_pins(args.pins_root, args.siblings_root, args.repo)
    details: dict[str, Any] = {
        "repo": manifest.repo,
        "source_commit": manifest.source_commit,
        "captured_at": manifest.captured_at,
        "files": sorted(manifest.files),
    }
    return 0, _result("pins refresh", "PASS", 0, details)

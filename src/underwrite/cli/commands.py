"""Command handlers for the `underwrite` CLI (main.py wires argparse to these).
Each handler takes the parsed `argparse.Namespace` and returns
`(exit_code, payload)`; `payload` is the cli_result.v1 shape defined by its contract.
main.py hands the payload to render.py and returns exit_code. For
absence, `underwrite_core.absence` is the canonical source of state and
decision logic, and `underwrite absence` is its product surface."""

from __future__ import annotations

import argparse
import os
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from underwrite_core.absence import AbsenceError, Exemption, check_population

from underwrite.cli.legacy_input import (
    CONFIG_MAX_BYTES,
    SELECTED_FILES_MAX,
    AdmissionError,
    decode_bytes,
    limit_policy_entries,
    read_bytes,
    require_date,
    resource_error,
    validate_date,
    validate_policy,
)
from underwrite.fleet import pins as pin_registry
from underwrite.fleet.drift import DriftOutcome, DriftVerdict
from underwrite.fleet.drift import (
    _inspect_drift as _inspect_drift,  # pyright: ignore[reportPrivateUsage]
)
from underwrite.fleet.pins import (
    Manifest,
    PinError,
    PinReadError,
    PinState,
    refresh_pins,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
SIBLINGS_ROOT_ENV = "UNDERWRITE_SIBLINGS_ROOT"


def _checkout_roots() -> tuple[Path | None, Path | None]:
    """Use checkout defaults only when this module belongs to that source tree."""
    source = _REPO_ROOT / "src" / "underwrite" / "cli" / "commands.py"
    pins = _REPO_ROOT / "contracts" / "pins"
    if source.resolve() != Path(__file__).resolve() or not pins.is_dir():
        return None, None
    try:
        metadata = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    project = metadata.get("project")
    if not isinstance(project, dict):
        return None, None
    if cast(dict[str, object], project).get("name") != "underwrite":
        return None, None
    return pins, _REPO_ROOT.parent


def _selected_roots(args: argparse.Namespace) -> tuple[Path | None, Path | None]:
    pins_root: Path | None = args.pins_root
    siblings_root: Path | None = args.siblings_root
    if siblings_root is None:
        override = os.environ.get(SIBLINGS_ROOT_ENV)
        siblings_root = Path(override) if override else None
    if pins_root is None or siblings_root is None:
        checkout_pins, checkout_siblings = _checkout_roots()
        pins_root = pins_root if pins_root is not None else checkout_pins
        siblings_root = siblings_root if siblings_root is not None else checkout_siblings
    return pins_root, siblings_root


def _required_root_details(
    pins_root: Path | None, siblings_root: Path | None
) -> dict[str, str] | None:
    if pins_root is None:
        return {"reason": "PINS_ROOT_REQUIRED", "location": "/pins"}
    if siblings_root is None:
        return {"reason": "SIBLINGS_ROOT_REQUIRED", "location": "/siblings"}
    return None


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


def _load_exemptions(path: Path) -> tuple[dict[str, Exemption], int]:
    """Admit every exemption under the installed shape and strict date contract."""
    captured = read_bytes(path, CONFIG_MAX_BYTES, "/exemptions")
    raw = decode_bytes(captured, "/exemptions")
    limit_policy_entries(raw, "exemptions", "/exemptions")
    validate_policy(raw, "exemptions.v1", "INVALID_EXEMPTION", "/exemptions")
    document = cast(dict[str, Any], raw)
    entries = cast(dict[str, dict[str, Any]], document["exemptions"])

    exemptions: dict[str, Exemption] = {}
    for key, entry in entries.items():
        reason = cast(str, entry["reason"])
        expiry = cast(str, entry["expiry"])
        adr = cast(str | None, entry.get("adr"))
        if not reason.strip():
            raise AdmissionError("INVALID_EXEMPTION", "INVALID_EXEMPTION", "/exemptions")
        require_date(expiry, "/exemptions")
        exemptions[key] = Exemption(reason=reason, expiry=expiry, adr=adr)
    return exemptions, len(captured)


def load_exemptions(path: Path) -> dict[str, Exemption]:
    """Public loader over the same one-capture admission path."""
    return _load_exemptions(path)[0]


def _matched_population(root: Path, pattern: str) -> int:
    """Retain Path.glob selection while bounding admitted regular matches."""
    count = 0
    try:
        for path in root.glob(pattern):
            if path.is_file():
                count += 1
                if count > SELECTED_FILES_MAX:
                    raise resource_error("/root", "selected_files", SELECTED_FILES_MAX)
    except AdmissionError:
        raise
    except (ValueError, NotImplementedError):
        raise AdmissionError("INVALID_GLOB", "INVALID_GLOB", "/glob") from None
    except OSError:
        raise AdmissionError("SOURCE_READ_FAILED", "SOURCE_READ_FAILED", "/root") from None
    return count


def cmd_absence(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """`underwrite absence` -- population = count of files matching --glob under --root."""
    try:
        validate_date(args.now)
        exemptions = load_exemptions(args.exemptions) if args.exemptions is not None else {}
    except AdmissionError as exc:
        return 2, _result("absence", "UNCONFIGURED", 2, exc.details())
    root: Path = args.root
    if not root.is_dir():
        return 2, _result("absence", "UNCONFIGURED", 2, {"root": str(root)})

    try:
        count = _matched_population(root, args.glob)
    except AdmissionError as exc:
        return 2, _result("absence", "UNCONFIGURED", 2, exc.details())
    exemption = exemptions.get(args.exemption_key) if args.exemption_key is not None else None

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
    manifests: Sequence[Manifest], verdict: DriftVerdict
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Return sorted active-repository details and retired pin counts.

    Retired repositories remain visible but do not count in the active population.
    """
    repos: dict[str, dict[str, Any]] = {}
    retired: dict[str, int] = {}
    states = {
        result.repo: result.state
        for result in verdict.pin_report.repositories
        if result.state is not PinState.RETIRED
    }
    for manifest in manifests:
        pins = len(manifest.files)
        if manifest.retired:
            retired[manifest.repo] = pins
            continue
        repo_results = [r for r in verdict.pin_report.results if r.repo == manifest.repo]
        repos[manifest.repo] = {
            "state": states[manifest.repo].value,
            "pins": pins,
            "source_commit": manifest.source_commit,
            "drifted": [r.relpath for r in repo_results if r.state is PinState.DRIFT],
        }
    return dict(sorted(repos.items())), dict(sorted(retired.items()))


def cmd_drift_check(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """`underwrite drift check` -- underwrite.fleet.drift.drift_check over pins + siblings."""
    pins_root, siblings_root = _selected_roots(args)
    if (missing := _required_root_details(pins_root, siblings_root)) is not None:
        return 2, _result("drift check", "UNCONFIGURED", 2, missing)
    assert pins_root is not None and siblings_root is not None
    try:
        validate_date(args.now)
        exemptions, config_bytes = (
            _load_exemptions(args.exemptions) if args.exemptions is not None else ({}, 0)
        )
    except AdmissionError as exc:
        return 2, _result("drift check", "UNCONFIGURED", 2, exc.details())
    if config_bytes > pin_registry.PIN_TOTAL_MAX_BYTES:
        refusal = resource_error("/pins", "captured_bytes", pin_registry.PIN_TOTAL_MAX_BYTES)
        return 2, _result("drift check", "UNCONFIGURED", 2, refusal.details())
    try:
        verdict, manifests = _inspect_drift(
            pins_root,
            siblings_root,
            exemptions=exemptions,
            now=args.now,
            remaining=pin_registry.PIN_TOTAL_MAX_BYTES - config_bytes,
        )
    except PinReadError as exc:
        return 2, _result("drift check", "UNCONFIGURED", 2, exc.details())
    repos, retired = _repo_table(manifests, verdict)

    details: dict[str, Any] = {
        "active_population": verdict.pin_report.active_population,
        "retired_population": verdict.pin_report.retired_population,
        "drifted": [f"{r.repo}/{r.relpath}" for r in verdict.pin_report.failures()],
        "unconfigured_repos": sorted(
            result.repo
            for result in verdict.pin_report.repositories
            if result.state is PinState.UNCONFIGURED
        ),
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
    pins_root, siblings_root = _selected_roots(args)
    if (missing := _required_root_details(pins_root, siblings_root)) is not None:
        return 2, _result(
            "pins refresh", "UNCONFIGURED", 2, {**missing, "publication": "not_started"}
        )
    assert pins_root is not None and siblings_root is not None
    try:
        manifest = refresh_pins(pins_root, siblings_root, args.repo)
    except PinError as exc:
        return 2, _result(
            "pins refresh",
            "UNCONFIGURED",
            2,
            {"reason": exc.reason, "publication": exc.publication},
        )
    except subprocess.CalledProcessError:
        return 2, _result(
            "pins refresh",
            "UNCONFIGURED",
            2,
            {"reason": "GIT_SOURCE_UNAVAILABLE", "publication": "not_started"},
        )
    details: dict[str, Any] = {
        "repo": manifest.repo,
        "source_commit": manifest.source_commit,
        "captured_at": manifest.captured_at,
        "files": sorted(manifest.files),
    }
    return 0, _result("pins refresh", "PASS", 0, details)

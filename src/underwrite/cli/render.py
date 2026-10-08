"""Render a cli_result.v1 payload (see commands.py) as a human-readable table,
or -- when `as_json` is set (the CLI's `--json` flag) -- as the payload
itself, unmodified. The contract defines the output shape."""

from __future__ import annotations

import json
from typing import Any

_REPO_TABLE_HEADER = ("repo", "state", "pins", "commit")
_SHORT_COMMIT_LEN = 7


def _render_repo_table(repos: dict[str, Any]) -> None:
    """One row per repo (`drift check`'s `details.repos`): repo, state, pins, short commit."""
    rows = [
        (repo, info["state"], str(info["pins"]), str(info["source_commit"])[:_SHORT_COMMIT_LEN])
        for repo, info in repos.items()
    ]
    widths = [len(header) for header in _REPO_TABLE_HEADER]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    print("  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(_REPO_TABLE_HEADER)))
    for row in rows:
        print("  " + "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def _render_leak_rows(leaks: list[Any]) -> None:
    """One row per gate_vocab/authority_vocab leak (`boundary scan`, boundary scan)."""
    for leak in leaks:
        line = f"  {leak['file']}  {leak['path']}  {leak['term']}  ({leak['family']})"
        if leak.get("allowed"):
            line += f"  allowed ({leak.get('reason')}, {leak.get('expiry')}, {leak.get('adr')})"
        elif leak.get("reason"):
            line += f"  {leak['reason']}"
        print(line)


def render(payload: dict[str, Any], *, as_json: bool) -> None:
    """Print `payload` (commands.py's cli_result.v1 dict) to stdout."""
    if as_json:
        print(json.dumps(payload, indent=2))
        return

    print(f"{payload['command']}: {payload['state']} (exit {payload['exit_code']})")
    details = payload["details"]
    skip_keys = ("repos", "retired", "leaks")
    scalar_details = {key: value for key, value in details.items() if key not in skip_keys}
    width = max((len(str(key)) for key in scalar_details), default=0)
    for key, value in scalar_details.items():
        print(f"  {str(key).ljust(width)}  {value}")

    repos = details.get("repos")
    if repos:
        _render_repo_table(repos)
    retired = details.get("retired")
    if retired:
        print("  retired: " + ", ".join(f"{repo} ({pins})" for repo, pins in retired.items()))
    leaks = details.get("leaks")
    if leaks:
        _render_leak_rows(leaks)

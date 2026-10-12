"""Current semantic contracts remain generated from their shared owners."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts import build_acceptance_v2

ROOT = Path(__file__).resolve().parents[2]


def test_event_contract_assets_match_generator() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/build_event_contracts.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_current_acceptance_contract_assets_match_generator() -> None:
    for name, schema in build_acceptance_v2.build_schemas().items():
        assert (ROOT / "contracts" / f"{name}.schema.json").read_bytes() == (
            json.dumps(schema, indent=2) + "\n"
        ).encode()

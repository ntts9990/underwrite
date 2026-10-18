"""Current semantic contracts remain generated from their shared owners."""

from __future__ import annotations

import json
import shutil
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


def test_acceptance_generation_needs_no_contract_templates(
    tmp_path: Path,
) -> None:
    expected = {
        f"{name}.schema.json": (ROOT / "contracts" / f"{name}.schema.json").read_bytes()
        for name in ("declared_claims.v2", "acceptance_decision.v2")
    }
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("build_acceptance_v2.py", "_schema_compose.py"):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    generated = subprocess.run(
        [sys.executable, str(scripts / "build_acceptance_v2.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    assert {path.name: path.read_bytes() for path in contracts.iterdir()} == expected

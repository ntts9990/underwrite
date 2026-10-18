"""The two-source bound capture schema is generated and packaged from authored owners."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
NAME = "deepeval_bound_record.v2.schema.json"


def test_generated_bound_record_contract_is_current_and_installed() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/build_deepeval_bound_record.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    authored = ROOT / "contracts" / NAME
    packaged = ROOT / "src/underwrite/_contracts" / NAME
    assert packaged.is_symlink()
    assert packaged.readlink() == Path("../../../contracts") / NAME
    assert packaged.read_bytes() == authored.read_bytes()
    Draft202012Validator.check_schema(json.loads(authored.read_bytes()))

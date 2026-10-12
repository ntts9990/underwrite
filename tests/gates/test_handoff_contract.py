"""The composed handoff contract stays standalone and generated from its owners."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


def test_generated_handoff_contract_matches_owners() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/build_paired_handoff_review.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_composite_contract_has_only_resolvable_local_references() -> None:
    document = json.loads((ROOT / "contracts/paired_handoff_review.v1.schema.json").read_bytes())
    Draft202012Validator.check_schema(document)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            mapping = cast(dict[str, Any], value)
            if "$ref" in mapping:
                reference = cast(str, mapping["$ref"])
                assert reference.startswith("#/")
                target = document
                for part in reference[2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            for child in mapping.values():
                walk(child)
        elif isinstance(value, list):
            for child in cast(list[object], value):
                walk(child)

    walk(document)

"""Structure measurements are reproducible from source and keep layer units distinct."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import structure_metrics


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    app = "src/underwrite"
    _write(tmp_path, f"{app}/__init__.py", "")
    _write(tmp_path, f"{app}/cli/main.py", "from underwrite.service import run\n")
    _write(
        tmp_path,
        f"{app}/service/__init__.py",
        "from ..instrument.evidence import judge\n\n\ndef _text():\n    pass\n",
    )
    _write(
        tmp_path,
        f"{app}/instrument/evidence/judge.py",
        "from underwrite_core import seal\n\n\ndef _text():\n"
        "    raise ValueError('BAD_INPUT_CODE')\n",
    )
    _write(
        tmp_path,
        f"{app}/instrument/ingest/read.py",
        "from ..evidence import judge\n\n\n"
        "def _text():\n    raise ReadError('READ_FAILED', 'detail')\n\n\n"
        "def _fail():\n    return _fail('lowercase')\n",
    )
    # `from .. import name` names a submodule; one helper in two modules stays below the floor.
    _write(
        tmp_path,
        f"{app}/instrument/ingest/load.py",
        "from .. import evidence\n\n\ndef _pair():\n    pass\n",
    )
    _write(tmp_path, f"{app}/cli/pair.py", "def _pair():\n    pass\n")
    _write(tmp_path, "packages/underwrite-core/src/underwrite_core/seal.py", "X = 1\n\n")
    return tmp_path


def test_units_split_mixed_instrument_layers() -> None:
    assert structure_metrics.unit("underwrite.instrument.ingest.read") == "instrument.ingest"
    assert structure_metrics.unit("underwrite.instrument.evidence") == "instrument.evidence"
    assert structure_metrics.unit("underwrite.cli.main") == "cli"
    assert structure_metrics.unit("underwrite_core.seal") == "underwrite_core"
    assert structure_metrics.unit("underwrite") == "underwrite"
    assert structure_metrics.unit("underwrite.instrument") == "instrument"


def test_measure_counts_packages_edges_helpers_and_codes(tree: Path) -> None:
    result = structure_metrics.measure(tree)

    assert result["packages"] == {
        "cli": {"files": 2, "lines": 3},
        "instrument": {"files": 3, "lines": 11},
        "service": {"files": 1, "lines": 3},
        "underwrite": {"files": 1, "lines": 0},
        "underwrite_core": {"files": 1, "lines": 1},
    }
    # Relative imports from a package `__init__` and from modules all reach evidence.
    edges = result["unit_edges"]
    assert {(edge["source"], edge["target"], edge["sites"]) for edge in edges["edges"]} == {
        ("cli", "service", 1),
        ("service", "instrument.evidence", 1),
        ("instrument.ingest", "instrument.evidence", 2),
        ("instrument.evidence", "underwrite_core", 1),
    }
    assert (edges["distinct"], edges["import_sites"], edges["cycles"]) == (4, 5, [])
    assert result["repeated_private_helpers"] == [{"name": "_text", "modules": 3}]
    assert result["error_code_estimate"] == {"distinct": 2, "modules": 2}


def test_cycles_report_only_mutually_reachable_units() -> None:
    edges = {"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}}
    assert structure_metrics.cycles(edges) == [["a", "b", "c"]]
    assert structure_metrics.cycles({"a": {"b"}, "b": set()}) == []


def test_cli_prints_versioned_json(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert structure_metrics.main(["--root", str(tree)]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == "underwrite.structure_metrics.v1"

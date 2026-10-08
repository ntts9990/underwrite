"""Standalone behavior and boundary checks."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import cast

import pytest
from _external_formats import EXTERNAL_DIRECTORIES, ingestor
from _script_loader import load_module_from_path
from underwrite_core.canonical import content_digest

from underwrite.instrument.ingest import formats
from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES
from underwrite.instrument.ingest.project import Source
from underwrite.instrument.ingest.transport import Ingestor

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "external_manifest.py"
EXTERNAL = REPO_ROOT / "fixtures/golden/external"
RECORD_KEYS = frozenset({"source_record", "capture_kind", "tool", "files", "recipe"})

external_manifest = load_module_from_path("external_manifest", SCRIPT_PATH)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _records() -> dict[str, dict[str, object]]:
    return {
        path.parent.name: cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(EXTERNAL.glob("*/observations.json"))
    }


def _files(record: dict[str, object]) -> dict[str, dict[str, object]]:
    return cast(dict[str, dict[str, object]], record["files"])


def _selector(entry: dict[str, object]) -> tuple[str, str]:
    source = cast(dict[str, str], entry["source"])
    return source["format"], source["format_version"]


def _ingestor() -> Ingestor:
    return ingestor(formats.external_codecs(), max_bytes=DEFAULT_MAX_BYTES)


def test_every_external_format_has_exactly_one_captured_directory() -> None:
    present = {path.name for path in EXTERNAL.iterdir() if path.is_dir()}
    assert present == EXTERNAL_DIRECTORIES
    for name in sorted(present):
        assert (EXTERNAL / name / "observations.json").is_file(), name
        assert (EXTERNAL / name / "SOURCE.md").is_file(), name


def test_every_capture_record_is_complete_and_matches_its_bytes() -> None:
    ingestor = _ingestor()
    for vendor, record in _records().items():
        assert RECORD_KEYS <= record.keys(), vendor
        assert record["source_record"] == "SOURCE.md"
        assert record["capture_kind"] == "sanitized_tool_payload_example"
        tool = cast(dict[str, str], record["tool"])
        version = tool.get("version", tool.get("server_version"))
        assert version, vendor
        assert (
            tool["name"]
            == {
                "deepeval": "deepeval",
                "inspect": "inspect_ai",
                "langfuse": "langfuse",
                "openinference": "openinference-semantic-conventions",
                "otlp-json": "otelcol-contrib",
                "promptfoo": "promptfoo",
            }[vendor]
        )
        files = _files(record)
        assert files, vendor
        for name, entry in files.items():
            target = EXTERNAL / vendor / name
            digest = "sha256:" + _sha256(target)
            assert entry["ref"] == entry["raw_hash"] == digest, (vendor, name)
            selector = _selector(entry)
            assert selector in formats.external_codecs(), (vendor, name)
            assert selector[1] == version, (vendor, name)
            pinned = ingestor.file(target, Source(*selector, None)).observation
            assert content_digest(pinned) == entry["observation_digest"], (vendor, name)
        recipe = cast(dict[str, dict[str, str]], record["recipe"])
        assert recipe, vendor
        for name, item in recipe.items():
            assert _sha256(EXTERNAL / vendor / name) == item["sha256"], (vendor, name)


def test_readers_and_captures_cover_each_other_exactly() -> None:
    captured: set[tuple[str, str]] = set()
    for vendor, record in _records().items():
        for entry in _files(record).values():
            fmt, version = _selector(entry)
            assert fmt.startswith(vendor + "."), (vendor, fmt)
            captured.add((fmt, version))
    assert captured == set(formats.external_codecs())


def test_committed_manifest_is_exactly_the_derivation() -> None:
    assert external_manifest.check(EXTERNAL, REPO_ROOT) == []
    document = json.loads((EXTERNAL / "manifest.json").read_text(encoding="utf-8"))
    assert set(document) == {"formats"}
    manifest = document["formats"]
    assert manifest == external_manifest.derive(EXTERNAL, REPO_ROOT)
    for entry in manifest:
        assert set(entry) == {"format", "version", "path", "raw_sha256"}
        assert (REPO_ROOT / entry["path"]).is_file()
    assert {entry["format"] for entry in manifest} == {sel[0] for sel in formats.external_codecs()}
    assert [entry["path"] for entry in manifest] == sorted(entry["path"] for entry in manifest)


def _drop_vendor(external: Path) -> str:
    shutil.rmtree(external / "promptfoo")
    return f"{external / 'manifest.json'}: differs from the derivation; regenerate with --write"


def _drop_version(external: Path) -> str:
    record_path = external / "inspect/observations.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    for entry in record["files"].values():
        del entry["source"]["format_version"]
    record_path.write_text(json.dumps(record), encoding="utf-8")
    return "incomplete source selector"


def _edit_bytes(external: Path) -> str:
    capture = external / "otlp-json/capture.json"
    capture.write_bytes(capture.read_bytes() + b"\n")
    return "raw digest does not match the bytes"


def _stale_manifest(external: Path) -> str:
    (external / "manifest.json").write_text('{"formats": []}\n', encoding="utf-8")
    return "differs from the derivation"


def _corrupt_record(external: Path) -> str:
    (external / "deepeval/observations.json").write_text("{not json", encoding="utf-8")
    return "not JSON"


FAULTS = {
    "drop_vendor": _drop_vendor,
    "drop_version": _drop_version,
    "edit_bytes": _edit_bytes,
    "stale_manifest": _stale_manifest,
    "corrupt_record": _corrupt_record,
}


@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_matrix_cannot_pass_with_a_missing_or_altered_capture(tmp_path: Path, fault: str) -> None:
    repo = tmp_path / "repo"
    external = repo / "fixtures/golden/external"
    shutil.copytree(EXTERNAL, external)
    expected = FAULTS[fault](external)
    problems = external_manifest.check(external, repo)
    assert len(problems) == 1 and expected in problems[0], (fault, problems)
    if fault == "drop_vendor":
        # The derivation still succeeds on five vendors: only the committed manifest and the
        # registry comparison expose the loss, which is why both are gates.
        derived = external_manifest.derive(external, repo)
        assert {entry["format"] for entry in derived} != {
            sel[0] for sel in formats.external_codecs()
        }

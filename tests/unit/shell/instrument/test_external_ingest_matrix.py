"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from _external_formats import EXTERNAL_DIRECTORIES, ingestor
from _repo_paths import repo_root
from underwrite_core.canonical import digest_bytes

from underwrite.instrument.ingest import formats
from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES
from underwrite.instrument.ingest.project import Source
from underwrite.instrument.ingest.transport import Ingestor, UnsupportedFormat

ROOT = repo_root(Path(__file__))
MANIFEST = ROOT / "fixtures/golden/external/manifest.json"
ENTRIES = cast(list[dict[str, str]], json.loads(MANIFEST.read_text(encoding="utf-8"))["formats"])
BY_PATH = {entry["path"]: entry for entry in ENTRIES}


def _ingestor() -> Ingestor:
    return ingestor(formats.external_codecs(), max_bytes=DEFAULT_MAX_BYTES)


def test_manifest_is_the_whole_external_registry_and_nothing_else() -> None:
    selectors = {(entry["format"], entry["version"]) for entry in ENTRIES}
    assert selectors == set(formats.external_codecs())
    assert not (selectors & set(formats.native_codecs()))
    assert set(formats.trusted_codecs()) == selectors | set(formats.native_codecs())
    # The trusted registry carries each external codec itself, not a wrapper or a copy;
    # the per-reader tests own only which function their own module pins.
    trusted = formats.trusted_codecs()
    assert all(trusted[key] is codec for key, codec in formats.external_codecs().items())

    # contract set the provenance gate pins, owned once.
    assert {sel[0].split(".")[0] for sel in selectors} == EXTERNAL_DIRECTORIES


def _ident(path: str) -> str:
    capture = Path(path)
    return f"{capture.parent.name}/{capture.name[:24]}"


@pytest.mark.parametrize("path", sorted(BY_PATH), ids=_ident)
def test_each_capture_ingests_identically_by_file_and_body(path: str) -> None:
    entry = BY_PATH[path]
    target = ROOT / path
    raw = target.read_bytes()
    assert digest_bytes(raw) == "sha256:" + entry["raw_sha256"]
    source = Source(entry["format"], entry["version"], f"urn:test:{Path(path).parent.name}")
    ingestor = _ingestor()
    from_file = ingestor.file(target, source)
    from_body = ingestor.http_body(raw, "captured:http-body-not-a-request", source)
    assert from_file.observation == from_body.observation
    observation = from_file.observation
    assert observation["schema"] == "observation.v1"
    assert observation["kind"] == "eval_run"
    assert observation["source"] == {"format": entry["format"], "format_version": entry["version"]}
    assert observation["raw"] == {"ref": source.ref, "hash": "sha256:" + entry["raw_sha256"]}
    assert observation["payload"]  # whole file preserved, never empty for a real capture
    # How the whole document sits inside `payload` (bare, or under one key) is the one
    # fact each reader test still states for itself, beside its codec.


# The OpenInference capture is, by design, also a valid OTLP/JSON export (shared envelope,
# same collector); no other cross-selector acceptance exists and none may appear silently.
CROSS_ACCEPTED = {("fixtures/golden/external/openinference/capture.json", "otlp-json.traces")}


@pytest.mark.parametrize("path", sorted(BY_PATH), ids=_ident)
def test_unregistered_selectors_are_refused_before_the_file_is_read(path: str) -> None:
    entry = BY_PATH[path]
    ingestor = _ingestor()
    missing = ROOT / path.replace(".json", ".absent.json")
    assert not missing.exists()
    for fmt, version in ((entry["format"], "latest"), ("other", entry["version"])):
        # A path that does not exist still gets UNSUPPORTED_FORMAT: the selector is
        # resolved before any byte is read.
        with pytest.raises(UnsupportedFormat):
            ingestor.file(missing, Source(fmt, version, "urn:test:cross"))


@pytest.mark.parametrize("path", sorted(BY_PATH), ids=_ident)
def test_every_other_registered_profile_refuses_the_capture_with_a_typed_reason(path: str) -> None:
    entry = BY_PATH[path]
    ingestor = _ingestor()
    accepted: set[str] = set()
    for fmt, version in formats.external_codecs():
        if (fmt, version) == (entry["format"], entry["version"]):
            continue
        try:
            observation = ingestor.file(
                ROOT / path, Source(fmt, version, "urn:test:cross")
            ).observation
        except ValueError as error:
            # Only a codec's own typed refusal counts; a transport or decode error would
            # mean the file never reached the other profile's rules.
            assert type(error).__name__.endswith("PayloadError"), type(error)
            assert error.args and isinstance(error.args[0], str)
            continue
        accepted.add(fmt)
        assert observation["source"] == {"format": fmt, "format_version": version}
    assert accepted == {fmt for capture, fmt in CROSS_ACCEPTED if capture == path}

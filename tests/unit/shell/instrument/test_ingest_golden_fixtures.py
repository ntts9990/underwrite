"""Local examples preserve whole-observation file/body identity."""

from pathlib import Path

from _repo_paths import repo_root
from underwrite_core.canonical import digest_bytes

from underwrite.instrument.ingest.formats import native_codecs
from underwrite.instrument.ingest.project import Source
from underwrite.instrument.ingest.transport import Ingestor


def test_native_example_file_and_body_match() -> None:
    root = repo_root(Path(__file__))
    path = root / "fixtures/examples/measurement/bundle.json"
    raw = path.read_bytes()
    source = Source("underwrite.evidence-bundle", "v1", None)
    ingestor = Ingestor(
        root / "contracts/observation.v1.schema.json",
        native_codecs(),
        max_bytes=len(raw),
        max_depth=64,
    )
    file = ingestor.file(path, source)
    body = ingestor.http_body(raw, "local:captured-body", source)
    assert file.observation == body.observation
    assert file.observation["raw"] == {"ref": digest_bytes(raw), "hash": digest_bytes(raw)}
    assert file.transport_locator != body.transport_locator

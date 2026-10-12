"""API observations use the same canonical JSON values as decoded CLI output."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import SAFE_INT_MAX, canonical_bytes, content_digest, digest_bytes

from underwrite.cli.main import main
from underwrite.instrument.ingest.application import packaged_ingestor
from underwrite.instrument.ingest.project import Projection, ProjectionError, Source, project

ROOT = Path(__file__).resolve().parents[4]
SOURCE = Source("deepeval.test-run", "4.1.1", None)


def _assert_exact(left: Any, right: Any) -> None:
    assert type(left) is type(right)
    if isinstance(left, dict):
        left_dict = cast(dict[str, object], left)
        right_dict = cast(dict[str, object], right)
        assert left_dict.keys() == right_dict.keys()
        for key in left_dict:
            _assert_exact(left_dict[key], right_dict[key])
    elif isinstance(left, list):
        left_list = cast(list[object], left)
        right_list = cast(list[object], right)
        assert len(left_list) == len(right_list)
        for a, b in zip(left_list, right_list, strict=True):
            _assert_exact(a, b)
    else:
        assert left == right


def test_actual_deepeval_transports_and_decoded_cli_have_exact_same_types(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = (ROOT / "fixtures/golden/external/deepeval/test_run_20260914_132634.json").read_bytes()
    path = tmp_path / "source.json"
    path.write_bytes(raw)
    ingest = packaged_ingestor(max_bytes=len(raw), max_depth=32)
    observations = [
        ingest.file(path, SOURCE).observation,
        ingest.http_body(raw, "body", SOURCE).observation,
        ingest.http_body_file(path, "body-file", SOURCE).observation,
    ]
    assert (
        main(
            [
                "ingest",
                "--format",
                SOURCE.format,
                "--version",
                SOURCE.format_version,
                "--file",
                str(path),
                "--json",
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert output.err == ""
    decoded = json.loads(output.out)
    for observation in observations:
        _assert_exact(observation, decoded)
        assert cast(dict[str, object], observation["raw"])["hash"] == digest_bytes(raw)
        assert canonical_bytes(observation).decode() + "\n" == output.out


def test_common_projection_normalizes_numbers_without_mutating_original() -> None:
    payload: dict[str, object] = {
        "score": 1.0,
        "zero": -0.0,
        "fraction": 0.125,
        "large": 1e30,
        "truth": True,
        "false": False,
        "null": None,
        "cafe\u0301": [1.0, 1.0, "A\u030a", False],
        "maximum": SAFE_INT_MAX,
        "float_maximum": float(SAFE_INT_MAX),
        "minimum": -SAFE_INT_MAX,
    }
    before = deepcopy(payload)
    source = Source("underwrite.evidence-bundle", "v1", None)
    raw = json.dumps(payload).encode()
    observation = project(Projection("eval_run", payload), source, raw)
    expected = {**payload, "café": [1, 1, "Å", False], "score": 1, "zero": 0}
    expected["float_maximum"] = SAFE_INT_MAX
    del expected["cafe\u0301"]
    _assert_exact(observation["payload"], expected)
    assert "absent" not in cast(dict[str, object], observation["payload"])
    assert math.copysign(1, cast(dict[str, Any], observation["payload"])["zero"]) == 1
    _assert_exact(payload, before)
    _assert_exact(observation, json.loads(canonical_bytes(observation)))
    pre_representation = {**observation, "payload": {**expected, "score": 1.0, "zero": -0.0}}
    assert canonical_bytes(pre_representation) == canonical_bytes(observation)
    assert content_digest(pre_representation) == content_digest(observation)


def test_buffered_projection_retains_original_codec_numeric_types() -> None:
    raw = (ROOT / "fixtures/golden/external/deepeval/test_run_20260914_132634.json").read_bytes()
    ingest = packaged_ingestor(max_bytes=len(raw), max_depth=32)
    result, original = ingest.buffered_projection(raw, "buffer", SOURCE)
    raw_doc = json.loads(raw)
    _assert_exact(original.payload, raw_doc)
    metric = cast(dict[str, Any], original.payload)["testCases"][0]["metricsData"][0]
    normalized = cast(dict[str, Any], result.observation["payload"])["testCases"][0]["metricsData"][
        0
    ]
    assert type(metric["score"]) is float
    assert type(normalized["score"]) is int
    assert original.payload is not result.observation["payload"]


def test_cycle_remains_a_typed_projection_refusal() -> None:
    payload: dict[str, object] = {}
    payload["cycle"] = payload
    with pytest.raises(ProjectionError, match="INVALID_PROJECTION"):
        project(Projection("eval_run", payload), Source("test", "v1", None), b"{}")

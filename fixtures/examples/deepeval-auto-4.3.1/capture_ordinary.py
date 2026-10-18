"""Capture one native DeepEval 4.3.1 ordinary TestRun with fixed local metrics."""

import atexit
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path

out = Path(sys.argv[1]).resolve()
assert out.is_dir() and not list(out.iterdir()), "new empty output directory required"
network_attempts = []


def deny_network(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "socket.sendto"):
        network_attempts.append(event)
        raise RuntimeError("DEEPEVAL_431_PROBE_NETWORK_DISABLED")


sys.addaudithook(deny_network)
atexit.register(lambda: (out / "network-attempts.json").write_text(json.dumps(network_attempts)))

version = importlib.metadata.version("deepeval")
assert version == "4.3.1", version

from deepeval import evaluate  # noqa: E402
from deepeval.evaluate.configs import (  # noqa: E402
    AsyncConfig,
    CacheConfig,
    DisplayConfig,
    ErrorConfig,
)
from deepeval.metrics import BaseMetric  # noqa: E402
from deepeval.test_case import LLMTestCase  # noqa: E402


class FixedMetric(BaseMetric):
    def __init__(self, name, score, *, raises=False):
        self.label = name
        self.fixed_score = score
        self.raises = raises
        self.threshold = 0.5
        self.flaky = False
        self.async_mode = False
        self.verbose_mode = False
        self.model = None
        self.evaluation_model = None

    def measure(self, test_case, *args, **kwargs):
        if self.raises:
            raise RuntimeError("synthetic deterministic evaluator error")
        self.score = self.fixed_score
        self.reason = "Synthetic fixed metric; no model or independent truth claim."
        self.success = self.is_successful()
        return self.score

    async def a_measure(self, test_case, *args, **kwargs):
        return self.measure(test_case, *args, **kwargs)

    @property
    def __name__(self):
        return self.label


results = out / "results"
results.mkdir()
evaluate(
    test_cases=[
        LLMTestCase(
            name="synthetic-ordinary-case",
            input="synthetic input",
            actual_output="synthetic output",
            expected_output="synthetic reference",
        )
    ],
    metrics=[
        FixedMetric("positive", 1),
        FixedMetric("zero", 0),
        FixedMetric("error", None, raises=True),
    ],
    identifier="deepeval-431-ordinary",
    async_config=AsyncConfig(run_async=False),
    display_config=DisplayConfig(
        show_indicator=False,
        print_results=False,
        inspect_after_run=False,
        results_folder=str(results),
    ),
    cache_config=CacheConfig(write_cache=False, use_cache=False),
    error_config=ErrorConfig(ignore_errors=True),
)

native = list(results.glob("test_run_*.json"))
assert len(native) == 1, native
raw = native[0].read_bytes()
source = json.loads(raw)
(out / "capture-summary.json").write_text(
    json.dumps(
        {
            "producer": "deepeval",
            "producer_version": version,
            "python": sys.version,
            "native_file": str(native[0]),
            "native_sha256": hashlib.sha256(raw).hexdigest(),
            "native_bytes": len(raw),
            "top_level_keys": sorted(source),
            "network_attempts_at_inspection": list(network_attempts),
        },
        indent=2,
    )
)
print(json.dumps({"native_file": str(native[0]), "bytes": len(raw)}))

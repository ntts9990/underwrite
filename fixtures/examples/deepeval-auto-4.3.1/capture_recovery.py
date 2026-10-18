"""Capture a native failed-tool/recovery trace with no model or network."""

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
        raise RuntimeError("DEEPEVAL_AGENT_PROBE_NETWORK_DISABLED")


sys.addaudithook(deny_network)
atexit.register(lambda: (out / "network-attempts.json").write_text(json.dumps(network_attempts)))
assert importlib.metadata.version("deepeval") == "4.3.1"

from deepeval.dataset import EvaluationDataset, Golden  # noqa: E402
from deepeval.evaluate.configs import (  # noqa: E402
    AsyncConfig,
    CacheConfig,
    DisplayConfig,
    ErrorConfig,
)
from deepeval.metrics import BaseMetric  # noqa: E402
from deepeval.tracing import observe, update_current_span, update_current_trace  # noqa: E402


class FixedMetric(BaseMetric):
    def __init__(self):
        self.threshold = 0.5
        self.flaky = False
        self.async_mode = False
        self.verbose_mode = False
        self.model = None
        self.evaluation_model = None

    def measure(self, test_case, *args, **kwargs):
        self.score = 1
        self.reason = "Synthetic fixed outcome; observed recovery is not causal proof."
        self.success = self.is_successful()
        return self.score

    async def a_measure(self, test_case, *args, **kwargs):
        return self.measure(test_case, *args, **kwargs)

    @property
    def __name__(self):
        return "trace_fixed_recovery"


@observe(type="tool")
def primary_lookup(question: str) -> str:
    update_current_span(input=question)
    raise LookupError("synthetic expected miss")


@observe(type="tool")
def fallback_lookup(question: str) -> str:
    value = "4"
    update_current_span(input=question, output=value)
    return value


@observe(type="agent")
def recovering_agent(question: str) -> str:
    update_current_trace(name="synthetic-recovery-agent", input=question)
    try:
        result = primary_lookup(question)
    except LookupError:
        result = fallback_lookup(question)
    update_current_span(input=question, output=result)
    update_current_trace(output=result, expected_output="4")
    return result


dataset = EvaluationDataset(
    goldens=[Golden(name="synthetic-recovery-case", input="2+2?", expected_output="4")]
)
results = out / "results"
results.mkdir()
for golden in dataset.evals_iterator(
    metrics=[FixedMetric()],
    identifier="deepeval-agent-recovery-001",
    display_config=DisplayConfig(
        show_indicator=False,
        print_results=False,
        inspect_after_run=False,
        results_folder=str(results),
    ),
    cache_config=CacheConfig(write_cache=False, use_cache=False),
    error_config=ErrorConfig(ignore_errors=True),
    async_config=AsyncConfig(run_async=False),
):
    recovering_agent(golden.input)

native = list(results.glob("test_run_*.json"))
assert len(native) == 1, native
data = native[0].read_bytes()
(out / "capture-summary.json").write_text(
    json.dumps(
        {
            "producer": "deepeval",
            "producer_version": "4.3.1",
            "native_file": str(native[0]),
            "native_sha256": hashlib.sha256(data).hexdigest(),
            "native_bytes": len(data),
            "top_level_keys": sorted(json.loads(data)),
            "network_attempts_at_inspection": list(network_attempts),
        },
        indent=2,
    )
)
print(json.dumps({"native_file": str(native[0]), "bytes": len(data)}))

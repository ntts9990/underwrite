"""Capture a native DeepEval 4.2.8 agent/tool trace without a model or network."""

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

version = importlib.metadata.version("deepeval")
assert version == "4.2.8", version

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
    def __init__(self, name, score):
        self.label = name
        self.fixed_score = score
        self.threshold = 0.5
        self.flaky = False
        self.async_mode = False
        self.verbose_mode = False
        self.model = None
        self.evaluation_model = None

    def measure(self, test_case, *args, **kwargs):
        self.score = self.fixed_score
        self.reason = "Synthetic deterministic metric; no model or verified task quality."
        self.success = self.is_successful()
        return self.score

    async def a_measure(self, test_case, *args, **kwargs):
        return self.measure(test_case, *args, **kwargs)

    @property
    def __name__(self):
        return self.label


@observe(type="tool", metrics=[FixedMetric("lookup_observed", 1)])
def lookup_rule(question: str) -> str:
    rule = "2+2=4"
    update_current_span(input=question, output=rule)
    return rule


@observe(type="tool", metrics=[FixedMetric("verification_observed", 1)])
def verify_answer(answer: str) -> bool:
    verified = answer == "4"
    update_current_span(input=answer, output=verified)
    return verified


@observe(type="agent")
def deterministic_agent(question: str) -> str:
    update_current_trace(name="synthetic-arithmetic-agent", input=question)
    rule = lookup_rule(question)
    answer = rule.split("=")[1]
    verified = verify_answer(answer)
    assert verified
    update_current_span(input=question, output=answer)
    update_current_trace(output=answer, expected_output="4")
    return answer


dataset = EvaluationDataset(
    goldens=[Golden(name="synthetic-arithmetic-case", input="2+2?", expected_output="4")]
)
results = out / "results"
results.mkdir()
for golden in dataset.evals_iterator(
    metrics=[FixedMetric("trace_fixed", 1)],
    identifier="deepeval-agent-probe-001",
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
    deterministic_agent(golden.input)

native = list(results.glob("test_run_*.json"))
assert len(native) == 1, native
data = native[0].read_bytes()
source = json.loads(data)
(out / "capture-summary.json").write_text(
    json.dumps(
        {
            "producer": "deepeval",
            "producer_version": version,
            "python": sys.version,
            "native_file": str(native[0]),
            "native_sha256": hashlib.sha256(data).hexdigest(),
            "native_bytes": len(data),
            "top_level_keys": sorted(source),
            "network_attempts_at_inspection": list(network_attempts),
        },
        indent=2,
    )
)
print(json.dumps({"native_file": str(native[0]), "bytes": len(data)}))

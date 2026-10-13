"""Public DeepEval evaluate/assert_test probe; deterministic metrics, no producer patches."""

import atexit
import importlib.metadata
import json
import sys
from pathlib import Path

out = Path(sys.argv[1]).resolve()
network_attempts = []


def deny_network(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "socket.sendto"):
        network_attempts.append(event)
        raise RuntimeError("C34_NETWORK_DISABLED")


sys.addaudithook(deny_network)
atexit.register(lambda: (out / "network-attempts.json").write_text(json.dumps(network_attempts)))
version = importlib.metadata.version("deepeval")
assert version == "4.2.8", version
(out / "producer-version.json").write_text(
    json.dumps(
        {
            "package": "deepeval",
            "version": version,
            "python": sys.version,
            "distribution_files": len(importlib.metadata.files("deepeval")),
        },
        indent=2,
    )
)


def capture():
    from deepeval.metrics import BaseMetric
    from deepeval.test_case import LLMTestCase

    class FixedMetric(BaseMetric):
        def __init__(self, name, score, threshold=0.5, flaky=False, raises=False):
            self.label = name
            self.fixed_score = score
            self.threshold = threshold
            self.flaky = flaky
            self.raises = raises
            self.async_mode = False
            self.verbose_mode = False
            self.model = None
            self.evaluation_model = None

        def measure(self, test_case, *args, **kwargs):
            if self.raises:
                raise RuntimeError("synthetic deterministic metric error")
            self.score = self.fixed_score
            self.reason = "Synthetic fixed metric; no model or independent truth claim."
            self.success = self.is_successful()
            return self.score

        async def a_measure(self, test_case, *args, **kwargs):
            return self.measure(test_case, *args, **kwargs)

        @property
        def __name__(self):
            return self.label

    def case(name, flaky=False):
        return LLMTestCase(
            name=name,
            input="synthetic input",
            actual_output="synthetic output",
            expected_output="synthetic reference",
            flaky=flaky,
        )

    def selected_metrics(name):
        return metrics_for(name, FixedMetric)

    outcomes = evaluate_cases(case, selected_metrics)
    gating = check_gating(case, selected_metrics, FixedMetric)
    print(
        json.dumps(
            {"evaluate": outcomes, "gating": gating, "network_attempts": network_attempts}, indent=2
        )
    )


def metrics_for(name, metric_type):
    if name == "ordinary":
        return [
            metric_type("positive", 1),
            metric_type("zero", 0),
            metric_type("error", None, raises=True),
        ]
    if name == "flaky_metric":
        return [metric_type("nonflaky_pass", 1), metric_type("flaky_fail", 0, flaky=True)]
    if name == "flaky_case":
        return [metric_type("nonflaky_fail", 0)]
    if name == "score_without_verdict":
        return [metric_type("nonflaky_pass", 1), metric_type("score_only", 0.3, threshold=None)]
    raise ValueError(name)


def evaluate_cases(case, metrics_for):
    from deepeval import evaluate
    from deepeval.evaluate.configs import AsyncConfig, CacheConfig, DisplayConfig, ErrorConfig

    outcomes = {}
    for name in ("ordinary", "flaky_metric", "flaky_case", "score_without_verdict"):
        folder = out / name
        folder.mkdir()
        try:
            result = evaluate(
                test_cases=[case(name, flaky=name == "flaky_case")],
                metrics=metrics_for(name),
                identifier="c34-" + name,
                async_config=AsyncConfig(run_async=False),
                display_config=DisplayConfig(
                    show_indicator=False,
                    print_results=False,
                    inspect_after_run=False,
                    results_folder=str(folder),
                ),
                cache_config=CacheConfig(write_cache=False, use_cache=False),
                error_config=ErrorConfig(ignore_errors=True),
            )
            outcomes[name] = {
                "state": "evaluated",
                "return_type": type(result).__name__,
                "test_run_exports": [p.name for p in folder.glob("test_run_*.json")],
            }
            (folder / "public-evaluation-result.json").write_text(result.model_dump_json(indent=2))
        except Exception as exc:
            outcomes[name] = {
                "state": "error",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
    (out / "evaluate-outcomes.json").write_text(json.dumps(outcomes, indent=2))
    return outcomes


def check_gating(case, metrics_for, metric_type):
    from deepeval import assert_test

    # Evaluate() is reporting; inspect assert_test() separately for actual gating.
    gating = {}
    for name, flaky in (("ordinary_fail", False), ("flaky_case", True), ("flaky_metric", False)):
        metrics = (
            metrics_for("flaky_metric") if name == "flaky_metric" else [metric_type("failed", 0)]
        )
        try:
            assert_test(case(name, flaky=flaky), metrics, run_async=False)
            gating[name] = {"raised": False}
        except AssertionError:
            gating[name] = {"raised": True, "error_type": "AssertionError"}
        except Exception as exc:
            gating[name] = {"raised": True, "error_type": type(exc).__name__, "message": str(exc)}
    (out / "gating.json").write_text(json.dumps(gating, indent=2))
    return gating


capture()

"""
Deterministic local DeepEval sample for the pinned 4.1.1 TestRun capture.

No model, no network, no credential: `ExactMatch` compares actual_output with
expected_output; `AlwaysErrors` raises so the run records a metric `error`.
Run with `deepeval test run sample_eval.py --ignore-errors` (see SOURCE.md).
"""

from deepeval import assert_test
from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase


class ExactMatch(BaseMetric):
    def __init__(self) -> None:
        self.threshold = 0.5
        self.async_mode = False

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        self.score = 1.0 if test_case.actual_output == test_case.expected_output else 0.0
        self.reason = "actual_output equals expected_output" if self.score else "mismatch"
        self.success = self.score >= self.threshold
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return bool(self.success)

    @property
    def __name__(self) -> str:
        return "Exact Match"


class AlwaysErrors(BaseMetric):
    def __init__(self) -> None:
        self.threshold = 0.5
        self.async_mode = False

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        raise RuntimeError("deterministic evaluator failure for the error capture")

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return False

    @property
    def __name__(self) -> str:
        return "Always Errors"


def test_observed_pass() -> None:
    assert_test(
        LLMTestCase(input="2+2", actual_output="4", expected_output="4"),
        [ExactMatch()],
    )


def test_observed_fail() -> None:
    assert_test(
        LLMTestCase(input="capital of Korea", actual_output="Busan", expected_output="Seoul"),
        [ExactMatch()],
    )


def test_error_and_observed() -> None:
    assert_test(
        LLMTestCase(input="한글 입력", actual_output="한글 출력", expected_output="한글 출력"),
        [ExactMatch(), AlwaysErrors()],
    )

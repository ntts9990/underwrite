"""Deterministic Inspect task for the underwrite golden capture.

Runs against the built-in ``mockllm/model`` provider (no credentials, no
network): every generation is the provider's fixed default output, so the
``includes`` scorer yields one correct and two incorrect samples with finite
scores only. Run with::

    inspect eval sample_task.py --model mockllm/model --log-format json --log-dir <dir>
"""

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import includes
from inspect_ai.solver import generate, system_message


@task
def underwrite_capture() -> Task:
    return Task(
        dataset=MemoryDataset(
            [
                Sample(
                    input="이 모델의 기본 출력 문장을 그대로 답하세요.",
                    target="Default output from mockllm/model",
                    metadata={"lang": "ko", "expect": "correct"},
                ),
                Sample(
                    input="대한민국의 수도는 어디입니까?",
                    target="서울",
                    metadata={"lang": "ko", "expect": "incorrect"},
                ),
                Sample(
                    input="Name the provider.",
                    target="mockllm",
                    metadata={"lang": "en", "expect": "correct"},
                ),
            ],
            name="underwrite-capture",
        ),
        solver=[system_message("You are a deterministic test model."), generate()],
        scorer=includes(),
        name="underwrite_capture",
        metadata={"purpose": "underwrite example"},
    )

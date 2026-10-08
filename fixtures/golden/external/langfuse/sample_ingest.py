"""
Deterministic local Langfuse producer for the pinned export capture.

No model call, no network beyond the local self-hosted Langfuse (LANGFUSE_HOST):
one trace with a span -> generation -> retriever tree, fixed inputs/outputs/usage
(Korean text exercises NFC), and scores of every data type at observation and
"""

import os
import sys

from langfuse import Langfuse

client = Langfuse(
    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    host=os.environ["LANGFUSE_HOST"],
    environment="underwrite-capture",
)
assert client.auth_check(), "local Langfuse did not accept the project keys"

with client.start_as_current_observation(
    name="underwrite-capture-root",
    as_type="span",
    input={"question": "한글 질문: 2 더하기 2는?"},
    metadata={"card": "example", "deterministic": True},
) as root:
    with root.start_as_current_observation(
        name="answer",
        as_type="generation",
        model="deterministic-echo",
        model_parameters={"temperature": 0},
        input=[{"role": "user", "content": "2 더하기 2는?"}],
        usage_details={"input": 7, "output": 1, "total": 8},
        cost_details={"total": 0.0},
    ) as generation:
        generation.update(output={"role": "assistant", "content": "4"})
        generation.score(name="exact_match", value=1.0, data_type="NUMERIC", comment="4 == 4")
        generation.score(name="is_correct", value=True, data_type="BOOLEAN")
    with root.start_as_current_observation(
        name="lookup",
        as_type="retriever",
        input={"query": "덧셈 규칙"},
        output={"documents": ["덧셈은 교환법칙을 만족한다"]},
    ) as retriever:
        retriever.score(name="relevance", value="high", data_type="CATEGORICAL")
    root.update(output={"answer": "4"})
    root.score_trace(name="task_success", value=1.0, data_type="NUMERIC")
    root.score_trace(name="reviewer_note", value="deterministic sample", data_type="TEXT")
    trace_id = client.get_current_trace_id()

client.flush()
client.shutdown()
print(f"trace_id={trace_id}")
sys.exit(0)

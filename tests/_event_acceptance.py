"""Synthetic declared-event fixtures shared by acceptance boundary tests."""

from typing import Any

from underwrite.measurement.event_read import assemble_event_read
from underwrite.measurement_contract.semantics import (
    ASSUMPTIONS,
    PROFILE,
    parse_semantics,
    semantic_digest,
)


def inputs(labels: list[int], *, prediction: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    owner = {"id": "synthetic", "version": "1", "reference": "synthetic example"}
    sem: dict[str, Any] = {
        "schema": "measurement_semantics.v1",
        "profile": PROFILE,
        "construct": {"id": "event", "definition": "Synthetic event rate"},
        "event": {"id": "event", "positive_label_definition": "One means event occurred"},
        "unit": "proportion",
        "sampling_unit": "case",
        "desirable_direction": "higher",
        "outcome": {
            "instrument": "label",
            "metric_id": "event",
            "label_source": owner,
            "rubric": owner,
        },
        "prediction": {"status": "unavailable"},
        "population": {"frame_id": "synthetic", "description": "Constructed cases"},
    }
    if prediction:
        sem["prediction"] = {
            "status": "declared_probability_of_event",
            "instrument": "forecast",
            "metric_id": "probability",
            "event_id": "event",
            "timing": "before_outcome",
            "reference": "synthetic",
        }
    ids = [f"case-{i}" for i in range(len(labels))]
    readings = [
        {
            "instrument": instrument,
            "metric_id": metric,
            "rows": [
                {
                    "case_id": key,
                    "repeat": 0,
                    "sequence": i,
                    "outcome": label,
                    "score": float(label),
                }
                for i, (key, label) in enumerate(zip(ids, labels, strict=True))
            ],
        }
        for instrument, metric in (("label", "event"), ("forecast", "probability"))
    ]
    observation = {
        "source": {"format": "underwrite.evidence-bundle", "format_version": "v1"},
        "kind": "eval_run",
        "payload": {
            "schema_version": "underwrite.evidence-bundle.v1",
            "measurement_semantics": sem,
            "readings": readings,
            "availability": [],
        },
    }
    policy: dict[str, Any] = {
        "schema": "measurement_policy.v3",
        "profile": PROFILE,
        "semantic_digest": semantic_digest(parse_semantics(sem)),
        "scope": "aggregate",
        "subject": {"subject_id": "synthetic", "conditions": {}},
        "expected_case_ids": ids,
        "eprocess": {
            "p0": 0.5,
            "alpha_total": 0.05,
            "prior_a": 1,
            "prior_b": 1,
            "max_observations": None,
        },
        "assumptions": dict.fromkeys(ASSUMPTIONS, "declared"),
        "diagnostics": {"ece_bins": 2, "min_bin_n": 2, "all_k": 2},
    }
    return observation, policy


def event_read(subject: str, verdict: str = "pass", state: str = "full") -> dict[str, Any]:
    labels = [0] * 8 if verdict == "fail" else [0, 1] * 4 if verdict == "warn" else [1] * 8
    observation, policy = inputs(labels)
    policy["subject"]["subject_id"] = subject
    if state != "full":
        assert verdict == "not_measured"
        keep = 4 if state == "partial" else 0
        for reading in observation["payload"]["readings"]:
            reading["rows"] = reading["rows"][:keep]
    elif verdict == "not_measured":
        policy["assumptions"][next(iter(ASSUMPTIONS))] = "unknown"
    return assemble_event_read(
        observation,
        policy,
        observation_digest="sha256:" + "c" * 64,
        policy_digest="sha256:" + "a" * 64,
    )


SEMANTIC_DIGEST = semantic_digest(
    parse_semantics(inputs([1] * 8)[0]["payload"]["measurement_semantics"])
)

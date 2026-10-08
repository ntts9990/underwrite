"""Standalone behavior and boundary checks."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import content_digest, tagged_digest

from underwrite.acceptance import incident as inc
from underwrite.acceptance.incident import IncidentInputError, extend_eval_set, regression_case
from underwrite.instrument.evidence.split import assign_split

ROOT = repo_root(Path(__file__))
RAW_HASH = "sha256:" + "c" * 64


def _observation(kind: str = "incident_record", **payload: Any) -> dict[str, Any]:
    if not payload:
        expectation = "expected_behavior" if kind == "incident_record" else "corrected_output"
        payload = {"input": {"prompt": "refund order 17"}, expectation: "ask for the order id"}
    return {
        "schema": "observation.v1",
        "source": {"format": "hand.constructed", "format_version": "v1"},
        "kind": kind,
        "payload": payload,
        "raw": {"ref": f"tickets/{kind}/17", "hash": RAW_HASH},
        "normalization": {"nfc": True},
    }


def _code(observation: object) -> str:
    with pytest.raises(IncidentInputError) as caught:
        regression_case(observation)
    return caught.value.code


@pytest.mark.parametrize(
    ("kind", "expectation"),
    [("incident_record", "expected_behavior"), ("human_correction", "corrected_output")],
)
def test_each_incident_kind_becomes_a_case_with_lineage(kind: str, expectation: str) -> None:
    observation = _observation(kind)
    case = regression_case(observation)
    digest = content_digest(observation)
    assert case.case_id == tagged_digest(inc.CASE_TAG, digest.encode("ascii"))
    assert case.kind == kind
    assert case.input == observation["payload"]["input"]
    assert case.expected == observation["payload"][expectation]
    assert case.lineage == inc.Lineage(digest, f"tickets/{kind}/17", RAW_HASH)
    assert inc.PAYLOAD_KEYS[kind] == ("input", expectation)  # type: ignore[index]


def test_case_is_deterministic_and_key_order_free() -> None:
    reordered = dict(reversed(list(_observation().items())))
    assert regression_case(reordered) == regression_case(_observation())


@pytest.mark.parametrize(
    "change",
    [
        ("payload", {"input": "other", "expected_behavior": "ask for the order id"}),
        ("raw", {"ref": "tickets/incident_record/18", "hash": RAW_HASH}),
        ("raw", {"ref": "tickets/incident_record/17", "hash": "sha256:" + "d" * 64}),
    ],
)
def test_a_different_record_is_a_different_case(change: tuple[str, Any]) -> None:
    changed = _observation()
    changed[change[0]] = change[1]
    assert regression_case(changed).case_id != regression_case(_observation()).case_id


def _schema_kinds() -> list[str]:
    schema = json.loads((ROOT / "contracts/observation.v1.schema.json").read_text("utf-8"))
    return list(schema["properties"]["kind"]["enum"])


def test_incident_kinds_are_observation_v1_kinds() -> None:
    assert set(inc.PAYLOAD_KEYS) <= set(_schema_kinds())


@pytest.mark.parametrize("kind", [k for k in _schema_kinds() if k not in inc.PAYLOAD_KEYS])
def test_every_other_kind_is_unsupported(kind: str) -> None:
    assert _code(_observation(kind, input="x", expected_behavior="y")) == "UNSUPPORTED_KIND"


def _with(path: tuple[str, ...], value: object) -> dict[str, Any]:
    observation = _observation()
    node: Any = observation
    for key in path[:-1]:
        node = node[key]
    if value is _DELETE:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return observation


_DELETE = object()
INVALID, KEY = "INVALID_OBSERVATION", "MISSING_PAYLOAD_KEY"
REFUSALS: list[tuple[str, object, str, str]] = [
    ("not an object", ["observation.v1"], INVALID, "/"),
    ("wrong schema", _with(("schema",), "observation.v2"), INVALID, "/schema"),
    ("unhashable kind", _with(("kind",), ["x"]), "UNSUPPORTED_KIND", "['x']"),
    (
        "case variant kind",
        _with(("kind",), "Incident_Record"),
        "UNSUPPORTED_KIND",
        "'Incident_Record'",
    ),
    ("missing kind", _with(("kind",), _DELETE), "UNSUPPORTED_KIND", "None"),
    ("payload not an object", _with(("payload",), "text"), INVALID, "/payload"),
    ("missing raw", _with(("raw",), _DELETE), INVALID, "/raw"),
    ("missing raw hash", _with(("raw", "hash"), _DELETE), INVALID, "/raw/hash"),
    ("malformed raw hash", _with(("raw", "hash"), "sha256:c"), INVALID, "/raw/hash"),
    ("missing raw ref", _with(("raw", "ref"), _DELETE), INVALID, "/raw/ref"),
    ("empty raw ref", _with(("raw", "ref"), ""), INVALID, "/raw/ref"),
    ("missing input", _with(("payload", "input"), _DELETE), KEY, "input"),
    (
        "missing expectation",
        _with(("payload", "expected_behavior"), _DELETE),
        KEY,
        "expected_behavior",
    ),
    (
        "correction with the incident key",
        _observation("human_correction", input="x", expected_behavior="y"),
        KEY,
        "corrected_output",
    ),
    ("non-finite payload", _with(("payload", "input"), float("nan")), INVALID, "/"),
    ("non-NFC payload", _with(("payload", "input"), "e\u0301"), INVALID, "/"),
    ("unsafe integer", _with(("payload", "input"), 2**60), INVALID, "/"),
]


@pytest.mark.parametrize("case", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_refuses_what_cannot_become_a_case(case: tuple[str, object, str, str]) -> None:
    _, observation, code, detail = case
    with pytest.raises(IncidentInputError) as caught:
        regression_case(observation)
    assert (caught.value.code, caught.value.detail) == (code, detail)
    assert str(caught.value) == f"{code}: {detail}"


def test_case_holds_private_copies() -> None:
    observation = _observation()
    before = copy.deepcopy(observation)
    case = regression_case(observation)
    assert observation == before
    case.input["prompt"] = "changed"
    assert observation == before
    observation["payload"]["input"]["prompt"] = "changed again"
    assert regression_case(before).input == {"prompt": "refund order 17"}


def _cases(count: int) -> list[inc.RegressionCase]:
    return [
        regression_case(_observation(input=f"q{n}", expected_behavior=f"a{n}"))
        for n in range(count)
    ]


def test_extend_eval_set_is_idempotent_and_order_preserving() -> None:
    first, second, third = _cases(3)
    eval_set = extend_eval_set((first,), [second, third, second])
    assert eval_set == (first, second, third)
    assert extend_eval_set(eval_set, [third, first]) == eval_set
    assert extend_eval_set(extend_eval_set((), [first]), [first]) == (first,)


def test_extend_eval_set_does_not_mutate_its_inputs() -> None:
    first, second = _cases(2)
    eval_set, cases = [first], [second]
    extend_eval_set(eval_set, cases)
    assert (eval_set, cases) == ([first], [second])


def test_conflicting_cases_under_one_id_are_refused() -> None:
    (case,) = _cases(1)
    forged = inc.RegressionCase(case.case_id, case.kind, "other input", case.expected, case.lineage)
    with pytest.raises(IncidentInputError) as caught:
        extend_eval_set([case], [forged])
    assert (caught.value.code, caught.value.detail) == ("CONFLICTING_CASE", case.case_id)


def test_new_cases_leave_prior_split_assignments_alone() -> None:
    prior, added = _cases(12)[:8], _cases(12)[8:]
    ids = [case.case_id for case in prior]
    grown = [case.case_id for case in extend_eval_set(prior, added)]
    before = assign_split(ids, salt="author-salt", confirm_fraction=0.25).assignments
    after = assign_split(grown, salt="author-salt", confirm_fraction=0.25).assignments
    assert set(before) <= set(after)

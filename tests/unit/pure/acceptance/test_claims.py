"""Standalone behavior and boundary checks."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import canonical_bytes, tagged_digest

from underwrite.acceptance import claims as cl
from underwrite.acceptance.claims import ClaimsInputError, parse_claims
from underwrite.instrument.evidence.split import assign_split
from underwrite.measurement import read_input

ROOT = repo_root(Path(__file__))
POLICY_A = "sha256:" + "a" * 64
POLICY_B = "sha256:" + "b" * 64


def _claim(claim_id: str, subject_id: str, effect: str = "validity") -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "subject_id": subject_id,
        "effect": effect,
        "policy_digest": POLICY_A,
    }


def _document() -> dict[str, Any]:
    pair = [
        _claim("quality", "suite.quality", "disqualifying"),
        _claim("latency", "suite.latency"),
    ]
    return {
        "schema": "declared_claims.v1",
        "candidate_id": "prompt-v2",
        "required_claims": {
            "same_batch": copy.deepcopy(pair),
            "held_in": copy.deepcopy(pair),
            "held_out": copy.deepcopy(pair),
        },
        "selection": {"salt": "author-salt", "confirm_fraction": 0.25, "case_ids": ["c2", "c1"]},
    }


def test_parses_every_basis_sorted_by_claim_id() -> None:
    parsed = parse_claims(_document())
    for basis in cl.BASES:
        ids = [claim.claim_id for claim in cl.claims_for_basis(parsed, basis)]
        assert ids == sorted(ids) == ["latency", "quality"]
    assert parsed.candidate_id == "prompt-v2"
    assert parsed.selection.case_ids == ("c1", "c2")
    assert re.fullmatch(cl.DIGEST_PATTERN, parsed.digest)


def test_digest_is_the_tagged_digest_of_the_normalized_body() -> None:
    document = _document()
    for basis in cl.BASES:
        claims: list[dict[str, Any]] = document["required_claims"][basis]
        claims.sort(key=lambda claim: claim["claim_id"])
    document["selection"]["case_ids"].sort()
    expected = tagged_digest(cl.DIGEST_TAG, canonical_bytes(document))
    assert parse_claims(_document()).digest == expected


def _reordered(document: dict[str, Any]) -> dict[str, Any]:
    for basis in cl.BASES:
        document["required_claims"][basis].reverse()
    document["selection"]["case_ids"].reverse()
    return dict(reversed(list(document.items())))


def test_digest_is_invariant_under_equivalent_reordering() -> None:
    assert parse_claims(_reordered(_document())).digest == parse_claims(_document()).digest


def _set(path: tuple[str | int, ...], value: object) -> dict[str, Any]:
    document = _document()
    node: Any = document
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return document


CHANGES = [
    (("candidate_id",), "prompt-v3"),
    (("required_claims", "same_batch", 0, "claim_id"), "quality-2"),
    (("required_claims", "held_in", 1, "subject_id"), "suite.other"),
    (("required_claims", "held_out", 0, "effect"), "validity"),
    (("required_claims", "held_out", 1, "policy_digest"), POLICY_B),
    (("selection", "salt"), "other-salt"),
    (("selection", "confirm_fraction"), 0.5),
    (("selection", "case_ids"), ["c1", "c3"]),
]


@pytest.mark.parametrize(("path", "value"), CHANGES)
def test_digest_changes_with_any_claim_or_selection_change(
    path: tuple[str | int, ...], value: object
) -> None:
    assert parse_claims(_set(path, value)).digest != parse_claims(_document()).digest


def _without(*path: str | int) -> dict[str, Any]:
    document = _document()
    node: Any = document
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    return document


def _claims_in(basis: str, count: int) -> dict[str, Any]:
    items = [_claim(f"c{index}", f"s{index}") for index in range(count)]
    return _set(("required_claims", basis), items)


REQ = "/required_claims"
REFUSALS: list[tuple[str, object, str, str]] = [
    ("not an object", [], "INVALID_CLAIMS", "/"),
    ("unknown top-level key", _set(("threshold",), 0.9), "INVALID_CLAIMS", "/"),
    ("wrong schema", _set(("schema",), "declared_claims.v2"), "INVALID_CLAIMS", "/schema"),
    ("missing candidate", _without("candidate_id"), "INVALID_CLAIMS", "/candidate_id"),
    ("bad candidate id", _set(("candidate_id",), "has space"), "INVALID_CLAIMS", "/candidate_id"),
    ("claims not an object", _set(("required_claims",), []), "INVALID_CLAIMS", REQ),
    ("missing basis", _without("required_claims", "held_out"), "MISSING_BASIS", f"{REQ}/held_out"),
    ("unknown basis", _set(("required_claims", "sealed_test"), []), "INVALID_CLAIMS", REQ),
    (
        "basis not a list",
        _set(("required_claims", "held_in"), {}),
        "INVALID_CLAIMS",
        f"{REQ}/held_in",
    ),
    ("empty basis", _claims_in("same_batch", 0), "CLAIM_COUNT_OUT_OF_RANGE", f"{REQ}/same_batch"),
    (
        "65 claims",
        _claims_in("held_in", cl.MAX_CLAIMS_PER_BASIS + 1),
        "CLAIM_COUNT_OUT_OF_RANGE",
        f"{REQ}/held_in",
    ),
    (
        "duplicate claim id",
        _set(("required_claims", "held_out", 1, "claim_id"), "quality"),
        "DUPLICATE_CLAIM_ID",
        f"{REQ}/held_out",
    ),
    (
        "duplicate subject id",
        _set(("required_claims", "same_batch", 1, "subject_id"), "suite.quality"),
        "DUPLICATE_SUBJECT_ID",
        f"{REQ}/same_batch",
    ),
    (
        "bad effect",
        _set(("required_claims", "held_in", 0, "effect"), "advisory"),
        "INVALID_EFFECT",
        f"{REQ}/held_in/0/effect",
    ),
    (
        "missing effect",
        _without("required_claims", "held_in", 1, "effect"),
        "INVALID_EFFECT",
        f"{REQ}/held_in/1/effect",
    ),
    (
        "missing policy digest",
        _without("required_claims", "held_out", 1, "policy_digest"),
        "INVALID_POLICY_DIGEST",
        f"{REQ}/held_out/1/policy_digest",
    ),
    (
        "short policy digest",
        _set(("required_claims", "held_out", 0, "policy_digest"), "sha256:abc"),
        "INVALID_POLICY_DIGEST",
        f"{REQ}/held_out/0/policy_digest",
    ),
    (
        "threshold inside a claim",
        _set(("required_claims", "held_out", 1, "threshold"), 0.9),
        "INVALID_CLAIMS",
        f"{REQ}/held_out/1",
    ),
    (
        "bad claim id",
        _set(("required_claims", "same_batch", 1, "claim_id"), ""),
        "INVALID_CLAIMS",
        f"{REQ}/same_batch/1/claim_id",
    ),
    (
        "bad subject id",
        _set(("required_claims", "same_batch", 0, "subject_id"), "a/b"),
        "INVALID_CLAIMS",
        f"{REQ}/same_batch/0/subject_id",
    ),
    (
        "claim not an object",
        _set(("required_claims", "same_batch", 0), "quality"),
        "INVALID_CLAIMS",
        f"{REQ}/same_batch/0",
    ),
    ("selection missing", _without("selection"), "INVALID_SELECTION", "/selection"),
    ("unknown selection key", _set(("selection", "seed"), 7), "INVALID_SELECTION", "/selection"),
    ("missing salt", _without("selection", "salt"), "INVALID_SELECTION", "/selection/salt"),
    ("empty salt", _set(("selection", "salt"), ""), "INVALID_SELECTION", "/selection/salt"),
    ("non-NFC salt", _set(("selection", "salt"), "é"), "INVALID_SELECTION", "/selection"),
    *[
        (
            f"fraction {value!r}",
            _set(("selection", "confirm_fraction"), value),
            "INVALID_SELECTION",
            "/selection/confirm_fraction",
        )
        for value in (1.0, 0.0, 1.5, -0.25, True, 1, "0.25", None)
    ],
    *[
        (
            f"case ids {value!r}",
            _set(("selection", "case_ids"), value),
            "INVALID_SELECTION",
            "/selection/case_ids",
        )
        for value in ([], ["c1", "c1"], [" "], ["c1", 2], "c1", None)
    ],
]


@pytest.mark.parametrize(
    ("label", "document", "code", "detail"), REFUSALS, ids=[r[0] for r in REFUSALS]
)
def test_refuses_malformed_claims(label: str, document: object, code: str, detail: str) -> None:
    del label
    with pytest.raises(ClaimsInputError) as caught:
        parse_claims(document)
    assert (caught.value.code, caught.value.detail) == (code, detail)
    assert str(caught.value) == f"{code}: {detail}"


@pytest.mark.parametrize("count", [1, cl.MAX_CLAIMS_PER_BASIS])
def test_basis_accepts_one_to_the_maximum_claims(count: int) -> None:
    parsed = parse_claims(_claims_in("held_in", count))
    assert len(parsed.held_in) == count


def test_selection_bound_is_the_largest_population_one_basis_can_measure() -> None:
    assert cl.MAX_SELECTION_CASES == cl.MAX_CLAIMS_PER_BASIS * read_input.MAX_CASES


@pytest.mark.parametrize("count", [1, cl.MAX_SELECTION_CASES])
def test_selection_accepts_one_to_the_bound(count: int) -> None:
    document = _document()
    document["selection"]["case_ids"] = [f"c{i}" for i in range(count)]
    assert len(parse_claims(document).selection.case_ids) == count


def test_selection_refuses_one_past_the_bound() -> None:
    document = _document()
    document["selection"]["case_ids"] = [f"c{i}" for i in range(cl.MAX_SELECTION_CASES + 1)]
    with pytest.raises(ClaimsInputError) as caught:
        parse_claims(document)
    assert (caught.value.code, caught.value.detail) == ("INVALID_SELECTION", "/selection/case_ids")


def test_input_is_not_mutated() -> None:
    document = _document()
    before = json.dumps(document, sort_keys=True)
    parse_claims(document)
    assert json.dumps(document, sort_keys=True) == before


def test_subject_pattern_is_read_v1s() -> None:
    schema = json.loads((ROOT / "contracts/read.v1.schema.json").read_text(encoding="utf-8"))
    owner = schema["$defs"]["subject"]["properties"]["subject_id"]["pattern"]
    assert cl.ID_PATTERN == owner


def _selection_with(case_ids: list[str], salt: str, fraction: float) -> cl.DeclaredClaims:
    document = _document()
    document["selection"] = {"salt": salt, "confirm_fraction": fraction, "case_ids": case_ids}
    return parse_claims(document)


def _assignment(parsed: cl.DeclaredClaims) -> dict[str, str]:
    selection = parsed.selection
    split = assign_split(
        list(selection.case_ids), salt=selection.salt, confirm_fraction=selection.confirm_fraction
    )
    return {item.case_id: item.split for item in split.assignments}


def test_selection_on_explore_cases_passes_and_a_confirm_case_leaks() -> None:
    population = [f"case-{index}" for index in range(40)]
    split = assign_split(population, salt="author-salt", confirm_fraction=0.25)
    explore = [a.case_id for a in split.assignments if a.split == "explore"]
    confirm = [a.case_id for a in split.assignments if a.split == "confirm"]
    assert explore and confirm
    clean = _selection_with(explore, "author-salt", 0.25)
    cl.check_selection(clean, _assignment(clean))
    leaky = _selection_with([*explore, *confirm[:2]], "author-salt", 0.25)
    with pytest.raises(ClaimsInputError) as caught:
        cl.check_selection(leaky, _assignment(leaky))
    assert caught.value.code == "SELECTION_LEAKS_CONFIRM"
    assert caught.value.detail == ",".join(sorted(confirm[:2]))


@pytest.mark.parametrize(
    "assignment",
    [
        {},
        {"c1": "explore"},
        {"c1": "explore", "c2": "explore", "c3": "explore"},
        {"c1": "explore", "c2": "holdout"},
    ],
)
def test_selection_assignment_must_cover_exactly_the_selection(assignment: dict[str, str]) -> None:
    with pytest.raises(ClaimsInputError) as caught:
        cl.check_selection(parse_claims(_document()), assignment)
    assert (caught.value.code, caught.value.detail) == ("INVALID_SELECTION", "/selection/case_ids")


def test_claims_for_an_unknown_basis_are_refused() -> None:
    with pytest.raises(ClaimsInputError) as caught:
        cl.claims_for_basis(parse_claims(_document()), "sealed_test")  # type: ignore[arg-type]
    assert (caught.value.code, caught.value.detail) == ("INVALID_CLAIMS", "/basis")


def test_limitations_name_the_unverified_preconditions() -> None:
    assert cl.LIMITATIONS == ("claims_sealing_is_digest_binding", "split_parameters_author_chosen")
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", code) for code in cl.LIMITATIONS)

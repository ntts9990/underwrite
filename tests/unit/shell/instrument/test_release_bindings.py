"""Fixed-profile binding facts preserve independent uncertainty and comparisons."""

from itertools import product

import pytest

from underwrite.instrument.ingest.project import Projection
from underwrite.instrument.ingest.release_bindings import (
    FIELDS,
    PROFILES,
    binding_facts,
    usable_identity,
)

LANGFUSE = ("langfuse.observations-v2", "4.35.0")
DEEPEVAL = ("deepeval.test-run", "4.1.1")
SUBJECT: dict[str, str] = dict.fromkeys(FIELDS, "requested")


@pytest.mark.parametrize(
    "usable,missing,unusable", tuple(product((0, 1, 2), (False, True), (False, True)))
)
@pytest.mark.parametrize("declaration", [None, "requested", "other"])
def test_fact_union(usable: int, missing: bool, unusable: bool, declaration: str | None) -> None:
    values: list[object] = list(("requested", "other")[:usable])
    if missing:
        values.append(None)
    if unusable:
        values.append("e\u0301")
    projection = Projection("eval_run", {"observations": [{"release": value} for value in values]})
    declared = {} if declaration is None else {"version": declaration}
    bindings, diagnostics, presence = binding_facts(
        projection, LANGFUSE, SUBJECT, declared, "/evidence/2"
    )
    codes = {item["code"] for item in diagnostics if item["field"] == "version"}
    expected: set[str] = set()
    if unusable:
        expected.update(("SOURCE_IDENTITY_UNUSABLE", "IDENTITY_UNKNOWN"))
    elif (usable and missing) or (not usable and declaration is None):
        expected.add("IDENTITY_UNKNOWN")
    if usable > 1:
        expected.update(("IDENTITY_AMBIGUOUS", "IDENTITY_MISMATCH"))
    if declaration == "other":
        expected.add("IDENTITY_MISMATCH")
    if declaration is not None and usable and (usable > 1 or declaration != "requested"):
        expected.add("IDENTITY_CONFLICT")
    assert codes == expected
    coverage = (
        "unusable"
        if unusable
        else "partial"
        if usable and missing
        else "complete"
        if usable
        else "absent"
    )
    origin = (
        "source_present"
        if usable
        else "declared"
        if declaration is not None and not unusable
        else "unknown"
    )
    state = next(
        (
            state
            for code, state in (
                ("IDENTITY_CONFLICT", "conflict"),
                ("IDENTITY_AMBIGUOUS", "ambiguous"),
                ("IDENTITY_MISMATCH", "mismatched"),
                ("IDENTITY_UNKNOWN", "unknown"),
            )
            if code in expected
        ),
        "matched",
    )
    assert bindings["version"] == {
        "coverage": coverage,
        "origin": origin,
        "state": state,
        "source_value": "requested" if usable == 1 else None,
        "declared_value": declaration,
    }
    assert presence == ("present" if values else "empty")
    assert all(item["location"] == "/evidence/2" for item in diagnostics)
    assert all(
        item["source_path"] == "/observations/*/release"
        for item in diagnostics
        if item["field"] == "version"
    )
    assert all(
        item["next_action"]
        == (
            "PROVIDE_BINDING_METADATA"
            if item["code"] == "IDENTITY_UNKNOWN"
            else "CHECK_BINDING_METADATA"
        )
        for item in diagnostics
        if item["field"] == "version"
    )


@pytest.mark.parametrize("value", [None, ""])
def test_absent_allows_declaration(value: object) -> None:
    bindings, _, _ = binding_facts(
        Projection("eval_run", {"observations": [{"release": value}]}),
        LANGFUSE,
        SUBJECT,
        SUBJECT,
        "",
    )
    assert bindings["version"]["origin"] == "declared"
    assert bindings["version"]["state"] == "matched"


@pytest.mark.parametrize(
    "value", [False, 12, [], {}, " ", "\t", "a\x00", "a" * 129, "e\u0301", "\ud800"]
)
def test_unusable_never_echoed_or_fallback(value: object) -> None:
    assert not usable_identity(value)
    bindings, diagnostics, _ = binding_facts(
        Projection("eval_run", {"observations": [{"release": value}]}),
        LANGFUSE,
        SUBJECT,
        SUBJECT,
        "",
    )
    assert bindings["version"] == {
        "coverage": "unusable",
        "origin": "unknown",
        "state": "unknown",
        "source_value": None,
        "declared_value": "requested",
    }
    assert {item["code"] for item in diagnostics if item["field"] == "version"} == {
        "IDENTITY_UNKNOWN",
        "SOURCE_IDENTITY_UNUSABLE",
    }


@pytest.mark.parametrize("value", ["é", "x" * 128, " value ", "한글", "😀"])
def test_usable_exact(value: str) -> None:
    assert usable_identity(value)
    bindings, _, _ = binding_facts(
        Projection("eval_run", {"observations": [{"release": value}]}),
        LANGFUSE,
        {**SUBJECT, "version": value},
        SUBJECT,
        "",
    )
    assert bindings["version"]["source_value"] == value


def test_unavailable_never_falls_back_but_compares_declaration() -> None:
    bindings, diagnostics, presence = binding_facts(
        None, ("unknown", "v1"), SUBJECT, {**SUBJECT, "target": "other"}, "/evidence/0"
    )
    assert presence == "unknown"
    assert all(
        value["coverage"] == "unavailable" and value["origin"] == "unknown"
        for value in bindings.values()
    )
    assert bindings["target"]["state"] == "mismatched"
    assert {item["code"] for item in diagnostics if item["field"] == "target"} == {
        "IDENTITY_UNKNOWN",
        "IDENTITY_MISMATCH",
    }
    assert all(item["source_path"] is None for item in diagnostics)


@pytest.mark.parametrize("selector,key", [(DEEPEVAL, "testCases"), (LANGFUSE, "observations")])
def test_empty_and_unmapped(selector: tuple[str, str], key: str) -> None:
    bindings, diagnostics, presence = binding_facts(
        Projection("eval_run", {key: []}), selector, SUBJECT, SUBJECT, ""
    )
    assert presence == "empty"
    assert all(value["state"] == "matched" for value in bindings.values())
    assert diagnostics == [
        {
            "code": "EMPTY_EVIDENCE",
            "location": "",
            "field": None,
            "source_path": None,
            "next_action": "PROVIDE_NONEMPTY_EVIDENCE",
        }
    ]


def test_native_identity_does_not_infer_unmapped_fields() -> None:
    cases: tuple[tuple[tuple[str, str], dict[str, object]], ...] = (
        (DEEPEVAL, {"testCases": [{}], "identifier": "requested"}),
        (LANGFUSE, {"observations": [{"project_id": "requested"}]}),
    )
    for selector, payload in cases:
        bindings, _, _ = binding_facts(Projection("eval_run", payload), selector, SUBJECT, {}, "")
        assert bindings["target"]["coverage"] == "unmapped"
        assert bindings["target"]["state"] == "unknown"
    assert set(PROFILES) == {DEEPEVAL, LANGFUSE}


def test_source_row_reordering_and_repetition_preserves_facts() -> None:
    rows: list[dict[str, str | None]] = [
        {"release": "requested", "environment": "wrong"},
        {"release": None},
        {"release": "other"},
    ]

    def facts(items: list[dict[str, str | None]]) -> object:
        return binding_facts(
            Projection("eval_run", {"observations": items}),
            LANGFUSE,
            SUBJECT,
            SUBJECT,
            "/evidence/1",
        )

    assert facts(rows) == facts(rows[::-1]) == facts(rows + rows)
    _, diagnostics, _ = binding_facts(
        Projection("eval_run", {"observations": rows}), LANGFUSE, SUBJECT, SUBJECT, "/evidence/1"
    )
    assert diagnostics == sorted(
        diagnostics,
        key=lambda item: (
            item["location"] or "",
            item["field"] or "",
            item["code"] or "",
            item["source_path"] or "",
        ),
    )


def test_composed_declaration_cannot_repair_decomposed_source() -> None:
    bindings, _, _ = binding_facts(
        Projection("eval_run", {"observations": [{"release": "e\u0301"}]}),
        LANGFUSE,
        {**SUBJECT, "version": "é"},
        {"version": "é"},
        "",
    )
    assert bindings["version"]["state"] == "unknown"


def test_broken_codec_contract_is_not_reported_as_absence() -> None:
    with pytest.raises(KeyError):
        binding_facts(Projection("eval_run", {}), LANGFUSE, SUBJECT, {}, "")
    with pytest.raises(KeyError):
        binding_facts(Projection("eval_run", {}), ("unknown", "v1"), SUBJECT, {}, "")


def test_missing_source_field_matches_null_and_empty_rows() -> None:
    def facts(rows: list[dict[str, object]]) -> object:
        return binding_facts(
            Projection("eval_run", {"observations": rows}), LANGFUSE, SUBJECT, SUBJECT, ""
        )

    assert facts([{}]) == facts([{"release": None}]) == facts([{"release": ""}])
    assert facts([{}, {"release": "requested"}]) == facts(
        [{"release": None}, {"release": "requested"}]
    )


def test_single_mismatch_with_missing_row_retains_unknown() -> None:
    bindings, diagnostics, presence = binding_facts(
        Projection("eval_run", {"observations": [{"release": "other"}, {"release": None}]}),
        LANGFUSE,
        SUBJECT,
        {},
        "/evidence/0",
    )
    assert presence == "present"
    assert bindings["version"] == {
        "coverage": "partial",
        "origin": "source_present",
        "state": "mismatched",
        "source_value": "other",
        "declared_value": None,
    }
    assert {item["code"] for item in diagnostics if item["field"] == "version"} == {
        "IDENTITY_MISMATCH",
        "IDENTITY_UNKNOWN",
    }


def test_deepeval_records_only_check_presence() -> None:
    projection = Projection(
        "eval_run", {"testCases": [{"success": False, "metricsData": [{"error": "failure"}]}]}
    )
    bindings, diagnostics, presence = binding_facts(projection, DEEPEVAL, SUBJECT, SUBJECT, "")
    assert presence == "present"
    assert diagnostics == []
    assert all(
        value["origin"] == "declared" and value["state"] == "matched" for value in bindings.values()
    )
    bindings, diagnostics, _ = binding_facts(projection, DEEPEVAL, SUBJECT, {}, "")
    assert all(value["state"] == "unknown" for value in bindings.values())
    assert {item["field"] for item in diagnostics} == set(FIELDS)

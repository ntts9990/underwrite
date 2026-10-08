"""Tests for underwrite_core.receipt: in-toto Statement v1 receipts (receipt.v1).

The shared core-purity AST gate (no wall clock, no filesystem/network,
stdlib-only imports) lives in test_purity.py, which walks every module
under underwrite_core in one parametrized test.
"""

import re

import pytest
from underwrite_core.canonical import content_digest
from underwrite_core.receipt import (
    HONESTY_NOTE,
    LEDGER_STATES,
    PREDICATE_TYPE,
    RAW_STATES,
    SIGNATURE_STATES,
    STATEMENT_TYPE,
    Artifact,
    Ladder,
    Limitations,
    ReceiptError,
    build_statement,
    statement_digest,
)

_DIGEST_A = "sha256:" + "ab" * 32
_DIGEST_B = "sha256:" + "cd" * 32


def _artifact(role: str = "evaluation_receipt", digest: str = _DIGEST_A) -> Artifact:
    return Artifact(role=role, digest=digest)


def _valid_limitations() -> Limitations:
    return Limitations(
        signature_verification="not_measured",
        raw_included="yes",
        ledger_anchored="not_measured",
        ladder=Ladder(
            selfcheck="pass", mock_replay="pass", aa_vacuous_rate=0.0, mutation_kill_rate=0.95
        ),
    )


def _valid_statement() -> dict[str, object]:
    return build_statement(
        subjects=[_artifact()],
        artifacts=[_artifact(), _artifact(role="measurement_receipt", digest=_DIGEST_B)],
        limitations=_valid_limitations(),
        run_id="run-0001",
        produced_at="2026-09-04T00:00:00Z",
    )


# --- constants -----------------------------------------------------------


def test_constants() -> None:
    assert STATEMENT_TYPE == "https://in-toto.io/Statement/v1"
    assert PREDICATE_TYPE == "https://underwrite.dev/receipt/v1"
    assert SIGNATURE_STATES == ("verified", "not_measured")
    assert RAW_STATES == ("yes", "no", "retention_expired")
    assert LEDGER_STATES == ("anchored", "not_measured")
    assert "issuer authenticity" in HONESTY_NOTE
    assert issubclass(ReceiptError, ValueError)


# --- build_statement shape -------------------------------------------------


def test_build_statement_shape() -> None:
    statement = _valid_statement()
    assert statement == {
        "_type": STATEMENT_TYPE,
        "subject": [{"name": "evaluation_receipt", "digest": {"sha256": "ab" * 32}}],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "schema": "receipt.v1",
            "run_id": "run-0001",
            "produced_at": "2026-09-04T00:00:00Z",
            "artifacts": [
                {"role": "evaluation_receipt", "digest": _DIGEST_A},
                {"role": "measurement_receipt", "digest": _DIGEST_B},
            ],
            "limitations": {
                "signature_verification": "not_measured",
                "raw_included": "yes",
                "ledger_anchored": "not_measured",
                "ladder": {
                    "selfcheck": "pass",
                    "mock_replay": "pass",
                    "aa_vacuous_rate": 0.0,
                    "mutation_kill_rate": 0.95,
                },
                "notes": [],
            },
            "authenticity": "not asserted",
            "honesty_note": HONESTY_NOTE,
        },
    }


def test_build_statement_defaults_predicate_type() -> None:
    statement = _valid_statement()
    assert statement["predicateType"] == PREDICATE_TYPE


def test_build_statement_notes_are_preserved_in_order() -> None:
    limitations = Limitations(
        signature_verification="verified",
        raw_included="no",
        ledger_anchored="anchored",
        ladder=Ladder(
            selfcheck="not_measured",
            mock_replay="not_measured",
            aa_vacuous_rate=None,
            mutation_kill_rate=None,
        ),
        notes=("first note", "second note"),
    )
    statement = build_statement(
        subjects=[_artifact()],
        artifacts=[],
        limitations=limitations,
        run_id="run-0002",
        produced_at="2026-09-04T00:00:00+09:00",
    )
    assert statement["predicate"] == {
        "schema": "receipt.v1",
        "run_id": "run-0002",
        "produced_at": "2026-09-04T00:00:00+09:00",
        "artifacts": [],
        "limitations": {
            "signature_verification": "verified",
            "raw_included": "no",
            "ledger_anchored": "anchored",
            "ladder": {
                "selfcheck": "not_measured",
                "mock_replay": "not_measured",
                "aa_vacuous_rate": None,
                "mutation_kill_rate": None,
            },
            "notes": ["first note", "second note"],
        },
        "authenticity": "not asserted",
        "honesty_note": HONESTY_NOTE,
    }


# --- digest ----------------------------------------------------------------


def test_statement_digest_is_stable_and_matches_content_digest() -> None:
    statement = _valid_statement()
    first = statement_digest(statement)
    second = statement_digest(statement)
    assert first == second
    assert first == content_digest(statement)
    assert first.startswith("sha256:")


# --- reject rules ------------------------------------------------------------


def test_build_statement_rejects_empty_subjects() -> None:
    with pytest.raises(ReceiptError, match=r"^EMPTY_SUBJECTS$"):
        build_statement(
            subjects=[],
            artifacts=[],
            limitations=_valid_limitations(),
            run_id="run-0001",
            produced_at="2026-09-04T00:00:00Z",
        )


@pytest.mark.parametrize(
    ("subjects", "artifacts", "expected_role"),
    [
        pytest.param(
            [_artifact(digest="not-a-digest")], [], "evaluation_receipt", id="subject_digest"
        ),
        pytest.param(
            [_artifact()],
            [_artifact(role="measurement_receipt", digest="sha256:short")],
            "measurement_receipt",
            id="artifact_digest",
        ),
        pytest.param(
            [_artifact(digest="sha256:" + "AB" * 32)],
            [],
            "evaluation_receipt",
            id="uppercase_hex_rejected",
        ),
    ],
)
def test_build_statement_rejects_malformed_digest(
    subjects: list[Artifact], artifacts: list[Artifact], expected_role: str
) -> None:
    with pytest.raises(ReceiptError, match=re.escape(expected_role)):
        build_statement(
            subjects=subjects,
            artifacts=artifacts,
            limitations=_valid_limitations(),
            run_id="run-0001",
            produced_at="2026-09-04T00:00:00Z",
        )


@pytest.mark.parametrize("produced_at", ["not-a-date", "2026-13-99", ""])
def test_build_statement_rejects_non_iso_produced_at(produced_at: str) -> None:
    with pytest.raises(ReceiptError, match=r"^INVALID_PRODUCED_AT:"):
        build_statement(
            subjects=[_artifact()],
            artifacts=[],
            limitations=_valid_limitations(),
            run_id="run-0001",
            produced_at=produced_at,
        )


@pytest.mark.parametrize(
    ("limitations", "expected_field"),
    [
        pytest.param(
            Limitations(
                signature_verification="bogus",
                raw_included="yes",
                ledger_anchored="not_measured",
                ladder=_valid_limitations().ladder,
            ),
            "signature_verification",
            id="signature_verification",
        ),
        pytest.param(
            Limitations(
                signature_verification="not_measured",
                raw_included="bogus",
                ledger_anchored="not_measured",
                ladder=_valid_limitations().ladder,
            ),
            "raw_included",
            id="raw_included",
        ),
        pytest.param(
            Limitations(
                signature_verification="not_measured",
                raw_included="yes",
                ledger_anchored="bogus",
                ladder=_valid_limitations().ladder,
            ),
            "ledger_anchored",
            id="ledger_anchored",
        ),
        pytest.param(
            Limitations(
                signature_verification="not_measured",
                raw_included="yes",
                ledger_anchored="not_measured",
                ladder=Ladder(
                    selfcheck="bogus",
                    mock_replay="pass",
                    aa_vacuous_rate=None,
                    mutation_kill_rate=None,
                ),
            ),
            "ladder.selfcheck",
            id="ladder_selfcheck",
        ),
        pytest.param(
            Limitations(
                signature_verification="not_measured",
                raw_included="yes",
                ledger_anchored="not_measured",
                ladder=Ladder(
                    selfcheck="pass",
                    mock_replay="bogus",
                    aa_vacuous_rate=None,
                    mutation_kill_rate=None,
                ),
            ),
            "ladder.mock_replay",
            id="ladder_mock_replay",
        ),
    ],
)
def test_build_statement_rejects_invalid_limitation(
    limitations: Limitations, expected_field: str
) -> None:
    # Exact field name (not just the generic "INVALID_LIMITATION" prefix) so a
    # mutation that attributes the failure to the WRONG field is still caught,
    # since "bogus" is invalid input no matter which field's allowed-tuple a
    # mutant mistakenly checks it against.
    with pytest.raises(ReceiptError, match=rf"^INVALID_LIMITATION:{re.escape(expected_field)}="):
        build_statement(
            subjects=[_artifact()],
            artifacts=[],
            limitations=limitations,
            run_id="run-0001",
            produced_at="2026-09-04T00:00:00Z",
        )


def test_build_statement_accepts_distinct_valid_value_per_limitation_field() -> None:
    # Each field's chosen value is valid in ITS OWN allowed-tuple but invalid
    # in every OTHER limitation field's tuple (SIGNATURE_STATES, RAW_STATES,
    # LEDGER_STATES, and _LADDER_STATES only share "not_measured" in common).
    # This would fail if a mutation swapped which allowed-tuple backs which
    # field, since the swapped-in tuple would then reject this field's value.
    limitations = Limitations(
        signature_verification="verified",
        raw_included="retention_expired",
        ledger_anchored="anchored",
        ladder=Ladder(
            selfcheck="pass", mock_replay="fail", aa_vacuous_rate=None, mutation_kill_rate=None
        ),
    )
    statement = build_statement(
        subjects=[_artifact()],
        artifacts=[],
        limitations=limitations,
        run_id="run-0001",
        produced_at="2026-09-04T00:00:00Z",
    )
    assert statement["predicate"] == {
        "schema": "receipt.v1",
        "run_id": "run-0001",
        "produced_at": "2026-09-04T00:00:00Z",
        "artifacts": [],
        "limitations": {
            "signature_verification": "verified",
            "raw_included": "retention_expired",
            "ledger_anchored": "anchored",
            "ladder": {
                "selfcheck": "pass",
                "mock_replay": "fail",
                "aa_vacuous_rate": None,
                "mutation_kill_rate": None,
            },
            "notes": [],
        },
        "authenticity": "not asserted",
        "honesty_note": HONESTY_NOTE,
    }

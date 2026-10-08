"""One deterministic composition for the aggregate and every eligible condition cell.
Source verdicts never enter the computation; the five primitives own their arithmetic."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, cast

from underwrite.measurement import availability, calibration, eprocess, jury, stratify
from underwrite.measurement.read_input import (
    JSON,
    Prepared,
    ReadInputError,
    ReadReason,
    Slot,
    prepare,
    require,
)

PANEL_STAGE_DIGITS = 4


def _missing(method: str, instrument: str, reasons: tuple[str, ...]) -> jury.Signal:
    return jury.Signal(method, instrument, "not_measured", None, None, 0, None, None, reasons)


def _reasons(slots: Sequence[Slot | None], *, prediction: bool) -> tuple[str, ...]:
    if not slots:
        return ("no_observations",)
    if any(
        s is None
        or s.loss is not None
        or s.outcome is None
        or (prediction and s.prediction is None)
        for s in slots
    ):
        return ("signal_not_measured",)
    return ()


def _eprocess(
    slots: Sequence[Slot | None], p: JSON, instrument: str
) -> tuple[dict[str, Any] | None, jury.Signal]:
    reasons = _reasons(slots, prediction=False)
    if reasons:
        return None, _missing(eprocess.METHOD, instrument, reasons)
    records = tuple(
        eprocess.Observation(cast("int", s.outcome), 1, s.cost) for s in slots if s is not None
    )
    try:
        projection = eprocess.projection_of(
            eprocess.run_stream(records, eprocess.EProcessPolicy(**p["eprocess"]))
        )
    except eprocess.EProcessError as error:
        if error.code == "COST_OVERFLOW":
            raise ReadInputError("INVALID_OBSERVATION", "COST_OVERFLOW", "/observation") from None
        reason: ReadReason = (
            cast("ReadReason", error.code)
            if error.code in ("NO_TRIALS", "COST_REQUIRED", "E_VALUE_OVERFLOW")
            else "INVALID_EPROCESS"
        )
        raise ReadInputError("INVALID_POLICY", reason, "/policy") from None
    signal = _missing(eprocess.METHOD, instrument, projection.reason_codes)
    if projection.availability == "measured":
        signal = jury.Signal(
            eprocess.METHOD,
            instrument,
            "measured",
            eprocess.verdict(projection),
            eprocess.score(projection),
            1,
            projection.e_value,
            projection.null_id,
            (),
        )
    return eprocess.project(projection), signal


def _calibration(
    slots: Sequence[Slot | None], binary: Sequence[Slot | None], p: JSON, instrument: str
) -> tuple[dict[str, Any], jury.Signal]:
    cal = p["calibration"]
    reasons = _reasons(slots, prediction=True)
    if reasons:
        brier = calibration.BrierMeasure("not_measured", None, len(slots), reasons)
        ece = calibration.ECEMeasure(
            "not_measured", None, len(slots), reasons, cal["bins"], p["min_bin_n"], ()
        )
    else:
        pairs = tuple(
            calibration.make_pair(s.prediction, s.outcome) for s in slots if s is not None
        )
        brier = calibration.brier(pairs)
        ece = calibration.ece(pairs, cal["bins"], p["min_bin_n"])
    binary_reasons = _reasons(binary, prediction=False)
    if binary_reasons:
        pass_k = calibration.PassKMeasure(
            "not_measured", None, len(binary), binary_reasons, cal["k"], 0
        )
    else:
        pass_k = calibration.pass_k(
            sum(cast("int", s.outcome) for s in binary if s is not None), len(binary), cal["k"]
        )
    signal = _missing("calibration", instrument, ece.reason_codes)
    if ece.value is not None:
        verdict: jury.Verdict = "warn"
        if ece.n >= cal["anchor_min_samples"]:
            verdict = (
                "pass"
                if ece.value <= cal["pass_ece"]
                else "warn"
                if ece.value <= cal["warn_ece"]
                else "fail"
            )
        signal = jury.Signal(
            "calibration",
            instrument,
            "measured",
            verdict,
            round(1 - ece.value, 6),
            min(1, ece.n / cal["anchor_min_samples"]),
            None,
            None,
            (),
        )
    return calibration.project_calibration(brier, ece, pass_k), signal


def _panel(fused: jury.JuryResult, cal: jury.Signal, p: JSON) -> availability.PanelRead | None:
    if fused.e_value is None or cal.availability != "measured":
        return None
    stage_score = round(1 - 1 / max(1, fused.e_value), PANEL_STAGE_DIGITS)
    stage = jury.Signal(
        "e_value_fusion",
        "panel",
        "measured",
        "pass" if fused.e_value >= p["eprocess"]["pass_e"] else "warn",
        stage_score,
        stage_score,
        None,
        None,
        (),
    )
    signals = (stage, cal)
    total = sum(s.confidence for s in signals)
    if total == 0:
        return None
    weights = {"pass": 1, "warn": 0.5, "fail": 0}
    score = sum(weights[cast("jury.Verdict", s.verdict)] * s.confidence for s in signals) / total
    bands = p["panel"]
    verdict = (
        "pass" if score >= bands["pass_at"] else "warn" if score >= bands["warn_at"] else "fail"
    )
    confident = {s.verdict for s in signals if s.confidence >= bands["conflict_confidence"]}
    escalate = (
        jury.disagreement(signals) >= bands["escalate_at"]
        or {"pass", "fail"} <= confident
        or stage.verdict == "warn"
    )
    return availability.make_panel_read(verdict, round(score, 4), escalate)


def _population(cases: Sequence[stratify.Case], prepared: Prepared) -> dict[str, Any]:
    p = prepared.policy
    pairs = [cast("tuple[Slot | None, Slot | None]", case.payload) for case in cases]
    binary, predictions = tuple(pair[0] for pair in pairs), tuple(pair[1] for pair in pairs)
    # Membership is fixed by the profile and selectors, before any values are computed.
    roster = ((eprocess.METHOD, prepared.instruments[0]),)
    ep, e_signal = _eprocess(binary, p, prepared.instruments[0])
    cal, cal_signal = _calibration(predictions, binary, p, prepared.instruments[1])
    signals = (e_signal, cal_signal)
    fused = jury.fuse(signals, combine="mean", independence=None, e_value_roster=roster)
    measured = jury.measured_instruments(signals)
    losses: dict[object, object] = {key: value for key, value in prepared.extra_losses.items()}
    for name in dict.fromkeys(prepared.instruments):
        if name not in measured:
            losses[name] = availability.classify_loss(
                slot.loss
                for pair in pairs
                for slot, owner in zip(pair, prepared.instruments, strict=True)
                if slot is not None and owner == name
            )
    available = availability.assess(
        availability.make_policy(p["required_instruments"], p["quorum"]), measured, losses
    )
    panel = _panel(fused, cal_signal, p)
    if panel is not None:
        result: dict[str, Any] = dict(
            availability.project_capped(availability.cap(panel, available))
        )
    else:
        reasons = [reason for signal in signals for reason in signal.reason_codes]
        reasons.extend(fused.reason_codes)
        if not reasons:
            reasons.append("signal_not_measured")
        # below_quorum follows the signal reasons here (cap leads with it); the loss reasons
        # come last, in availability's order.
        if available.state == "below_quorum":
            reasons.append("below_quorum")
        reasons.extend(availability.loss_reasons(available))
        result = {
            "verdict": "not_measured",
            "score": None,
            "escalate": True,
            "reason_codes": list(dict.fromkeys(reasons)),
        }
    result.update(
        signals=[jury.project_signal(s) for s in signals],
        eprocess=ep,
        jury=jury.project(fused),
        calibration=cal,
        availability=availability.project(available),
    )
    return result


def assemble_read(
    observation: JSON, policy: JSON, *, observation_digest: str, policy_digest: str
) -> dict[str, Any]:
    """Compose read.v1 from explicit JSON and caller-computed canonical input digests.

    Digests bind declared inputs; they are neither independently verified here nor
    evidence of authenticated provenance or historical preregistration.
    """
    for digest in (observation_digest, policy_digest):
        require(
            re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None,
            "INVALID_DIGEST",
            "/policy",
        )
    prepared = prepare(observation, policy)
    result = _population(prepared.cases, prepared)
    strata = policy["stratification"]

    def measure(cases: tuple[stratify.Case, ...]) -> stratify.Outcome:
        cell = _population(cases, prepared)
        if cell["verdict"] == "not_measured":
            return "not_measured", tuple(cell["reason_codes"])
        return cell["verdict"], cell["score"]

    cells = (
        ()
        if strata["mode"] == "none"
        else stratify.stratify(
            prepared.cases,
            strata["axes"],
            policy["min_cell_n"],
            measure,
            requested=strata["requested"],
        )
    )
    result.update(
        schema="read.v1",
        subject={**policy["subject"], "conditions": dict(policy["subject"]["conditions"])},
        seed=policy["seed"],
        sources={"observations": [observation_digest], "policy": policy_digest},
        strata=[stratify.project(cell) for cell in cells],
    )
    return result

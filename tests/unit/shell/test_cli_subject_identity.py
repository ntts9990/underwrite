"""Measured subject identity composes unchanged through current acceptance."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from _baseline_exchange import build_baseline_exchange
from _repo_paths import repo_root
from underwrite_core.canonical import content_digest

from underwrite.acceptance import claims as cl
from underwrite.cli.main import main

ROOT = repo_root(Path(__file__))
FIXTURES = ROOT / "fixtures/examples/event-acceptance"
ERROR_EXIT = 2


def _write(root: Path, name: str, value: object) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _json_command(args: list[str], capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert main([*args, "--json"]) == 0
    output = capsys.readouterr()
    assert not output.err
    return json.loads(output.out)


@pytest.mark.parametrize(
    "subject",
    [
        "synthetic.event-rate",
        "정확도",
        "quality score",
        "quality/rate",
        "quality\x1b[31m\nINJECTED",
    ],
)
def test_project_prepare_measure_seal_accept_preserves_subject(
    subject: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    for name, raw in build_baseline_exchange().files().items():
        (exchange / name).write_bytes(raw)
    observation = _json_command(
        ["project-baseline", "--exchange", str(exchange), "--direction", "higher"], capsys
    )
    observation_path = _write(tmp_path, "observation.json", observation)
    policy = _json_command(
        [
            "prepare-event-policy",
            "--observation",
            str(observation_path),
            "--subject-id",
            subject,
            "--p0",
            "0.3",
            "--ece-bins",
            "2",
            "--min-bin-n",
            "2",
            "--all-k",
            "2",
        ],
        capsys,
    )
    assert policy["subject"]["subject_id"] == subject
    assert set(policy["assumptions"].values()) == {"unknown"}
    policy_path = _write(tmp_path, "policy.json", policy)
    read = _json_command(
        ["measure", "--observation", str(observation_path), "--policy", str(policy_path)], capsys
    )
    assert read["schema"] == "read.v2" and read["subject"]["subject_id"] == subject
    read_path = _write(tmp_path, "read.json", read)
    template = json.loads((FIXTURES / "claims.json").read_bytes())
    claim = {
        "claim_id": "event",
        "subject_id": subject,
        "effect": "disqualifying",
        "policy_digest": content_digest(policy),
        "semantic_digest": policy["semantic_digest"],
    }
    declaration: dict[str, Any] = {
        "schema": cl.SCHEMA,
        "candidate_id": template["candidate_id"],
        "required_claims": {basis: [dict(claim)] for basis in cl.BASES},
        "selection": template["selection"],
    }
    sealed = cl.parse_claims(declaration)
    candidate = json.loads((FIXTURES / "candidate-single.json").read_bytes())
    candidate["claims_digest"] = sealed.digest
    args = [
        "accept",
        "--candidate",
        str(_write(tmp_path, "candidate.json", candidate)),
        "--claims",
        str(_write(tmp_path, "claims.json", declaration)),
        "--read",
        str(read_path),
    ]
    decision = _json_command(args, capsys)
    assert decision["classification"] == "indeterminate"
    assert decision["claims"][0]["subject_id"] == subject
    assert decision["claims"][0]["read"]["digest"] == content_digest(read)
    assert decision["merge_authorized"] is False
    assert main(args) == 0
    human = capsys.readouterr()
    assert not human.err
    assert "\x1b" not in human.out and "\nINJECTED" not in human.out
    if "\x1b" in subject:
        assert json.dumps(subject, ensure_ascii=True) in human.out

    for field, value, code, reason in (
        ("subject_id", subject + " other", "UNEXPECTED_CLAIM", "UNEXPECTED_CLAIM"),
        ("policy_digest", "sha256:" + "0" * 64, "READ_POLICY_MISMATCH", "READ_POLICY_MISMATCH"),
        ("semantic_digest", "sha256:" + "0" * 64, "INVALID_READ", "READ_SEMANTIC_MISMATCH"),
        ("effect", "validity", "CLAIMS_DIGEST_MISMATCH", "CLAIMS_DIGEST_MISMATCH"),
    ):
        changed = deepcopy(declaration)
        changed["required_claims"][candidate["basis"]][0][field] = value
        changed_candidate = dict(candidate)
        if field != "effect":  # A changed declaration with the original seal must refuse.
            changed_candidate["claims_digest"] = cl.parse_claims(changed).digest
        wrong_args = [
            "accept",
            "--candidate",
            str(_write(tmp_path, "changed-candidate.json", changed_candidate)),
            "--claims",
            str(_write(tmp_path, "changed-claims.json", changed)),
            "--read",
            str(read_path),
            "--json",
        ]
        assert main(wrong_args) == ERROR_EXIT
        refused = capsys.readouterr()
        assert not refused.out
        error = json.loads(refused.err)
        assert (error["code"], error["reason"]) == (code, reason)

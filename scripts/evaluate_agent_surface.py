"""Prepare label-redacted prompts and score caller-supplied MCP tool-selection trials.

This offline harness does not call a model or establish agent performance.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast

from underwrite_core.canonical import canonical_bytes, digest_bytes

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / "fixtures/agent-surface/selection-intents.v1.json"
CATALOG_SNAPSHOT = ROOT / "fixtures/agent-surface/tool-catalog-seven.v1.json"
CATALOG_SCHEMA = "underwrite.agent_surface_catalog_snapshot.v1"
CATALOG_DIGEST = "sha256:3f3b9a6fc020c530eafa62bc7877d6d6271a630fa3ecb42b87d81f64a5374049"
SUITE_SCHEMA = "underwrite.agent_surface_selection_suite.v1"
TRIAL_SCHEMA = "underwrite.agent_surface_selection_trial.v1"
REPORT_SCHEMA = "underwrite.agent_surface_selection_score.v1"
CLAIMS = frozenset(
    {
        "DISCOVERY_ONLY",
        "STRUCTURE_ONLY",
        "DECLARED_COUNTS_ONLY",
        "SOURCE_CLAIMS_UNVERIFIED",
        "STORED_BYTES_ONLY",
        "HISTORICAL_VERIFIER_NOT_RERUN",
        "MISSINGNESS_NOT_SUCCESS",
        "NO_APPROVAL",
        "UNSUPPORTED_REQUEST",
        "PRODUCER_AUTHENTICATED",
        "IMPROVEMENT_PROVEN",
        "PAYMENT_AUTHORIZED",
        "FRESH_NATIVE_VERIFICATION",
        "MISSING_AS_SUCCESS",
    }
)
UNSUPPORTED_CLAIMS = frozenset(
    {
        "PRODUCER_AUTHENTICATED",
        "IMPROVEMENT_PROVEN",
        "PAYMENT_AUTHORIZED",
        "FRESH_NATIVE_VERIFICATION",
        "MISSING_AS_SUCCESS",
    }
)
SPLITS = ("development", "held_out")
ID = re.compile(r"[a-z][a-z0-9_]{2,63}\Z")
MAX_PROMPT_CHARS = 500
MAX_CALLS = 1
MAX_OBSERVED_CALLS = 20


@dataclass(frozen=True)
class Case:
    case_id: str
    split: Literal["development", "held_out"]
    intent_group: str
    prompt: str
    first_tool: str | None
    max_calls: int
    forbidden_tools: tuple[str, ...]
    required_claims: tuple[str, ...]
    forbidden_claims: tuple[str, ...]


@dataclass(frozen=True)
class Trial:
    case_id: str
    calls: tuple[str, ...]
    claims: tuple[str, ...]


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("OBJECT_REQUIRED")
    payload = cast(dict[object, object], value)
    if any(type(key) is not str for key in payload):
        raise ValueError("OBJECT_REQUIRED")
    return cast(dict[str, object], value)


def catalog() -> list[dict[str, object]]:
    """Pin this historical evaluation to the seven tools the model actually saw."""
    document = _object(json.loads(CATALOG_SNAPSHOT.read_text(encoding="utf-8")))
    if set(document) != {"schema", "tools"} or document["schema"] != CATALOG_SCHEMA:
        raise ValueError("CATALOG_SNAPSHOT_INVALID")
    items = document["tools"]
    if not isinstance(items, list):
        raise ValueError("CATALOG_SNAPSHOT_INVALID")
    raw_items = cast(list[object], items)
    if any(not isinstance(item, dict) for item in raw_items):
        raise ValueError("CATALOG_SNAPSHOT_INVALID")
    tools = cast(list[dict[str, object]], raw_items)
    if digest_bytes(canonical_bytes({"tools": tools})) != CATALOG_DIGEST:
        raise ValueError("CATALOG_SNAPSHOT_DRIFT")
    return tools


def _identifier(value: object) -> str:
    if type(value) is not str or ID.fullmatch(value) is None:
        raise ValueError("IDENTIFIER_INVALID")
    return value


def _strings(value: object, allowed: frozenset[str] | set[str]) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("STRING_LIST_REQUIRED")
    items = cast(list[object], value)
    if any(type(item) is not str or item not in allowed for item in items):
        raise ValueError("STRING_LIST_VOCABULARY_INVALID")
    strings = cast(list[str], items)
    if len(strings) != len(set(strings)):
        raise ValueError("STRING_LIST_DUPLICATE")
    return tuple(strings)


def _calls(value: object, allowed: set[str]) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("TRIAL_CALLS_INVALID")
    items = cast(list[object], value)
    if len(items) > MAX_OBSERVED_CALLS:
        raise ValueError("TRIAL_CALLS_INVALID")
    if any(type(item) is not str or item not in allowed for item in items):
        raise ValueError("TRIAL_CALLS_INVALID")
    return tuple(cast(list[str], items))


def _split(value: object) -> Literal["development", "held_out"]:
    if value == "development":
        return "development"
    if value == "held_out":
        return "held_out"
    raise ValueError("SUITE_SPLIT_INVALID")


def _parse_case(value: object, tool_names: set[str]) -> Case:
    item = _object(value)
    if set(item) != {
        "case_id",
        "split",
        "intent_group",
        "prompt",
        "first_tool",
        "max_calls",
        "forbidden_tools",
        "required_claims",
        "forbidden_claims",
    }:
        raise ValueError("SUITE_CASE_FIELDS_INVALID")
    case_id = _identifier(item["case_id"])
    group = _identifier(item["intent_group"])
    split = _split(item["split"])
    prompt = item["prompt"]
    first_tool = item["first_tool"]
    max_calls = item["max_calls"]
    if type(prompt) is not str or not 1 <= len(prompt) <= MAX_PROMPT_CHARS:
        raise ValueError("SUITE_CASE_INVALID")
    if first_tool is not None and (type(first_tool) is not str or first_tool not in tool_names):
        raise ValueError("SUITE_TOOL_UNKNOWN")
    if type(max_calls) is not int or max_calls < 0 or max_calls > MAX_CALLS:
        raise ValueError("SUITE_CALL_BUDGET_INVALID")
    forbidden = _strings(item["forbidden_tools"], tool_names)
    required = _strings(item["required_claims"], CLAIMS)
    forbidden_claims = _strings(item["forbidden_claims"], CLAIMS)
    if set(required) & set(forbidden_claims) or not UNSUPPORTED_CLAIMS.issubset(forbidden_claims):
        raise ValueError("SUITE_UNSUPPORTED_CLAIM_EXPECTED")
    if first_tool is None and (
        max_calls != 0 or "UNSUPPORTED_REQUEST" not in required or set(forbidden) != tool_names
    ):
        raise ValueError("SUITE_REFUSAL_INVALID")
    if first_tool is not None and (first_tool in forbidden or max_calls == 0):
        raise ValueError("SUITE_TOOL_CONFLICT")
    return Case(
        case_id,
        split,
        group,
        prompt,
        first_tool,
        max_calls,
        forbidden,
        required,
        forbidden_claims,
    )


def read_suite(value: object) -> tuple[Case, ...]:
    """Require distinct intent groups and balanced coverage of the installed catalog."""
    document = _object(value)
    if set(document) != {"schema", "cases"} or document["schema"] != SUITE_SCHEMA:
        raise ValueError("SUITE_SCHEMA_INVALID")
    rows = document["cases"]
    if not isinstance(rows, list):
        raise ValueError("SUITE_CASES_REQUIRED")
    tool_names = {cast(str, tool["name"]) for tool in catalog()}
    seen_ids: set[str] = set()
    seen_groups: set[str] = set()
    seen_prompts: set[str] = set()
    forbidden_by_tool: dict[str | None, frozenset[str]] = {}
    coverage: dict[str, set[str]] = {split: set() for split in SPLITS}
    refusals: dict[str, int] = {split: 0 for split in SPLITS}
    cases: list[Case] = []
    for row in cast(list[object], rows):
        case = _parse_case(row, tool_names)
        normalized_prompt = " ".join(case.prompt.casefold().split())
        if (
            case.case_id in seen_ids
            or case.intent_group in seen_groups
            or normalized_prompt in seen_prompts
        ):
            raise ValueError("SUITE_INTENT_REUSED")
        claim_policy = frozenset(case.forbidden_claims)
        if (
            case.first_tool in forbidden_by_tool
            and forbidden_by_tool[case.first_tool] != claim_policy
        ):
            raise ValueError("SUITE_CLAIM_POLICY_DRIFT")
        forbidden_by_tool[case.first_tool] = claim_policy
        if case.first_tool is None:
            refusals[case.split] += 1
        else:
            coverage[case.split].add(case.first_tool)
        seen_ids.add(case.case_id)
        seen_groups.add(case.intent_group)
        seen_prompts.add(normalized_prompt)
        cases.append(case)
    if any(coverage[split] != tool_names or refusals[split] < 1 for split in SPLITS):
        raise ValueError("SUITE_COVERAGE_INCOMPLETE")
    return tuple(cases)


def read_trials(value: object, cases: tuple[Case, ...]) -> dict[str, Trial]:
    document = _object(value)
    if set(document) != {"schema", "suite_digest", "catalog_digest", "trials"} or (
        document["schema"] != TRIAL_SCHEMA
    ):
        raise ValueError("TRIAL_SCHEMA_INVALID")
    if (
        document["suite_digest"] != _suite_digest(cases)
        or document["catalog_digest"] != _catalog_digest()
    ):
        raise ValueError("TRIAL_IDENTITY_MISMATCH")
    rows = document["trials"]
    if not isinstance(rows, list):
        raise ValueError("TRIAL_ROWS_REQUIRED")
    case_ids = {case.case_id for case in cases}
    tool_names = {cast(str, tool["name"]) for tool in catalog()}
    trials: dict[str, Trial] = {}
    for row in cast(list[object], rows):
        item = _object(row)
        if set(item) != {"case_id", "calls", "claims"}:
            raise ValueError("TRIAL_FIELDS_INVALID")
        case_id = _identifier(item["case_id"])
        if case_id not in case_ids or case_id in trials:
            raise ValueError("TRIAL_CASE_INVALID")
        calls = _calls(item["calls"], tool_names)
        claims = _strings(item["claims"], CLAIMS)
        trials[case_id] = Trial(case_id, calls, claims)
    return trials


def _suite_digest(cases: tuple[Case, ...]) -> str:
    rows = json.loads(json.dumps([asdict(case) for case in cases]))
    return digest_bytes(canonical_bytes(rows))


def _catalog_digest() -> str:
    return digest_bytes(canonical_bytes({"tools": catalog()}))


def trial_template(cases: tuple[Case, ...]) -> dict[str, object]:
    """Bind one empty caller trial to the exact suite and tool catalog."""
    return {
        "schema": TRIAL_SCHEMA,
        "suite_digest": _suite_digest(cases),
        "catalog_digest": _catalog_digest(),
        "trials": [],
    }


def score(cases: tuple[Case, ...], trials: dict[str, Trial]) -> dict[str, object]:
    """Score supplied structured choices, without inferring how they were produced."""
    normalized_trials = json.loads(
        json.dumps([asdict(trials[case_id]) for case_id in sorted(trials)])
    )
    trial_digest = digest_bytes(canonical_bytes(normalized_trials))
    details: list[dict[str, object]] = []
    for case in cases:
        trial = trials.get(case.case_id)
        calls = trial.calls if trial is not None else ()
        claims = trial.claims if trial is not None else ()
        forbidden_calls = sum(name in case.forbidden_tools for name in calls)
        forbidden_claims = sum(name in case.forbidden_claims for name in claims)
        unsupported_claims = sum(name in UNSUPPORTED_CLAIMS for name in claims)
        first_correct = trial is not None and (calls[0] if calls else None) == case.first_tool
        within_budget = trial is not None and len(calls) <= case.max_calls
        interpretation = (
            trial is not None
            and set(case.required_claims).issubset(claims)
            and forbidden_claims == 0
        )
        details.append(
            {
                "case_id": case.case_id,
                "split": case.split,
                "submitted": trial is not None,
                "first_call_correct": first_correct,
                "call_count": len(calls),
                "calls_within_budget": within_budget,
                "forbidden_call_count": forbidden_calls,
                "forbidden_claim_count": forbidden_claims,
                "unsupported_claim_count": unsupported_claims,
                "interpretation_covered": interpretation,
                "pass": first_correct
                and within_budget
                and forbidden_calls == 0
                and forbidden_claims == 0
                and interpretation,
            }
        )
    by_split = {
        split: {
            "cases": sum(row["split"] == split for row in details),
            "submitted": sum(row["split"] == split and row["submitted"] is True for row in details),
            "first_call_correct": sum(
                row["split"] == split and row["first_call_correct"] is True for row in details
            ),
            "interpretation_covered": sum(
                row["split"] == split and row["interpretation_covered"] is True for row in details
            ),
            "forbidden_calls": sum(
                cast(int, row["forbidden_call_count"]) for row in details if row["split"] == split
            ),
            "total_calls": sum(
                cast(int, row["call_count"]) for row in details if row["split"] == split
            ),
            "over_budget": sum(
                row["split"] == split
                and row["submitted"] is True
                and row["calls_within_budget"] is False
                for row in details
            ),
            "unsupported_claims": sum(
                cast(int, row["unsupported_claim_count"])
                for row in details
                if row["split"] == split
            ),
            "forbidden_claims": sum(
                cast(int, row["forbidden_claim_count"]) for row in details if row["split"] == split
            ),
            "passed": sum(row["split"] == split and row["pass"] is True for row in details),
        }
        for split in SPLITS
    }
    return {
        "schema": REPORT_SCHEMA,
        "suite_digest": _suite_digest(cases),
        "catalog_digest": _catalog_digest(),
        "trial_digest": trial_digest,
        "trial_source": "caller_supplied_unverified",
        "model_executed_by_harness": False,
        "performance_claim_validated": False,
        "held_out_blinding_proven": False,
        "intent_independence_proven": False,
        "scoring_scope": "structured_self_report_only",
        "by_split": by_split,
        "cases": details,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    choices = parser.add_mutually_exclusive_group(required=True)
    choices.add_argument("--prompts", action="store_true")
    choices.add_argument("--trial", type=Path)
    parser.add_argument("--split", choices=SPLITS)
    args = parser.parse_args(argv)
    cases = read_suite(json.loads(args.suite.read_text(encoding="utf-8")))
    if args.prompts:
        if args.split is None:
            parser.error("--split is required for --prompts")
        selected = [case for case in cases if case.split == args.split]
        report: dict[str, object] = {
            "schema": "underwrite.agent_surface_selection_prompts.v1",
            "suite_digest": _suite_digest(cases),
            "catalog_digest": _catalog_digest(),
            "cases": [{"case_id": case.case_id, "prompt": case.prompt} for case in selected],
        }
    else:
        if args.split is not None:
            parser.error("--split applies only to --prompts")
        trials = read_trials(json.loads(args.trial.read_text(encoding="utf-8")), cases)
        report = score(cases, trials)
    json.dump(report, sys.stdout, ensure_ascii=True, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

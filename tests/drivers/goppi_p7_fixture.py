"""Small public synthetic P7 handoff builder; no private producer artifacts."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.instrument.evidence.goppi_p7_handoff import (
    MAPPING_HANDOFF,
    MAPPING_INPUT,
    MAPPING_MANIFEST,
    MAPPING_POLICY,
    MAPPING_PROVENANCE,
    ORIGINAL_PLAN,
    ORIGINAL_PUBLIC,
    ORIGINAL_RESULT,
    ORIGINAL_SCOPE,
    REGRADE_COMPLETION,
    REGRADE_RECEIPTS,
    REGRADE_ROWS,
    REGRADE_SOURCE,
    ArtifactIdentity,
    GoppiP7Documents,
)


def _digest(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


def _raw(data: object) -> bytes:
    return canonical_bytes(data) + b"\n"


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class SyntheticP7Fixture:
    documents: GoppiP7Documents
    files: dict[str, bytes]

    def write(
        self, source_root: Path, handoff_relative: str = "study/paired-analysis-input"
    ) -> Path:
        handoff_dir = source_root / handoff_relative
        original = cast(dict[str, object], self.documents.regrade_source["original"])
        for logical, raw in self.files.items():
            if logical.startswith("mapping/"):
                target = handoff_dir / logical.removeprefix("mapping/")
            elif logical.startswith("regrade/"):
                target = (
                    source_root
                    / "study/offline-verifier-regrade"
                    / logical.removeprefix("regrade/")
                )
            elif logical == ORIGINAL_PUBLIC:
                target = handoff_dir.parent / "public-summary.json"
            elif logical == ORIGINAL_RESULT:
                target = source_root / str(original["result_path"])
            elif logical == ORIGINAL_SCOPE:
                target = source_root / str(original["execution_scope_path"])
            elif logical == ORIGINAL_PLAN:
                target = source_root / str(original["plan_path"])
            else:
                raise AssertionError(f"unmapped synthetic logical artifact: {logical}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        return handoff_dir


def build_goppi_p7_fixture(  # noqa: PLR0915 - mutually-bound fixture graph
    case_outcomes: tuple[tuple[bool, bool], ...] = ((False, False), (True, True)),
    *,
    confirm_cases: int = 0,
) -> SyntheticP7Fixture:
    if not case_outcomes:
        raise ValueError("at least one synthetic case is required")
    case_ids = [f"case-{index}" for index in range(len(case_outcomes))]
    run_id = _digest("run")
    regrade_id = _digest("regrade")
    evalset = _digest("evalset")
    assignment = _digest("assignment")
    split_policy = _digest("split-policy")
    scope_digest = _digest("scope")
    plan_digest = _digest("plan")
    config_digest = _digest("config")
    evaluator = _digest("evaluator")
    corrected = _digest("corrected-invocation")
    failed = _digest("failed-invocation")
    baseline_context = _digest("baseline-context")
    candidate_context = _digest("candidate-context")
    completion_content = _digest("completion-content")
    run_alias = "synthetic-run"
    run_hex = run_id.removeprefix("sha256:")
    plan_hex = plan_digest.removeprefix("sha256:")

    primary = list(case_ids)
    planned: list[dict[str, object]] = []
    records: list[dict[str, object]] = []
    observations: list[dict[str, object]] = []
    rows: list[dict[str, object]] = []
    receipt_docs: dict[str, dict[str, object]] = {}
    receipt_bytes: dict[str, bytes] = {}
    transitions = {
        "zero_to_zero": 0,
        "zero_to_one": 0,
        "one_to_zero": 0,
        "one_to_one": 0,
        "incomplete_pair_count": 0,
    }
    for case_id, outcomes in zip(case_ids, case_outcomes, strict=True):
        words = tuple("one" if value else "zero" for value in outcomes)
        transitions[f"{words[0]}_to_{words[1]}"] += 1
        for arm, outcome in zip(("baseline", "candidate"), outcomes, strict=True):
            planned.append({"case_id": case_id, "arm": arm, "repeat": 0})
            classification = "observed_true" if outcome else "observed_false"
            records.append(
                {
                    "case_id": case_id,
                    "arm": arm,
                    "repeat": 0,
                    "outcome": int(outcome),
                    "source_reason": f"offline_regrade_{classification}",
                }
            )
            receipt_digests: dict[str, str] = {}
            request_digests: dict[str, str] = {}
            for operation in ("runner", "verifier"):
                receipt_digests[operation] = _digest(f"{case_id}-{arm}-{operation}-receipt")
                request_digests[operation] = _digest(f"{case_id}-{arm}-{operation}-request")
                observations.append(
                    {
                        "case_id": case_id,
                        "variant": arm,
                        "operation": operation,
                        "stage": "same_batch",
                        "receipt": {
                            "content_digest": receipt_digests[operation],
                            "request_digest": request_digests[operation],
                        },
                    }
                )
            answer_digest = _digest(f"{case_id}-{arm}-answer")
            answer_manifest_digest = _digest(f"{case_id}-{arm}-manifest-content")
            trace_parts = (
                "goppi:p7-offline-regrade-trace:v1",
                receipt_digests["runner"],
                answer_manifest_digest,
            )
            trace_payload = "".join(f"{len(part.encode('utf-8'))}:{part}" for part in trace_parts)
            trace_digest = _sha(trace_payload.encode("utf-8"))
            slot_id = _digest(f"{case_id}-{arm}-slot")
            request_sha = _digest(f"{case_id}-{arm}-regrade-request")
            verifier_digest = _digest("verifier")
            verifier_runtime = _digest("verifier-runtime")
            response: dict[str, object] = {
                "schema": "goppi.verifier_response.v2",
                "case_id": case_id,
                "run_id": run_id,
                "variant_id": arm,
                "config_hash": config_digest,
                "trace_digest": trace_digest,
                "verifier": {
                    "verifier_id": "synthetic-verifier-v1",
                    "verifier_digest": verifier_digest,
                },
                "verifier_runtime_digest": verifier_runtime,
                "outcome": {"kind": "binary", "value": outcome},
            }
            stdout = canonical_bytes(response)
            process: dict[str, object] = {
                "exit_code": 0,
                "stdout_base64": base64.b64encode(stdout).decode(),
                "stdout_bytes": len(stdout),
                "stdout_sha256": _sha(stdout),
                "stderr_base64": "",
                "stderr_bytes": 0,
                "stderr_sha256": _sha(b""),
            }
            receipt: dict[str, object] = {
                "schema": "goppi.p7_offline_regrade_protocol_receipt.v1",
                "regrade_id": regrade_id,
                "slot_id": slot_id,
                "request_sha256": request_sha,
                "response": response,
                "process": process,
                "content_digest": _digest(f"{slot_id}-receipt-content"),
                "authentication_tag": _digest(f"{slot_id}-receipt-auth"),
            }
            receipt_name = f"private/protocol-receipts/{slot_id[7:]}.json"
            logical_receipt = f"regrade/{receipt_name}"
            receipt_docs[logical_receipt] = receipt
            receipt_bytes[logical_receipt] = _raw(receipt)
            old: dict[str, object] = {
                "run_id": run_id,
                "run_alias": run_alias,
                "stage": "same_batch",
                "repeat_index": 0,
                "repeat_run_id": run_id,
                "arm": arm,
                "case_id": case_id,
                "runner_attempt_id": f"attempt-{case_id}-{arm}",
                "config_digest": config_digest,
                "arm_context_digest": (
                    baseline_context if arm == "baseline" else candidate_context
                ),
                "answer_manifest_path": f"answers/{case_id}/{arm}/manifest.json",
                "answer_manifest_file_sha256": _digest(f"{case_id}-{arm}-manifest-file"),
                "answer_manifest_content_digest": answer_manifest_digest,
                "answer_object_path": f"answers/{case_id}/{arm}/answer.json",
                "answer_object_file_sha256": _digest(f"{case_id}-{arm}-answer-file"),
                "answer_digest": answer_digest,
                "runner_receipt_digest": receipt_digests["runner"],
                "runner_request_digest": request_digests["runner"],
                "verifier_receipt_digest": receipt_digests["verifier"],
                "verifier_request_digest": request_digests["verifier"],
                "verifier_digest": verifier_digest,
                "original_verifier_runtime_digest": _digest("original-verifier-runtime"),
                "original_verifier_infra_status": "missing",
            }
            regrade: dict[str, object] = {
                "classification": classification,
                "verifier_id": "synthetic-verifier-v1",
                "verifier_digest": verifier_digest,
                "verifier_runtime_digest": verifier_runtime,
                "request_sha256": request_sha,
                "response_path": receipt_name,
                "response_file_sha256": _sha(receipt_bytes[logical_receipt]),
                "protocol_exit_code": 0,
                "protocol_stdout_sha256": process["stdout_sha256"],
                "protocol_stdout_bytes": process["stdout_bytes"],
                "protocol_stderr_sha256": process["stderr_sha256"],
                "protocol_stderr_bytes": 0,
                "outcome": response["outcome"],
            }
            row_provenance: dict[str, object] = {
                "draft_digest": _digest("draft"),
                "evalset_digest": evalset,
                "split_policy_digest": split_policy,
                "assignment_digest": assignment,
                "execution_scope_digest": scope_digest,
                "private_material_artifact_digest": _digest("private-material"),
                "private_material_case_digest": _digest(f"material-{case_id}"),
                "corrected_invocation_digest": corrected,
                "evaluator_identity_digest": evaluator,
            }
            row: dict[str, object] = {
                "schema": "goppi.p7_offline_verifier_regrade_row.v1",
                "regrade_id": regrade_id,
                "slot_id": slot_id,
                "original": old,
                "regrade": regrade,
                "provenance": row_provenance,
                "content_digest": _digest(f"{slot_id}-row-content"),
                "authentication_tag": _digest(f"{slot_id}-row-auth"),
            }
            rows.append(row)

    paired_input: dict[str, object] = {
        "schema": "paired_binary_input.v1",
        "source": {
            "producer": "goppi-offline-verifier-regrade",
            "version": "goppi.p7_offline_verifier_regrade_completion.v1",
            "run_id": run_id,
        },
        "outcome_definition": {
            "name": "goppi_offline_regrade_binary_task_success",
            "positive_means": (
                "persisted answer passed the same verifier core under corrected protocol invocation"
            ),
        },
        "metadata": {
            "baseline_context_id": baseline_context,
            "candidate_context_id": candidate_context,
            "evaluator_id": evaluator,
            "source_completion_digest": completion_content,
            "regrade_id": regrade_id,
            "corrected_invocation_digest": corrected,
            "original_failed_invocation_digest": failed,
        },
        "primary_case_ids": primary,
        "planned_slots": planned,
        "slot_records": records,
    }
    policy: dict[str, object] = {
        "schema": "paired_binary_policy.v1",
        "seed": 42,
        "permutation_iterations": 10000,
        "bootstrap_iterations": 1000,
        "confidence": 0.95,
        "max_work": 2000000,
        "assumptions": {
            "independent_units": False,
            "sign_flip_invariance": False,
            "fixed_sample": True,
        },
    }
    original_result: dict[str, object] = {
        "schema": "goppi.synthetic_result.v1",
        "backend": "native",
        "profile": "synthetic",
        "reservation": {},
        "cycle": {"run_id": run_id},
        "observations": observations,
        "authentication_tag": _digest("result-auth"),
    }
    original_scope: dict[str, object] = {
        "schema": "goppi.local_execution_scope.v1",
        "alias": run_alias,
        "evalset_digest": evalset,
        "assignment_digest": assignment,
        "plan_digest": plan_digest,
        "content_digest": scope_digest,
        "pause_after_stage": "same_batch",
    }
    original_plan: dict[str, object] = {
        "schema": "goppi.synthetic_plan.v1",
        "content_digest": plan_digest,
        "repeats": 1,
    }
    original_public: dict[str, object] = {
        "schema": "goppi.synthetic_public_summary.v1",
        "evalset_digest": evalset,
        "cases": [
            {
                "case_id": case_id,
                "family": "synthetic",
                "source_group_id": "synthetic",
                "split": "explore",
                "verifier_digest": _digest("verifier"),
            }
            for case_id in case_ids
        ],
    }
    cast(list[object], original_public["cases"]).extend(
        {"case_id": f"confirm-{index}", "split": "confirm"} for index in range(confirm_cases)
    )
    source_original: dict[str, object] = {
        "run_id": run_id,
        "run_alias": run_alias,
        "evalset_digest": evalset,
        "assignment_digest": assignment,
        "plan_digest": plan_digest,
        "execution_scope_digest": scope_digest,
        "plan_file_sha256": "",
        "execution_scope_file_sha256": "",
        "result_file_sha256": "",
        "plan_path": f"study/private-project/.goppi/native-plans/{plan_hex}.json",
        "execution_scope_path": (f"study/private-project/.goppi/execution-scopes/{run_alias}.json"),
        "result_path": f"study/private-project/.goppi/runs/{run_hex}/local-result.json",
        "baseline_snapshot_digest": baseline_context,
        "candidate_snapshot_digest": candidate_context,
        "project_config_digest": config_digest,
        "split_policy_digest": split_policy,
        "original_planned_slots": len(planned),
        "original_missing_slots": len(planned),
        "original_outcome_status": "infra_missing",
        "original_outcomes_rewritten": False,
    }
    regrade_source: dict[str, object] = {
        "schema": "goppi.p7_offline_verifier_regrade_source.v1",
        "regrade_id": regrade_id,
        "original": source_original,
        "runtime": {"evaluator_identity_digest": evaluator},
        "private_material": {"path": "private/evalset/explore.json"},
        "verifier": {
            "corrected": {"invocation_digest": corrected},
            "original": {"failed_invocation_digest": failed},
        },
        "content_digest": _digest("source-content"),
        "authentication_tag": _digest("source-auth"),
    }

    files: dict[str, bytes] = {
        MAPPING_INPUT: _raw(paired_input),
        MAPPING_POLICY: _raw(policy),
        REGRADE_SOURCE: _raw(regrade_source),
        REGRADE_ROWS: b"".join(_raw(row) for row in rows),
        "regrade/private/schema.json": _raw({"schema": "synthetic-row-schema.v1"}),
        "regrade/private/controls.json": _raw({"schema": "synthetic-controls.v1"}),
        "regrade/public-summary.json": _raw({"schema": "synthetic-regrade-summary.v1"}),
        ORIGINAL_RESULT: _raw(original_result),
        ORIGINAL_SCOPE: _raw(original_scope),
        ORIGINAL_PLAN: _raw(original_plan),
        ORIGINAL_PUBLIC: _raw(original_public),
        **receipt_bytes,
    }
    source_original["result_file_sha256"] = _sha(files[ORIGINAL_RESULT])
    source_original["execution_scope_file_sha256"] = _sha(files[ORIGINAL_SCOPE])
    source_original["plan_file_sha256"] = _sha(files[ORIGINAL_PLAN])
    files[REGRADE_SOURCE] = _raw(regrade_source)

    receipt_manifest: dict[str, object] = {
        "schema": "goppi.p7_offline_regrade_receipts_manifest.v1",
        "regrade_id": regrade_id,
        "count": len(receipt_bytes),
        "files": [
            {
                "path": logical.removeprefix("regrade/"),
                "sha256": _sha(raw),
                "bytes": len(raw),
            }
            for logical, raw in sorted(receipt_bytes.items())
        ],
        "content_digest": _digest("receipt-manifest-content"),
        "authentication_tag": _digest("receipt-manifest-auth"),
    }
    files[REGRADE_RECEIPTS] = _raw(receipt_manifest)
    completion_files = [
        REGRADE_SOURCE,
        REGRADE_ROWS,
        "regrade/private/schema.json",
        "regrade/private/controls.json",
        REGRADE_RECEIPTS,
        "regrade/public-summary.json",
    ]
    completion: dict[str, object] = {
        "schema": "goppi.p7_offline_verifier_regrade_completion.v1",
        "status": "complete",
        "regrade_id": regrade_id,
        "content_digest": completion_content,
        "authentication_tag": _digest("completion-auth"),
        "files": [
            {
                "path": logical.removeprefix("regrade/"),
                "sha256": _sha(files[logical]),
                "bytes": len(files[logical]),
            }
            for logical in completion_files
        ],
        "protocol_receipts": {
            "count": len(receipt_bytes),
            "manifest_path": "private/protocol-receipts-manifest.json",
            "manifest_sha256": _sha(files[REGRADE_RECEIPTS]),
        },
        "original_outcomes_rewritten": False,
    }
    files[REGRADE_COMPLETION] = _raw(completion)
    provenance: dict[str, object] = {
        "schema": "goppi.p7_regrade_mapping_provenance.v1",
        "status": "mapped_not_analyzed",
        "original_run": {
            "run_id": run_id,
            "original_planned_slots": len(planned),
            "original_missing_slots": len(planned),
            "original_outcome_status": "infra_missing",
            "original_outcomes_rewritten": False,
        },
        "offline_regrade": {
            "regrade_id": regrade_id,
            "completion_sha256": _sha(files[REGRADE_COMPLETION]),
            "completion_content_digest": completion_content,
            "evaluator_identity_digest": evaluator,
            "original_failed_invocation_digest": failed,
            "corrected_invocation_digest": corrected,
        },
        "mapping": {
            "spec_sha256": _digest("mapping-spec"),
            "policy_sha256": _sha(files[MAPPING_POLICY]),
            "paired_input_digest": digest_bytes(canonical_bytes(paired_input)),
            "case_count": len(case_ids),
            "planned_slot_count": len(planned),
            "observed_slot_count": len(records),
            "missing_slot_count": 0,
            "transitions": transitions,
        },
        "claims": {
            "new_model_run": False,
            "original_sealed_outcomes_replaced": False,
            "source_claims_independently_verified": False,
        },
    }
    files[MAPPING_PROVENANCE] = _raw(provenance)
    handoff: dict[str, object] = {
        "schema": "goppi.p7_regrade_consumer_handoff.v1",
        "status": "ready_for_descriptive_analysis",
        "entry_files": {
            "paired_binary_input": {
                "path": "paired-input.json",
                "sha256": _sha(files[MAPPING_INPUT]),
            },
            "analysis_policy": {
                "path": "analysis-policy.json",
                "sha256": _sha(files[MAPPING_POLICY]),
            },
            "derivation_provenance": {
                "path": "derivation-provenance.json",
                "sha256": _sha(files[MAPPING_PROVENANCE]),
            },
            "source_regrade_completion": {
                "path": "study/offline-verifier-regrade/completion-manifest.json",
                "sha256": _sha(files[REGRADE_COMPLETION]),
            },
            "source_original_run_result": {
                "path": source_original["result_path"],
                "sha256": _sha(files[ORIGINAL_RESULT]),
            },
        },
        "limits": {
            "stage": "same_batch",
            "split": "explore",
            "repeat": 0,
            "cases": len(case_ids),
            "planned_slots": len(planned),
            "descriptive_policy_only": True,
            "expected_analysis_status": "not_measured",
            "expected_analysis_reason": "ASSUMPTIONS_NOT_DECLARED",
            "confirm_or_later_stage_claim": False,
            "representative_population_claim": False,
        },
    }
    files[MAPPING_HANDOFF] = _raw(handoff)
    mapping_manifest: dict[str, object] = {
        "schema": "goppi.p7_regrade_mapping_bundle_manifest.v1",
        "status": "complete",
        "files": [
            {
                "path": logical.removeprefix("mapping/"),
                "sha256": _sha(files[logical]),
                "bytes": len(files[logical]),
            }
            for logical in (MAPPING_INPUT, MAPPING_POLICY, MAPPING_PROVENANCE, MAPPING_HANDOFF)
        ],
        "policy_included": False,
        "analysis_included": False,
        "original_outcomes_rewritten": False,
    }
    files[MAPPING_MANIFEST] = _raw(mapping_manifest)
    identities = {logical: ArtifactIdentity(_sha(raw), len(raw)) for logical, raw in files.items()}
    documents = GoppiP7Documents(
        handoff=handoff,
        mapping_manifest=mapping_manifest,
        paired_input=paired_input,
        analysis_policy=policy,
        derivation_provenance=provenance,
        completion_manifest=completion,
        regrade_source=regrade_source,
        regrade_rows=tuple(rows),
        receipt_manifest=receipt_manifest,
        protocol_receipts=receipt_docs,
        original_result=original_result,
        original_scope=original_scope,
        original_plan=original_plan,
        original_public_summary=original_public,
        byte_identities=identities,
    )
    return SyntheticP7Fixture(documents, files)

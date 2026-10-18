"""Exercise installed wheels; optionally resolve the core only from public PyPI."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, cast

if __package__:
    from . import _wheel_smoke as wheel
else:
    import _wheel_smoke as wheel

EVIDENCE_ERROR_EXIT = 2
STORAGE_ERROR_EXIT = 3
BASELINE_RECORD_FILE_COUNT = 7


def _cli_json(cli: Path, args: list[str], output: Path, cwd: Path) -> dict[str, object]:
    result = wheel.success([str(cli), *args, "--json"], cwd)
    wheel.require(result.stderr == "", f"unexpected CLI stderr: {result.stderr[:500]}")
    try:
        document: object = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise wheel.SmokeFailure(f"invalid CLI JSON for {args[0]}") from exc
    wheel.require(isinstance(document, dict), f"CLI JSON is not an object for {args[0]}")
    output.write_text(result.stdout, encoding="utf-8")
    return cast(dict[str, object], document)


def _registry_core_smoke(dist: Path) -> None:
    candidates = sorted(dist.glob("underwrite-*.whl"))
    wheel.require(len(candidates) == 1, "expected one application wheel for registry-core smoke")
    app = candidates[0]
    with tempfile.TemporaryDirectory(prefix="underwrite-registry-core-smoke-") as directory:
        work = Path(directory).resolve()
        wheel.require(
            not work.is_relative_to(wheel.ROOT), "temporary directory must be outside checkout"
        )
        venv = work / "venv"
        _, registry = wheel.install_registry_core(work, venv, app)
        cli = venv / "bin/underwrite"
        fixtures = wheel.ROOT / "fixtures/examples"
        _native_measurement_pilot(cli, fixtures, work)
        _baseline_event_pilot(cli, fixtures, work)
        _measurement_record_pilot(cli, work)
        _measurement_replay_pilot(cli, work)
        _trajectory_pilot(cli, work)
        print(
            json.dumps(
                {
                    "mode": "registry-core",
                    "app_wheel": app.name,
                    "app_sha256": hashlib.sha256(app.read_bytes()).hexdigest(),
                    "published_core": registry,
                    "index": wheel.REGISTRY_INDEX,
                    "installed_package_bytes_match_archives": True,
                    "tasks": "measurement_record_replay_and_trajectory",
                    "state": "passed",
                },
                sort_keys=True,
            )
        )


def _promptfoo_numeric_pilot(
    cli: Path, evidence: Path, work: Path, baseline: dict[str, object]
) -> None:
    body = json.loads((evidence / "synthetic-promptfoo-category-mismatch.json").read_bytes())
    for name in ("successes", "failures", "errors"):
        body["results"]["stats"][name] = float(body["results"]["stats"][name])
    for row in body["results"]["results"]:
        for name in ("promptIdx", "testIdx"):
            row[name] = float(row[name])
    source = work / "promptfoo-integral-counts.json"
    source.write_text(json.dumps(body), encoding="utf-8")
    manifest = work / "promptfoo-integral-counts.case.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "artifact_case.v1",
                "artifact": {"path": source.name},
                "source": {"format": "promptfoo.eval-output", "format_version": "0.123.0"},
            }
        ),
        encoding="utf-8",
    )
    observed = _cli_json(
        cli,
        [
            "ingest",
            "--format",
            "promptfoo.eval-output",
            "--version",
            "0.123.0",
            "--file",
            str(source),
        ],
        work / "promptfoo-integral-observation.json",
        work,
    )
    audited = _cli_json(
        cli,
        ["audit-counts", str(manifest)],
        work / "promptfoo-integral-audit.json",
        work,
    )
    _cli_json(
        cli,
        ["audit-promptfoo", str(source)],
        work / "promptfoo-integral-direct.json",
        work,
    )
    wheel.require(
        (work / "promptfoo-integral-direct.json").read_bytes()
        == (work / "promptfoo-integral-audit.json").read_bytes(),
        "raw and manifest canonical output differs",
    )
    for key in baseline:
        if key == "raw_hash":
            wheel.require(
                audited[key] != baseline[key], "different raw sources lost their identity"
            )
        else:
            wheel.require(
                audited[key] == baseline[key], "integral count spelling changed accounting"
            )
    raw = cast(dict[str, object], observed["raw"])
    wheel.require(
        raw["hash"] == audited["raw_hash"], "ingest/audit consumed different source bytes"
    )


def _promptfoo_record_pilot(
    cli: Path,
    original: Path,
    work: Path,
    expected: dict[str, object],
) -> None:
    raw = original.read_bytes()
    source = work / "selected-promptfoo.json"
    source.write_bytes(raw)
    store = work / "promptfoo-records"
    report = _cli_json(
        cli,
        [
            "records",
            "audit-promptfoo",
            str(source),
            "--root",
            str(store),
            "--id",
            "captured",
        ],
        work / "promptfoo-record.json",
        work,
    )
    analysis = cast(dict[str, object], report["analysis"])
    storage = cast(dict[str, object], report["storage"])
    wheel.require(analysis["report"] == expected, "record capture changed accounting")
    wheel.require(storage["state"] == "saved", "record capture did not save")
    wheel.require(
        cast(dict[str, object], storage["verification"])["valid"] is True,
        "record capture did not verify",
    )
    wheel.require(
        (store / "records/captured/files/source/promptfoo.bin").read_bytes() == raw,
        "captured source differs from the selected bytes",
    )
    source.unlink()
    recovered = _cli_json(
        cli,
        [
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "captured",
            "--name",
            "analysis/accounting.json",
        ],
        work / "recovered-accounting.json",
        work,
    )
    wheel.require(recovered["content"] == expected, "selected read changed the saved result")
    wheel.require(
        recovered["manifest_digest"] == storage["manifest_digest"]
        and recovered["verification_scope"] == "selected_file_against_manifest",
        "selected read misrepresented its identity or scope",
    )
    malformed = work / "malformed-promptfoo.json"
    malformed.write_bytes(b"{")
    failed = wheel.command(
        [
            str(cli),
            "records",
            "audit-promptfoo",
            str(malformed),
            "--root",
            str(store),
            "--id",
            "diagnostic",
            "--json",
        ],
        work,
    )
    wheel.require(
        failed.returncode == EVIDENCE_ERROR_EXIT and failed.stderr == "",
        "wrong saved-diagnostic exit",
    )
    diagnostic = json.loads(failed.stdout)
    wheel.require(
        diagnostic["analysis"]["state"] == "error" and diagnostic["storage"]["state"] == "saved",
        "saved analysis failure lost independent outcomes",
    )
    wheel.require(
        (store / "records/diagnostic/files/source/promptfoo.bin").read_bytes() == b"{",
        "malformed complete source was not retained",
    )
    recovered_error = _cli_json(
        cli,
        [
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "diagnostic",
            "--name",
            "analysis/error.json",
        ],
        work / "recovered-error.json",
        work,
    )
    wheel.require(
        recovered_error["content"] == diagnostic["analysis"]["error"],
        "selected read replaced the saved diagnostic",
    )
    invocation = _cli_json(
        cli,
        [
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "diagnostic",
            "--name",
            "invocation.json",
        ],
        work / "recovered-invocation.json",
        work,
    )
    content = cast(dict[str, object], invocation["content"])
    arguments = cast(dict[str, object], content["arguments"])
    wheel.require(
        arguments["selection_mode"] == "auto"
        and "reader_profile" not in arguments
        and "producer_version" not in arguments,
        "failed automatic input selection fabricated an applied reader or source version",
    )


def _promptfoo_manifest_pilot(cli: Path, directory: Path, raw: bytes) -> None:
    source = directory / "manifest-source.json"
    source.write_bytes(raw)
    expected_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
    artifact = {"path": source.name, "expected_hash": expected_hash}
    manifest = {
        "schema": "artifact_case.v1",
        "artifact": artifact,
        "source": {
            "format": "promptfoo.eval-output",
            "format_version": "0.124.1",
            "ref": "synthetic:manifest-export",
            "locator": "synthetic:manifest-locator",
        },
    }
    case = directory / "case.json"
    case.write_text(json.dumps(manifest), encoding="utf-8")
    inspected = _cli_json(cli, ["inspect", str(case)], directory / "inspection.json", directory)
    counted = _cli_json(
        cli, ["audit-counts", str(case)], directory / "manifest-audit.json", directory
    )
    identity = cast(dict[str, object], counted["source"])
    wheel.require(
        counted["schema"] == "promptfoo_accounting.v2"
        and counted["raw_hash"] == inspected["raw_hash"] == expected_hash
        and inspected["expected_hash_check"] == "matched"
        and identity["producer_version"] == json.loads(raw)["metadata"]["promptfooVersion"]
        and identity["producer_version_state"] == "declared"
        and identity["claims_verified"] is False
        and "observation_digest" not in counted
        and counted["row_tallies"] == {"successes": 3, "failures": 1, "errors": 3, "total": 7},
        "manifest audit lost raw hash binding, declared source identity or counts",
    )
    artifact["expected_hash"] = "sha256:" + "0" * 64
    case.write_text(json.dumps(manifest), encoding="utf-8")
    refused = wheel.command([str(cli), "audit-counts", str(case), "--json"], directory)
    wheel.require(
        refused.returncode == EVIDENCE_ERROR_EXIT
        and refused.stdout == ""
        and json.loads(refused.stderr)["code"] == "EXPECTED_HASH_MISMATCH",
        "manifest count audit accepted mismatching artifact bytes",
    )


def _promptfoo_auto_mutations(cli: Path, source: Path, work: Path) -> None:
    payload = json.loads(source.read_bytes())
    payload.pop("metadata", None)
    payload["unconsumed"] = "PRIVATE_CANARY"
    selected = work / "undeclared-source.json"
    selected.write_text(json.dumps(payload), encoding="utf-8")
    report = _cli_json(
        cli,
        ["audit-promptfoo", str(selected), "--view", "row-index"],
        work / "undeclared-result.json",
        work,
    )
    identity = cast(dict[str, object], report["source"])
    wheel.require(
        identity["producer_version"] is None
        and identity["producer_version_state"] == "unknown"
        and "PRIVATE_CANARY" not in json.dumps(report),
        "auto intake fabricated a version or leaked an unconsumed field",
    )
    payload["results"]["version"] = 4
    selected.write_text(json.dumps(payload), encoding="utf-8")
    refused = wheel.command([str(cli), "audit-promptfoo", str(selected), "--json"], work)
    wheel.require(
        refused.returncode == EVIDENCE_ERROR_EXIT
        and refused.stdout == ""
        and "PRIVATE_CANARY" not in refused.stderr,
        "unknown export generation bypassed the auto reader",
    )


def _promptfoo_auto_pilot(cli: Path, work: Path) -> None:
    retired = ("promptfoo_accounting", "promptfoo_row_index", "promptfoo_record")
    assets = wheel.success(
        [
            str(cli.with_name("python")),
            "-c",
            "import json,sys; from importlib.resources import files; "
            "names=json.loads(sys.argv[1]); "
            "print(json.dumps({name: files('underwrite').joinpath('_contracts', "
            "name+'.v1.schema.json').is_file() for name in names}))",
            json.dumps(retired),
        ],
        work,
    )
    wheel.require(
        json.loads(assets.stdout) == {name: False for name in retired},
        "installed package retained superseded Promptfoo output schemas",
    )
    for folder in ("promptfoo", "promptfoo-0.124.1"):
        directory = work / f"{folder}-auto"
        directory.mkdir()
        source = directory / "source.json"
        raw = (wheel.ROOT / "fixtures/golden/external" / folder / "results.json").read_bytes()
        source.write_bytes(raw)
        declared_version = json.loads(raw)["metadata"]["promptfooVersion"]
        report = _cli_json(
            cli, ["audit-promptfoo", str(source)], directory / "auto.json", directory
        )
        removed = wheel.command(
            [str(cli), "audit-promptfoo", str(source), "--version", declared_version, "--json"],
            directory,
        )
        wheel.require(
            removed.returncode == EVIDENCE_ERROR_EXIT
            and removed.stdout == ""
            and json.loads(removed.stderr)["code"] == "USAGE_ERROR",
            "removed Promptfoo output selector was not refused",
        )
        wheel.require(
            report["schema"] == "promptfoo_accounting.v2"
            and report["raw_hash"] == "sha256:" + hashlib.sha256(raw).hexdigest()
            and "observation_digest" not in report,
            "auto accounting changed byte identity or invented a full-source observation",
        )
        identity = cast(dict[str, object], report["source"])
        wheel.require(
            identity["producer_version"] == declared_version
            and identity["producer_version_state"] == "declared"
            and identity["claims_verified"] is False,
            "auto accounting lost declared-version limits",
        )
        api = wheel.success(
            [
                str(cli.with_name("python")),
                "-c",
                "import json,sys; from pathlib import Path; "
                "from underwrite.instrument.ingest.accounting import audit_promptfoo_bytes; "
                "print(json.dumps(audit_promptfoo_bytes(Path(sys.argv[1]).read_bytes())))",
                str(source),
            ],
            directory,
        )
        wheel.require(json.loads(api.stdout) == report, "installed auto API and CLI disagree")
        _promptfoo_record_pilot(cli, source, directory, report)
        verified = _cli_json(
            cli,
            [
                "records",
                "verify",
                "--root",
                str(directory / "promptfoo-records"),
                "--id",
                "captured",
            ],
            directory / "whole-verification.json",
            directory,
        )
        wheel.require(verified["valid"] is True, "auto record did not verify after input removal")
        _promptfoo_auto_mutations(cli, source, directory)


def _promptfoo_current_pilot(cli: Path, work: Path) -> None:
    directory = work / "promptfoo-current"
    directory.mkdir()
    source = wheel.ROOT / "fixtures/golden/external/promptfoo-0.124.1/results.json"
    raw = source.read_bytes()
    observation = directory / "observation.json"
    projected = _cli_json(
        cli,
        [
            "ingest",
            "--format",
            "promptfoo.eval-output",
            "--version",
            "0.124.1",
            "--file",
            str(source),
        ],
        observation,
        directory,
    )
    wheel.require(projected["payload"] == json.loads(raw), "new profile changed source")
    report = _cli_json(
        cli,
        ["audit-promptfoo", str(source)],
        directory / "accounting.json",
        directory,
    )
    expected = {"successes": 3, "failures": 1, "errors": 3, "total": 7}
    wheel.require(
        report["row_tallies"] == report["producer_stats"] == expected
        and report["declared_relation"] == "consistent",
        "current Promptfoo capture changed declared category meaning",
    )
    row_view = _cli_json(
        cli,
        ["audit-promptfoo", str(source), "--view", "row-index"],
        directory / "row-index.json",
        directory,
    )
    selected_rows = row_view.get("rows")
    wheel.require(
        isinstance(selected_rows, list), "installed Promptfoo row index did not return rows"
    )
    source_rows = cast(list[object], selected_rows)
    wheel.require(
        all(isinstance(row, dict) for row in source_rows),
        "installed Promptfoo row index contains non-object rows",
    )
    rows = cast(list[dict[str, object]], source_rows)
    wheel.require(
        row_view.get("schema") == "promptfoo_row_index.v2"
        and row_view.get("raw_hash") == "sha256:" + hashlib.sha256(raw).hexdigest()
        and [row.get("row_index") for row in rows] == list(range(expected["total"]))
        and [row.get("category") for row in rows]
        == ["successes", "failures", "errors", "errors", "successes", "errors", "successes"]
        and all(
            set(row)
            == {"row_index", "category", "source_success", "source_failure_reason", "grading_pass"}
            for row in rows
        )
        and row_view.get("verification") == [],
        "installed Promptfoo row index changed selection, identity or content boundary",
    )
    _promptfoo_record_pilot(cli, source, directory, report)
    _promptfoo_manifest_pilot(cli, directory, raw)
    refused = wheel.command(
        [
            str(cli),
            "measure",
            "--observation",
            str(observation),
            "--policy",
            str(wheel.ROOT / "fixtures/examples/event-acceptance/policy.json"),
            "--json",
        ],
        directory,
    )
    wheel.require(
        refused.returncode == EVIDENCE_ERROR_EXIT
        and refused.stdout == ""
        and json.loads(refused.stderr)["code"] == "UNSUPPORTED_PROFILE",
        "Promptfoo version admission bypassed native measurement boundary",
    )


def _evidence_pilots(cli: Path, fixtures: Path, work: Path) -> None:
    evidence = fixtures / "evidence"
    audit = _cli_json(
        cli,
        ["audit-counts", str(evidence / "synthetic-promptfoo-category-mismatch.case.json")],
        work / "audit.json",
        work,
    )
    wheel.require(audit.get("declared_relation") == "inconsistent", "count mismatch was hidden")
    _promptfoo_numeric_pilot(cli, evidence, work, audit)
    _promptfoo_record_pilot(
        cli, evidence / "synthetic-promptfoo-category-mismatch.json", work, audit
    )
    _promptfoo_current_pilot(cli, work)
    _promptfoo_auto_pilot(cli, work)
    comparison = _cli_json(
        cli,
        [
            "compare-declarations",
            "--baseline",
            str(evidence / "baseline-accounting.json"),
            "--candidate",
            str(evidence / "candidate-accounting.json"),
            "--manifest",
            str(evidence / "comparison-declarations.json"),
        ],
        work / "comparison.json",
        work,
    )
    wheel.require(comparison.get("declared_relation") == "different", "declaration mismatch hidden")
    wheel.require(comparison.get("actual_conditions_verified") is False, "unverified conditions")
    for fixture, expected in (
        ("paired-complete", "measured"),
        ("paired-incomplete", "not_measured"),
        ("observed-and-missing", "not_measured"),
    ):
        paired = _cli_json(
            cli,
            [
                "pair-binary",
                "--input",
                str(evidence / f"synthetic-{fixture}.json"),
                "--policy",
                str(evidence / "paired-binary-policy.json"),
            ],
            work / f"paired-{fixture}.json",
            work,
        )
        wheel.require(paired.get("schema") == "paired_binary_evidence.v1", "paired schema mismatch")
        wheel.require(paired.get("status") == expected, "paired missingness was hidden")
        if fixture == "observed-and-missing":
            accounting = cast(dict[str, object], paired["accounting"])
            examples = cast(list[dict[str, object]], accounting["observed_reason_examples"])
            wheel.require(len(examples) == 1, "observed reason was lost")
            wheel.require(examples[0]["outcome"] == 0, "observed zero was recoded")
            wheel.require(examples[0]["source_claim_unverified"] is True, "source reason promoted")
    review = _cli_json(
        cli,
        [
            "review-pair",
            "--input",
            str(evidence / "synthetic-paired-complete.json"),
            "--policy",
            str(evidence / "paired-binary-policy.json"),
            "--context",
            str(evidence / "synthetic-review-partition.json"),
        ],
        work / "paired-review.json",
        work,
    )
    wheel.require(review.get("schema") == "paired_review.v1", "review schema mismatch")
    wheel.require(
        review.get("actual_conditions_verified") is False, "review verified source claims"
    )
    reference = cast(dict[str, object], review["paired_evidence_ref"])
    wheel.require(reference.get("status") == "measured", "review lost measured status")
    history = cast(dict[str, object], review["evaluation_history"])
    statuses = cast(dict[str, object], history["selected_statuses"])
    wheel.require(statuses.get("baseline") == "invalidated", "history invalidation was lost")
    partition = cast(dict[str, object], review["case_partition"])
    groups = cast(list[dict[str, object]], partition["groups"])
    wheel.require(groups[-1]["category"] is None, "unknown category was lost")
    wheel.require(groups[0]["case_count"] == 0, "empty category was lost")
    error = wheel.command([str(cli), "pair-binary", "--json"], work)
    wheel.require(
        error.returncode == EVIDENCE_ERROR_EXIT and error.stdout == "",
        "pilot error channel mismatch",
    )
    diagnostic = json.loads(error.stderr)
    wheel.require(diagnostic.get("schema") == "evidence_error.v1", "pilot error schema mismatch")


def _bound_deepeval_record_pilot(
    cli: Path, work: Path, capabilities: dict[str, object], *, source_fixture: Path
) -> None:
    commands = cast(list[dict[str, object]], capabilities["commands"])
    records_entry = next(item for item in commands if item["command"] == "records")
    profiles = cast(list[dict[str, object]], records_entry["profiles"])
    selector = "audit-deepeval|owner-asserted-metric-bindings"
    selected = next(item for item in profiles if item["name"] == selector)
    required = ["file", "bindings", "root", "id"]
    wheel.require(
        selected["required_inputs"] == required
        and selected["input_schemas"] == ["deepeval_metric_bindings.v2"]
        and selected["output_schemas"] == ["deepeval_bound_record.v2"],
        "installed bound DeepEval capability does not describe its executable route",
    )
    output_schemas = cast(list[str], selected["output_schemas"])
    source = work / "bound-deepeval-source.json"
    source_raw = source_fixture.read_bytes()
    source.write_bytes(source_raw)
    bindings = work / "bound-deepeval-bindings.json"
    pack_raw = (
        wheel.ROOT / "fixtures/examples/evidence/synthetic-deepeval.bindings.v2.json"
    ).read_bytes()
    bindings.write_bytes(pack_raw)
    store = work / "bound-deepeval-store"
    values = {"bindings": str(bindings), "root": str(store), "id": "bound"}
    args = ["records", "audit-deepeval", str(source)]
    for requirement in cast(list[str], selected["required_inputs"])[1:]:
        args.extend(("--" + requirement, values[requirement]))
    report = _cli_json(cli, args, work / "bound-deepeval-record.json", work)
    analysis = cast(dict[str, object], report["analysis"])
    result = cast(dict[str, object], analysis["report"])
    source_identity = cast(dict[str, object], cast(dict[str, object], result["source"])["identity"])
    sources = cast(dict[str, dict[str, object]], report["sources"])
    storage = cast(dict[str, object], report["storage"])
    verification = cast(dict[str, object], storage["verification"])
    wheel.require(
        report["schema"] == output_schemas[0]
        and source_identity["raw_hash"]
        == sources["deepeval"]["raw_hash"]
        == "sha256:" + hashlib.sha256(source_raw).hexdigest()
        and sources["bindings"]["raw_hash"] == "sha256:" + hashlib.sha256(pack_raw).hexdigest()
        and verification["files_checked"] == 2 + len(sources),
        "installed bound DeepEval capture changed source identity or retained file count",
    )
    identity = cast(dict[str, object], result["source"])
    declaration = cast(dict[str, object], result["declaration_source"])
    wheel.require(
        result["schema"] == "evidence_result.v2"
        and identity["producer_version"] is None
        and identity["producer_version_state"] == "unknown"
        and declaration["raw_hash"] == sources["bindings"]["raw_hash"],
        "automatic bound result invented a producer version or lost its declaration bytes",
    )
    direct = _cli_json(
        cli,
        ["audit-deepeval", str(source), "--bindings", str(bindings)],
        work / "bound-direct.json",
        work,
    )
    wheel.require(direct == result, "automatic direct and retained bound results differ")
    for view, schema in (
        ("summary", "common_result_inspection.v3"),
        ("actions", "common_result_inspection_actions.v3"),
    ):
        inspection = _cli_json(
            cli,
            ["inspect-result", str(work / "bound-direct.json"), "--view", view],
            work / f"bound-inspected-{view}.json",
            work,
        )
        wheel.require(
            inspection["schema"] == schema
            and inspection["selected_result"] == {
                "format": "evidence_result.v2",
                "raw_hash": "sha256:" + hashlib.sha256(
                    (work / "bound-direct.json").read_bytes()
                ).hexdigest(),
            },
            "current bound inspection changed its original input identity",
        )
    source.unlink()
    bindings.unlink()
    verified = _cli_json(
        cli,
        ["records", "verify", "--root", str(store), "--id", "bound"],
        work / "bound-deepeval-verification.json",
        work,
    )
    recovered = _cli_json(
        cli,
        [
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "bound",
            "--name",
            "analysis/evidence_result.json",
        ],
        work / "bound-deepeval-recovered.json",
        work,
    )
    wheel.require(
        verified["valid"] is True and recovered["content"] == result,
        "installed bound DeepEval result did not survive source removal",
    )
    recovery_code = (
        "import json,sys; from pathlib import Path; "
        "from underwrite.service.records import read_record_file; "
        "print(json.dumps({role:read_record_file(Path(sys.argv[1]),'bound',"
        "f'source/{role}.bin',max_bytes=4*1024*1024).sha256 "
        "for role in ('deepeval','bindings')}))"
    )
    raw_recovery = wheel.success(
        [str(cli.with_name("python")), "-c", recovery_code, str(store)], work
    )
    wheel.require(
        json.loads(raw_recovery.stdout)
        == {role: captured["raw_hash"] for role, captured in sources.items()},
        "bound record did not recover both original source byte identities",
    )


def _deepeval_legacy_pack_refusals(cli: Path, work: Path) -> None:
    """Keep old scope assertions intact and test direct versus capture admission order."""
    selected = work / "rejected-owner-pack"
    selected.mkdir()
    old_pack = wheel.ROOT / "fixtures/examples/evidence/synthetic-deepeval-4.2.8.bindings.json"
    refused = wheel.command(
        [
            str(cli),
            "audit-deepeval",
            str(selected / "missing-source.json"),
            "--bindings",
            str(old_pack),
            "--json",
        ],
        selected,
    )
    error = json.loads(refused.stderr)
    wheel.require(
        refused.returncode == EVIDENCE_ERROR_EXIT
        and refused.stdout == ""
        and error["code"] == "UNSUPPORTED_PROFILE"
        and error["reason"] == "DEEPEVAL_BINDING_PACK_SCOPE_UNSUPPORTED"
        and error["location"] == "/bindings/schema",
        "old pack was widened or direct binding read its missing TestRun before pack admission",
    )
    source = selected / "source.json"
    source_raw = (wheel.ROOT / "fixtures/examples/deepeval-auto-4.3.1/ordinary.json").read_bytes()
    source.write_bytes(source_raw)
    pack_raw = old_pack.read_bytes()
    store = selected / "records"
    captured = wheel.command(
        [
            str(cli),
            "records",
            "audit-deepeval",
            str(source),
            "--bindings",
            str(old_pack),
            "--root",
            str(store),
            "--id",
            "old-pack-refused",
            "--json",
        ],
        selected,
    )
    report = json.loads(captured.stdout)
    diagnostic_files = (
        "source/deepeval.bin",
        "source/bindings.bin",
        "analysis/error.json",
        "invocation.json",
    )
    wheel.require(
        captured.returncode == EVIDENCE_ERROR_EXIT
        and captured.stderr == ""
        and report["schema"] == "deepeval_bound_record.v2"
        and report["analysis"] == {"state": "error", "phase": "analysis", "error": error}
        and report["storage"]["state"] == "saved"
        and report["storage"]["verification"]["valid"] is True
        and report["storage"]["verification"]["files_checked"] == len(diagnostic_files),
        "old-pack capture lost its distinct retained analysis diagnostic semantics",
    )
    for role, raw in (("deepeval", source_raw), ("bindings", pack_raw)):
        wheel.require(
            report["sources"][role]["state"] == "captured"
            and report["sources"][role]["raw_hash"] == "sha256:" + hashlib.sha256(raw).hexdigest()
            and (store / "records/old-pack-refused/files/source" / f"{role}.bin").read_bytes()
            == raw,
            "old-pack diagnostic capture changed or lost a complete original input",
        )


def _record_pilots(cli: Path, fixtures: Path, work: Path) -> None:
    installed_capabilities = _cli_json(cli, ["capabilities"], work / "capabilities.json", work)
    checkout_capabilities = wheel.success(
        [sys.executable, "-m", "underwrite.cli.main", "capabilities", "--json"], wheel.ROOT
    )
    wheel.require(
        installed_capabilities == json.loads(checkout_capabilities.stdout),
        "checkout and installed capabilities differ",
    )
    for name, source_fixture in (
        (
            "bound-source-4.2.8",
            wheel.ROOT / "fixtures/golden/external/deepeval-4.2.8/ordinary.json",
        ),
        ("bound-source-4.3.1", wheel.ROOT / "fixtures/examples/deepeval-auto-4.3.1/ordinary.json"),
    ):
        selected_work = work / name
        selected_work.mkdir()
        _bound_deepeval_record_pilot(
            cli, selected_work, installed_capabilities, source_fixture=source_fixture
        )
    _deepeval_legacy_pack_refusals(cli, work)
    commands = cast(list[dict[str, object]], installed_capabilities["commands"])
    for command, name, fixture, version, schema in (
        (
            "audit-deepeval",
            "deepeval.test-run|auto",
            "deepeval-4.2.8/ordinary.json",
            None,
            "deepeval_accounting.v3",
        ),
        (
            "audit-promptfoo",
            "promptfoo.eval-output|auto",
            "promptfoo-0.124.1/results.json",
            "0.124.1",
            "promptfoo_accounting.v2",
        ),
    ):
        descriptor = next(item for item in commands if item["command"] == command)
        profiles = cast(list[dict[str, object]], descriptor["profiles"])
        selected = next(item for item in profiles if item["name"] == name)
        wheel.require(
            selected["required_inputs"] == ["file"] and selected["output_schemas"] == [schema],
            f"installed {name} capability selector changed",
        )
        args = [command, str(wheel.ROOT / "fixtures/golden/external" / fixture)]
        observed = _cli_json(cli, args, work / f"{command}-advertised.json", work)
        source = cast(dict[str, object], observed["source"])
        wheel.require(
            observed["schema"] == schema
            and source["producer_version"] == version
            and source["producer_version_state"] == ("unknown" if version is None else "declared"),
            f"installed {name} capability selected the wrong source profile",
        )
    audit = _cli_json(
        cli,
        ["audit-run", "--input", str(fixtures / "run-audit/synthetic-inventory.json")],
        work / "run-audit.json",
        work,
    )
    wheel.require(audit.get("schema") == "binary_run_audit.v1", "run audit schema mismatch")
    store = work / "records-store"
    saved = _cli_json(
        cli,
        [
            "records",
            "save",
            "--root",
            str(store),
            "--spec",
            str(fixtures / "records/record-spec.json"),
        ],
        work / "saved.json",
        work,
    )
    wheel.require(saved.get("schema") == "record_view.v1", "record view schema mismatch")
    shown = _cli_json(
        cli,
        ["records", "show", "--root", str(store), "--id", "synthetic.capture-001"],
        work / "shown.json",
        work,
    )
    wheel.require(saved == shown, "installed record save/show changed manifest")
    checked = _cli_json(
        cli,
        ["records", "verify", "--root", str(store), "--id", "synthetic.capture-001"],
        work / "verified.json",
        work,
    )
    wheel.require(checked.get("valid") is True, "installed record verification failed")
    listing = _cli_json(cli, ["records", "list", "--root", str(store)], work / "catalog.json", work)
    wheel.require(listing.get("record_count") == 1, "installed record catalog mismatch")


def _handoff_pilot(cli: Path, work: Path) -> None:
    source = work / "handoff-source"
    wheel.success(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; "
            "from tests.drivers.goppi_p7_fixture import build_goppi_p7_fixture; "
            "build_goppi_p7_fixture().write(Path(sys.argv[1])); "
            "build_goppi_p7_fixture(((True, True),) * 22).write(Path(sys.argv[2]))",
            str(source),
            str(work / "larger-handoff-source"),
        ],
        wheel.ROOT,
    )
    store = work / "handoff-store"
    report = _cli_json(
        cli,
        [
            "review-handoff",
            "--handoff",
            str(source / "study/paired-analysis-input"),
            "--source-root",
            str(source),
            "--record-root",
            str(store),
            "--record-id",
            "synthetic.handoff",
        ],
        work / "handoff-result.json",
        work,
    )
    analysis = cast(dict[str, object], report["analysis"])
    summary = cast(dict[str, object], analysis["summary"])
    wheel.require(summary["measurement_status"] == "not_measured", "handoff fabricated measurement")
    wheel.require(summary["conditions_coverage"] == "unknown", "handoff fabricated conditions")
    saved = cast(dict[str, object], report["storage"])
    wheel.require(saved["state"] == "saved", "installed handoff capture failed")
    verification = _cli_json(
        cli,
        ["records", "verify", "--root", str(store), "--id", "synthetic.handoff"],
        work / "handoff-verified.json",
        work,
    )
    wheel.require(verification["valid"] is True, "installed handoff record is damaged")

    larger_source = work / "larger-handoff-source"
    larger_args = [
        "review-handoff",
        "--handoff",
        str(larger_source / "study/paired-analysis-input"),
        "--source-root",
        str(larger_source),
    ]
    larger = _cli_json(cli, larger_args, work / "larger-handoff.json", work)
    wheel.require(
        cast(dict[str, object], larger["analysis"])["state"] == "result",
        "installed analysis inherited storage capacity",
    )
    refused_store = work / "refused-handoff-store"
    refused = wheel.command(
        [
            str(cli),
            *larger_args,
            "--record-root",
            str(refused_store),
            "--record-id",
            "too-large",
            "--json",
        ],
        work,
    )
    wheel.require(
        refused.returncode == STORAGE_ERROR_EXIT and refused.stderr == "",
        "wrong storage refusal exit",
    )
    refusal = json.loads(refused.stdout)
    wheel.require(refusal["analysis"] == larger["analysis"], "storage refusal lost analysis")
    wheel.require(
        refusal["storage"]["error"]["code"] == "INPUT_LIMIT_EXCEEDED",
        "wrong storage refusal diagnostic",
    )
    wheel.require(not refused_store.exists(), "oversized capture mutated store")


def _deepeval_record_pilot(
    cli: Path,
    work: Path,
    source: Path,
    expected: dict[str, object],
    *,
    view: str = "accounting",
) -> None:
    selected = work / "deepeval-selected.json"
    raw = source.read_bytes()
    selected.write_bytes(raw)
    store = work / "deepeval-records"
    arguments = ["records", "audit-deepeval", str(selected), "--root", str(store)]
    if view != "accounting":
        arguments.extend(["--view", view])
    report = _cli_json(cli, [*arguments, "--id", "captured"], work / "deepeval-record.json", work)
    analysis = cast(dict[str, object], report["analysis"])
    storage = cast(dict[str, object], report["storage"])
    wheel.require(
        report["schema"]
        == ("deepeval_record.v3" if view == "accounting" else "deepeval_trace_record.v2")
        and analysis["report"] == expected,
        "DeepEval capture changed current analysis contract",
    )
    wheel.require(storage["state"] == "saved", "DeepEval capture failed to save")
    wheel.require(
        (store / "records/captured/files/source/deepeval.bin").read_bytes() == raw,
        "DeepEval source bytes changed",
    )
    selected.unlink()
    verification = _cli_json(
        cli,
        ["records", "verify", "--root", str(store), "--id", "captured"],
        work / "deepeval-verified.json",
        work,
    )
    wheel.require(
        verification == storage["verification"], "DeepEval retained record no longer verifies"
    )
    recovered = _cli_json(
        cli,
        [
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "captured",
            "--name",
            f"analysis/{view}.json",
        ],
        work / "deepeval-recovered.json",
        work,
    )
    wheel.require(
        recovered["content"] == expected, "DeepEval retained analysis could not be reused"
    )
    wheel.require(
        recovered["manifest_digest"] == storage["manifest_digest"],
        "DeepEval recovered manifest differs",
    )
    raw_identity = wheel.success(
        [
            str(cli.with_name("python")),
            "-c",
            "import sys; from pathlib import Path; "
            "from underwrite.service.records import read_record_file; "
            "from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES; "
            "print(read_record_file(Path(sys.argv[1]), 'captured', 'source/deepeval.bin', "
            "max_bytes=DEFAULT_MAX_BYTES).sha256)",
            str(store),
        ],
        work,
    )
    wheel.require(
        raw_identity.stdout.strip() == expected["raw_hash"],
        "bounded stored-source read lost original identity after input removal",
    )
    failed = wheel.command([str(cli), *arguments, "--id", "unavailable", "--json"], work)
    wheel.require(
        failed.returncode == EVIDENCE_ERROR_EXIT and failed.stderr == "",
        "DeepEval diagnostic capture used the wrong channel or exit",
    )
    diagnostic = json.loads(failed.stdout)
    shown = _cli_json(
        cli,
        ["records", "show", "--root", str(store), "--id", "unavailable"],
        work / "deepeval-unavailable-record.json",
        work,
    )
    metadata = cast(dict[str, object], cast(dict[str, object], shown["record"])["metadata"])
    wheel.require(
        "source_format_version" not in diagnostic
        and not {"format_version", "producer_version", "producer_version_state", "reader_profile"}
        & metadata.keys(),
        "failed DeepEval capture invented a producer version or applied reader",
    )
    wheel.require(
        diagnostic["source"]["state"] == "unavailable"
        and diagnostic["analysis"]["phase"] == "acquisition"
        and diagnostic["storage"]["state"] == "saved",
        "DeepEval acquisition failure lost independent outcomes",
    )


def _deepeval_pilot(cli: Path, work: Path) -> None:
    source = work / "normalization-source.json"
    source.write_text(
        json.dumps(
            {
                "testCases": [
                    {
                        "name": "synthetic",
                        "input": "local",
                        "success": False,
                        "metricsData": [
                            {"name": "example", "threshold": 0.5, "success": False, "score": -0.0},
                            {
                                "name": "evaluator",
                                "threshold": 0.5,
                                "success": False,
                                "error": "synthetic error",
                            },
                            {"name": "integer", "threshold": 1, "success": True, "score": 1},
                        ],
                    }
                ],
                "traceMetricsScores": {
                    "agent": {},
                    "tool": {},
                    "retriever": {},
                    "llm": {},
                    "base": {},
                },
            }
        ),
        encoding="utf-8",
    )
    observed = _cli_json(
        cli,
        ["ingest", "--format", "deepeval.test-run", "--version", "4.1.1", "--file", str(source)],
        work / "normalization-cli.json",
        work,
    )
    library = wheel.success(
        [
            str(cli.parent / "python"),
            "-c",
            "import json, sys; from pathlib import Path; "
            "from underwrite.instrument.ingest.application import packaged_ingestor; "
            "from underwrite.instrument.ingest.project import Source; "
            "result = packaged_ingestor(max_bytes=4096, max_depth=16).file("
            "Path(sys.argv[1]), Source('deepeval.test-run', '4.1.1', None)); "
            "print(json.dumps(result.observation, sort_keys=True))",
            str(source),
        ],
        work,
    )
    wheel.require(library.stderr == "", "installed library emitted unexpected diagnostics")
    wheel.require(
        library.stdout.strip() == json.dumps(observed, sort_keys=True),
        "installed API and CLI disagree on normalized JSON value representation",
    )
    payload = cast(dict[str, object], observed["payload"])
    cases = cast(list[dict[str, object]], payload["testCases"])
    metric = cast(list[dict[str, object]], cases[0]["metricsData"])[0]
    wheel.require(
        type(metric["score"]) is int and metric["score"] == 0,
        "canonical negative zero was not normalized",
    )
    wheel.require(
        metric["success"] is False and "error" not in metric,
        "normalization merged boolean, score, or field absence",
    )

    accounting = _cli_json(
        cli,
        ["audit-deepeval", str(source)],
        work / "deepeval-accounting.json",
        work,
    )
    counts = cast(dict[str, dict[str, int]], accounting["counts"])
    _deepeval_record_pilot(cli, work, source, accounting)
    expected_scored = 2
    wheel.require(
        counts["metrics"]["scored"] == expected_scored and counts["metrics"]["error"] == 1,
        "installed accounting conflated score and evaluator error",
    )
    rows = cast(list[dict[str, object]], accounting["cases"])
    error_metric = cast(list[dict[str, object]], rows[0]["metrics"])[1]
    wheel.require(
        error_metric["state"] == "error" and "score" not in error_metric,
        "installed accounting invented a score for an error",
    )


def _deepeval_flaky_fields_pilot(cli: Path, work: Path) -> None:
    golden = wheel.ROOT / "fixtures/golden/external/deepeval-4.2.8"
    for scenario in ("ordinary", "flaky_metric", "flaky_case", "score_without_verdict"):
        directory = work / f"deepeval-flaky-fields-{scenario}"
        directory.mkdir()
        source = directory / "source.json"
        source.write_bytes((golden / f"{scenario}.json").read_bytes())
        observation = directory / "observation.json"
        _cli_json(
            cli,
            [
                "ingest",
                "--format",
                "deepeval.test-run",
                "--version",
                "4.2.8",
                "--file",
                str(source),
            ],
            observation,
            directory,
        )
        report = _cli_json(
            cli,
            ["audit-deepeval", str(source)],
            directory / "accounting.json",
            directory,
        )
        wheel.require(
            report["schema"] == "deepeval_accounting.v3"
            and cast(dict[str, object], report["source"])["producer_version_state"] == "unknown"
            and cast(dict[str, object], report["source"])["producer_version"] is None,
            "wrong current DeepEval contract or invented producer release",
        )
        case = cast(list[dict[str, object]], report["cases"])[0]
        metrics = cast(list[dict[str, object]], case["metrics"])
        if scenario == "ordinary":
            wheel.require(
                metrics[1]["score"] == 0
                and metrics[2]["state"] == "error"
                and "score" not in metrics[2],
                "DeepEval field-era accounting collapsed zero and evaluator error",
            )
        elif scenario == "flaky_metric":
            wheel.require(
                case["source_success"] is True
                and metrics[1]["source_flaky"] is True
                and metrics[1]["source_success"] is False,
                "DeepEval flaky metric changed independent case verdict",
            )
        elif scenario == "flaky_case":
            wheel.require(
                case["source_flaky"] is True and case["source_success"] is False,
                "DeepEval flaky case was promoted to pass",
            )
        else:
            expected_score = 0.3
            wheel.require(
                metrics[1]["state"] == "scored"
                and metrics[1]["score"] == expected_score
                and metrics[1]["source_success"] is None
                and metrics[1]["threshold"] is None,
                "DeepEval score-only metric acquired a verdict or lost its score",
            )
            refused = wheel.command(
                [
                    str(cli),
                    "measure",
                    "--observation",
                    str(observation),
                    "--policy",
                    str(wheel.ROOT / "fixtures/examples/event-acceptance/policy.json"),
                    "--json",
                ],
                directory,
            )
            wheel.require(
                refused.returncode == EVIDENCE_ERROR_EXIT
                and refused.stdout == ""
                and json.loads(refused.stderr)["code"] == "UNSUPPORTED_PROFILE",
                "DeepEval source ingestion bypassed native measurement boundary",
            )
        if scenario in {"flaky_case", "score_without_verdict"}:
            _deepeval_record_pilot(cli, directory, source, report)


def _deepeval_trace_refusals(cli: Path, source: Path, work: Path) -> None:
    for options, code in (
        (["--version", "4.1.1"], "USAGE_ERROR"),
        (["--version", "4.2.8", "--bindings", "missing"], "USAGE_ERROR"),
        (["--bindings", "missing"], "UNSUPPORTED_FORMAT"),
    ):
        refused = wheel.command(
            [str(cli), "audit-deepeval", "missing", "--view", "trace-index", *options, "--json"],
            work,
        )
        wheel.require(
            refused.returncode == EVIDENCE_ERROR_EXIT
            and refused.stdout == ""
            and json.loads(refused.stderr)["code"] == code,
            "trace selection must refuse unsupported combinations before file acquisition",
        )
    missing = wheel.command(
        [str(cli), "audit-deepeval", "missing", "--view", "trace-index", "--json"], work
    )
    wheel.require(
        missing.returncode == EVIDENCE_ERROR_EXIT
        and missing.stdout == ""
        and json.loads(missing.stderr)["code"] == "SOURCE_READ_FAILED",
        "automatic trace intake did not preserve missing-source acquisition failure",
    )
    ordinary = wheel.command([str(cli), "audit-deepeval", str(source), "--json"], work)
    wheel.require(
        ordinary.returncode == EVIDENCE_ERROR_EXIT
        and ordinary.stdout == ""
        and json.loads(ordinary.stderr)["reason"] == "DEEPEVAL_SCOPE_UNSUPPORTED",
        "trace view changed ordinary DeepEval scope",
    )


def _deepeval_trace_pilot(cli: Path, work: Path) -> None:
    for scenario in ("success", "recovery"):
        source = work / f"deepeval-trace-{scenario}.json"
        raw = (
            wheel.ROOT / f"fixtures/golden/external/deepeval-4.2.8/traces/{scenario}.json"
        ).read_bytes()
        source.write_bytes(raw)
        report = _cli_json(
            cli,
            ["audit-deepeval", str(source), "--view", "trace-index"],
            work / f"deepeval-trace-{scenario}-index.json",
            work,
        )
        wheel.require(
            report["schema"] == "deepeval_trace_index.v2"
            and report["raw_hash"] == "sha256:" + hashlib.sha256(raw).hexdigest(),
            "trace index lost original byte identity or schema",
        )
        case = cast(list[dict[str, Any]], report["cases"])[0]
        wheel.require(
            case["source_success"] is True
            and case["trace_status"] == "SUCCESS"
            and any(row["source_status"] == "ERRORED" for row in case["spans"])
            == (scenario == "recovery"),
            "trace index collapsed case success and step errors",
        )
        wheel.require(
            all(
                row["parent_state"] == "resolved"
                for row in case["spans"]
                if row["group"] == "toolSpans"
            ),
            "native tool parents did not resolve within their trace",
        )
        native_trace = json.loads(raw)["testCases"][0]["trace"]
        encoded = json.dumps(report)
        wheel.require(
            all(native_trace[key] not in encoded for key in ("uuid", "name", "input")),
            "content-minimized trace index leaked private source fields",
        )
        python = cli.with_name("python")
        check = wheel.success(
            [
                str(python),
                "-c",
                "import json,sys; from pathlib import Path; "
                "from underwrite.instrument.ingest.deepeval_trace "
                "import audit_deepeval_trace_bytes; "
                "print(json.dumps(audit_deepeval_trace_bytes(Path(sys.argv[1]).read_bytes())))",
                str(source),
            ],
            work,
        )
        wheel.require(json.loads(check.stdout) == report, "installed trace API and CLI disagree")
    _deepeval_trace_refusals(cli, source, work)


def _deepeval_auto_pilot(cli: Path, work: Path) -> None:
    retired_assets = (
        "deepeval_accounting.v1",
        "deepeval_accounting.v2",
        "deepeval_record.v1",
        "deepeval_record.v2",
        "deepeval_trace_index.v1",
        "deepeval_trace_record.v1",
        "deepeval_metric_bindings.v1",
        "deepeval_bound_record.v1",
    )
    retired_filenames = [name + ".schema.json" for name in retired_assets]
    check_assets = (
        "import json; from importlib.resources import files; "
        "names={item.name for item in files('underwrite').joinpath('_contracts').iterdir()}; "
        f"print(json.dumps(sorted(names.intersection({retired_filenames!r}))))"
    )
    resources = wheel.success([str(cli.with_name("python")), "-c", check_assets], work)
    wheel.require(json.loads(resources.stdout) == [], "retired DeepEval contracts remain installed")
    golden = wheel.ROOT / "fixtures/golden/external"
    native = wheel.ROOT / "fixtures/examples/deepeval-auto-4.3.1"
    scenarios = (
        (golden / "deepeval/test_run_20260914_132634.json", "accounting"),
        (golden / "deepeval-4.2.8/ordinary.json", "accounting"),
        (native / "ordinary.json", "accounting"),
        (golden / "deepeval-4.2.8/traces/recovery.json", "trace-index"),
        (native / "recovery.json", "trace-index"),
    )
    for index, (source, view) in enumerate(scenarios):
        selected = work / f"deepeval-auto-{index}"
        selected.mkdir()
        report = _cli_json(
            cli, ["audit-deepeval", str(source), "--view", view], selected / "report.json", selected
        )
        schema = "deepeval_accounting.v3" if view == "accounting" else "deepeval_trace_index.v2"
        identity = cast(dict[str, object], report["source"])
        wheel.require(
            report["schema"] == schema
            and identity["producer_version"] is None
            and identity["producer_version_state"] == "unknown"
            and identity["claims_verified"] is False
            and report["raw_hash"] == "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest(),
            "automatic DeepEval intake changed bytes or invented producer identity",
        )
        module = "deepeval_accounting" if view == "accounting" else "deepeval_trace"
        function = "audit_deepeval_bytes" if view == "accounting" else "audit_deepeval_trace_bytes"
        code = (
            "import json,sys; from pathlib import Path; "
            f"from underwrite.instrument.ingest.{module} import {function}; "
            f"print(json.dumps({function}(Path(sys.argv[1]).read_bytes()),sort_keys=True))"
        )
        api = wheel.success([str(cli.parent / "python"), "-c", code, str(source)], selected)
        wheel.require(
            json.loads(api.stdout) == report, "automatic DeepEval API/CLI outcomes differ"
        )
        _deepeval_record_pilot(cli, selected, source, report, view=view)
    invalid = json.loads((native / "ordinary.json").read_bytes())
    invalid["testCases"][0]["metricsData"][0]["expectationsData"] = {
        "private": "EXPECTATION_CANARY"
    }
    path = work / "deepeval-unsupported-expectation.json"
    path.write_text(json.dumps(invalid), encoding="utf-8")
    refused = wheel.command([str(cli), "audit-deepeval", str(path), "--json"], work)
    error = json.loads(refused.stderr)
    wheel.require(
        refused.returncode == EVIDENCE_ERROR_EXIT
        and refused.stdout == ""
        and error["code"] == "UNSUPPORTED_PROFILE"
        and error["location"] == "/testCases/0/metricsData/0/expectationsData"
        and "EXPECTATION_CANARY" not in refused.stderr,
        "automatic DeepEval intake silently counted an unsupported expectation",
    )


def _langfuse_record_pilot(  # noqa: PLR0913 — keep both source roles and view explicit
    cli: Path,
    work: Path,
    observed: Path,
    scores: Path,
    expected: dict[str, object],
    *,
    view: str = "accounting",
) -> None:
    record_schema = "langfuse_row_record.v2" if view == "row-index" else "langfuse_record.v2"
    slug = "langfuse-current"
    if view != "accounting":
        slug += "-" + view
    input_directory = work / f"{slug}-record-inputs"
    input_directory.mkdir()
    inputs = (input_directory / "observations.json", input_directory / "scores.json")
    for source, copied in zip((observed, scores), inputs, strict=True):
        copied.write_bytes(source.read_bytes())
    store = work / f"{slug}-records"
    arguments = [
        "records",
        "audit-langfuse",
        "--observations",
        str(inputs[0]),
        "--scores",
        str(inputs[1]),
        "--root",
        str(store),
    ]
    arguments.extend(("--view", view))
    report = _cli_json(cli, [*arguments, "--id", "captured"], work / f"{slug}-record.json", work)
    analysis = cast(dict[str, object], report["analysis"])
    storage = cast(dict[str, object], report["storage"])
    wheel.require(report["schema"] == record_schema, "Langfuse record selected the wrong contract")
    wheel.require(analysis["report"] == expected, "Langfuse capture changed analysis")
    wheel.require(storage["state"] == "saved", "Langfuse capture failed to save")
    expected_hashes: dict[str, str] = {}
    for source, role in zip(inputs, ("observations", "scores"), strict=True):
        expected_hashes[role] = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
        wheel.require(
            (store / f"records/captured/files/source/{role}.bin").read_bytes()
            == source.read_bytes(),
            "Langfuse retained source differs",
        )
        source.unlink()
    verified = _cli_json(
        cli,
        ["records", "verify", "--root", str(store), "--id", "captured"],
        work / f"{slug}-record-verified.json",
        work,
    )
    wheel.require(verified["valid"] is True, "Langfuse retained record did not verify")
    recovered = _cli_json(
        cli,
        [
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "captured",
            "--name",
            f"analysis/{view}.json",
        ],
        work / f"{slug}-recovered.json",
        work,
    )
    wheel.require(recovered["content"] == expected, "Langfuse stored analysis could not be reused")
    wheel.require(
        recovered["manifest_digest"] == storage["manifest_digest"],
        "Langfuse recovered manifest differs",
    )
    source_code = (
        "import json,sys; from pathlib import Path; "
        "from underwrite.service.records import read_record_file; "
        "from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES; "
        "print(json.dumps({role:read_record_file(Path(sys.argv[1]),'captured',"
        "f'source/{role}.bin',max_bytes=DEFAULT_MAX_BYTES).sha256 "
        "for role in ('observations','scores')}))"
    )
    source_read = wheel.success([str(cli.parent / "python"), "-c", source_code, str(store)], work)
    wheel.require(
        json.loads(source_read.stdout) == expected_hashes,
        "Langfuse verified source recovery changed original bytes",
    )
    failed = wheel.command([str(cli), *arguments, "--id", "unavailable", "--json"], work)
    wheel.require(
        failed.returncode == EVIDENCE_ERROR_EXIT and failed.stderr == "",
        "Langfuse diagnostic capture used the wrong channel or exit",
    )
    diagnostic = json.loads(failed.stdout)
    wheel.require(
        diagnostic["analysis"]["phase"] == "acquisition"
        and diagnostic["storage"]["state"] == "saved",
        "Langfuse acquisition failure lost independent storage",
    )
    for source in diagnostic["sources"].values():
        wheel.require(
            source["state"] == "unavailable" and "error" in source,
            "Langfuse input failure was not retained",
        )


def _langfuse_pilot(cli: Path, work: Path) -> None:
    golden = wheel.ROOT / "fixtures/golden/external/langfuse"
    observed = golden / "observations_v2-2026-09-14T06-40-00.json"
    scores = golden / "scores-2026-09-14T06-40-00.json"
    report = _cli_json(
        cli,
        [
            "audit-langfuse",
            "--observations",
            str(observed),
            "--scores",
            str(scores),
        ],
        work / "langfuse-accounting.json",
        work,
    )
    counts = cast(dict[str, object], report["counts"])
    wheel.require(
        counts["by_data_type"] == {"NUMERIC": 2, "BOOLEAN": 1, "CATEGORICAL": 1, "TEXT": 1},
        "installed Langfuse score kinds changed",
    )
    wheel.require(
        counts["associations"]
        == {
            "observation_local": 3,
            "trace_level": 2,
            "unresolved_observation_reference": 0,
        },
        "installed Langfuse associations changed",
    )
    wheel.require(
        counts["observations_without_local_scores"] == 1,
        "trace-level scores were incorrectly attached to an observation",
    )
    wheel.require(
        report["planned"]
        == {
            "status": "unknown",
            "count": None,
            "missing_execution_count": None,
        },
        "installed accounting fabricated planned population",
    )
    rows = cast(list[dict[str, object]], report["scores"])
    for row in rows:
        if row["data_type"] in ("TEXT", "CATEGORICAL"):
            wheel.require(
                row["value"] == 0 and isinstance(row["string_value"], str),
                "non-numeric score fields were not retained",
            )


def _native_refusal(cli: Path, args: list[str], work: Path, code: str, reason: str) -> None:
    result = wheel.command([str(cli), *args, "--json"], work)
    wheel.require(
        result.returncode == EVIDENCE_ERROR_EXIT and result.stdout == "",
        "native refusal wrote success",
    )
    try:
        error: object = json.loads(result.stderr)
    except json.JSONDecodeError as exc:
        raise wheel.SmokeFailure("native refusal did not write JSON stderr") from exc
    wheel.require(isinstance(error, dict), "native refusal was not an object")
    payload = cast(dict[str, object], error)
    wheel.require(
        payload.get("code") == code and payload.get("reason") == reason, "wrong native refusal"
    )


def _langfuse_cli_pilot(cli: Path, work: Path) -> None:
    fixtures = wheel.ROOT / "fixtures/examples/langfuse-cli"
    selected: dict[str, Path] = {}
    raw: dict[str, bytes] = {}
    for role in ("observations", "scores"):
        raw[role] = (fixtures / f"{role}.json").read_bytes()
        selected[role] = work / f"langfuse-cli-{role}.json"
        selected[role].write_bytes(raw[role])
    args = [
        "audit-langfuse",
        "--observations",
        str(selected["observations"]),
        "--scores",
        str(selected["scores"]),
    ]
    report = _cli_json(cli, args, work / "langfuse-cli-accounting.json", work)
    wheel.require(report["schema"] == "langfuse_accounting.v2", "wrong CLI capture schema")
    sources = cast(dict[str, dict[str, object]], report["sources"])
    for role, data in raw.items():
        wheel.require(
            sources[role]["raw_hash"] == "sha256:" + hashlib.sha256(data).hexdigest(),
            "CLI capture source bytes changed",
        )
    native_scores = json.loads(raw["scores"])["body"]["data"]
    scores = cast(list[dict[str, object]], report["scores"])
    wheel.require(len(scores) == len(native_scores), "CLI capture lost supplied score rows")
    for score, native in zip(scores, native_scores, strict=True):
        wheel.require(
            score["data_type"] == native["dataType"]
            and type(score["value"]) is type(native["value"])
            and score["value"] == native["value"],
            "CLI capture changed native score types or values",
        )
    index = _cli_json(cli, [*args, "--view", "row-index"], work / "langfuse-cli-index.json", work)
    wheel.require(
        index["schema"] == "langfuse_row_index.v2" and index["counts"] == report["counts"],
        "CLI row index changed accounting",
    )
    for row in cast(list[dict[str, object]], index["score_rows"]):
        wheel.require(
            not {"id", "name", "value", "project_id", "subject"}.intersection(row),
            "CLI row index exposed score content",
        )
    api_code = (
        "import json,sys; from pathlib import Path; "
        "from underwrite.instrument.ingest.langfuse_accounting import audit_langfuse_bytes; "
        "print(json.dumps(audit_langfuse_bytes("
        "Path(sys.argv[1]).read_bytes(),Path(sys.argv[2]).read_bytes()),sort_keys=True))"
    )
    api = wheel.success(
        [str(cli.parent / "python"), "-c", api_code, *map(str, selected.values())], work
    )
    wheel.require(json.loads(api.stdout) == report, "installed CLI/API capture outcomes differ")
    partial = _cli_json(
        cli,
        [
            "audit-langfuse",
            "--observations",
            str(fixtures / "observations-body.json"),
            "--scores",
            str(fixtures / "scores-truncated.json"),
        ],
        work / "langfuse-cli-partial.json",
        work,
    )
    retrieval = cast(dict[str, dict[str, object]], partial["retrieval"])
    pagination = cast(dict[str, object], retrieval["scores"]["pagination"])
    planned = cast(dict[str, object], partial["planned"])
    wheel.require(
        pagination["truncated"] is True
        and planned["status"] == "unknown"
        and planned["missing_execution_count"] is None,
        "CLI partial capture became complete planned evidence",
    )
    invalid = json.loads(raw["scores"])
    invalid["status"] = 403
    selected["scores"].write_text(json.dumps(invalid), encoding="utf-8")
    _native_refusal(cli, args, work, "INVALID_INPUT", "LANGFUSE_CLI_NON_SUCCESS_STATUS")


def _langfuse_auto_pilot(cli: Path, work: Path) -> None:
    wheel.success(
        [
            str(cli.parent / "python"),
            "-I",
            "-c",
            "from importlib.resources import files; r=files('underwrite').joinpath('_contracts'); "
            "assert all(not r.joinpath(n+'.v1.schema.json').is_file() for n in "
            "('langfuse_accounting','langfuse_row_index','langfuse_cli_accounting',"
            "'langfuse_cli_row_index','langfuse_record','langfuse_cli_record',"
            "'langfuse_row_record')); "
            "assert r.joinpath('langfuse_row_record.v2.schema.json').is_file()",
        ],
        work,
    )
    _native_refusal(
        cli,
        [
            "audit-langfuse",
            "--observations",
            "not-read.json",
            "--scores",
            "not-read.json",
            "--profile",
            "blob-4.35.0",
        ],
        work,
        "USAGE_ERROR",
        "INVALID_ARGUMENTS_SEE_HELP",
    )
    golden = wheel.ROOT / "fixtures/golden/external/langfuse"
    native = wheel.ROOT / "fixtures/examples/langfuse-cli"
    pairs = (
        (
            golden / "observations_v2-2026-09-14T06-40-00.json",
            golden / "scores-2026-09-14T06-40-00.json",
            "langfuse.blob-array.selected-fields.v1",
        ),
        (
            native / "observations.json",
            native / "scores.json",
            "langfuse.cli-list.selected-fields.v1",
        ),
        (
            native / "observations-body.json",
            native / "scores-body.json",
            "langfuse.cli-list.selected-fields.v1",
        ),
        (
            native / "observations-body.json",
            native / "scores-truncated.json",
            "langfuse.cli-list.selected-fields.v1",
        ),
    )
    for index, (observed, scores, reader) in enumerate(pairs):
        selected = work / f"langfuse-auto-{index}"
        selected.mkdir()
        args = ["audit-langfuse", "--observations", str(observed), "--scores", str(scores)]
        report = _cli_json(cli, args, selected / "accounting.json", selected)
        wheel.require(
            report["schema"] == "langfuse_accounting.v2" and report["reader_profile"] == reader,
            "automatic Langfuse intake selected the wrong reader",
        )
        for role, source in zip(("observations", "scores"), (observed, scores), strict=True):
            identity = cast(dict[str, dict[str, object]], report["sources"])[role]
            wheel.require(
                identity["raw_hash"] == "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
                and identity["producer_version"] is None
                and identity["producer_version_state"] == "unknown"
                and identity["claims_verified"] is False,
                "automatic Langfuse intake changed bytes or invented producer identity",
            )
        rows = _cli_json(cli, [*args, "--view", "row-index"], selected / "rows.json", selected)
        wheel.require(
            rows["schema"] == "langfuse_row_index.v2"
            and rows["reader_profile"] == reader
            and rows["counts"] == report["counts"],
            "automatic Langfuse row projection changed accounting",
        )
        api_code = (
            "import json,sys; from pathlib import Path; "
            "from underwrite.instrument.ingest.langfuse_accounting import audit_langfuse_bytes; "
            "print(json.dumps(audit_langfuse_bytes("
            "Path(sys.argv[1]).read_bytes(),Path(sys.argv[2]).read_bytes()),sort_keys=True))"
        )
        api = wheel.success(
            [str(cli.parent / "python"), "-c", api_code, str(observed), str(scores)], selected
        )
        wheel.require(
            json.loads(api.stdout) == report, "automatic Langfuse API/CLI outcomes differ"
        )
        _langfuse_record_pilot(
            cli,
            selected,
            observed,
            scores,
            report,
        )
        _langfuse_record_pilot(
            cli,
            selected,
            observed,
            scores,
            rows,
            view="row-index",
        )
    _native_refusal(
        cli,
        ["audit-langfuse", "--observations", str(pairs[0][0]), "--scores", str(pairs[1][1])],
        work,
        "UNSUPPORTED_PROFILE",
        "LANGFUSE_AUTO_MIXED_FAMILIES",
    )


def _native_measurement_pilot(cli: Path, fixtures: Path, work: Path) -> None:
    retired = (
        "read.v1", "measurement_policy.v1", "measurement_policy.v2",
        "declared_claims.v1", "declared_claims.v2",
        "acceptance_decision.v1", "acceptance_decision.v2", "statistical_seed.v1",
    )
    assets = wheel.success(
        [
            str(cli.with_name("python")), "-I", "-c",
            "import json,sys; from importlib.resources import files; "
            "from importlib.util import find_spec; "
            "root=files('underwrite').joinpath('_contracts'); "
            "print(json.dumps({'schemas': [name for name in json.loads(sys.argv[1]) "
            "if root.joinpath(name+'.schema.json').is_file()], "
            "'modules': [name for name in ('read','availability','jury','stratify') "
            "if find_spec('underwrite.measurement.'+name) is not None]}))",
            json.dumps(retired),
        ],
        work,
    )
    wheel.require(
        json.loads(assets.stdout) == {"schemas": [], "modules": []},
        "installed package retained the obsolete native measurement engine",
    )
    source = fixtures / "event-measurement"
    reads: list[Path] = []
    for variant, verdict in (("", "pass"), ("failure-", "fail")):
        observation = work / f"{variant}observation.json"
        observed = _cli_json(
            cli,
            [
                "ingest",
                "--format",
                "underwrite.evidence-bundle",
                "--version",
                "v1",
                "--file",
                str(source / f"{variant}bundle.json"),
            ],
            observation,
            work,
        )
        wheel.require(observed.get("schema") == "observation.v1", "ingest schema mismatch")
        wheel.require(
            observed.get("source")
            == {
                "format": "underwrite.evidence-bundle",
                "format_version": "v1",
            },
            "ingest source mismatch",
        )
        output = work / f"{variant}read.json"
        read = _cli_json(
            cli,
            [
                "measure",
                "--observation",
                str(observation),
                "--policy",
                str(fixtures / "event-acceptance/policy.json"),
            ],
            output,
            work,
        )
        wheel.require(read.get("schema") == "read.v2", "event read schema mismatch")
        wheel.require(read.get("verdict") == verdict, "event read direction mismatch")
        wheel.require(
            "score" not in read and "confidence" not in read, "generic confidence returned"
        )
        reads.append(output)
    candidate = fixtures / "event-acceptance/candidate-single.json"
    claims = fixtures / "event-acceptance/claims.json"
    for index, classification in enumerate(("screened", "rejected")):
        decision = _cli_json(
            cli,
            [
                "accept",
                "--candidate",
                str(candidate),
                "--claims",
                str(claims),
                "--read",
                str(reads[index]),
            ],
            work / f"accept-{classification}.json",
            work,
        )
        wheel.require(
            decision.get("schema") == "acceptance_decision.v3", "decision schema mismatch"
        )
        wheel.require(decision.get("classification") == classification, "classification mismatch")
        wheel.require(decision.get("human_review") == "required", "human review mismatch")
        wheel.require(decision.get("merge_authorized") is False, "merge authorization mismatch")
    _native_subject_pilot(cli, fixtures, work)
    _native_refusal(
        cli,
        [
            "measure",
            "--observation",
            str(work / "observation.json"),
            "--policy",
            str(fixtures / "measurement/policy.json"),
        ],
        work,
        "INVALID_POLICY",
        "LEGACY_POLICY_REQUIRES_MIGRATION",
    )
    _native_refusal(
        cli,
        [
            "accept",
            "--candidate",
            str(candidate),
            "--claims",
            str(fixtures / "acceptance/claims.json"),
            "--read",
            str(reads[0]),
        ],
        work,
        "INVALID_CLAIMS",
        "LEGACY_CLAIMS_REQUIRES_MIGRATION",
    )
    _native_refusal(
        cli,
        [
            "accept",
            "--candidate",
            str(candidate),
            "--claims",
            str(claims),
            "--read",
            str(fixtures / "acceptance/read.json"),
        ],
        work,
        "INVALID_READ",
        "LEGACY_READ_REQUIRES_MIGRATION",
    )
    substituted = json.loads(reads[0].read_bytes())
    substituted["semantics"]["construct"]["definition"] = "A substituted meaning"
    altered = work / "substituted-read.json"
    altered.write_text(json.dumps(substituted), encoding="utf-8")
    _native_refusal(
        cli,
        ["accept", "--candidate", str(candidate), "--claims", str(claims), "--read", str(altered)],
        work,
        "INVALID_READ",
        "SEMANTIC_DIGEST_MISMATCH",
    )


def _baseline_evidence_pilot(cli: Path, exchange: Path, work: Path) -> None:
    """Check the installed descriptive CLI and directory API on one exchange."""
    evidence = _cli_json(
        cli,
        ["audit-baseline", "--exchange", str(exchange)],
        work / "baseline-evidence.json",
        work,
    )
    wheel.require(evidence.get("schema") == "evidence_result.v2", "baseline evidence schema")
    wheel.require(evidence.get("verification") == [], "baseline invented portable verification")
    evidence_source = cast(dict[str, object], evidence["source"])
    wheel.require(
        evidence_source["format"] == "goppi.baseline_case_exchange"
        and evidence_source["producer_version"] is None
        and evidence_source["producer_version_state"] == "unknown"
        and evidence_source["reader_profile"] == "goppi.baseline-exchange-v2.selected-fields.v1"
        and evidence["declaration_source"] is None,
        "baseline invented a producer version or separate owner declaration",
    )
    identity = cast(dict[str, object], evidence_source["identity"])
    members = cast(dict[str, str], identity["members"])
    wheel.require(
        identity["manifest_raw_hash"]
        == "sha256:" + hashlib.sha256((exchange / "manifest.json").read_bytes()).hexdigest()
        and members
        == {
            name: "sha256:" + hashlib.sha256((exchange / name).read_bytes()).hexdigest()
            for name in ("manifest.json", "export.json", "rows.jsonl", "schema.json", "scope.json")
        },
        "baseline evidence did not bind each raw source file",
    )
    evidence_population = cast(dict[str, object], evidence["population"])
    wheel.require(
        evidence_population == {"unit": "case", "observed_count": 4, "planned_count": 4},
        "baseline evidence source-record population changed",
    )
    evidence_items = cast(list[dict[str, object]], evidence["items"])
    wheel.require(
        [(item["availability"], item["value"]) for item in evidence_items]
        == [("observed", True), ("observed", False), ("observed", False), ("missing", None)],
        "baseline evidence source states changed",
    )
    api_code = (
        "import json,sys; from pathlib import Path; "
        "from underwrite.instrument.ingest.goppi_baseline import audit_baseline_exchange; "
        "print(json.dumps(audit_baseline_exchange(Path(sys.argv[1])),sort_keys=True))"
    )
    api = wheel.success([str(cli.parent / "python"), "-c", api_code, str(exchange)], work)
    wheel.require(
        json.loads(api.stdout) == evidence, "installed baseline Python API differs from CLI"
    )
    for view, schema in (
        ("summary", "common_result_inspection.v3"),
        ("actions", "common_result_inspection_actions.v3"),
    ):
        inspected = _cli_json(
            cli,
            ["inspect-result", str(work / "baseline-evidence.json"), "--view", view],
            work / f"baseline-inspection-{view}.json",
            work,
        )
        wheel.require(
            inspected["schema"] == schema
            and inspected["selected_result"] == {
                "format": "evidence_result.v2",
                "raw_hash": "sha256:" + hashlib.sha256(
                    (work / "baseline-evidence.json").read_bytes()
                ).hexdigest(),
            },
            "baseline inspection changed the original result format or bytes",
        )
    wheel.require(
        "SYNTHETIC_ANSWER_NOT_FOR_PROJECTION" not in json.dumps(evidence)
        and "indeterminate:agent_budget" not in json.dumps(evidence),
        "baseline descriptive result leaked private source content",
    )


def _native_subject_pilot(cli: Path, fixtures: Path, work: Path) -> None:
    subject = "정확도 / quality\nFORGED_STATUS=PASS\x1b[31m"
    wheel.success(
        [
            str(cli.with_name("python")), "-I", "-c",
            """
import json, sys
from pathlib import Path
from underwrite.acceptance.claims import parse_claims
from underwrite_core.canonical import canonical_bytes, content_digest
source, work = map(Path, sys.argv[1:3])
subject = sys.argv[3]
policy = json.loads((source / 'policy.json').read_bytes())
previous = policy['subject']['subject_id']
policy['subject']['subject_id'] = subject
policy_digest = content_digest(policy)
claims = json.loads((source / 'claims.json').read_bytes())
for basis in claims['required_claims'].values():
    for claim in basis:
        if claim['subject_id'] == previous:
            claim.update(subject_id=subject, policy_digest=policy_digest)
candidate = json.loads((source / 'candidate-single.json').read_bytes())
candidate['claims_digest'] = parse_claims(claims).digest
for name, value in [('policy', policy), ('claims', claims), ('candidate', candidate)]:
    (work / ('subject-' + name + '.json')).write_bytes(canonical_bytes(value))
""",
            str(fixtures / "event-acceptance"), str(work), subject,
        ],
        work,
    )
    read = work / "subject-read.json"
    _cli_json(
        cli,
        ["measure", "--observation", str(work / "observation.json"),
         "--policy", str(work / "subject-policy.json")],
        read,
        work,
    )
    arguments = [
        "accept", "--candidate", str(work / "subject-candidate.json"),
        "--claims", str(work / "subject-claims.json"), "--read", str(read),
    ]
    decision = _cli_json(cli, arguments, work / "subject-decision.json", work)
    claims = cast(list[dict[str, object]], decision["claims"])
    wheel.require(
        decision["schema"] == "acceptance_decision.v3"
        and decision["classification"] == "screened"
        and claims[0]["subject_id"] == subject
        and decision["merge_authorized"] is False,
        "installed acceptance failed to bind the exact measured subject",
    )
    human = wheel.success([str(cli), *arguments], work)
    wheel.require(
        not human.stderr and json.dumps(subject, ensure_ascii=True) in human.stdout
        and "\x1b" not in human.stdout and "\nFORGED_STATUS" not in human.stdout,
        "human acceptance output did not escape the measured subject",
    )


def _common_result_inspection_pilot(cli: Path, fixtures: Path, work: Path) -> None:
    wheel.success(
        [
            str(cli.parent / "python"),
            "-I",
            "-c",
            "from importlib.resources import files; r=files('underwrite').joinpath('_contracts'); "
            "assert all(not r.joinpath(n+'.schema.json').is_file() for n in "
            "('common_result_inspection.v1','common_result_inspection.v2',"
            "'common_result_inspection_actions.v1','common_result_inspection_actions.v2',"
            "'baseline_record.v1')); "
            "assert r.joinpath('evidence_result.v1.schema.json').is_file()",
        ],
        work,
    )
    selected = fixtures / "evidence/common-result-c55.json"
    inspection = _cli_json(
        cli,
        ["inspect-result", str(selected)],
        work / "common-result-inspection.json",
        work,
    )
    wheel.require(
        inspection.get("schema") == "common_result_inspection.v3", "result inspection schema"
    )
    wheel.require(
        inspection.get("selected_result") == {
            "format": "evidence_result.v1",
            "raw_hash": "sha256:" + hashlib.sha256(selected.read_bytes()).hexdigest(),
        },
        "result inspection raw-byte identity",
    )
    wheel.require(
        inspection.get("availability_counts")
        == {"observed": 2, "missing": 2, "error": 1, "abstained": 2, "total": 7},
        "result inspection availability",
    )
    api_code = (
        "import json,sys; from pathlib import Path; "
        "from underwrite.cli.inspect_result import inspect_result_file; "
        "print(json.dumps(inspect_result_file(Path(sys.argv[1])),sort_keys=True))"
    )
    api = wheel.success([str(cli.parent / "python"), "-c", api_code, str(selected)], work)
    wheel.require(json.loads(api.stdout) == inspection, "installed result inspection API differs")
    actions = _cli_json(
        cli,
        ["inspect-result", str(selected), "--view", "actions"],
        work / "common-result-inspection-actions.json",
        work,
    )
    owner = json.loads(selected.read_bytes())
    contract = json.loads((wheel.ROOT / "contracts/evidence_result.v1.schema.json").read_bytes())
    action_vocab = contract["$defs"]["item"]["properties"]["next_action"]["enum"]
    binding_vocab = [
        branch["properties"]["semantic_binding"]["const"]
        for branch in contract["$defs"]["metric_binding"]["oneOf"]
    ]
    action_control = Counter(item["next_action"] for item in owner["items"])
    binding_control = Counter(item["metric"]["semantic_binding"] for item in owner["items"])
    wheel.require(
        actions.get("schema") == "common_result_inspection_actions.v3"
        and actions.get("item_next_action_counts")
        == {name: action_control[name] for name in action_vocab}
        and actions.get("declared_semantic_binding_counts")
        == {name: binding_control[name] for name in binding_vocab}
        and actions.get("availability_counts") == inspection.get("availability_counts")
        and actions.get("selected_result") == inspection.get("selected_result"),
        "installed action inspection differs from selected bytes",
    )
    action_api_code = (
        "import json,sys; from pathlib import Path; "
        "from underwrite.cli.inspect_result import inspect_result_file; "
        "print(json.dumps(inspect_result_file(Path(sys.argv[1]),view='actions'),sort_keys=True))"
    )
    action_api = wheel.success(
        [str(cli.parent / "python"), "-c", action_api_code, str(selected)], work
    )
    wheel.require(
        json.loads(action_api.stdout) == actions, "installed action inspection API differs"
    )
    capabilities = _cli_json(cli, ["capabilities"], work / "inspection-capabilities.json", work)
    descriptor = next(
        item for item in cast(list[dict[str, object]], capabilities["commands"])
        if item["command"] == "inspect-result"
    )
    selected_profile = next(
        item for item in cast(list[dict[str, object]], descriptor["profiles"])
        if item["name"] == "evidence_result.v1|actions"
    )
    wheel.require(
        selected_profile["required_inputs"] == ["file", "view"]
        and selected_profile["output_schemas"] == ["common_result_inspection_actions.v3"],
        "installed action inspection capability selector changed",
    )
    wheel.require(
        str(selected) not in json.dumps(inspection)
        and "goppi.operator_answer_assessment" not in json.dumps(inspection),
        "result inspection leaked input content",
    )
    wheel.require(
        str(selected) not in json.dumps(actions)
        and "goppi.operator_answer_assessment" not in json.dumps(actions),
        "action inspection leaked input content",
    )
    invalid = work / "private-invalid-result.json"
    invalid.write_bytes(b'{"schema":"private","schema":"private"}')
    refused = wheel.command([str(cli), "inspect-result", str(invalid), "--json"], work)
    wheel.require(
        refused.returncode == EVIDENCE_ERROR_EXIT
        and refused.stdout == ""
        and str(invalid) not in refused.stderr
        and json.loads(refused.stderr)["reason"] == "INVALID_JSON_INPUT",
        "installed result inspection refusal",
    )


def _baseline_event_pilot(cli: Path, fixtures: Path, work: Path) -> None:
    source = fixtures / "baseline-event"
    exchange = source / "exchange"
    _baseline_evidence_pilot(cli, exchange, work)
    observation_path = work / "baseline-observation.json"
    observation = _cli_json(
        cli,
        ["project-baseline", "--exchange", str(exchange), "--direction", "higher"],
        observation_path,
        work,
    )
    encoded = observation_path.read_text(encoding="utf-8")
    wheel.require(observation.get("schema") == "observation.v1", "baseline projection schema")
    wheel.require("SYNTHETIC_ANSWER_NOT_FOR_PROJECTION" not in encoded, "baseline answer leaked")
    wheel.require("indeterminate:agent_budget" not in encoded, "baseline private reason leaked")
    choices = json.loads((source / "policy.json").read_bytes())
    policy_options = [
        "--subject-id",
        choices["subject"]["subject_id"],
        "--p0",
        str(choices["eprocess"]["p0"]),
        "--ece-bins",
        str(choices["diagnostics"]["ece_bins"]),
        "--min-bin-n",
        str(choices["diagnostics"]["min_bin_n"]),
        "--all-k",
        str(choices["diagnostics"]["all_k"]),
    ]
    policy_path = work / "baseline-prepared-policy.json"
    policy = _cli_json(
        cli,
        ["prepare-event-policy", "--observation", str(observation_path), *policy_options],
        policy_path,
        work,
    )
    wheel.require(
        policy == choices, "prepared policy changed explicit choices or declared bindings"
    )
    missing_inventory = json.loads(observation_path.read_bytes())
    del missing_inventory["payload"]["sources"][0]["expected_case_ids"]
    missing_path = work / "baseline-without-inventory.json"
    missing_path.write_text(json.dumps(missing_inventory), encoding="utf-8")
    _native_refusal(
        cli,
        ["prepare-event-policy", "--observation", str(missing_path), *policy_options],
        work,
        "INVALID_INPUT",
        "BASELINE_EXPECTED_CASES_REQUIRED",
    )
    read = _cli_json(
        cli,
        [
            "measure",
            "--observation",
            str(observation_path),
            "--policy",
            str(policy_path),
        ],
        work / "baseline-read.json",
        work,
    )
    wheel.require(read.get("verdict") == "not_measured", "baseline inference was fabricated")
    population = cast(dict[str, object], read["population"])
    predictions = cast(dict[str, object], population["predictions"])
    wheel.require(
        predictions["status"] == "unavailable" and predictions["observed_count"] is None,
        "baseline predictions were fabricated",
    )
    diagnostics = cast(dict[str, dict[str, object]], read["diagnostics"])
    source_counts = json.loads((source / "exchange/export.json").read_bytes())["counts"]
    event = diagnostics["event_rate"]
    wheel.require(
        event["positive_count"] == source_counts["observed_true"]
        and event["n"] == source_counts["observed_true"] + source_counts["observed_false"],
        "baseline primary labels changed",
    )
    wheel.require(diagnostics["brier"]["value"] is None, "baseline forecast loss was fabricated")
    altered = work / "altered-baseline-exchange"
    altered.mkdir()
    for path in (source / "exchange").iterdir():
        (altered / path.name).write_bytes(path.read_bytes())
    rows = altered / "rows.jsonl"
    rows.write_bytes(rows.read_bytes().replace(b"observed_true", b"observed_false", 1))
    _native_refusal(
        cli,
        ["project-baseline", "--exchange", str(altered), "--direction", "higher"],
        work,
        "INVALID_INPUT",
        "BASELINE_FILE_IDENTITY_MISMATCH",
    )
    _native_refusal(
        cli,
        ["audit-baseline", "--exchange", str(altered)],
        work,
        "INVALID_INPUT",
        "BASELINE_FILE_IDENTITY_MISMATCH",
    )
    api_refusal_code = (
        "import json,sys\n"
        "from pathlib import Path\n"
        "from underwrite.instrument.ingest.goppi_baseline import audit_baseline_exchange\n"
        "from underwrite.instrument.ingest.local_capture import CaptureError\n"
        "from underwrite.instrument.evidence.goppi_baseline import BaselineProjectionError\n"
        "try:\n"
        "    audit_baseline_exchange(Path(sys.argv[1]))\n"
        "except (CaptureError, BaselineProjectionError) as exc:\n"
        "    print(json.dumps({'code': exc.code, 'reason': exc.reason, "
        "'location': exc.location}))\n"
        "else:\n"
        "    raise SystemExit(3)\n"
    )
    api_refusal = wheel.success(
        [str(cli.parent / "python"), "-c", api_refusal_code, str(altered)], work
    )
    api_error = json.loads(api_refusal.stdout)
    wheel.require(
        api_refusal.stderr == ""
        and api_error["code"] == "INVALID_INPUT"
        and api_error["reason"] == "BASELINE_FILE_IDENTITY_MISMATCH"
        and str(altered) not in api_refusal.stdout,
        "installed baseline Python API did not refuse tampered bytes finitely",
    )


def _baseline_record_pilot(cli: Path, fixtures: Path, work: Path) -> None:
    """Retain exact captured baseline bytes and read the result after source removal."""
    exchange = work / "record-baseline-exchange"
    exchange.mkdir()
    source = fixtures / "baseline-event/exchange"
    for path in source.iterdir():
        (exchange / path.name).write_bytes(path.read_bytes())
    record_root = work / "baseline-record-store"
    identifier = "baseline.smoke-001"
    report = _cli_json(
        cli,
        [
            "records", "audit-baseline", "--exchange", str(exchange),
            "--root", str(record_root), "--id", identifier,
        ],
        work / "baseline-record.json",
        work,
    )
    wheel.require(report.get("schema") == "baseline_record.v2", "baseline record schema")
    analysis = cast(dict[str, object], report["analysis"])
    storage = cast(dict[str, object], report["storage"])
    wheel.require(analysis["state"] == "result", "baseline record did not save a result")
    wheel.require(
        analysis["report"] == json.loads((work / "baseline-evidence.json").read_bytes()),
        "baseline record changed descriptive result",
    )
    verification = cast(dict[str, object], storage["verification"])
    wheel.require(
        storage["state"] == "saved"
        and verification["files_checked"] == BASELINE_RECORD_FILE_COUNT,
        "baseline record did not verify seven retained files",
    )
    encoded = json.dumps(report)
    wheel.require(
        str(exchange) not in encoded and "SYNTHETIC_ANSWER_NOT_FOR_PROJECTION" not in encoded,
        "baseline record aggregate leaked private source",
    )
    raw_rows_hash = hashlib.sha256((exchange / "rows.jsonl").read_bytes()).hexdigest()
    shutil.rmtree(exchange)
    verified = _cli_json(
        cli,
        ["records", "verify", "--root", str(record_root), "--id", identifier],
        work / "baseline-record-verification.json",
        work,
    )
    wheel.require(
        verified["valid"] is True
        and verified["files_checked"] == BASELINE_RECORD_FILE_COUNT,
        "baseline record verification failed after source removal",
    )
    selected = _cli_json(
        cli,
        [
            "records", "read-json", "--root", str(record_root), "--id", identifier,
            "--name", "analysis/evidence_result.json",
        ],
        work / "baseline-record-selected.json",
        work,
    )
    wheel.require(
        selected["content"] == analysis["report"], "baseline saved result cannot be read back"
    )
    raw_code = (
        "import hashlib,sys; from pathlib import Path; "
        "from underwrite.service.records import read_record_file; "
        "from underwrite.instrument.ingest.goppi_baseline import ROWS_MAX_BYTES; "
        "print(hashlib.sha256(read_record_file(Path(sys.argv[1]),sys.argv[2],"
        "'source/rows.jsonl',max_bytes=ROWS_MAX_BYTES).data).hexdigest())"
    )
    raw = wheel.success(
        [str(cli.parent / "python"), "-c", raw_code, str(record_root), identifier], work
    )
    wheel.require(raw.stdout.strip() == raw_rows_hash, "baseline raw rows changed in record")


def _measurement_record_pilot(cli: Path, work: Path) -> None:
    observation = work / "measurement-input.json"
    raw = (work / "baseline-observation.json").read_bytes() + b"\n"
    observation.write_bytes(raw)
    policy = work / "baseline-prepared-policy.json"
    store = work / "measurement-records"
    args = [
        "records",
        "measure",
        "--observation",
        str(observation),
        "--policy",
        str(policy),
        "--root",
        str(store),
        "--id",
        "measurement-001",
    ]
    result = _cli_json(cli, args, work / "measurement-record.json", work)
    analysis = cast(dict[str, object], result["analysis"])
    storage = cast(dict[str, object], result["storage"])
    expected = json.loads((work / "baseline-read.json").read_bytes())
    wheel.require(result.get("schema") == "measurement_record.v1", "measurement capture schema")
    wheel.require(analysis.get("report") == expected, "capture changed measurement")
    wheel.require(storage.get("state") == "saved", "measurement was not retained")
    source = _cli_json(
        cli,
        [
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "measurement-001",
            "--name",
            "source/observation.bin",
        ],
        work / "measurement-source.json",
        work,
    )
    wheel.require(
        source.get("sha256") == "sha256:" + hashlib.sha256(raw).hexdigest()
        and source.get("bytes") == len(raw),
        "capture did not preserve exact source bytes",
    )
    recovered = _cli_json(
        cli,
        [
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "measurement-001",
            "--name",
            "analysis/read.json",
        ],
        work / "measurement-recovered.json",
        work,
    )
    wheel.require(recovered.get("content") == expected, "stored read changed")
    conflict = wheel.command([str(cli), *args, "--json"], work)
    conflict_report = json.loads(conflict.stdout)
    wheel.require(
        conflict.returncode == STORAGE_ERROR_EXIT
        and conflict.stderr == ""
        and conflict_report["storage"]["error"]["code"] == "RECORD_EXISTS"
        and conflict_report["analysis"]["report"] == expected,
        "record conflict lost analysis or overwrote the record",
    )
    malformed = work / "malformed-measurement.json"
    malformed.write_bytes(b"{broken")
    failure = wheel.command(
        [
            str(cli),
            "records",
            "measure",
            "--observation",
            str(malformed),
            "--policy",
            str(work / "absent-policy.json"),
            "--root",
            str(store),
            "--id",
            "measurement-diagnostic",
            "--json",
        ],
        work,
    )
    report = json.loads(failure.stdout)
    wheel.require(
        failure.returncode == EVIDENCE_ERROR_EXIT
        and failure.stderr == ""
        and report["storage"]["state"] == "saved"
        and report["analysis"]["phase"] == "analysis"
        and report["analysis"]["error"]["code"] == "INVALID_OBSERVATION",
        "diagnostic capture changed measurement error precedence",
    )


def _measurement_replay_pilot(cli: Path, work: Path) -> None:
    store = work / "measurement-records"
    observation = work / "measurement-input.json"
    policy = work / "baseline-prepared-policy.json"
    source_bytes, policy_bytes = observation.read_bytes(), policy.read_bytes()
    observation.unlink()
    policy.unlink()
    replay = _cli_json(
        cli,
        ["records", "replay-measure", "--root", str(store), "--id", "measurement-001"],
        work / "measurement-replay.json",
        work,
    )
    wheel.require(replay.get("schema") == "measurement_replay.v1", "replay schema mismatch")
    wheel.require(
        replay["comparison"]
        == {"state": "match", "scope": "canonical_outcome_kind_and_full_document"},
        "saved measurement did not recalculate after originals disappeared",
    )
    _native_refusal(
        cli,
        ["records", "replay-measure", "--root", str(store), "--id", "measurement-diagnostic"],
        work,
        "INVALID_INPUT",
        "INCOMPLETE_MEASUREMENT_RECORD",
    )
    policy.write_bytes(policy_bytes)
    malformed = work / "malformed-measurement.json"
    diagnostic = wheel.command(
        [
            str(cli),
            "records",
            "measure",
            "--observation",
            str(malformed),
            "--policy",
            str(policy),
            "--root",
            str(store),
            "--id",
            "complete-diagnostic",
            "--json",
        ],
        work,
    )
    wheel.require(diagnostic.returncode == EVIDENCE_ERROR_EXIT, "diagnostic was not captured")
    error_replay = _cli_json(
        cli,
        ["records", "replay-measure", "--root", str(store), "--id", "complete-diagnostic"],
        work / "diagnostic-replay.json",
        work,
    )
    recomputed = cast(dict[str, object], error_replay["recomputed"])
    outcome = cast(dict[str, object], recomputed["outcome"])
    wheel.require(
        error_replay["comparison"] == replay["comparison"] and outcome["state"] == "error",
        "matching refusal became a successful measurement",
    )
    large = work / "large-measurement-input.json"
    large.write_bytes(source_bytes + b" " * (1024 * 1024))
    _cli_json(
        cli,
        [
            "records",
            "measure",
            "--observation",
            str(large),
            "--policy",
            str(policy),
            "--root",
            str(store),
            "--id",
            "large-measurement",
        ],
        work / "large-measurement-record.json",
        work,
    )
    large.unlink()
    policy.unlink()
    large_replay = _cli_json(
        cli,
        ["records", "replay-measure", "--root", str(store), "--id", "large-measurement"],
        work / "large-measurement-replay.json",
        work,
    )
    wheel.require(large_replay["comparison"] == replay["comparison"], "large input replay failed")
    refused = wheel.command(
        [
            str(cli),
            "records",
            "read-json",
            "--root",
            str(store),
            "--id",
            "large-measurement",
            "--name",
            "source/observation.bin",
            "--json",
        ],
        work,
    )
    failure = json.loads(refused.stderr)
    wheel.require(
        refused.returncode == EVIDENCE_ERROR_EXIT
        and refused.stdout == ""
        and failure["schema"] == "record_error.v1"
        and failure["code"] == "INPUT_LIMIT_EXCEEDED",
        "replay widened the JSON viewer limit",
    )


def _trajectory_pilot(cli: Path, work: Path) -> None:
    """Installed wiring with an explicit stub, not native Goppi validation evidence."""
    exchange = work / "trajectory-exchange"
    shutil.copytree(wheel.ROOT / "fixtures/conformance/goppi-trajectory/exchange", exchange)
    verifier = work / "offline-verifier-stub"
    verifier.write_text(
        f"#!{cli.parent / 'python'}\n"
        + (wheel.ROOT / "tests/drivers/goppi_trajectory_verifier_stub.py").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    verifier.chmod(0o700)
    args = ["audit-trajectory", "--exchange", str(exchange), "--goppi-executable", str(verifier)]
    report = _cli_json(cli, args, work / "trajectory-review.json", work)
    populations = cast(dict[str, object], report["populations"])
    manifest = json.loads((exchange / "paired/manifest.json").read_bytes())
    wheel.require(report["schema"] == "goppi_trajectory_review.v1", "trajectory output contract")
    wheel.require(populations["paired_rows"] == manifest["row_count"], "trajectory row population")
    tasks = cast(list[dict[str, object]], report["next_verification_tasks"])
    wheel.require(
        "inspect_tool_status_difference" in {task["kind"] for task in tasks},
        "trajectory tool difference missing",
    )
    code = (
        "import json,sys; from pathlib import Path; "
        "from underwrite.instrument.ingest.goppi_trajectory import audit_goppi_trajectory; "
        "from underwrite.instrument.ingest.goppi_verifier import GoppiCliVerifier; "
        "print(json.dumps(audit_goppi_trajectory(Path(sys.argv[1]),"
        "verifier=GoppiCliVerifier(Path(sys.argv[2])))))"
    )
    result = wheel.success(
        [str(cli.parent / "python"), "-c", code, str(exchange), str(verifier)], work
    )
    wheel.require(json.loads(result.stdout) == report, "trajectory installed API/CLI mismatch")
    _native_refusal(
        cli,
        ["audit-trajectory", "--exchange", str(exchange / "paired"),
         "--goppi-executable", str(verifier)],
        work,
        "INVALID_INPUT",
        "TRAJECTORY_COMPLETE_HANDOFF_REQUIRED",
    )
    _native_refusal(
        cli,
        ["audit-trajectory", "--exchange", str(exchange),
         "--goppi-executable", str(work / "missing-verifier")],
        work,
        "SOURCE_READ_FAILED",
        "GOPPI_VERIFIER_UNAVAILABLE",
    )
    _trajectory_record_pilot(cli, work, exchange, verifier, report)


def _trajectory_record_pilot(
    cli: Path, work: Path, exchange: Path, verifier: Path, expected: dict[str, object]
) -> None:
    """Exercise installed retention with the explicit process stub, then remove it."""
    store = work / "trajectory-records"
    identifier = "trajectory.smoke-001"
    captured = _cli_json(
        cli,
        ["records", "audit-trajectory", "--exchange", str(exchange),
         "--goppi-executable", str(verifier), "--root", str(store), "--id", identifier],
        work / "trajectory-record.json", work,
    )
    analysis = cast(dict[str, object], captured["analysis"])
    storage = cast(dict[str, object], captured["storage"])
    wheel.require(captured["schema"] == "trajectory_record.v1", "trajectory record contract")
    wheel.require(analysis["report"] == expected, "trajectory retention changed analysis")
    wheel.require(storage["state"] == "saved", "trajectory retention failed")
    hashes = {
        path.relative_to(exchange).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in exchange.rglob("*") if path.is_file()
    }
    shutil.rmtree(exchange)
    verifier.unlink()
    reopened = _cli_json(
        cli,
        ["records", "read-trajectory", "--root", str(store), "--id", identifier],
        work / "trajectory-record-read.json", work,
    )
    wheel.require(reopened["analysis"] == analysis, "trajectory reopen changed historical analysis")
    wheel.require(
        reopened["native_verification_performed_now"] is False,
        "trajectory reopen invented native verification",
    )
    verification = cast(dict[str, object], reopened["current_storage_verification"])
    wheel.require(verification["valid"] is True, "trajectory retained bytes did not verify")
    code = (
        "import hashlib,json,sys; from pathlib import Path; "
        "from underwrite.service.records import read_record_file; "
        "from underwrite.instrument.ingest.goppi_trajectory import trajectory_member_limits; "
        "print(json.dumps({name:hashlib.sha256(read_record_file(Path(sys.argv[1]),"
        "sys.argv[2],'source/'+name,max_bytes=limit).data).hexdigest() "
        "for name,limit in trajectory_member_limits().items()}))"
    )
    restored = wheel.success([str(cli.parent / "python"), "-c", code, str(store), identifier], work)
    wheel.require(
        json.loads(restored.stdout) == hashes, "trajectory raw source recovery changed bytes"
    )


def _smoke(dist: Path) -> None:
    core_wheel, app_wheel = wheel.wheels(dist)
    fixtures = wheel.ROOT / "fixtures/examples"
    with tempfile.TemporaryDirectory(prefix="underwrite-wheel-smoke-") as directory:
        work = Path(directory).resolve()
        wheel.require(
            not work.is_relative_to(wheel.ROOT), "temporary directory must be outside checkout"
        )
        venv = work / "venv"
        cli = venv / "bin/underwrite"
        wheels = (core_wheel, app_wheel)
        wheel.install_wheels(work, venv, wheels)
        wheel.check_wheel_origins(venv, wheels)
        wheel.stale_core_upgrade(work, wheels)
        _evidence_pilots(cli, fixtures, work)
        _record_pilots(cli, fixtures, work)
        _handoff_pilot(cli, work)
        _deepeval_pilot(cli, work)
        _deepeval_flaky_fields_pilot(cli, work)
        _deepeval_trace_pilot(cli, work)
        _deepeval_auto_pilot(cli, work)
        _langfuse_pilot(cli, work)
        _langfuse_cli_pilot(cli, work)
        _langfuse_auto_pilot(cli, work)
        _trajectory_pilot(cli, work)

        _native_measurement_pilot(cli, fixtures, work)
        _baseline_event_pilot(cli, fixtures, work)
        _common_result_inspection_pilot(cli, fixtures, work)
        _baseline_record_pilot(cli, fixtures, work)
        _measurement_record_pilot(cli, work)
        _measurement_replay_pilot(cli, work)

        malformed = work / "malformed-bundle.json"
        malformed.write_text(
            '{"schema_version":"underwrite.evidence-bundle.v1","run_id":"",'
            '"sources":[],"readings":[],"availability":[]}\n',
            encoding="utf-8",
        )
        error = wheel.command(
            [
                str(cli),
                "ingest",
                "--format",
                "underwrite.evidence-bundle",
                "--version",
                "v1",
                "--file",
                str(malformed),
                "--json",
            ],
            work,
        )
        wheel.require(error.returncode == 1, f"malformed ingest exit was {error.returncode}")
        wheel.require(error.stdout == "", "malformed ingest wrote stdout")
        try:
            diagnostic: object = json.loads(error.stderr)
        except json.JSONDecodeError as exc:
            raise wheel.SmokeFailure("malformed ingest did not write JSON stderr") from exc
        wheel.require(
            diagnostic
            == {
                "schema": "instrument_error.v1",
                "command": "ingest",
                "code": "INVALID_INPUT",
                "reason": "MALFORMED_EVIDENCE_BUNDLE_PAYLOAD",
                "exit_code": 1,
            },
            "malformed ingest diagnostic mismatch",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist",
        required=True,
        type=Path,
        help="Built app/core wheels; registry-core mode requires only the app wheel",
    )
    parser.add_argument(
        "--registry-core",
        action="store_true",
        help="Install only the candidate app and resolve core from public PyPI",
    )
    args = parser.parse_args(argv)
    try:
        if args.registry_core:
            _registry_core_smoke(args.dist.resolve())
        else:
            _smoke(args.dist.resolve())
    except (wheel.SmokeFailure, OSError, json.JSONDecodeError) as exc:
        print(f"wheel smoke failed: {exc}", file=sys.stderr)
        return 1
    print(
        "installed wheel native/evidence flows and malformed-input smoke passed"
        if not args.registry_core
        else "installed application with published core smoke passed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

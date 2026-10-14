"""Wire one command registration source to help, dispatch, and capability discovery."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from underwrite.cli import (
    accept,
    audit_baseline,
    audit_counts,
    audit_deepeval,
    audit_langfuse,
    audit_promptfoo,
    audit_run,
    boundary,
    capabilities,
    commands,
    compare_declarations,
    handoff_review,
    inspection,
    instrument,
    measure,
    pair_binary,
    prepare_event_policy,
    project_baseline,
    records,
    release,
    render,
    review_pair,
)
from underwrite.cli.capabilities import CommandDescriptor, Handler, profile
from underwrite.instrument.evidence.langfuse_accounting import RULE_PROFILE as LANGFUSE_RULE_PROFILE
from underwrite.instrument.ingest.accounting import SOURCE as PROMPTFOO_SOURCE
from underwrite.instrument.ingest.codecs.promptfoo import SUPPORTED_VERSIONS as PROMPTFOO_VERSIONS
from underwrite.instrument.ingest.deepeval_accounting import PROFILES as DEEPEVAL_PROFILES
from underwrite.instrument.ingest.deepeval_accounting import SOURCE as DEEPEVAL_SOURCE

Standalone = Callable[[list[str]], int]


def _standalone(handler: Standalone) -> Handler:
    def invoke(command: str, argv: list[str]) -> int:
        del command
        return handler(argv)

    return invoke


def _instrument(command: str, argv: list[str]) -> int:
    return instrument.main(command, argv)


def _configure_absence(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, required=True, help="Population directory.")
    parser.add_argument("--glob", required=True, help="Glob pattern relative to --root.")
    parser.add_argument("--min", type=int, required=True, help="Minimum population size.")
    parser.add_argument("--exemptions", type=Path, default=None, help="exemptions.v1 JSON path.")
    parser.add_argument("--exemption-key", default=None, help="Key into --exemptions.")
    parser.add_argument("--now", default=None, help="ISO8601 date vs. exemption expiry.")
    parser.add_argument("--json", action="store_true", help="Print cli_result.v1 JSON.")
    parser.set_defaults(handler=commands.cmd_absence)


def _configure_drift(parser: argparse.ArgumentParser) -> None:
    drift_sub = parser.add_subparsers(dest="drift_command", required=True)
    check = drift_sub.add_parser("check", help="Verify pins and fold in the population rule.")
    check.add_argument("--pins-root", type=Path, default=commands.DEFAULT_PINS_ROOT)
    check.add_argument("--siblings-root", type=Path, default=commands.default_siblings_root())
    check.add_argument("--exemptions", type=Path, default=None, help="exemptions.v1 JSON path.")
    check.add_argument("--now", default=None, help="ISO8601 date vs. exemption expiry.")
    check.add_argument("--json", action="store_true", help="Print cli_result.v1 JSON.")
    check.set_defaults(handler=commands.cmd_drift_check)


def _configure_pins(parser: argparse.ArgumentParser) -> None:
    pins_sub = parser.add_subparsers(dest="pins_command", required=True)
    refresh = pins_sub.add_parser("refresh", help="Re-pin a repo from its sibling checkout.")
    refresh.add_argument("--repo", required=True, help="Repo name under --pins-root/active/.")
    refresh.add_argument("--pins-root", type=Path, default=commands.DEFAULT_PINS_ROOT)
    refresh.add_argument("--siblings-root", type=Path, default=commands.default_siblings_root())
    refresh.set_defaults(handler=commands.cmd_pins_refresh)


def _configure_boundary(parser: argparse.ArgumentParser) -> None:
    boundary_sub = parser.add_subparsers(dest="boundary_command", required=True)
    scan = boundary_sub.add_parser(
        "scan", help="Scan JSON/JSONL files for adjudication-vocabulary leaks."
    )
    scan.add_argument("paths", type=Path, nargs="+", metavar="PATH")
    scan.add_argument("--allowlist", type=Path, default=None, help="boundary_allowlist.v1 JSON.")
    scan.add_argument("--now", default=None, help="ISO8601 date vs. allowlist expiry.")
    scan.add_argument("--json", action="store_true", help="Print cli_result.v1 JSON.")
    scan.set_defaults(handler=boundary.cmd_boundary_scan)


COMMAND_DESCRIPTORS: tuple[CommandDescriptor, ...] = (
    CommandDescriptor(
        "ingest",
        "Project an artifact (project is an explicit alias).",
        instrument.configure,
        (
            profile(
                "registered-codecs",
                ("format", "version", "file|http_body_file"),
                (),
                ("observation.v1",),
            ),
        ),
        _instrument,
        ("project",),
    ),
    CommandDescriptor(
        "audit-baseline",
        "Audit exact goppi baseline exchange v2 as descriptive common evidence.",
        audit_baseline.configure,
        (
            profile(
                "goppi-baseline-exchange-v2",
                ("exchange",),
                (),
                ("evidence_result.v1",),
            ),
        ),
        _standalone(audit_baseline.main),
    ),
    CommandDescriptor(
        "project-baseline",
        "Project exact goppi baseline exchange v2 source claims.",
        project_baseline.configure,
        (
            profile(
                "goppi-baseline-exchange-v2",
                ("exchange", "direction"),
                (),
                ("observation.v1",),
            ),
        ),
        _standalone(project_baseline.main),
    ),
    CommandDescriptor(
        "prepare-event-policy",
        "Prepare a draft event policy with explicit choices and unknown assumptions.",
        prepare_event_policy.configure,
        (
            profile(
                "goppi-baseline-exchange-v2",
                ("observation", "subject_id", "p0", "ece_bins", "min_bin_n", "all_k"),
                ("observation.v1",),
                ("measurement_policy.v3",),
            ),
        ),
        _standalone(prepare_event_policy.main),
    ),
    CommandDescriptor(
        "records",
        "Store and verify private result bundles.",
        records.configure,
        (
            profile("save", ("root", "spec"), ("record_spec.v1",), ("record_view.v1",)),
            profile("audit-promptfoo", ("file", "root", "id"), (), ("promptfoo_record.v1",)),
            profile(
                "audit-deepeval",
                ("file", "root", "id"),
                (),
                tuple(selected.record_schema for selected in DEEPEVAL_PROFILES.values()),
            ),
            profile(
                "audit-langfuse",
                ("observations", "scores", "root", "id"),
                (),
                ("langfuse_record.v1",),
            ),
            profile(
                "measure",
                ("observation", "policy", "root", "id"),
                ("observation.v1", "measurement_policy.v3"),
                ("measurement_record.v1",),
            ),
            profile(
                "audit-baseline",
                ("exchange", "root", "id"),
                (),
                ("baseline_record.v1",),
            ),
            profile("replay-measure", ("root", "id"), (), ("measurement_replay.v1",)),
            profile("list", ("root",), (), ("record_catalog.v1",)),
            profile("show", ("root", "id"), (), ("record_view.v1",)),
            profile("read-json", ("root", "id", "name"), (), ("record_document.v1",)),
            profile("verify", ("root", "id"), (), ("record_verification.v1",)),
        ),
        _standalone(records.main),
    ),
    CommandDescriptor(
        "audit-run",
        "Audit producer execution units.",
        audit_run.configure,
        (
            profile(
                "neutral-inventory",
                ("input",),
                ("binary_run_inventory.v1",),
                ("binary_run_audit.v1",),
            ),
            profile(
                "goppi-grounded-pilot-exchange",
                ("goppi_grounded_pilot_exchange",),
                (),
                ("binary_run_audit.v1",),
            ),
        ),
        _standalone(audit_run.main),
    ),
    CommandDescriptor(
        "audit-langfuse",
        "Account for supplied Langfuse score kinds and associations.",
        audit_langfuse.configure,
        (
            profile(
                LANGFUSE_RULE_PROFILE,
                ("observations", "scores"),
                (),
                ("langfuse_accounting.v1",),
            ),
            profile(
                "langfuse.row-index.v1",
                ("observations", "scores", "view"),
                (),
                ("langfuse_row_index.v1",),
            ),
        ),
        _standalone(audit_langfuse.main),
    ),
    CommandDescriptor(
        "audit-deepeval",
        "Distinguish scores, evaluator errors, and unscored DeepEval metrics.",
        audit_deepeval.configure,
        tuple(
            profile(
                "|".join((DEEPEVAL_SOURCE[0], version)),
                ("file",),
                (),
                (selected.accounting_schema,),
            )
            for version, selected in DEEPEVAL_PROFILES.items()
        )
        + (
            profile(
                "deepeval.test-run|4.2.8|owner-asserted-metric-bindings",
                ("file", "version", "bindings"),
                ("deepeval_metric_bindings.v1",),
                ("evidence_result.v1",),
            ),
        ),
        _standalone(audit_deepeval.main),
    ),
    CommandDescriptor(
        "audit-promptfoo",
        "Audit Promptfoo counts directly from a local export.",
        audit_promptfoo.configure,
        tuple(
            profile(
                "|".join((PROMPTFOO_SOURCE[0], version)),
                ("file",),
                (),
                ("promptfoo_accounting.v1",),
            )
            for version in PROMPTFOO_VERSIONS
        ),
        _standalone(audit_promptfoo.main),
    ),
    CommandDescriptor(
        "audit-counts",
        "Audit declared counts in one supported artifact.",
        audit_counts.configure,
        tuple(
            profile(
                f"promptfoo-{version}",
                ("case",),
                ("artifact_case.v1",),
                ("promptfoo_accounting.v1",),
            )
            for version in PROMPTFOO_VERSIONS
        ),
        _standalone(audit_counts.main),
    ),
    CommandDescriptor(
        "compare-declarations",
        "Compare supplied condition and metric declarations.",
        compare_declarations.configure,
        (
            profile(
                "promptfoo-accounting",
                ("baseline", "candidate", "manifest"),
                ("promptfoo_accounting.v1", "declared_comparison_case.v1"),
                ("declared_comparison.v1",),
            ),
        ),
        _standalone(compare_declarations.main),
    ),
    CommandDescriptor(
        "pair-binary",
        "Compute bounded paired binary evidence.",
        pair_binary.configure,
        (
            profile(
                "binary",
                ("input", "policy"),
                ("paired_binary_input.v1", "paired_binary_policy.v1"),
                ("paired_binary_evidence.v1",),
            ),
        ),
        _standalone(pair_binary.main),
    ),
    CommandDescriptor(
        "review-pair",
        "Review declared paired comparison context.",
        review_pair.configure,
        (
            profile(
                "binary-review",
                ("input", "policy", "context"),
                (
                    "paired_binary_input.v1",
                    "paired_binary_policy.v1",
                    "paired_review_context.v1",
                ),
                ("paired_review.v1",),
            ),
        ),
        _standalone(review_pair.main),
    ),
    CommandDescriptor(
        "review-handoff",
        "Review a local producer handoff and optionally save its evidence.",
        handoff_review.configure,
        (
            profile(
                "goppi.p7_regrade_consumer_handoff.v1",
                ("handoff",),
                (),
                ("paired_handoff_review.v1",),
            ),
        ),
        _standalone(handoff_review.main),
    ),
    CommandDescriptor(
        "inspect",
        "Inspect one local artifact case.",
        inspection.configure,
        (
            profile(
                "artifact-case",
                ("case",),
                ("artifact_case.v1",),
                ("artifact_inspection.v1",),
            ),
        ),
        _standalone(inspection.main),
    ),
    CommandDescriptor(
        "check",
        "Check declared release linkage.",
        release.configure,
        (
            profile(
                "release-case",
                ("release",),
                ("release_case.v1",),
                ("release_inspection.v1",),
            ),
        ),
        _standalone(release.main),
    ),
    CommandDescriptor(
        "measure",
        "Compute one local measurement read.",
        measure.configure,
        (
            profile(
                "binary-event-directional.v1",
                ("observation", "policy"),
                ("observation.v1", "measurement_policy.v3"),
                ("read.v2",),
            ),
        ),
        _standalone(measure.main),
    ),
    CommandDescriptor(
        "accept",
        "Classify one change candidate's declared evidence.",
        accept.configure,
        (
            profile(
                "declared-claims",
                ("candidate", "claims", "read"),
                ("change_candidate.v1", "declared_claims.v2", "read.v2"),
                ("acceptance_decision.v2",),
            ),
        ),
        _standalone(accept.main),
    ),
    CommandDescriptor(
        "absence",
        "Population-absence gate.",
        _configure_absence,
        (
            profile(
                "population",
                ("root", "glob", "min"),
                ("exemptions.v1",),
                ("cli_result.v1",),
            ),
        ),
    ),
    CommandDescriptor(
        "drift",
        "Contract pin drift guard.",
        _configure_drift,
        (
            profile(
                "check",
                (),
                ("exemptions.v1",),
                ("cli_result.v1",),
            ),
        ),
    ),
    CommandDescriptor(
        "pins",
        "Contract pin registry.",
        _configure_pins,
        (profile("refresh", ("repo",), (), ()),),
    ),
    CommandDescriptor(
        "boundary",
        "Vocabulary-boundary scanner.",
        _configure_boundary,
        (
            profile(
                "scan",
                ("paths",),
                ("boundary_allowlist.v1",),
                ("cli_result.v1",),
            ),
        ),
    ),
    CommandDescriptor(
        "capabilities",
        "List installed command and codec capabilities.",
        capabilities.configure,
        (profile("default", (), (), ("capabilities.v1",)),),
        _standalone(capabilities.main),
    ),
)


def _dispatch() -> dict[str, CommandDescriptor]:
    result: dict[str, CommandDescriptor] = {}
    for descriptor in COMMAND_DESCRIPTORS:
        for name in (descriptor.command, *descriptor.aliases):
            if name in result:
                raise ValueError("DUPLICATE_COMMAND_REGISTRATION")
            result[name] = descriptor
    return result


COMMANDS = _dispatch()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="underwrite")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for descriptor in COMMAND_DESCRIPTORS:
        child = subparsers.add_parser(
            descriptor.command,
            aliases=list(descriptor.aliases),
            help=descriptor.help,
        )
        descriptor.configure(child)
    return parser


def main(argv: list[str] | None = None) -> int:
    args_list = sys.argv[1:] if argv is None else argv
    if args_list and (descriptor := COMMANDS.get(args_list[0])) is not None:
        if descriptor.handler is not None:
            return descriptor.handler(args_list[0], args_list[1:])
    args = build_parser().parse_args(args_list)
    exit_code, payload = args.handler(args)
    render.render(payload, as_json=bool(getattr(args, "json", False)))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

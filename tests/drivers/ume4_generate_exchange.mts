/**
 * Copy this file to tests/drivers/ in the archived goppi revision and run with:
 *   pnpm exec tsx tests/drivers/ume4_generate_exchange.mts OUTPUT_DIRECTORY
 *   pnpm exec tsx tests/drivers/ume4_generate_exchange.mts OUTPUT_DIRECTORY --variant false-missing
 *
 * The committed goppi fixture/helper and exporter own the exchange semantics.
 */
import { createHash } from "node:crypto"
import { rm } from "node:fs/promises"
import { resolve } from "node:path"

import { exportBaselineDiscoveryV2 } from "../../src/application/baseline-discovery/export-v2.js"
import {
  executeBaselineDiscovery,
  prepareBaselineDiscovery,
} from "../../src/application/baseline-discovery/service.js"
import {
  type AnyNativeProcessRequest,
  calculateAnyNativeRequestDigest,
  sealNativeProcessReceipt,
} from "../../src/runtime/native-contracts.js"
import * as nativeExecutor from "../../src/runtime/native-executor.js"
import { createNativeAaFixture } from "../application/local-aa/native-fixtures.js"

const outputRoot = process.argv[2]
const variant =
  process.argv.length === 3
    ? "all-true"
    : process.argv.length === 5 &&
        process.argv[3] === "--variant" &&
        process.argv[4] === "false-missing"
      ? "false-missing"
      : undefined
if (outputRoot === undefined || variant === undefined) {
  throw new Error("usage: ume4_generate_exchange.mts OUTPUT_DIRECTORY [--variant false-missing]")
}

const alias = variant === "all-true" ? "ume4-pinned-conformance" : "ume4-false-missing"
const fixture = await createNativeAaFixture()
try {
  const attempts = fixture.approved.partition.explore_case_ids.length
  const preparation = await prepareBaselineDiscovery({
    projectRoot: fixture.root,
    alias,
    plan: {
      schema: "goppi.baseline_discovery_plan_authoring.v1",
      plan_id: `${alias}-plan`,
      approved_draft: fixture.authored.draft.content_digest,
      backend: "native",
      runner: fixture.plan.runner,
      verifier: fixture.plan.verifier,
      resources: fixture.plan.resources,
      rollout_budget: {
        max_fresh_runner_attempts: attempts,
        max_reported_cost_usd: 0,
        reported_cost_is_not_provider_billing_cap: true,
      },
      retention_profile: "audit_private",
    },
  })
  const execution = await executeBaselineDiscovery({
    projectRoot: fixture.root,
    alias,
    authorization: {
      schema: "goppi.baseline_execution_grant.v1",
      authorization_id: `${alias}-grant`,
      run_alias: alias,
      input_binding_digest: preparation.input_binding_digest,
      authorized_by: "synthetic-fixture-reviewer",
      authorized_at: "2026-10-11T00:00:00.000Z",
      expires_at: "2026-10-12T00:00:00.000Z",
      max_fresh_runner_attempts: attempts,
      max_reported_cost_usd: 0,
      reported_cost_is_not_provider_billing_cap: true,
    },
    now: new Date("2026-10-11T01:00:00.000Z"),
    ...(variant === "false-missing" ? { createExecutor: falseMissingExecutor } : {}),
  })
  if (
    variant === "false-missing" &&
    (execution.observed_false !== 1 || execution.missing !== 1 || execution.status !== "partial")
  ) {
    throw new Error("false-missing source did not reach the expected producer states")
  }
  const result = await exportBaselineDiscoveryV2({
    projectRoot: fixture.root,
    alias,
    outputRoot: resolve(outputRoot),
    generatedAt: "2026-10-11T12:00:00.000Z",
  })
  process.stdout.write(`${JSON.stringify({ output: result.output, rows: result.rows })}\n`)
} finally {
  await rm(fixture.root, { recursive: true, force: true })
}

function falseMissingExecutor(
  config: Parameters<typeof nativeExecutor.createLocalNativeExecutor>[0],
): ReturnType<typeof nativeExecutor.createLocalNativeExecutor> {
  const delegate = nativeExecutor.createLocalNativeExecutor(config)
  let verifier = 0
  return {
    ...delegate,
    execute: async (request, options) => {
      if (request.operation === "verifier") {
        verifier += 1
        if (verifier === 1) return verifierMalformedObservation(request)
        if (verifier === 2) return { kind: "unavailable", code: "STORAGE_FAILED", retryable: false }
      }
      return delegate.execute(request, options)
    },
  }
}

function verifierMalformedObservation(request: AnyNativeProcessRequest) {
  const protocol = JSON.parse(request.stdin.content)
  const expected = protocol.expected
  const stdout = JSON.stringify({
    schema: "goppi.verifier_response.v2",
    case_id: expected.case_id,
    run_id: expected.run_id,
    variant_id: expected.variant_id,
    config_hash: expected.config_hash,
    trace_digest: expected.trace_digest,
    verifier: expected.verifier,
    verifier_runtime_digest: expected.verifier_runtime_digest,
    outcome: { kind: "indeterminate", reason: "answer_malformed" },
  })
  const digest = (value: string) =>
    `sha256:${createHash("sha256").update(value, "utf8").digest("hex")}`
  const platform = process.platform === "darwin" ? "darwin" : "linux"
  return {
    kind: "observed" as const,
    process: { kind: "completed" as const, exitCode: 0, stdout, stderr: "" },
    cleanup: "complete" as const,
    receipt: sealNativeProcessReceipt({
      schema: "goppi.native_process_receipt.v1",
      backend: "native",
      evidence_class: "trusted_local_operator_observation",
      attempt_id: request.attempt_id,
      request_digest: calculateAnyNativeRequestDigest(request),
      executable_digest: request.executable.content_digest,
      executable_source: "verified_host_system_path",
      profile_identity_digest: request.profile_identity_digest,
      snapshot_digest: request.harness_snapshot.source_digest,
      platform,
      architecture: process.arch,
      process_instance_id: null,
      observed_pid: null,
      observed_pgid: null,
      observed_start_token: null,
      termination_reason: "completed",
      exit_code: 0,
      exit_signal: null,
      output_available: true,
      stdout: { bytes: Buffer.byteLength(stdout), digest: digest(stdout), truncated: false },
      stderr: { bytes: 0, digest: digest(""), truncated: false },
      observed_started_at: "2026-10-11T01:00:00.000Z",
      observed_finished_at: "2026-10-11T01:00:01.000Z",
      duration_ms: 1000,
    }),
  }
}

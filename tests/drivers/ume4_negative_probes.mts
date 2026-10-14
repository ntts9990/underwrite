/**
 * Copy into tests/drivers/ of the pinned goppi archive. Run with its tsx and
 * give a retained public exchange, an installed Underwrite CLI, pins, and output.
 * Only temporary copies are mutated; no case IDs, answers, or paths are emitted.
 */
import { spawnSync } from "node:child_process"
import { createHash } from "node:crypto"
import { lstat, mkdir, mkdtemp, readdir, readFile, realpath, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"

import { BASELINE_CASE_EXCHANGE_V2_MANIFEST_DOMAIN } from "../../src/application/baseline-discovery/export-manifest-contract.js"
import { canonicalJson } from "../../src/content-addressing/canonical-json.js"
import { calculateLengthPrefixedSha256 } from "../../src/content-addressing/length-prefixed-sha256.js"

const FILES = ["manifest.json", "export.json", "rows.jsonl", "schema.json", "scope.json"]
const args = process.argv.slice(2)
if (args.length !== 4) throw new Error("usage: BASE_EXCHANGE UNDERWRITE_CLI PINS_JSON OUTPUT_JSON")
const [baseExchange, underwriteCli, pinsPath, outputPath] = args as [string, string, string, string]

type Manifest = {
  schema: string
  content_digest: string
  files: { path: string; sha256: string; bytes: number }[]
  [key: string]: unknown
}
type Probe = {
  id: string
  mutation: string
  first_validation_layer: { goppi: string; underwrite: string }
  expected: {
    goppi: string
    underwrite: { code: string; reason: string; location: string }
  }
}

function sha(raw: Uint8Array): string {
  return `sha256:${createHash("sha256").update(raw).digest("hex")}`
}

async function rewriteManifest(directory: string, mutate: (manifest: Manifest) => void) {
  const path = join(directory, "manifest.json")
  const manifest = JSON.parse(await readFile(path, "utf8")) as Manifest
  mutate(manifest)
  const payload: Record<string, unknown> = { ...manifest }
  delete payload.content_digest
  manifest.content_digest = calculateLengthPrefixedSha256([
    BASELINE_CASE_EXCHANGE_V2_MANIFEST_DOMAIN,
    canonicalJson(payload),
  ])
  await writeFile(path, `${canonicalJson(manifest)}\n`)
}

async function mutateCopy(directory: string, id: string) {
  if (id === "negative-member-byte") {
    const path = join(directory, "rows.jsonl")
    const rows = await readFile(path, "utf8")
    const marker = '"case_id":"'
    const offset = rows.indexOf(marker) + marker.length
    if (offset < marker.length) throw new Error("missing synthetic case")
    const replacement = rows[offset] === "a" ? "b" : "a"
    await writeFile(path, rows.slice(0, offset) + replacement + rows.slice(offset + 1))
  } else if (id === "negative-row-removal") {
    const path = join(directory, "rows.jsonl")
    const rows = (await readFile(path, "utf8")).trimEnd().split("\n")
    if (rows.length < 2) throw new Error("at least two synthetic rows required")
    await writeFile(path, `${rows.slice(0, -1).join("\n")}\n`)
    const raw = await readFile(path)
    await rewriteManifest(directory, (manifest) => {
      const member = manifest.files.find((entry) => entry.path === "rows.jsonl")
      if (member === undefined) throw new Error("missing rows member")
      member.sha256 = sha(raw)
      member.bytes = raw.length
    })
  } else if (id === "negative-version") {
    await rewriteManifest(directory, (manifest) => {
      manifest.schema = "goppi.baseline_case_exchange.manifest.v3"
    })
  } else {
    throw new Error("unknown probe")
  }
}

function run(command: string, argv: string[]) {
  const result = spawnSync(command, argv, { encoding: "utf8" })
  if (result.error !== undefined) throw new Error("consumer could not start")
  return {
    exit_code: result.status,
    stdout_empty: result.stdout === "",
    stderr: result.stderr.trim(),
  }
}

const probes: Probe[] = [
  {
    id: "negative-member-byte",
    mutation:
      "Changed one byte of first synthetic case ID in rows.jsonl; retained original manifest byte/hash claims.",
    first_validation_layer: { goppi: "member_byte_identity", underwrite: "member_byte_identity" },
    expected: {
      goppi: "NEUTRAL_EXCHANGE_FILE_IDENTITY_INVALID",
      underwrite: {
        code: "INVALID_INPUT",
        reason: "BASELINE_FILE_IDENTITY_MISMATCH",
        location: "/manifest",
      },
    },
  },
  {
    id: "negative-row-removal",
    mutation:
      "Removed the second complete JSONL row, then recomputed rows.jsonl byte count and SHA-256 in manifest.files and recomputed manifest.content_digest with pinned goppi canonicalJson and length-prefixed digest.",
    first_validation_layer: { goppi: "scope_roster_count", underwrite: "row_population" },
    expected: {
      goppi: "NEUTRAL_EXCHANGE_SCOPE_INVALID",
      underwrite: {
        code: "INVALID_INPUT",
        reason: "BASELINE_POPULATION_INVALID",
        location: "/rows",
      },
    },
  },
  {
    id: "negative-version",
    mutation:
      "Changed manifest.schema from v2 to v3 and recomputed manifest.content_digest with pinned goppi canonicalJson and length-prefixed digest.",
    first_validation_layer: { goppi: "manifest_schema_version", underwrite: "manifest_profile" },
    expected: {
      goppi: "NEUTRAL_EXCHANGE_MANIFEST_INVALID",
      underwrite: {
        code: "INVALID_INPUT",
        reason: "BASELINE_PROFILE_INVALID",
        location: "/manifest",
      },
    },
  },
]

async function main() {
  const pins = JSON.parse(await readFile(pinsPath, "utf8")) as {
    goppi_commit: string
    underwrite_base_commit: string
    captures: { all_true: { members: Record<string, { bytes: number; sha256: string }> } }
  }
  const base = await realpath(resolve(baseExchange))
  const found = await readdir(base)
  if (found.length !== FILES.length || FILES.some((name) => !found.includes(name))) {
    throw new Error("unexpected source members")
  }
  const captured = new Map<string, Uint8Array>()
  const pinnedMembers = pins.captures.all_true.members
  if (Object.keys(pinnedMembers).length !== FILES.length) throw new Error("incomplete pin")
  for (const name of FILES) {
    const path = join(base, name)
    const stat = await lstat(path)
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size !== pinnedMembers[name]?.bytes) {
      throw new Error("nonregular or unpinned source member")
    }
    const raw = await readFile(path)
    if (pinnedMembers[name]?.bytes !== raw.length || pinnedMembers[name]?.sha256 !== sha(raw)) {
      throw new Error("source pin mismatch")
    }
    captured.set(name, raw)
  }
  const originalManifest = sha(captured.get("manifest.json") as Uint8Array)
  const temporary = await realpath(await mkdtemp(join(tmpdir(), "ume4-negative-")))
  try {
    const results = []
    for (const probe of probes) {
      const directory = join(temporary, probe.id)
      await mkdir(directory)
      for (const name of FILES)
        await writeFile(join(directory, name), captured.get(name) as Uint8Array)
      await mutateCopy(directory, probe.id)
      const memberSha256 = Object.fromEntries(
        await Promise.all(
          FILES.map(async (name) => [name, sha(await readFile(join(directory, name)))]),
        ),
      )
      const neutral = run(process.execPath, [
        resolve("experiments/product-baseline-exchange/neutral-consumer.mjs"),
        "--exchange",
        directory,
      ])
      const underwrite = run(underwriteCli, ["audit-baseline", "--exchange", directory, "--json"])
      const diagnostic = JSON.parse(underwrite.stderr) as {
        code: string
        reason: string
        location: string
      }
      if (
        neutral.exit_code !== 2 ||
        !neutral.stdout_empty ||
        neutral.stderr !== probe.expected.goppi ||
        underwrite.exit_code !== 2 ||
        !underwrite.stdout_empty ||
        diagnostic.code !== probe.expected.underwrite.code ||
        diagnostic.reason !== probe.expected.underwrite.reason ||
        diagnostic.location !== probe.expected.underwrite.location
      ) {
        throw new Error("unexpected consumer result")
      }
      results.push({
        id: probe.id,
        mutation: probe.mutation,
        first_validation_layer: probe.first_validation_layer,
        member_sha256: memberSha256,
        goppi_neutral: {
          exit_code: neutral.exit_code,
          stderr_code: probe.expected.goppi,
          stdout_empty: neutral.stdout_empty,
        },
        underwrite_installed: {
          exit_code: underwrite.exit_code,
          ...probe.expected.underwrite,
          stdout_empty: underwrite.stdout_empty,
        },
      })
    }
    const report = {
      schema: "ume4.pinned_negative_probes.v1",
      scope:
        "temporary copies of the retained synthetic five-file exchange; no mutated fixture published",
      goppi_commit: pins.goppi_commit,
      underwrite_base_commit: pins.underwrite_base_commit,
      base_manifest_sha256: originalManifest,
      probes: results,
    }
    await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`)
  } finally {
    await rm(temporary, { recursive: true, force: true })
  }
}

try {
  await main()
} catch {
  process.stderr.write("UME4_NEGATIVE_PROBE_FAILED\n")
  process.exitCode = 2
}

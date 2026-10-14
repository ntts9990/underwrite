# Pinned goppi baseline exchange v2

This directory retains two completed, producer-generated, public synthetic
baseline exchanges. Each five-file directory contains exact bytes exported by
committed goppi. Each `neutral-result*.json` is the unedited stdout of that
same goppi revision's experimental neutral consumer on its retained copy.

## Pins

[`pins.json`](pins.json) is the machine-readable owner of the two immutable commits, source and tool hashes, generator identity, both exact five-file captures, neutral outputs, and negative result identity. `tests/gates/test_ume4_pinned_exchange.py` checks the retained bytes against it. The capture-time all-true generator predates the false/missing variant; both versions are identified there.

The goppi live checkout was read only. The committed tree was extracted with
`git archive` into a temporary directory, with dependencies and Node installed
there. The machine's pnpm 11.9.0 launcher selected the package's declared
pnpm 9.15.9 after entering the archive; the version check below occurs there.
This is the capture procedure with paths parameterized. Run it from the
Underwrite root. The retained hashes in `pins.json` identify the captured run even when
a fresh run has different attempt IDs or durations.

```sh
set -eu
UNDERWRITE_REPO=$(pwd -P)
GOPPI_REPO=$(realpath ../goppi)
PINNED_ARCHIVE=$(mktemp -d /tmp/ume4-goppi.XXXXXX)
PINNED_ARCHIVE=$(realpath "$PINNED_ARCHIVE")
git -C "$GOPPI_REPO" archive 77a0a72f2cb545212c0f79a7c696c6837ae4eb6a | tar -x -C "$PINNED_ARCHIVE"
npm install --prefix "$PINNED_ARCHIVE/.runtime" --no-save --no-audit --no-fund node@24.20.0
cd "$PINNED_ARCHIVE"
PINNED_NODE="$PINNED_ARCHIVE/.runtime/node_modules/node/bin/node"
PNPM_LAUNCHER=$(realpath "$(command -v pnpm)")
test "$("$PINNED_NODE" "$PNPM_LAUNCHER" --version)" = "9.15.9"
"$PINNED_NODE" "$PNPM_LAUNCHER" install --frozen-lockfile --store-dir "$PINNED_ARCHIVE/.pnpm-store"
mkdir -p tests/drivers
cp "$UNDERWRITE_REPO/tests/drivers/ume4_generate_exchange.mts" tests/drivers/ume4_generate_exchange.mts
"$PINNED_NODE" "$PNPM_LAUNCHER" exec tsx tests/drivers/ume4_generate_exchange.mts "$PINNED_ARCHIVE/exchange"
"$PINNED_NODE" "$PNPM_LAUNCHER" exec tsx tests/drivers/ume4_generate_exchange.mts "$PINNED_ARCHIVE/false-missing-exchange" --variant false-missing
"$PINNED_NODE" "$PNPM_LAUNCHER" exec tsc --noEmit
"$PINNED_NODE" "$PNPM_LAUNCHER" exec biome check tests/drivers/ume4_generate_exchange.mts
"$PINNED_NODE" "$PINNED_ARCHIVE/experiments/product-baseline-exchange/neutral-consumer.mjs" --exchange "$PINNED_ARCHIVE/exchange" > "$PINNED_ARCHIVE/neutral-result.json"
"$PINNED_NODE" "$PINNED_ARCHIVE/experiments/product-baseline-exchange/neutral-consumer.mjs" --exchange "$PINNED_ARCHIVE/false-missing-exchange" > "$PINNED_ARCHIVE/neutral-result-false-missing.json"
```

The reviewed capture's five export members and neutral stdout were copied
unchanged into this fixture directory. The reproduction commands keep new output
in the temporary archive, so they cannot overwrite these pinned bytes.

The all-true exchange in `goppi-v2-exchange/` has exactly five members. Their measured byte identities match the four manifest claims and the manifest itself; exact sizes and SHA-256 values are in `pins.json`.

The neutral consumer exited zero and emitted the pinned `neutral-result.json`:
two planned rows, two `observed_true`, zero `observed_false`, zero missing.
The ordered roster is `aa-case-001`, `aa-case-005`; both have family
`local-aa-conformance`, source kind `synthetic`, binary outcome, and verifier ID
`local-aa-verifier-v1`. The exporter was called without `includeAnswers`, so its
default is false. Both retained rows say `answer.included=false` and
`availability=omitted_by_default`.

The separate `goppi-v2-false-missing/` exchange used the driver's explicit
`--variant false-missing` path. It copies the committed test's native executor
injection pattern: the first verifier returns a recorded malformed-answer
outcome, which goppi routes to `observed_false`; the second verifier is
unavailable, which remains `missing`. The source run is partial, while the
export's `status=complete` means its five-file publication completed. No
unavailable verifier was counted as false. The second capture's exact member sizes and SHA-256 values are in `pins.json`.

The pinned neutral consumer exited zero on the retained second capture and
emitted the pinned `neutral-result-false-missing.json`:
two planned rows, one `observed_false`, one missing, zero `observed_true`.
Underwrite's local `.venv/bin/underwrite audit-baseline --exchange
fixtures/conformance/ume4/goppi-v2-false-missing --json` also exited zero: its
two items are an observed `false` source claim and a missing `null` with reason
`SOURCE_MISSING`. Its JSON was retained only in ignored local diagnostics at
`output/ume4/underwrite-false-missing.json`; the public comparison gate recomputes this result.

All five files of each capture were inspected as UTF-8 bytes and parsed where
applicable. Each five-member set, manifest sizes and hashes, ordered roster,
classification, row statuses, and answer omissions were checked. No exported byte contains the
fixture's private verifier canary or answer values, local user paths, or a
credential payload. The public `schema.json` defines possible answer fields
and includes a supported Claude runner enum; this capture has no Claude runtime
invocation, answer, or credential payload. The scope explicitly excludes private
truth, confirm metadata, instructions, source references, and reviewer details.

These are two binary, homogeneous, two-case vectors within Underwrite's
200-case limit. They do not establish scalar or larger-population interoperability.
The exporter checks local source authenticity within its stated operator scope;
the neutral consumer reports `source_claims_verified=false`, and neither output
grants inference, paired-effect, generalization, approval, or deployment authority.
The fixed export timestamp does not make regeneration byte identical: native
attempt IDs and durations can vary. The hashes in `pins.json` identify these retained
captures, not a promise about a fresh run. Exchange generation and consumption
made no model or external-service calls; package installation used the registry.

## Negative probes

The all-true retained exchange was copied three times into the isolated
temporary archive. Only those copies were changed. The row and manifest
mutation digests were recomputed using goppi's pinned `canonicalJson` and
`calculateLengthPrefixedSha256` with its v2 manifest domain. The exact synthetic
member hashes, mutation recipes, exit codes, error fields, validation layers,
and stdout emptiness are in the tracked, path-free [`negative-results.json`](negative-results.json). Its exact SHA-256 and base manifest identity are pinned in `pins.json`.

To rerun those exact probes from a fresh checkout, first complete the isolated
goppi setup above. Build the pinned Underwrite base in the same temporary
directory, then run the probe driver from the pinned goppi archive. This reads
the retained public fixture and writes only temporary mutated copies:

```sh
UNDERWRITE_BASE="$PINNED_ARCHIVE/underwrite-base"
mkdir -p "$UNDERWRITE_BASE"
git -C "$UNDERWRITE_REPO" archive 1fb05456f4fd77c2d72744e128a03979f9b12b73 | tar -x -C "$UNDERWRITE_BASE"
cd "$UNDERWRITE_BASE"
uv build --all-packages --wheel --out-dir "$PINNED_ARCHIVE/underwrite-dist"
uv venv "$PINNED_ARCHIVE/underwrite-venv"
uv pip install --python "$PINNED_ARCHIVE/underwrite-venv/bin/python" \
  "$PINNED_ARCHIVE/underwrite-dist/underwrite_core-0.0.1-py3-none-any.whl" \
  "$PINNED_ARCHIVE/underwrite-dist/underwrite-0.0.1-py3-none-any.whl"
cd "$PINNED_ARCHIVE"
cp "$UNDERWRITE_REPO/tests/drivers/ume4_negative_probes.mts" tests/drivers/ume4_negative_probes.mts
"$PINNED_NODE" "$PNPM_LAUNCHER" exec tsc --noEmit
"$PINNED_NODE" "$PNPM_LAUNCHER" exec biome check tests/drivers/ume4_negative_probes.mts
"$PINNED_NODE" "$PNPM_LAUNCHER" exec tsx tests/drivers/ume4_negative_probes.mts \
  "$UNDERWRITE_REPO/fixtures/conformance/ume4/goppi-v2-exchange" \
  "$PINNED_ARCHIVE/underwrite-venv/bin/underwrite" \
  "$UNDERWRITE_REPO/fixtures/conformance/ume4/pins.json" \
  "$PINNED_ARCHIVE/negative-results.json"
cmp "$PINNED_ARCHIVE/negative-results.json" \
  "$UNDERWRITE_REPO/fixtures/conformance/ume4/negative-results.json"
```

| Mutation of a temporary copy | goppi neutral, first refusal | Underwrite CLI, first refusal |
| --- | --- | --- |
| Change one `rows.jsonl` case-ID byte without updating the manifest | `NEUTRAL_EXCHANGE_FILE_IDENTITY_INVALID` (member identity) | `INVALID_INPUT` / `BASELINE_FILE_IDENTITY_MISMATCH` at `/manifest` |
| Remove one complete JSONL row; recompute row size/hash and manifest digest | `NEUTRAL_EXCHANGE_SCOPE_INVALID` (scope roster/count) | `INVALID_INPUT` / `BASELINE_POPULATION_INVALID` at `/rows` |
| Change manifest schema from v2 to v3; recompute manifest digest | `NEUTRAL_EXCHANGE_MANIFEST_INVALID` (manifest profile) | `INVALID_INPUT` / `BASELINE_PROFILE_INVALID` at `/manifest` |

Each consumer exited 2 with empty stdout on each probe. These results describe
only the tested mutations and validation order; the experimental neutral
consumer does not promise a universal typed error contract.

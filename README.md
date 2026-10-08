# underwrite

underwrite turns local evaluation artifacts into a common observation, computes
measurements under an explicit policy, and classifies change candidates against
declared evidence. It keeps missing evidence visible: `not_measured`,
`indeterminate`, and `abstained` are distinct outcomes.

It is a Python CLI and library. Artifact inspection checks bytes and declared
hashes; release checks examine declared linkage and record presence. These checks
do not establish independent provenance or grant deployment permission.

## Install from source

Requires Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/ntts9990/underwrite.git
cd underwrite
uv sync --frozen --all-groups
uv run --frozen --no-sync underwrite --help
```

The repository includes the `underwrite-core` workspace package. No adjacent
checkout is needed to install or use the example workflows.

## Normalize an artifact

Choose a format and exact supported version explicitly:

| Producer | `--format` | `--version` |
| --- | --- | --- |
| DeepEval | `deepeval.test-run` | `4.1.1` |
| Inspect AI | `inspect.eval-log` | `0.3.263` |
| Langfuse | `langfuse.observations-v2` or `langfuse.scores` | `4.35.0` |
| OpenInference | `openinference.traces` | `0.1.38` |
| OTLP JSON | `otlp-json.traces` | `0.160.0` |
| promptfoo | `promptfoo.eval-output` | `0.123.0` |
| Native evidence bundle | `underwrite.evidence-bundle` | `v1` |

These are bounded payload profiles, not compatibility claims for every export
from those producers. `project` is an explicit alias of `ingest`.

```sh
mkdir -p output
uv run --frozen --no-sync underwrite ingest \
  --format underwrite.evidence-bundle --version v1 \
  --file fixtures/examples/measurement/bundle.json --json \
  > output/observation.json
uv run --frozen --no-sync underwrite measure \
  --observation output/observation.json \
  --policy fixtures/examples/acceptance/policy-quality.json --json \
  > output/read-quality.json
uv run --frozen --no-sync underwrite measure \
  --observation output/observation.json \
  --policy fixtures/examples/acceptance/policy-latency.json --json \
  > output/read-latency.json
```

The native bundle declares `schema_version: underwrite.evidence-bundle.v1` and
carries readings, source claims, and availability records. An observation keeps
those source claims separate from the measurement. Measurement requires a
complete policy identifying inputs, subject, calibration, quorum, and statistical
thresholds. A successful command exit means the read was produced; inspect the
read's state before drawing a conclusion.

`--http-body-file FILE` reads an already captured HTTP body from a local file;
it does not fetch a URL. Optional `--source-ref` and `--source-locator` describe
the supplied source. When no source reference is given, byte identity supplies
a content hash, not an independently verified origin. See command `--help` for
input size and depth limits.

Malformed native input produces a typed error on stderr and no observation on
stdout. Try a bundle with an empty run identifier:

```sh
mkdir -p output
printf '%s\n' '{"schema_version":"underwrite.evidence-bundle.v1","run_id":"","sources":[],"readings":[],"availability":[]}' \
  > output/malformed-bundle.json
if uv run --frozen --no-sync underwrite ingest \
  --format underwrite.evidence-bundle --version v1 \
  --file output/malformed-bundle.json --json \
  > output/malformed.stdout 2> output/malformed.stderr; then
  ingest_exit=0
else
  ingest_exit=$?
fi
printf 'exit: %s\n' "$ingest_exit"
wc -c < output/malformed.stdout
cat output/malformed.stderr
```

This reports exit `1`, stdout size `0`, and this stderr JSON:

```json
{"schema":"instrument_error.v1","command":"ingest","code":"INVALID_INPUT","reason":"MALFORMED_EVIDENCE_BUNDLE_PAYLOAD","exit_code":1}
```

## Classify a candidate

`accept` binds a candidate to declared claims and policy-matched reads. It reports
whether the supplied evidence supports the requested acceptance basis and retains
uncertainty or missing measurements in the decision.

```sh
uv run --frozen --no-sync underwrite accept \
  --candidate fixtures/examples/acceptance/candidate.json \
  --claims fixtures/examples/acceptance/claims.json \
  --read output/read-quality.json \
  --read output/read-latency.json --json
```

Repeat `--read` for each required claim. Exit 0 means a classification was
produced, not approval. This example produces `screened`, requires human review,
and keeps `merge_authorized` false. The inputs are synthetic local examples;
the reads above are computed from the supplied bundle and policies.

## Other checks

- `inspect CASE.json --json`: inspect one local artifact manifest and its projection.
- `check RELEASE.json --json`: check a release manifest's declared evidence linkage.
- `absence --root DIR --glob '*.json' --min 1 --json`: require a nonempty population.
- `boundary scan DIR --json`: scan JSON and JSONL strings for configured verdict vocabulary.
- `drift check --pins-root PINS --siblings-root SOURCES --json`: compare a user-supplied
  pin registry against local source checkouts.
- `pins refresh --repo NAME --pins-root PINS --siblings-root SOURCES`: refresh a
  configured repository's pins from local bytes.

Pin commands are generic utilities; no preconfigured source registry is included.
Allowlist and exemption evaluation requires an explicit `--now` when applicable.
Malformed inputs and missing paths remain configuration errors rather than passes.
Use each command's `--help` for its options and exit behavior. JSON contracts live
in [contracts/](contracts/); each command family has its own output and error schema.

## Development and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for the checks and contribution process,
[SECURITY.md](SECURITY.md) for private vulnerability reporting, and
[AGENTS.md](AGENTS.md) for technical boundaries. The core package uses only the
Python standard library; application adapters own local I/O and schema validation.

underwrite is licensed under [Apache-2.0](LICENSE).
[LICENSING.md](LICENSING.md) records the adapted policy templates' CC licenses.

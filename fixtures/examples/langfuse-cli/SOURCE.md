# Langfuse CLI 1.2.4 offline capture examples

These five files are unchanged outputs of published `@langfuse/cli@1.2.4`
(release tag commit `20aa047299c9dbd9d0ba117c4db830d7cf3382f8`) with API snapshot
`4.35.0` (pinned OpenAPI SHA-256
`8f3a25d28afbf8bdd710d8e7a5c60d65ff5c71315764c46c8dea504e8f67adb6`).
The CLI called a **controlled loopback HTTP server** serving schema-validated
synthetic observations and scores. No Langfuse account, live service, user data,
or producer evaluation was involved. These files demonstrate the local CLI
format and Underwrite's pinned offline reader; they do not prove live-server
field selection, collection completeness, or an atomic pair.

| File | Native CLI channel | SHA-256 of file bytes |
| --- | --- | --- |
| `observations.json` | `api observations list --all --json` envelope | `e5979d2955f929700b088e97562ee3d5d4ad236fda266dd183e437415fa9559b` |
| `scores.json` | `api scores list --all --json` envelope | `faf6df9ccef6d3082f1abb4843afab7c1e45fd565ebb4870cce17f443e80ead3` |
| `observations-body.json` | `api --output FILE observations list --all` body | `ea876b4c0731cdaafd5aeb8babedd0ffbf17ceac62d30a9397f28d6c222ee3a6` |
| `scores-body.json` | `api --output FILE scores list --all` body | `e24eb1c31bf962c0e95fea0840888f4a60086506119379668d30bd31c483f9e9` |
| `scores-truncated.json` | `api scores list --all --max-items 3 --json` envelope | `6a337c5e07638e0be3ec142860943d76ed4689d6c5c332160fe5f0de1d60e087` |

The synthetic commands selected bounded observation `fromStartTime`/`toStartTime`
and score `fromTimestamp`/`toTimestamp` ranges, requested score `subject`, and
used `--limit 2` to exercise cursor pagination. The `--all` examples collect
two observation pages and three score pages; the truncated example reports
`meta.truncated=true`. The JSON files do not authenticate those request choices.
To regenerate them, provide an already installed `@langfuse/cli@1.2.4`
executable, the pinned `4.35.0` OpenAPI YAML, and a **new** output directory.
This repository's existing Python environment has PyYAML and jsonschema; the
recipe installs nothing and contacts only its bounded synthetic loopback server:

```sh
uv run --frozen --no-sync python fixtures/examples/langfuse-cli/capture.py \
  --cli /path/to/langfuse-1.2.4 \
  --openapi /path/to/pinned-4.35.0-openapi.yml \
  --out /tmp/new-langfuse-cli-captures
```

`capture.py` checks the OpenAPI SHA-256, CLI version, generated response pages,
expected request fields/windows, and final collection shape. It refuses an
existing output path. With the pinned tool and spec, the five regenerated file
hashes above matched exactly in a local run. This is a controlled API mock, not
a live-service interoperability claim.

For a real task, use the official CLI to capture each chosen local file, inspect
its actual status and pagination metadata, then use the advertised current
Underwrite intake. The development checkout selects the supported family from
both files and no longer accepts `--profile`. Published `underwrite==0.1.1`
predates this route; published 0.2.0 supports automatic intake too:

```sh
uv run --frozen --no-sync underwrite audit-langfuse \
  --observations observations.json --scores scores.json --json
```

Keep the original captures private when they include score text, comments,
headers, observation I/O or identifiers. Underwrite reports only associations
within the supplied pair; missing planned executions and matching source windows
remain unknown.

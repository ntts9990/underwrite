"""``underwrite accept``: one change candidate, its declared claims and the reads supplied
for its basis, to one ``acceptance_decision.v1`` on stdout (exit 0 whatever the
classification) or one ``acceptance_error.v1`` on stderr (exit 2). I/O only: the read
count is bounded before any file is opened, authority fields are refused before any
schema is applied, candidate and claims are validated before any read is opened, each
read is validated as soon as it is decoded, the split is computed here and every decision
is ``decision.decide``'s, validated against its contract before it is written once."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn

from underwrite_core.canonical import canonical_bytes

from underwrite.acceptance import decision as dc
from underwrite.acceptance.claims import MAX_CLAIMS_PER_BASIS
from underwrite.cli import measure
from underwrite.cli.local_json import read_document, validate
from underwrite.instrument.evidence.split import assign_split
from underwrite.instrument.ingest.application import NEXT_ACTIONS
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator

CANDIDATE_MAX_BYTES = 64 * 1024
CLAIMS_MAX_BYTES = 1024 * 1024
DOCUMENT_MAX_DEPTH = 16
# A read is what `underwrite measure` wrote: at most its output bytes. measure bounds no
# output depth; its observation depth is a loose ceiling on any read it writes.
READ_MAX_BYTES = measure.OUTPUT_MAX_BYTES
READ_MAX_DEPTH = measure.OBSERVATION_MAX_DEPTH
# declared_claims.v1 basis_claims maxItems: one basis binds at most this many reads.
MAX_READS = MAX_CLAIMS_PER_BASIS
OUTPUT_MAX_BYTES = measure.OUTPUT_MAX_BYTES
_INVALID = {"candidate": "INVALID_CANDIDATE", "claims": "INVALID_CLAIMS", "reads": "INVALID_READ"}
# Codes whose next action does not follow from the refused input (dc.REFUSAL_ACTIONS).
_ACTIONS = {
    **NEXT_ACTIONS,
    "READ_COUNT_EXCEEDED": "REDUCE_INPUT_SIZE",
    "INPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE",
    "OUTPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE",
}


class AcceptError(ValueError):
    """Only fixed codes, reasons and structural locations reach this error envelope."""

    def __init__(
        self,
        code: str,
        reason: str,
        location: str = "",
        fields: Mapping[str, object] | None = None,
    ) -> None:
        action = _ACTIONS.get(code) or dc.REFUSAL_ACTIONS[location.split("/")[1]]
        self.payload: dict[str, object] = {
            "schema": "acceptance_error.v1",
            "code": code,
            "reason": reason,
            "location": location,
            "next_action": action,
            "retryable": False,
            "exit_code": 2,
            **(fields or {}),
        }
        super().__init__(code)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise AcceptError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP")


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--candidate", required=True, type=Path, help="change_candidate.v1.")
    parser.add_argument("--claims", required=True, type=Path, help="declared_claims.v1.")
    parser.add_argument(
        "--read",
        required=True,
        action="append",
        type=Path,
        help="read.v1 from `underwrite measure`; repeat once per claim of the basis.",
    )
    parser.add_argument("--json", action="store_true", help="Canonical decision on stdout.")
    parser.description = (
        "Classify one change candidate against its declared claims; exit 0 means a "
        "classification was produced, never approval. "
        f"At most {MAX_READS} reads. Limits: candidate {CANDIDATE_MAX_BYTES} bytes, "
        f"claims {CLAIMS_MAX_BYTES} bytes (depth {DOCUMENT_MAX_DEPTH}); each read "
        f"{READ_MAX_BYTES} bytes / depth {READ_MAX_DEPTH}; "
        f"output {OUTPUT_MAX_BYTES} bytes including newline."
    )


def _document(path: Path, location: str, max_bytes: int, max_depth: int) -> dict[str, Any]:
    invalid = _INVALID[location.split("/")[1]]
    return read_document(path, max_bytes, max_depth, location, invalid, AcceptError)[0]


def _decide(args: argparse.Namespace) -> dict[str, Any]:
    if len(args.read) > MAX_READS:
        raise AcceptError("READ_COUNT_EXCEEDED", "READ_COUNT_EXCEEDED", "/reads")
    candidate = _document(args.candidate, "/candidate", CANDIDATE_MAX_BYTES, DOCUMENT_MAX_DEPTH)
    claims = _document(args.claims, "/claims", CLAIMS_MAX_BYTES, DOCUMENT_MAX_DEPTH)
    for name, document in (("candidate", candidate), ("claims", claims)):
        if dc.authority_keys(document):
            raise AcceptError("AUTHORITY_FIELD_REFUSED", "AUTHORITY_FIELD_REFUSED", f"/{name}")
    candidate_validator = packaged_validator(dc.CANDIDATE_SCHEMA)
    validate(candidate_validator, candidate, AcceptError, "INVALID_CANDIDATE", "/candidate")
    claims_validator = packaged_validator("declared_claims.v1")
    validate(claims_validator, claims, AcceptError, "INVALID_CLAIMS", "/claims")
    read_validator = measure.measurement_validators()[0]
    reads: list[dict[str, Any]] = []
    for index, path in enumerate(args.read):
        location = f"/reads/{index}"
        read = _document(path, location, READ_MAX_BYTES, READ_MAX_DEPTH)
        validate(read_validator, read, AcceptError, "INVALID_READ", location)
        reads.append(read)
    # Canonical decoding (NFC text) and the schema (non-blank unique ids, a fraction in
    # (0, 1)) already hold every precondition of the split kernel.
    selection = claims["selection"]
    split = assign_split(
        selection["case_ids"],
        salt=selection["salt"],
        confirm_fraction=selection["confirm_fraction"],
    )
    assignment = {item.case_id: item.split for item in split.assignments}
    try:
        decision = dc.decide(candidate, claims, reads, assignment)
    except dc.AcceptanceRefusal as exc:
        raise AcceptError(exc.code, exc.reason, exc.location, exc.fields) from None
    validate(packaged_validator(dc.SCHEMA), decision, AcceptError, "INTERNAL_ERROR")
    return decision


def _human(decision: dict[str, Any]) -> str:
    lines = [
        "Acceptance classified; exit 0 is not approval. Human review is required, "
        "merge_authorized is false and the declared basis was not verified.",
        f"candidate: {decision['candidate_id']} (basis {decision['basis']})",
        f"classification: {decision['classification']} "
        f"({decision['from_state']} -> {decision['next_state']})",
        "reason_codes: " + measure.display(decision["reason_codes"]),
    ]
    for claim in decision["claims"]:
        read = claim["read"]
        lines.append(
            f"claim {claim['claim_id']} ({claim['effect']}, {claim['subject_id']}): "
            + ("no read supplied" if read is None else measure.display(read))
        )
    lines.append("inputs: " + measure.display(decision["inputs"]))
    lines.append("selection: " + measure.display(decision["selection"]))
    lines.append("limitations: " + measure.display(decision["limitations"]))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = _Parser(prog="underwrite accept", allow_abbrev=False)
    configure(parser)
    try:
        args = parser.parse_args(argv)
        decision = _decide(args)
        output = (canonical_bytes(decision).decode() if args.json else _human(decision)) + "\n"
        if len(output.encode()) > OUTPUT_MAX_BYTES:
            raise AcceptError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    except AcceptError as exc:
        error = exc
    except SchemaConfigurationError:
        error = AcceptError("SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_SCHEMA")
    except Exception:
        error = AcceptError("INTERNAL_ERROR", "INTERNAL_ERROR")
    else:
        sys.stdout.write(output)
        return 0
    sys.stderr.write(canonical_bytes(error.payload).decode() + "\n")
    return 2

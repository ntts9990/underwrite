"""Reproduce synthetic Langfuse CLI captures against a pinned local API mock.

Requires an already installed @langfuse/cli 1.2.4 and the pinned 4.35.0 OpenAPI
file. Writes only to an explicitly named, nonexistent output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import yaml
from jsonschema import Draft202012Validator

OPENAPI_SHA256 = "8f3a25d28afbf8bdd710d8e7a5c60d65ff5c71315764c46c8dea504e8f67adb6"
CLI_VERSION = "1.2.4"
CLI_TIMEOUT_SECONDS = 30
STAMP = "2026-01-01T12:00:00Z"
PROJECT = "project-synthetic"
TRACE = "trace-synthetic"
TRUNCATED_ITEMS = 3


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rows() -> dict[str, list[dict[str, object]]]:
    observations: list[dict[str, object]] = [
        {
            "id": f"obs-{index}",
            "traceId": TRACE,
            "startTime": STAMP,
            "endTime": STAMP,
            "projectId": PROJECT,
            "parentObservationId": "obs-root",
            "type": "GENERATION" if index % 2 else "SPAN",
            "modelId": "model-synthetic",
            "inputPrice": "0",
            "outputPrice": "0",
            "totalPrice": "0",
            "name": f"step-{index}",
            "usageDetails": {"input": index},
            "input": {"synthetic": index},
        }
        for index in range(1, 5)
    ]
    values: tuple[tuple[str, object], ...] = (
        ("NUMERIC", 0.5),
        ("BOOLEAN", False),
        ("CATEGORICAL", "helpful"),
        ("TEXT", "synthetic review"),
        ("CORRECTION", "synthetic correction"),
    )
    scores: list[dict[str, object]] = [
        {
            "id": f"score-{index}",
            "projectId": PROJECT,
            "name": "quality",
            "source": "API",
            "timestamp": STAMP,
            "environment": "test",
            "createdAt": STAMP,
            "updatedAt": STAMP,
            "dataType": kind,
            "value": value,
            "subject": {"kind": "observation", "id": f"obs-{min(index, 4)}", "traceId": TRACE},
        }
        for index, (kind, value) in enumerate(values, start=1)
    ]
    return {"observations": observations, "scores": scores}


def _server(
    rows: dict[str, list[dict[str, object]]], components: dict[str, object]
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "BaseHTTP/0.6"
        sys_version = "Python/3.12.13"

        def date_time_string(self, timestamp: float | None = None) -> str:
            return "Thu, 01 Jan 2026 00:00:00 GMT"

        def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP callback
            parsed = urlsplit(self.path)
            role = {
                "/api/public/v2/observations": "observations",
                "/api/public/v3/scores": "scores",
            }.get(parsed.path)
            if role is None:
                self.send_error(404)
                return
            query = parse_qs(parsed.query)
            try:
                cursor = query.get("cursor", ["next-0"])
                offset = int(cursor[0].removeprefix("next-"))
                expected_fields = (
                    "core,basic,io,usage" if role == "observations" else "details,subject"
                )
                start = "fromStartTime" if role == "observations" else "fromTimestamp"
                end = "toStartTime" if role == "observations" else "toTimestamp"
                if (
                    cursor != [f"next-{offset}"]
                    or offset < 0
                    or offset >= len(rows[role])
                    or offset % 2
                    or query.get("limit") != ["2"]
                    or query.get("fields") != [expected_fields]
                    or query.get(start) != ["2026-01-01T00:00:00Z"]
                    or query.get(end) != ["2026-01-02T00:00:00Z"]
                ):
                    raise ValueError("unexpected bounded request")
            except (ValueError, IndexError):
                self.send_error(400)
                return
            data = rows[role][offset : offset + 2]
            next_offset = offset + 2
            meta: dict[str, object] = (
                {"cursor": f"next-{next_offset}"} if next_offset < len(rows[role]) else {}
            )
            if role == "scores":
                meta["limit"] = 2
            body = {"data": data, "meta": meta}
            schema_name = (
                "ObservationsV2Response" if role == "observations" else "GetScoresV3Response"
            )
            Draft202012Validator(
                {"components": components, "$ref": f"#/components/schemas/{schema_name}"}
            ).validate(body)  # pyright: ignore[reportUnknownMemberType]
            payload = json.dumps(body, separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def _capture_one(
    cli: Path,
    out: Path,
    env: dict[str, str],
    rows: dict[str, list[dict[str, object]]],
    case: tuple[str, str, bool, int],
) -> None:
    role, target, body_only, max_items = case
    fields = "core,basic,io,usage" if role == "observations" else "details,subject"
    start = "--from-start-time" if role == "observations" else "--from-timestamp"
    end = "--to-start-time" if role == "observations" else "--to-timestamp"
    command = [str(cli), "--api-version", "4.35.0", "api"]
    if body_only:
        command.extend(("--output", str(out / target)))
    command.extend(
        (
            role,
            "list",
            "--fields",
            fields,
            start,
            "2026-01-01T00:00:00Z",
            end,
            "2026-01-02T00:00:00Z",
            "--limit",
            "2",
            "--all",
            "--max-items",
            str(max_items),
        )
    )
    if not body_only:
        command.append("--json")
    completed = subprocess.run(
        command, env=env, capture_output=True, timeout=CLI_TIMEOUT_SECONDS, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{role} CLI capture failed with exit {completed.returncode}")
    if body_only:
        if completed.stdout or not (out / target).is_file():
            raise RuntimeError(f"{role} --output channel was not used")
    else:
        (out / target).write_bytes(completed.stdout)
    captured = json.loads((out / target).read_bytes())
    if not isinstance(captured, dict):
        raise RuntimeError(f"{role} CLI output was not an object")
    body = cast(dict[str, Any], captured if body_only else captured["body"])
    truncated = max_items == TRUNCATED_ITEMS
    expected_count = TRUNCATED_ITEMS if truncated else len(rows[role])
    if len(body["data"]) != expected_count or body["meta"]["truncated"] != truncated:
        raise RuntimeError(f"{role} CLI collection shape changed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cli", type=Path, required=True, help="Installed Langfuse CLI executable."
    )
    parser.add_argument("--openapi", type=Path, required=True, help="Pinned 4.35.0 openapi.yml.")
    parser.add_argument("--out", type=Path, required=True, help="New output directory.")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("--out must name a new directory")
    if _hash(args.openapi.read_bytes()) != OPENAPI_SHA256:
        parser.error("--openapi bytes do not match the pinned snapshot")
    try:
        version = subprocess.run(
            [str(args.cli), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=CLI_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        parser.error("--cli version check failed")
    if version.returncode != 0 or version.stdout.strip() != CLI_VERSION:
        parser.error("--cli must be @langfuse/cli 1.2.4")
    components = yaml.safe_load(args.openapi.read_text(encoding="utf-8"))["components"]
    rows = _rows()
    args.out.mkdir(parents=True, exist_ok=False)
    handler = _server(rows, components)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "LANGFUSE_PUBLIC_KEY": "pk-local-synthetic",
        "LANGFUSE_SECRET_KEY": "sk-local-synthetic",
        "LANGFUSE_HOST": f"http://127.0.0.1:{server.server_port}",
    }
    try:
        for case in (
            ("observations", "observations.json", False, 10),
            ("scores", "scores.json", False, 10),
            ("observations", "observations-body.json", True, 10),
            ("scores", "scores-body.json", True, 10),
            ("scores", "scores-truncated.json", False, TRUNCATED_ITEMS),
        ):
            _capture_one(args.cli, args.out, env, rows, case)
        print(f"Synthetic Langfuse CLI captures written to {args.out}")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


if __name__ == "__main__":
    main()

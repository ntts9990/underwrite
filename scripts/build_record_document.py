"""Card: record-reuse | Paper: record_document.v1 | Stage: storage-read.

Compose selected-file JSON identity from the existing record field owners.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _schema_compose import object_schema, reference

from underwrite.service.records import RECORD_READ_MAX_BYTES

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts/record_document.v1.schema.json"


def build_schema() -> dict[str, Any]:
    owner = json.loads((ROOT / "contracts/result_record.v1.schema.json").read_bytes())
    definitions = {
        name: owner["$defs"][name] for name in ("record_id", "logical_name", "media_type", "digest")
    }
    size = {
        **owner["$defs"]["stored_file"]["properties"]["bytes"],
        "maximum": RECORD_READ_MAX_BYTES,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:underwrite:contract:record_document.v1",
        "title": "record_document.v1",
        "description": (
            "Parsed JSON from one verified captured file; identities refer to original bytes. "
            "Other files and links are not verified, and provenance is not authenticated."
        ),
        **object_schema(
            {
                "schema": {"const": "record_document.v1"},
                "record_id": reference("record_id"),
                "name": reference("logical_name"),
                "media_type": reference("media_type"),
                "bytes": size,
                "sha256": reference("digest"),
                "manifest_digest": reference("digest"),
                "verification_scope": {"const": "selected_file_against_manifest"},
                "content_representation": {"const": "parsed_json"},
                "content": {
                    "description": (
                        "A decoded JSON value admitted by the existing canonical serializer; "
                        "numeric representation is not exact decimal-literal preservation."
                    )
                },
            }
        ),
        "$defs": definitions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = (json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != raw:
            print("record_document.v1.schema.json is stale")
            return 1
    else:
        TARGET.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Standalone behavior and boundary checks."""

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from underwrite_core.bundle import Blob, plan_layout

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "contracts" / "bundle_index.v1.schema.json"


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_schema_is_valid_draft_2020_12() -> None:
    jsonschema.Draft202012Validator.check_schema(_schema())


def test_index_json_from_plan_layout_validates() -> None:
    blobs = [
        Blob(role="receipt", media_type="application/json", data=b'{"a":1}'),
        Blob(role="evidence", media_type="application/json", data=b'{"b":2}'),
    ]
    files = plan_layout(blobs)

    jsonschema.validate(instance=json.loads(files["index.json"]), schema=_schema())


def test_schema_rejects_descriptor_with_bad_digest() -> None:
    index = {
        "schema": "bundle_index.v1",
        "descriptors": [
            {
                "role": "receipt",
                "mediaType": "application/json",
                "digest": "sha256:not-hex",
                "size": 7,
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=index, schema=_schema())

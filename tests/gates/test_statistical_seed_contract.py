"""Standalone behavior and boundary checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _random_policy import seed_violations
from _repo_paths import repo_root
from jsonschema import Draft202012Validator, ValidationError, validate

ROOT = repo_root(Path(__file__).resolve())
OWNER = ROOT / "contracts/statistical_seed.v1.schema.json"
EVIDENCE = ROOT / "src/underwrite/instrument/evidence"
_APPROVED_POLICY_SEED = 42  # Independent contract oracle, never used as a RNG default.


def test_statistical_seed_schema_owns_the_approved_value() -> None:
    schema = json.loads(OWNER.read_text())
    Draft202012Validator.check_schema(schema)

    assert schema["const"] == _APPROVED_POLICY_SEED
    assert type(schema["const"]) is int
    assert schema["type"] == "integer"
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "urn:underwrite:contract:statistical_seed.v1"
    validate(schema["const"], schema)
    validate(float(schema["const"]), schema)  # JSON-number integer semantics.
    invalid_values: tuple[object, ...] = (True, False, None, "42", schema["const"] + 1, [])
    for invalid in invalid_values:
        with pytest.raises(ValidationError):
            validate(invalid, schema)


def test_present_future_read_contract_references_seed_owner() -> None:
    owner = json.loads(OWNER.read_text())
    read = ROOT / "contracts/read.v1.schema.json"
    if read.exists():
        seed = json.loads(read.read_text())["properties"]["seed"]
        assert seed == {"$ref": owner["$id"]}


def test_implemented_stochastic_evidence_has_explicit_local_rngs() -> None:
    paths = sorted(EVIDENCE.glob("*.py"))
    assert paths
    errors = {path.name: lines for path in paths if (lines := seed_violations(path.read_text()))}
    assert errors == {}


@pytest.mark.parametrize(
    "source",
    [
        "import random\ndef sample(seed):\n return random.Random(seed)",
        "import random as r\ndef sample(policy):\n return r.Random(policy.seed)",
        "from random import Random as R\ndef sample(seed):\n return R(seed)",
        "from random import Random as R\ndef sample(s, /):\n return R(s)",
        "from random import Random as R\ndef sample(*, s):\n return R(s)",
        "from random import Random as R\ndef sample(unrelated=42, *, s):\n return R(s)",
        "DATA = {'seed': 42}\ndef sample(seed):\n return seed",
    ],
)
def test_seed_guard_accepts_explicit_policy_and_unrelated_data(source: str) -> None:
    assert seed_violations(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "import random\ndef sample(seed):\n return random.Random()",
        "import random\ndef sample(seed):\n return random.Random(42)",
        "import random\ndef sample(seed=13):\n return random.Random(seed)",
        "from random import Random\ndef sample(*, seed=None):\n return Random(seed)",
        "class Policy:\n seed: int = 42",
        "import random\ndef sample(seed):\n return random.random()",
        "from random import randint\ndef sample(seed):\n return randint(0, 1)",
        "import random\nshared = random.Random(seed)",
        "from random import Random as R\ndef sample(s=42):\n return R(s)",
        "from random import Random as R\ndef sample(s=42, /):\n return R(s)",
        "from random import Random as R\ndef sample(*, s=42):\n return R(s)",
        "import random\ndef sample(policy=DEFAULT_POLICY):\n return random.Random(policy.seed)",
        "import random\ndef sample(*, policy=DEFAULT_POLICY):\n return random.Random(policy.seed)",
        "import random\ndef sample(policy):\n return random.Random(policy.inner.seed)",
        "import random\ndef sample(policy):\n"
        " policy.inner.seed = 42\n return random.Random(policy.inner.seed)",
    ],
)
def test_seed_guard_catches_missing_global_and_copied_seed_policy(source: str) -> None:
    assert seed_violations(source)

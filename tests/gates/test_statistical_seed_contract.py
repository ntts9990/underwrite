"""Stochastic evidence uses caller-supplied seeds and local RNG instances."""

from __future__ import annotations

from pathlib import Path

import pytest
from _random_policy import seed_violations
from _repo_paths import repo_root

ROOT = repo_root(Path(__file__).resolve())
EVIDENCE = ROOT / "src/underwrite/instrument/evidence"


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

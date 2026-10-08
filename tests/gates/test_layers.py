"""Standalone behavior and boundary checks."""

import ast
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from _random_policy import seed_violations

REPO_ROOT = Path(__file__).resolve().parents[2]
IMPORTLINTER_CONFIG = REPO_ROOT / ".importlinter"
INIT_CHECK_SCRIPT = REPO_ROOT / "scripts" / "init_check.py"
EXEMPTIONS_PATH = REPO_ROOT / "rules" / "exemptions.json"
PURE_LAYER_EXEMPTION_KEY = "pure-layer-not-populated"

_STDLIB = set(sys.stdlib_module_names)


# agent-assurance-app-plan.md §3.1). Each app package may depend on stdlib,
# underwrite_core, and its pure siblings, but never on the shell layer.
_CORE_DIR = REPO_ROOT / "packages" / "underwrite-core" / "src" / "underwrite_core"
_PURE_APP_DIRS = {
    "underwrite.instrument.evidence": REPO_ROOT / "src/underwrite/instrument/evidence",
    "underwrite.measurement": REPO_ROOT / "src/underwrite/measurement",
    "underwrite.acceptance": REPO_ROOT / "src/underwrite/acceptance",
}
_PURE_APP_PREFIXES = tuple(_PURE_APP_DIRS.keys())
_PURE_APP_ALLOWED_PREFIXES = ("underwrite_core", *_PURE_APP_PREFIXES)

_CORE_PURITY_TEST_PATH = REPO_ROOT / "packages" / "underwrite-core" / "tests" / "test_purity.py"
# Attribute access: datetime.now/.today, date.today, time.time/.monotonic,
# os.environ. Bare names: open(...), socket, subprocess, random. Identical to
# packages/underwrite-core/tests/test_purity.py's sets of the same name --
# test_purity_forbidden_sets_match_core_purity_gate below binds the two.
_FORBIDDEN_ATTRS = {"now", "today", "time", "monotonic", "environ"}
_FORBIDDEN_NAMES = {"open", "socket", "subprocess", "random"}


def _count_real_modules(directory: Path) -> int:
    """Standalone behavior and boundary checks."""
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.rglob("*.py") if path.name != "__init__.py")


def _population_gate(count: int, *, now: str) -> None:
    assert count > 0


def _module_is_allowed(module_name: str, allowed_prefixes: tuple[str, ...]) -> bool:
    """A module is allowed if it's stdlib, or exactly/under one of `allowed_prefixes`."""
    if module_name.split(".")[0] in _STDLIB:
        return True
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in allowed_prefixes
    )


def find_disallowed_imports(directory: Path, allowed_prefixes: tuple[str, ...]) -> list[str]:
    """Return one message per disallowed top-level import found under `directory`."""
    violations: list[str] = []
    for source_path in sorted(directory.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not _module_is_allowed(alias.name, allowed_prefixes):
                        violations.append(f"{source_path}: disallowed import {alias.name!r}")
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0 or node.module is None:
                    continue
                if not _module_is_allowed(node.module, allowed_prefixes):
                    violations.append(f"{source_path}: disallowed import {node.module!r}")
    return violations


def test_lint_imports_passes() -> None:
    result = subprocess.run(
        ["uv", "run", "--frozen", "--no-sync", "lint-imports"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_importlinter_config_declares_no_import_exceptions() -> None:
    text = IMPORTLINTER_CONFIG.read_text(encoding="utf-8")
    assert "ignore_imports" not in text


def test_core_package_has_no_third_party_imports() -> None:
    violations = find_disallowed_imports(_CORE_DIR, allowed_prefixes=("underwrite_core",))
    assert violations == []


@pytest.mark.parametrize("prefix", sorted(_PURE_APP_PREFIXES))
def test_pure_app_package_has_no_third_party_imports(prefix: str) -> None:
    directory = _PURE_APP_DIRS[prefix]
    _population_gate(_count_real_modules(directory), now=date.today().isoformat())
    violations = find_disallowed_imports(directory, _PURE_APP_ALLOWED_PREFIXES)
    assert violations == []


def find_forbidden_wallclock_io_usage(directory: Path) -> list[str]:
    """Standalone behavior and boundary checks."""
    violations: list[str] = []
    for source_path in sorted(directory.rglob("*.py")):
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ATTRS:
                violations.append(f"{source_path}: found .{node.attr}")
            elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES - {"random"}:
                violations.append(f"{source_path}: found name {node.id}")
        violations.extend(
            f"{source_path}:{line}: invalid RNG/seed policy" for line in seed_violations(source)
        )
    return violations


@pytest.mark.parametrize("prefix", sorted(_PURE_APP_PREFIXES))
def test_pure_app_package_avoids_wallclock_and_io(prefix: str) -> None:
    directory = _PURE_APP_DIRS[prefix]
    _population_gate(_count_real_modules(directory), now=date.today().isoformat())
    violations = find_forbidden_wallclock_io_usage(directory)
    assert violations == []


def test_purity_forbidden_sets_match_core_purity_gate() -> None:
    """`_FORBIDDEN_ATTRS`/`_FORBIDDEN_NAMES` above must stay identical to
    packages/underwrite-core/tests/test_purity.py's sets of the same name. Read via
    AST rather than imported (that module lives in a sibling package's tests/ tree
    with no stable cross-package import path from here), so a drift in either copy
    is caught here instead of silently diverging."""
    tree = ast.parse(
        _CORE_PURITY_TEST_PATH.read_text(encoding="utf-8"),
        filename=str(_CORE_PURITY_TEST_PATH),
    )
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"_FORBIDDEN_ATTRS", "_FORBIDDEN_NAMES"}
        ):
            found[node.targets[0].id] = ast.literal_eval(node.value)

    assert found.get("_FORBIDDEN_ATTRS") == _FORBIDDEN_ATTRS
    assert found.get("_FORBIDDEN_NAMES") == _FORBIDDEN_NAMES


def test_forbidden_wallclock_usage_is_detected_in_temp_tree(tmp_path: Path) -> None:
    """Red-injection: a temp pure module using `datetime.now()` and one using
    `open(...)` must both be flagged."""
    (tmp_path / "_clock_offender.py").write_text(
        "import datetime\n\n\ndef f() -> None:\n    datetime.datetime.now()\n",
        encoding="utf-8",
    )
    (tmp_path / "_io_offender.py").write_text(
        "def f() -> None:\n    open('x')\n",
        encoding="utf-8",
    )

    violations = find_forbidden_wallclock_io_usage(tmp_path)

    assert any("_clock_offender.py" in message and ".now" in message for message in violations)
    assert any("_io_offender.py" in message and "name open" in message for message in violations)


@pytest.mark.parametrize(
    "source",
    [
        "import random\ndef sample(seed):\n rng = random.Random(seed)\n return rng.random()",
        "import random as r\ndef sample(policy):\n"
        " rng = r.Random(policy.seed)\n return rng.shuffle([])",
        "from random import Random as R\ndef sample(seed):\n"
        " rng = R(seed)\n return rng.getrandbits(1)",
        "import random\nclass State:\n rng: random.Random\n"
        "def sample(rng: random.Random) -> random.Random:\n return rng",
        "from random import Random as R\n"
        "def sample(rng: R | None = None) -> R | None:\n return rng",
    ],
)
def test_app_rng_policy_accepts_explicit_local_rngs_and_type_annotations(
    tmp_path: Path, source: str
) -> None:
    (tmp_path / "seeded.py").write_text(source, encoding="utf-8")
    assert find_forbidden_wallclock_io_usage(tmp_path) == []


@pytest.mark.parametrize(
    "source",
    [
        "import random as r\ndef sample(seed):\n return r.Random()",
        "from random import Random as R\ndef sample(seed):\n return R(None)",
        "from random import Random as R\ndef sample(seed):\n return R(42)",
        "import random as r\nshared = r.Random(seed)",
        "from random import Random as R\nclass State:\n rng = R(seed)",
        "from random import Random as R\ndef sample(rng=R(seed)):\n return rng",
        "from random import Random as R\n@decorate(R(seed))\ndef sample(seed):\n return seed",
        "import random as r\ndef sample(seed):\n return r.random()",
        "from random import choice\ndef sample(seed):\n return choice([1])",
        "import random as r\nfactory = r.Random\ndef sample(seed):\n return factory(seed)",
        "from random import Random as R\ndef sample(seed):\n factory = R\n return factory(seed)",
        "import random as r\ndef sample(seed):\n return r",
        "from random import Random as R\ndef sample(seed):\n return R",
        "import random as r\ndef sample(seed):\n alias = r\n return alias.Random(seed)",
        "import random as r\ndef sample(seed):\n return getattr(r, 'Random')(seed)",
        "from random import Random as R\ndef sample(seed):\n return R(int(42))",
        "from random import Random as R\nSEED = 42\ndef sample():\n return R(SEED)",
        "from random import Random as R\ndef sample(seed):\n seed = 42\n return R(seed)",
        "from random import Random as R\ndef sample(policy):\n"
        " policy.seed = 42\n return R(policy.seed)",
        "from random import Random as R\ndef sample(seed):\n global rng\n rng = R(seed)",
        "from random import Random as R\ndef outer():\n rng = None\n"
        " def sample(seed):\n  nonlocal rng\n  rng = R(seed)\n return sample",
        "from random import Random as R\ndef sample(seed):\n rng = R(seed)\n rng.seed()",
        "from random import Random as R\ndef sample(seed):\n return R(seed, x=seed)",
    ],
)
def test_app_rng_policy_rejects_unseeded_global_and_unsupported_escapes(
    tmp_path: Path, source: str
) -> None:
    (tmp_path / "unsafe_rng.py").write_text(source, encoding="utf-8")
    assert find_forbidden_wallclock_io_usage(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "def sample(seed):\n return open('x')",
        "import socket\ndef sample(seed):\n return socket.socket()",
        "import subprocess\ndef sample(seed):\n return subprocess.run(['x'])",
        "import datetime\ndef sample(seed):\n return datetime.datetime.now()",
        "import datetime\ndef sample(seed):\n return datetime.date.today()",
        "import time\ndef sample(seed):\n return time.time()",
        "import time\ndef sample(seed):\n return time.monotonic()",
        "import os\ndef sample(seed):\n return os.environ['X']",
    ],
)
def test_app_rng_exception_does_not_change_other_purity_bans(tmp_path: Path, source: str) -> None:
    (tmp_path / "still_forbidden.py").write_text(source, encoding="utf-8")
    assert find_forbidden_wallclock_io_usage(tmp_path)


def test_importlinter_pure_layer_contract_has_a_subject() -> None:
    """Standalone behavior and boundary checks."""
    population = sum(_count_real_modules(directory) for directory in _PURE_APP_DIRS.values())
    _population_gate(population, now=date.today().isoformat())

    contract_text = IMPORTLINTER_CONFIG.read_text(encoding="utf-8")
    contract_start = contract_text.index("[importlinter:contract:pure-layer-stdlib-and-core-only]")
    next_contract = contract_text.find("\n[importlinter:contract:", contract_start + 1)
    contract_block = contract_text[contract_start : next_contract if next_contract != -1 else None]
    for prefix in _PURE_APP_PREFIXES:
        assert prefix in contract_block, f"{prefix} missing from the contract's source_modules"


def test_disallowed_import_is_detected_in_temp_copy(tmp_path: Path) -> None:
    copy_dir = tmp_path / "measurement"
    shutil.copytree(_PURE_APP_DIRS["underwrite.measurement"], copy_dir)
    (copy_dir / "_offender.py").write_text("import psycopg\n", encoding="utf-8")

    violations = find_disallowed_imports(copy_dir, _PURE_APP_ALLOWED_PREFIXES)

    assert any("psycopg" in message for message in violations)

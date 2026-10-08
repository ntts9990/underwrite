"""Shared loader for packages/underwrite-core/build/verify.py, used by both
the drift and parity suites (test_verify_generated_drift.py,
test_verify_parity.py).

Loads the module by file path, as an external auditor running it standalone
would -- not as a package import. Registering it in `sys.modules` BEFORE
`exec_module` is required, not just customary: verify.py's dataclasses
(hoisted `from __future__ import annotations`) resolve their field type
strings via `sys.modules[cls.__module__]` at class-creation time and crash
otherwise.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def load_module_from_path(name: str, path: Path) -> Any:
    """Standalone behavior and boundary checks."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

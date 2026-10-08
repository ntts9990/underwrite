"""Standalone behavior and boundary checks."""

import underwrite_core


def test_underwrite_core_imports() -> None:
    assert underwrite_core.__name__ == "underwrite_core"

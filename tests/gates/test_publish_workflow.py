"""Publish selection remains opt-in and depends on the appropriate acceptance branch."""

from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).parents[2]


def test_app_only_default_requires_registry_acceptance_before_publication() -> None:
    # BaseLoader preserves GitHub's `on` key rather than YAML 1.1 Boolean coercion.
    workflow = cast(
        dict[str, Any],
        yaml.load((ROOT / ".github/workflows/publish.yml").read_text(), Loader=yaml.BaseLoader),
    )
    selection = workflow["on"]["workflow_dispatch"]["inputs"]["publish_core"]
    assert selection["type"] == "boolean" and selection["default"] == "false"
    build = workflow["jobs"]["build"]
    steps = cast(list[dict[str, Any]], build["steps"])
    registry = next(step for step in steps if "--registry-core" in step.get("run", ""))
    assert registry["if"] == "${{ !inputs.publish_core }}"
    assert "scripts/smoke_wheels.py --dist dist --registry-core" in registry["run"]
    upload = next(
        step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert steps.index(registry) < steps.index(upload)
    assert any("scripts/preflight.py --mode full" in step.get("run", "") for step in steps)
    assert any(
        step.get("run", "").endswith("scripts/smoke_wheels.py --dist dist") for step in steps
    )
    assert all(
        "continue-on-error" not in step and "always()" not in step.get("if", "") for step in steps
    )
    publish = workflow["jobs"]["publish"]
    assert publish["needs"] == "build"
    assert publish["if"] == "github.ref == 'refs/heads/main'"
    assert publish["environment"] == "pypi"
    assert publish["permissions"]["id-token"] == "write"
    publications = [step for step in publish["steps"] if "uv publish" in step.get("run", "")]
    core, app = publications
    assert core["if"] == "${{ inputs.publish_core }}"
    assert "dist/underwrite_core-*.whl" in core["run"]
    assert "if" not in app  # Normal success() means a selected core failure blocks the app.
    assert "dist/underwrite-*.whl" in app["run"]
    assert all("--check-url https://pypi.org/simple/" in step["run"] for step in publications)
    assert all("continue-on-error" not in step for step in publications)

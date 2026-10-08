"""Standalone behavior and boundary checks."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import Any

import pytest
from _repo_paths import repo_root

ROOT = repo_root(Path(__file__).resolve())
CORE = ROOT / "packages/underwrite-core"
LICENSES = ROOT / "LICENSES"
PROJECTS = (ROOT, CORE)
SPDX = "Apache-2.0"
# The Apache Software Foundation published LICENSE-2.0.txt, byte for byte.
APACHE_2_0_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
HOLDER = "ntts9990"
CONTACT = f"https://github.com/{HOLDER}"
SPDX_OPERATORS = frozenset({"AND", "OR", "WITH"})


def _strings(value: Any) -> list[str]:
    return [value] if isinstance(value, str) else [str(item) for item in value]


def _annotations() -> list[dict[str, Any]]:
    reuse = tomllib.loads((ROOT / "REUSE.toml").read_text(encoding="utf-8"))
    assert reuse["version"] == 1
    return reuse["annotations"]


def test_license_file_is_the_unmodified_apache_2_0_text() -> None:
    assert hashlib.sha256((ROOT / "LICENSE").read_bytes()).hexdigest() == APACHE_2_0_SHA256


@pytest.mark.parametrize("project", PROJECTS, ids=lambda p: p.name)
def test_package_declares_the_license_and_ships_its_files(project: Path) -> None:
    meta = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert meta["license"] == SPDX
    assert meta["license-files"] == ["LICENSE", "NOTICE"]
    for name in meta["license-files"]:
        assert (project / name).resolve() == (ROOT / name).resolve()


@pytest.mark.parametrize("name", ["LICENSE", "NOTICE"])
def test_core_links_the_root_files_instead_of_copying_them(name: str) -> None:
    link = CORE / name
    assert link.is_symlink()
    assert link.resolve() == (ROOT / name).resolve()


def test_public_policy_documents_exist() -> None:
    for document in ("LICENSING.md", "TRADEMARKS.md", "CONTRIBUTING.md", "SECURITY.md"):
        assert (ROOT / document).is_file()


def test_reuse_default_annotation_is_the_holder_under_apache() -> None:
    default, *narrower = _annotations()
    assert _strings(default["path"]) == ["**"]
    assert default["SPDX-License-Identifier"] == SPDX
    assert _strings(default["SPDX-FileCopyrightText"]) == [f"2026 {HOLDER} <{CONTACT}>"]
    assert all("**" not in _strings(annotation["path"]) for annotation in narrower)


def test_examples_use_the_owned_apache_default_and_only_cc_documents_override() -> None:
    default, *overrides = _annotations()
    assert default["SPDX-License-Identifier"] == SPDX
    assert any((ROOT / "fixtures/examples").rglob("*.json"))
    assert any((ROOT / "fixtures/golden/external").rglob("*.json"))
    assert {tuple(_strings(a["path"])): a["SPDX-License-Identifier"] for a in overrides} == {
        ("CODE_OF_CONDUCT.md",): "CC-BY-SA-4.0",
        ("TRADEMARKS.md",): "CC-BY-4.0",
    }


def test_licenses_dir_holds_exactly_the_texts_reuse_toml_uses() -> None:
    used = {
        token
        for annotation in _annotations()
        for expression in _strings(annotation["SPDX-License-Identifier"])
        for token in expression.replace("(", " ").replace(")", " ").split()
        if token not in SPDX_OPERATORS
    }
    assert SPDX in used
    assert sorted(path.name for path in LICENSES.iterdir()) == sorted(f"{i}.txt" for i in used)
    license_link = LICENSES / f"{SPDX}.txt"
    assert license_link.is_symlink()
    assert license_link.resolve() == (ROOT / "LICENSE").resolve()

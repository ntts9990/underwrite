"""Card: wheel-smoke | Paper: installed-artifact verification | Stage: release-quality

Isolate wheel acquisition, installation and package identity checks from CLI pilots.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tomllib
import urllib.parse
import urllib.request
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
COMMAND_TIMEOUT = 120
REGISTRY_INDEX = "https://pypi.org/simple/"
REGISTRY_MAX_BYTES = 16 * 1024 * 1024


class SmokeFailure(Exception):
    """A wheel or installed CLI did not satisfy the release smoke contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def wheels(dist: Path) -> tuple[Path, Path]:
    require(dist.is_dir(), f"distribution directory is missing: {dist}")
    pairs: list[Path] = []
    for pattern in ("underwrite_core-*.whl", "underwrite-*.whl"):
        found = sorted(dist.glob(pattern))
        require(len(found) == 1, f"expected one {pattern} in {dist}; found {len(found)}")
        pairs.append(found[0])
    return pairs[0], pairs[1]


def command(
    args: list[str],
    cwd: Path,
    *,
    project_environment: Path | None = None,
    registry_only: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if registry_only:
        env = {key: value for key, value in env.items() if not key.startswith(("UV_", "PIP_"))}
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(name, None)
    if project_environment is not None:
        env["UV_PROJECT_ENVIRONMENT"] = str(project_environment)
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise SmokeFailure(f"command timed out after {COMMAND_TIMEOUT}s: {args[0]}") from exc


def success(
    args: list[str],
    cwd: Path,
    *,
    project_environment: Path | None = None,
    registry_only: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = command(
        args, cwd, project_environment=project_environment, registry_only=registry_only
    )
    require(
        result.returncode == 0,
        f"command failed ({result.returncode}): {args[0]}: {result.stderr[:500]}",
    )
    return result


def prepare_wheel_environment(work: Path, venv: Path) -> None:
    success(
        [
            "uv",
            "sync",
            "--frozen",
            "--no-default-groups",
            "--no-install-workspace",
            "--project",
            str(ROOT),
            "--python",
            sys.executable,
        ],
        work,
        project_environment=venv,
    )


def install_app_from_local_dist(work: Path, venv: Path, wheels: tuple[Path, Path]) -> None:
    python = venv / "bin/python"
    success(
        [
            "uv",
            "pip",
            "install",
            "--offline",
            "--no-index",
            "--find-links",
            str(wheels[0].parent),
            "--python",
            str(python),
            str(wheels[1]),
        ],
        work,
        project_environment=venv,
    )
    success(["uv", "pip", "check", "--python", str(python)], work, project_environment=venv)


def install_wheels(work: Path, venv: Path, wheels: tuple[Path, Path]) -> None:
    prepare_wheel_environment(work, venv)
    python = venv / "bin/python"
    absent = command(
        [
            str(python),
            "-I",
            "-c",
            "from importlib.metadata import version; print(version('underwrite-core'))",
        ],
        work,
        project_environment=venv,
    )
    require(absent.returncode != 0, "core was installed before app dependency resolution")
    install_app_from_local_dist(work, venv, wheels)


class _NoRegistryRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise SmokeFailure("registry redirects are not accepted")


def registry_bytes(url: str) -> bytes:
    """Bound release-only registry reads; URLs are restricted to official PyPI hosts."""
    parsed = urllib.parse.urlsplit(url)
    require(
        parsed.scheme == "https" and parsed.netloc in {"pypi.org", "files.pythonhosted.org"},
        "unexpected registry artifact URL",
    )
    opener = urllib.request.build_opener(_NoRegistryRedirects())
    with opener.open(url, timeout=30) as response:
        raw = response.read(REGISTRY_MAX_BYTES + 1)
    require(len(raw) <= REGISTRY_MAX_BYTES, "registry artifact exceeded smoke byte limit")
    return raw


def published_core(work: Path, version: str) -> tuple[Path, dict[str, object]]:
    try:
        return published_core_artifact(work, version)
    except (KeyError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        raise SmokeFailure("invalid published core metadata or archive") from exc


def published_core_artifact(work: Path, version: str) -> tuple[Path, dict[str, object]]:
    url = "https://pypi.org/pypi/underwrite-core/" + urllib.parse.quote(version, safe="") + "/json"
    metadata = json.loads(registry_bytes(url))
    require(
        metadata["info"]["name"] == "underwrite-core" and metadata["info"]["version"] == version,
        "published core release metadata mismatch",
    )
    choices = [
        item
        for item in metadata["urls"]
        if item["packagetype"] == "bdist_wheel"
        and item["filename"] == f"underwrite_core-{version}-py3-none-any.whl"
    ]
    require(len(choices) == 1, "expected one published portable core wheel")
    selected = choices[0]
    raw = registry_bytes(selected["url"])
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == selected["digests"]["sha256"], "published core wheel hash mismatch")
    require(Path(selected["filename"]).name == selected["filename"], "invalid core wheel filename")
    wheel = work / selected["filename"]
    wheel.write_bytes(raw)
    with zipfile.ZipFile(wheel) as archive:
        name = f"underwrite_core-{version}.dist-info/METADATA"
        project = BytesParser().parsebytes(archive.read(name))
        require(
            project["Name"] == "underwrite-core" and project["Version"] == version,
            "published core wheel metadata mismatch",
        )
    return wheel, {"version": version, "url": selected["url"], "sha256": digest}


def install_registry_core(
    work: Path, venv: Path, app_wheel: Path
) -> tuple[Path, dict[str, object]]:
    """Use app dependency resolution in a fresh venv; no workspace or local core input."""
    success(
        ["uv", "--no-config", "--no-cache", "venv", "--python", sys.executable, str(venv)],
        work,
        registry_only=True,
    )
    python = venv / "bin/python"
    success(
        [
            "uv",
            "--no-config",
            "--no-cache",
            "pip",
            "install",
            "--no-sources",
            "--default-index",
            REGISTRY_INDEX,
            "--python",
            str(python),
            str(app_wheel),
        ],
        work,
        registry_only=True,
    )
    success(
        ["uv", "--no-config", "pip", "check", "--python", str(python)], work, registry_only=True
    )
    resolved = success(
        [
            str(python),
            "-I",
            "-c",
            "from importlib.metadata import version; print(version('underwrite-core'))",
        ],
        work,
        registry_only=True,
    )
    core, evidence = published_core(work, resolved.stdout.strip())
    check_wheel_origins(venv, (core, app_wheel))
    return core, evidence


def stale_core_upgrade(work: Path, wheels: tuple[Path, Path]) -> None:
    """Use a synthetic old-version surrogate, not published PyPI bytes."""
    build = tomllib.loads((ROOT / "packages/underwrite-core/pyproject.toml").read_text())[
        "build-system"
    ]
    source = work / "stale-core-source"
    package = source / "src/underwrite_core"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "canonical.py").write_text("", encoding="utf-8")
    (source / "pyproject.toml").write_text(
        '[project]\nname = "underwrite-core"\nversion = "0.0.1"\n'
        'requires-python = ">=3.12"\n'
        "[build-system]\n"
        f"requires = [{', '.join(json.dumps(requirement) for requirement in build['requires'])}]\n"
        f"build-backend = {json.dumps(build['build-backend'])}\n"
        '[tool.uv.build-backend]\nmodule-name = "underwrite_core"\nmodule-root = "src"\n',
        encoding="utf-8",
    )
    dist = work / "stale-core-dist"
    success(
        [
            "uv",
            "build",
            "--offline",
            "--wheel",
            "--no-sources",
            "--project",
            str(source),
            "--out-dir",
            str(dist),
        ],
        work,
    )
    old = list(dist.glob("underwrite_core-0.0.1-*.whl"))
    require(len(old) == 1, "stale-core surrogate wheel missing")
    venv = work / "stale-venv"
    prepare_wheel_environment(work, venv)
    python = venv / "bin/python"
    success(
        ["uv", "pip", "install", "--offline", "--no-deps", "--python", str(python), str(old[0])],
        work,
        project_environment=venv,
    )
    missing = command(
        [str(python), "-I", "-c", "from underwrite_core.canonical import safe_integer_value"],
        work,
    )
    require(missing.returncode != 0, "stale-core surrogate unexpectedly has the new API")
    install_app_from_local_dist(work, venv, wheels)
    check_wheel_origins(venv, wheels)
    success([str(venv / "bin/underwrite"), "--help"], work)


def check_wheel_origins(venv: Path, wheels: tuple[Path, Path]) -> None:
    """Check installed versions and package bytes, including a resolved core wheel."""
    site = (
        venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    )
    for distribution, wheel in zip(("underwrite_core", "underwrite"), wheels, strict=True):
        with zipfile.ZipFile(wheel) as archive:
            metadata_name = next(
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
            )
            metadata = BytesParser().parsebytes(archive.read(metadata_name))
            version = metadata["Version"]
            records = list(site.glob(f"{distribution}-*.dist-info"))
            require(
                len(records) == 1 and records[0].name == f"{distribution}-{version}.dist-info",
                f"installed {distribution} version differs from built wheel",
            )
            installed = BytesParser().parsebytes((records[0] / "METADATA").read_bytes())
            require(
                installed["Name"] == metadata["Name"]
                and installed["Version"] == version
                and installed.get_all("Requires-Dist") == metadata.get_all("Requires-Dist"),
                f"installed {distribution} metadata differs from built wheel",
            )
            members = [
                name
                for name in archive.namelist()
                if name.startswith(f"{distribution}/") and not name.endswith("/")
            ]
            require(bool(members), f"built {distribution} has no package members")
            for name in members:
                require(
                    (site / name).read_bytes() == archive.read(name),
                    f"installed {distribution} bytes differ from built wheel",
                )
        if distribution == "underwrite":
            origin: object = json.loads((records[0] / "direct_url.json").read_text())
            require(isinstance(origin, dict), "invalid underwrite origin record")
            record = cast(dict[str, object], origin)
            require(
                record.get("url") == wheel.as_uri() and "dir_info" not in record,
                "underwrite was not installed from its built wheel",
            )

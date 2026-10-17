"""Standalone behavior and boundary checks."""

from __future__ import annotations

import io
import stat
import subprocess
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

import pytest
from _repo_paths import repo_root

ROOT = repo_root(Path(__file__).resolve())
PACKAGED = Path("src/underwrite/_contracts")
OWNERS = Path("contracts")
WHEEL_DIR = "underwrite/_contracts/"


def population(root: Path) -> list[str]:
    return sorted(path.name for path in (root / PACKAGED).iterdir())


def check_source(root: Path, name: str) -> bytes:
    resource, owner = root / PACKAGED / name, root / OWNERS / name
    assert resource.is_symlink(), "RESOURCE_NOT_FILE_SYMLINK"
    assert resource.readlink() == Path("../../..") / OWNERS / name, "WRONG_RESOURCE_LINK_TARGET"
    assert not owner.is_symlink(), "CANONICAL_OWNER_NOT_AUTHORED_FILE"
    assert owner.is_file() and resource.is_file(), "RESOURCE_LINK_NOT_CANONICAL_FILE"
    assert resource.resolve() == owner.resolve(), "RESOURCE_LINK_NOT_CANONICAL_FILE"
    assert resource.read_bytes() == owner.read_bytes(), "RESOURCE_BYTES_DIFFER"
    return owner.read_bytes()


def check_build_config(root: Path) -> None:
    config = tomllib.loads((root / "pyproject.toml").read_text())
    assert config["build-system"]["build-backend"] == "uv_build"
    includes = config["tool"]["uv"]["build-backend"]["source-include"]
    owners = sorted(item for item in includes if item.startswith(f"{OWNERS}/"))
    assert owners == [f"{OWNERS}/{name}" for name in population(root)], "UNLINKED_OR_UNINCLUDED"


def check_wheel(path: Path, name: str, expected: bytes) -> None:
    with zipfile.ZipFile(path) as archive:
        matches = [item for item in archive.infolist() if item.filename == WHEEL_DIR + name]
        assert len(matches) == 1, "RESOURCE_MEMBER_POPULATION"
        assert stat.S_ISREG(matches[0].external_attr >> 16), "RESOURCE_NOT_REGULAR_FILE"
        assert archive.read(matches[0]) == expected, "RESOURCE_BYTES_DIFFER"


def check_sdist(path: Path, name: str, expected: bytes) -> None:
    with tarfile.open(path) as archive:
        for suffix in (f"{OWNERS}/{name}", f"{PACKAGED}/{name}"):
            matches = [item for item in archive.getmembers() if item.name.endswith("/" + suffix)]
            assert len(matches) == 1, "RESOURCE_MEMBER_POPULATION"
            assert matches[0].isfile(), "RESOURCE_NOT_REGULAR_FILE"
            stream = archive.extractfile(matches[0])
            assert stream is not None
            assert stream.read() == expected, "RESOURCE_BYTES_DIFFER"


@pytest.fixture(scope="session")
def distributions(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One workspace build shared by contract and distribution-content checks."""
    out = tmp_path_factory.mktemp("dist")
    result = subprocess.run(
        ["uv", "build", "--offline", "--all-packages", "--out-dir", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return out


@pytest.fixture(scope="session")
def built(distributions: Path) -> tuple[Path, Path]:
    wheels = list(distributions.glob("underwrite-*.whl"))
    sdists = list(distributions.glob("underwrite-*.tar.gz"))
    assert len(wheels) == len(sdists) == 1
    return wheels[0], sdists[0]


def check_shipping_members(shipped: set[str], allowed: set[str]) -> None:
    assert shipped <= allowed, f"UNEXPECTED_DISTRIBUTION_FILES: {sorted(shipped - allowed)}"


@pytest.mark.parametrize("project_root", [Path("."), Path("packages/underwrite-core")])
def test_distributions_ship_only_runtime_build_and_legal_files(
    distributions: Path, project_root: Path
) -> None:
    root = ROOT / project_root
    config = tomllib.loads((root / "pyproject.toml").read_text())
    project = config["project"]
    backend = config["tool"]["uv"]["build-backend"]
    module = backend["module-name"]
    source = root / backend["module-root"]
    runtime = {path.relative_to(source).as_posix() for path in (source / module).rglob("*.py")}
    typed = f"{module}/py.typed"
    if (source / typed).is_file():
        runtime.add(typed)
    schemas = population(ROOT) if module == "underwrite" else []
    runtime.update(f"{module}/_contracts/{name}" for name in schemas)
    archive_name = f"{project['name'].replace('-', '_')}-{project['version']}"
    metadata = f"{archive_name}.dist-info"
    legal = set(project["license-files"])
    wheel_files = (
        runtime
        | {f"{metadata}/{name}" for name in ("WHEEL", "METADATA", "RECORD")}
        | {f"{metadata}/licenses/{name}" for name in legal}
    )
    if project.get("scripts"):
        wheel_files.add(f"{metadata}/entry_points.txt")
    with zipfile.ZipFile(distributions / f"{archive_name}-py3-none-any.whl") as archive:
        check_shipping_members(
            {item.filename for item in archive.infolist() if not item.is_dir()}, wheel_files
        )
    sdist_files = (
        {f"{backend['module-root']}/{name}" for name in runtime}
        | legal
        | {"PKG-INFO", "pyproject.toml", "pyproject.toml.orig", project["readme"]}
        | {f"contracts/{name}" for name in schemas}
    )
    with tarfile.open(distributions / f"{archive_name}.tar.gz") as archive:
        check_shipping_members(
            {
                item.name.removeprefix(f"{archive_name}/")
                for item in archive.getmembers()
                if not item.isdir()
            },
            sdist_files,
        )


def test_population_is_the_build_includes() -> None:
    assert population(ROOT)
    check_build_config(ROOT)


@pytest.mark.parametrize("name", population(ROOT))
def test_packaged_contract_links_its_owner_and_ships_its_bytes(
    built: tuple[Path, Path], name: str
) -> None:
    expected = check_source(ROOT, name)
    check_wheel(built[0], name, expected)
    check_sdist(built[1], name, expected)


def test_wheel_ships_no_contract_outside_the_population(built: tuple[Path, Path]) -> None:
    with zipfile.ZipFile(built[0]) as archive:
        shipped = sorted(
            item.filename.removeprefix(WHEEL_DIR)
            for item in archive.infolist()
            if item.filename.startswith(WHEEL_DIR) and not item.is_dir()
        )
    assert shipped == population(ROOT)


def test_app_wheel_and_sdist_keep_authored_core_requirement(built: tuple[Path, Path]) -> None:
    app = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    requirement = next(
        dependency for dependency in app["dependencies"] if dependency.startswith("underwrite-core")
    )
    with zipfile.ZipFile(built[0]) as archive:
        metadata_file = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        wheel_metadata = BytesParser().parsebytes(archive.read(metadata_file))
    with tarfile.open(built[1]) as archive:
        member = next(
            item
            for item in archive.getmembers()
            if item.name.count("/") == 1 and item.name.endswith("/PKG-INFO")
        )
        stream = archive.extractfile(member)
        assert stream is not None
        sdist_metadata = BytesParser().parsebytes(stream.read())
    for metadata in (wheel_metadata, sdist_metadata):
        assert metadata["Version"] == app["version"]
        assert requirement in (metadata.get_all("Requires-Dist") or [])


def _tree(tmp_path: Path, names: tuple[str, ...], includes: tuple[str, ...]) -> Path:
    (tmp_path / OWNERS).mkdir()
    (tmp_path / PACKAGED).mkdir(parents=True)
    for name in names:
        (tmp_path / OWNERS / name).write_bytes(b'{"canonical":"owner"}')
        (tmp_path / PACKAGED / name).symlink_to(Path("../../..") / OWNERS / name)
    listed = ", ".join(f'"{item}"' for item in includes)
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nbuild-backend = "uv_build"\n'
        f"[tool.uv.build-backend]\nsource-include = [{listed}]\n"
    )
    return tmp_path


def test_config_guard_accepts_a_linked_included_population(tmp_path: Path) -> None:
    check_build_config(_tree(tmp_path, ("a.schema.json",), ("contracts/a.schema.json",)))


@pytest.mark.parametrize(
    "names,includes",
    [
        # A contracts/ schema the build includes but no _contracts link packages.
        (("a.schema.json",), ("contracts/a.schema.json", "contracts/b.schema.json")),
        # A packaged link whose owner the sdist would not carry.
        (("a.schema.json", "b.schema.json"), ("contracts/a.schema.json",)),
    ],
    ids=["included-not-linked", "linked-not-included"],
)
def test_config_guard_rejects_an_unpackaged_or_unincluded_schema(
    tmp_path: Path, names: tuple[str, ...], includes: tuple[str, ...]
) -> None:
    with pytest.raises(AssertionError, match="UNLINKED_OR_UNINCLUDED"):
        check_build_config(_tree(tmp_path, names, includes))


@pytest.mark.parametrize(
    "fault", ["missing", "flattened", "hand-copy", "wrong-same-bytes", "dangling"]
)
def test_source_guard_rejects_copies_and_wrong_targets_even_with_equal_bytes(
    tmp_path: Path, fault: str
) -> None:
    name = "a.schema.json"
    owner = tmp_path / OWNERS / name
    owner.parent.mkdir(parents=True)
    owner.write_bytes(b'{"canonical":"owner"}')
    resource = tmp_path / PACKAGED / name
    resource.parent.mkdir(parents=True)
    if fault == "flattened":
        resource.write_text(f"../../../contracts/{name}")
    elif fault == "hand-copy":
        resource.write_bytes(owner.read_bytes())
    elif fault == "wrong-same-bytes":
        owner.with_name("other.json").write_bytes(owner.read_bytes())
        resource.symlink_to("../../../contracts/other.json")
    elif fault == "dangling":
        resource.symlink_to(Path("../../..") / OWNERS / name)
        owner.unlink()
    with pytest.raises(AssertionError):
        check_source(tmp_path, name)


@pytest.mark.parametrize("fault", ["flattened", "symlink"])
def test_archive_guards_reject_link_text_and_link_members(tmp_path: Path, fault: str) -> None:
    name, expected = "a.schema.json", b'{"canonical":"owner"}'
    target = f"../../../contracts/{name}"
    body = target.encode() if fault == "flattened" else expected
    wheel = tmp_path / "negative.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        info = zipfile.ZipInfo(WHEEL_DIR + name)
        info.external_attr = ((stat.S_IFLNK if fault == "symlink" else stat.S_IFREG) | 0o644) << 16
        archive.writestr(info, body)
    with pytest.raises(AssertionError):
        check_wheel(wheel, name, expected)
    sdist = tmp_path / "negative.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        for path in (OWNERS / name, PACKAGED / name):
            member = tarfile.TarInfo(f"underwrite-test/{path}")
            member.size = len(body)
            if fault == "symlink":
                member.type = tarfile.SYMTYPE
                member.linkname = target
            archive.addfile(member, io.BytesIO(body))
    with pytest.raises(AssertionError):
        check_sdist(sdist, name, expected)


@pytest.mark.parametrize("count", [0, 2])
def test_archive_guard_requires_one_resource_member(tmp_path: Path, count: int) -> None:
    name, expected = "a.schema.json", b"{}"
    wheel = tmp_path / "population.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for index in range(count):
            info = zipfile.ZipInfo(WHEEL_DIR + name)
            info.external_attr = stat.S_IFREG << 16
            if index:
                with pytest.warns(UserWarning, match="Duplicate name"):
                    archive.writestr(info, expected)
            else:
                archive.writestr(info, expected)
    with pytest.raises(AssertionError, match="^RESOURCE_MEMBER_POPULATION"):
        check_wheel(wheel, name, expected)

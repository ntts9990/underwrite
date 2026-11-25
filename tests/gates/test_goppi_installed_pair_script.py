"""Adversarial boundaries for the optional installed-pair acceptance script."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from _script_loader import load_module_from_path

ROOT = Path(__file__).resolve().parents[2]
check = cast(
    Any,
    load_module_from_path(
        "check_goppi_installed_pair", ROOT / "scripts/check_goppi_installed_pair.py"
    ),
)


def test_artifact_snapshots_survive_source_replacement(tmp_path: Path) -> None:
    inputs: dict[str, Path] = {}
    originals: dict[str, bytes] = {}
    for index, role in enumerate(check.ARTIFACT_ROLES):
        source = tmp_path / f"source-{index}"
        originals[role] = f"original-{role}".encode()
        source.write_bytes(originals[role])
        inputs[role] = source

    snapshots, identities = check.snapshot_artifacts(inputs, tmp_path / "private")
    for role, source in inputs.items():
        replacement = source.with_suffix(".replacement")
        replacement.write_bytes(b"replacement")
        os.replace(replacement, source)
        assert snapshots[role].read_bytes() == originals[role]
        assert identities[role]["sha256"] == check.digest(snapshots[role])


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("flood", "PROCESS_OUTPUT_LIMIT"), ("timeout", "PROCESS_TIMEOUT")],
)
def test_process_failure_kills_descendants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected: str,
) -> None:
    marker = tmp_path / "escaped"
    child = (
        f"import pathlib,time;time.sleep(.5);pathlib.Path({str(marker)!r}).write_text('escaped')"
    )
    action = "import os;os.write(1,b'x'*4096);os.write(2,b'y'*4096)" if mode == "flood" else "pass"
    parent = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}]);"
        f"{action};time.sleep(10)"
    )
    monkeypatch.setattr(check, "STDOUT_MAX_BYTES", 64 if mode == "flood" else 4096)
    monkeypatch.setattr(check, "STDERR_MAX_BYTES", 64 if mode == "flood" else 4096)
    monkeypatch.setattr(check, "DEADLINE_SECONDS", 0.1 if mode == "timeout" else 2.0)

    with pytest.raises(check.Failure) as caught:
        check.run([sys.executable, "-c", parent], tmp_path)
    assert caught.value.reason == expected
    time.sleep(0.7)
    assert not marker.exists()


def test_runtime_environment_does_not_forward_provider_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "private")
    node = tmp_path / "artifacts/node"
    node.parent.mkdir()
    node.touch()
    env = check.isolated_env(tmp_path, node)

    assert "OPENAI_API_KEY" not in env
    assert env["UV_PYTHON_DOWNLOADS"] == "never"
    assert env["NPM_CONFIG_USERCONFIG"].startswith(str(tmp_path))
    assert env["UV_CACHE_DIR"].startswith(str(tmp_path))

    fake = tmp_path / "fake-goppi.py"
    fake.write_text(
        "import json,os;print(json.dumps({'credential':os.getenv('OPENAI_API_KEY')}))",
        encoding="utf-8",
    )
    result = check.native(Path(sys.executable), fake, tmp_path, tmp_path, env)
    assert json.loads(result.stdout) == {"credential": None}


def _positive_review() -> dict[str, Any]:
    stable = {
        "difference": {"outcome_changed": True},
        "baseline": {
            "outcome": {"status": "observed", "scored_value": False},
            "tool_status_counts": [{"tool_name": "read", "status": "error"}],
        },
        "candidate": {
            "outcome": {"status": "observed", "scored_value": True},
            "tool_status_counts": [{"tool_name": "read", "status": "ok"}],
        },
    }
    return {
        "schema": check.CONTRACTS["underwrite_review"],
        "state": "analyzed",
        "trust_scope": dict(check.REVIEW_TRUST),
        "cycle": dict(check.REVIEW_CYCLE),
        "authority": dict(check.REVIEW_AUTHORITY),
        "populations": dict(check.POPULATIONS),
        "comparisons": [copy.deepcopy(stable) for _ in range(72)],
    }


@pytest.mark.parametrize("mutation", ["population", "stable_change", "undisclosed_cycle"])
def test_positive_review_rejects_population_or_semantic_drift(mutation: str) -> None:
    review = _positive_review()
    check.validate_review(review)
    if mutation == "population":
        review["populations"]["paired_rows"] = 143
    elif mutation == "undisclosed_cycle":
        review.pop("cycle")
    else:
        for comparison in review["comparisons"]:
            comparison["difference"]["outcome_changed"] = False
    with pytest.raises(check.Failure, match="UNDERWRITE_POSITIVE_CONTRACT_DRIFT"):
        check.validate_review(review)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("native_verification", "local_goppi_same_byte_snapshot"),
        ("verifier_identity_verified", True),
        ("content_closure", "self_consistent_content_closure"),
    ],
)
def test_positive_review_rejects_native_or_elevated_underwrite_trust(
    field: str, value: object
) -> None:
    review = _positive_review()
    review["trust_scope"][field] = value
    with pytest.raises(check.Failure, match="UNDERWRITE_POSITIVE_CONTRACT_DRIFT"):
        check.validate_review(review)


def test_native_missing_trajectory_reason_is_exact() -> None:
    native = subprocess.CompletedProcess(
        [],
        3,
        json.dumps({"data": {"storage_code": "WRONG_REASON"}}).encode(),
        b"",
    )
    audit = subprocess.CompletedProcess(
        [],
        2,
        b"",
        json.dumps({"reason": "TRAJECTORY_COMPLETE_HANDOFF_REQUIRED"}).encode(),
    )
    with pytest.raises(check.Failure, match="NATIVE_MISSING_TRAJECTORY_REASON_DRIFT"):
        check.validate_missing(native, audit)


V2_PRE_VERIFIER_REFUSAL_NAMES = {
    "malformed-assignment-commitment",
    "malformed-export-build",
    "missing-execution-binding",
    "missing-export-build",
    "not-run-stage-build",
    "not-run-stage-rows",
    "runner-default-level-mismatch",
    "split-salt-limit-mismatch",
    "unknown-classification",
    "unknown-not-run-reason",
}
V2_AFTER_VERIFIER_REFUSAL_NAMES = {
    "missing-trajectory-binding",
    "runner-model-mismatch",
    "trajectory-assignment-mismatch",
}


def test_v2_refusal_expectations_pin_exact_fixture_set_and_public_errors() -> None:
    fixture_root = ROOT / "fixtures/conformance/goppi-execution-v2/paired-exchange-v2-rejections"
    fixture_names = {
        path.name for path in fixture_root.iterdir() if path.is_dir()
    }
    expected_names = V2_PRE_VERIFIER_REFUSAL_NAMES | V2_AFTER_VERIFIER_REFUSAL_NAMES
    assert fixture_names == expected_names == set(check.V2_REFUSAL_EXPECTATIONS)
    assert len(fixture_names) == check.V2_REFUSAL_CASES
    check.validate_v2_refusal_case_names(sorted(expected_names))
    assert check.V2_PRE_VERIFIER_REFUSAL == check.V2RefusalExpectation(
        phase="pre_verifier",
        code="INPUT_BINDING_MISMATCH",
        reason="GOPPI_VERIFIER_BINDING_MISMATCH",
        location="/trajectory/manifest.json",
        exit_code=2,
        next_action="CORRECT_INPUT_BINDING",
        retryable=False,
    )
    assert check.V2_AFTER_VERIFIER_REFUSAL == check.V2RefusalExpectation(
        phase="after_verifier",
        code="INVALID_INPUT",
        reason="GOPPI_VERIFIER_REFUSED",
        location="/exchange",
        exit_code=2,
        next_action="CORRECT_INPUT",
        retryable=False,
    )
    assert {
        name
        for name, expected in check.V2_REFUSAL_EXPECTATIONS.items()
        if expected.phase == "pre_verifier"
    } == V2_PRE_VERIFIER_REFUSAL_NAMES
    assert {
        name
        for name, expected in check.V2_REFUSAL_EXPECTATIONS.items()
        if expected.phase == "after_verifier"
    } == V2_AFTER_VERIFIER_REFUSAL_NAMES
    assert all(
        check.V2_REFUSAL_EXPECTATIONS[name] == check.V2_PRE_VERIFIER_REFUSAL
        for name in V2_PRE_VERIFIER_REFUSAL_NAMES
    )
    assert all(
        check.V2_REFUSAL_EXPECTATIONS[name] == check.V2_AFTER_VERIFIER_REFUSAL
        for name in V2_AFTER_VERIFIER_REFUSAL_NAMES
    )


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate"])
def test_v2_refusal_case_set_rejects_fixture_drift(mutation: str) -> None:
    names = sorted(check.V2_REFUSAL_EXPECTATIONS)
    if mutation == "missing":
        names.pop()
    elif mutation == "extra":
        names.append("unrecognized-refusal")
    else:
        names.append(names[0])
    with pytest.raises(check.Failure, match="FIXTURE_V2_REFUSAL_SET_DRIFT"):
        check.validate_v2_refusal_case_names(names)


@pytest.mark.parametrize("case_name", sorted(check.V2_REFUSAL_EXPECTATIONS))
def test_v2_underwrite_refusal_validates_each_case_contract(case_name: str) -> None:
    expected = check.V2_REFUSAL_EXPECTATIONS[case_name]
    payload = {
        "schema": "evidence_error.v1",
        "code": expected.code,
        "reason": expected.reason,
        "location": expected.location,
        "exit_code": expected.exit_code,
        "next_action": expected.next_action,
        "retryable": expected.retryable,
    }
    result = subprocess.CompletedProcess(
        [], expected.exit_code, b"", json.dumps(payload).encode()
    )
    assert check.validate_v2_underwrite_refusal(result, case_name) == expected.phase


@pytest.mark.parametrize(
    ("case_name", "expected_phase"),
    [
        ("malformed-assignment-commitment", "pre_verifier"),
        ("missing-trajectory-binding", "after_verifier"),
    ],
)
def test_v2_refusal_report_marks_phase_as_inferred(
    case_name: str, expected_phase: Literal["pre_verifier", "after_verifier"]
) -> None:
    record = check.v2_refusal_check_record(case_name, expected_phase)
    assert record == {
        "name": "refusal_v2:" + case_name,
        "expected_phase": expected_phase,
        "phase_basis": "inferred_from_exact_public_error_contract",
        "status": "passed",
    }
    assert "phase" not in record


@pytest.mark.parametrize(
    "case_name", ["malformed-assignment-commitment", "missing-trajectory-binding"]
)
@pytest.mark.parametrize("field", ["code", "reason", "location", "exit_code", "next_action"])
def test_v2_underwrite_refusal_rejects_each_public_error_field_drift(
    case_name: str, field: str
) -> None:
    expected = check.V2_REFUSAL_EXPECTATIONS[case_name]
    payload = {
        "schema": "evidence_error.v1",
        "code": expected.code,
        "reason": expected.reason,
        "location": expected.location,
        "exit_code": expected.exit_code,
        "next_action": expected.next_action,
        "retryable": expected.retryable,
    }
    result_exit_code = expected.exit_code
    if field == "exit_code":
        payload[field] = expected.exit_code + 1
        result_exit_code += 1
    else:
        payload[field] = "DRIFT"
    result = subprocess.CompletedProcess(
        [], result_exit_code, b"", json.dumps(payload).encode()
    )
    with pytest.raises(check.Failure, match="UNDERWRITE_V2_REFUSAL_DRIFT"):
        check.validate_v2_underwrite_refusal(result, case_name)


@pytest.mark.parametrize("field", ["schema", "status", "code", "exit_code", "storage_code"])
def test_v2_native_refusal_requires_exact_command_and_storage_error(field: str) -> None:
    payload: dict[str, Any] = {
        "schema": "goppi.command_result.v1",
        "status": "refused",
        "code": "COMMAND_FAILED",
        "exit_code": 3,
        "data": {"storage_code": "PAIRED_VERIFY_EXCHANGE_INVALID"},
    }
    valid = subprocess.CompletedProcess([], 3, json.dumps(payload).encode(), b"")
    check.validate_v2_native_refusal(valid, "malformed-assignment-commitment")
    result_exit_code = 3
    if field == "storage_code":
        payload["data"][field] = "DRIFT"
    elif field == "exit_code":
        payload[field] = 4
        result_exit_code = 4
    else:
        payload[field] = "DRIFT"
    result = subprocess.CompletedProcess(
        [], result_exit_code, json.dumps(payload).encode(), b""
    )
    with pytest.raises(check.Failure, match="NATIVE_V2_REFUSAL_DRIFT"):
        check.validate_v2_native_refusal(result, "malformed-assignment-commitment")


@pytest.mark.parametrize(
    "mutation",
    [
        "contract",
        "npm_resolution",
        "python_resolution",
        "goppi_package",
        "unexpected_tarball",
        "bytes_not_matched",
            "malformed_digest",
            "extra_field",
            "package_tree",
    ],
)
def test_success_summary_requires_contract_and_resolution_identities(mutation: str) -> None:
    tarball = "sha256:" + "c" * 64
    tree_files = {"package.json": (2, "a" * 64), "dist/cli.mjs": (2, "b" * 64)}
    tree_hash = check._dir_hash(tree_files)
    file_records = [
        {"path": name, "bytes": size, "sha256": "sha256:" + digest}
        for name, (size, digest) in sorted(tree_files.items())
    ]
    summary: dict[str, Any] = {
        "artifacts": {"goppi_tarball": {"bytes": 1, "sha256": tarball}},
        "contracts": dict(check.CONTRACTS),
        "trust_limits": dict(check.TRUST_LIMITS),
        "npm_resolution": {"package_count": 1, "sha256": "sha256:" + "a" * 64},
        "python_resolution": {"package_count": 1, "sha256": "sha256:" + "b" * 64},
        "goppi_package": {
            "tarball_sha256_expected": tarball,
            "manifest_sha256": "sha256:" + "d" * 64,
            "cli_target": "dist/cli.js",
            "cli_sha256": "sha256:" + "e" * 64,
            "installed_bytes_match_tarball": True,
        },
        "package_tree": {
            "algorithm": "goppi_dirHash_sha256_summary_v1",
            "export_build_dir_hash": "sha256:" + "f" * 64,
            "tarball": {
                "dir_hash": tree_hash,
                "matches_export_build_digest": False,
                "files": file_records,
            },
            "installed": {
                "dir_hash": tree_hash,
                "matches_export_build_digest": False,
                "files": copy.deepcopy(file_records),
            },
            "tarball_matches_installed_tree": True,
        },
    }
    check.validate_success_summary(summary)
    if mutation == "contract":
        summary["contracts"]["native_verification"] = "wrong"
    elif mutation == "unexpected_tarball":
        summary["artifacts"]["goppi_tarball"]["sha256"] = "sha256:" + "f" * 64
    elif mutation == "bytes_not_matched":
        summary["goppi_package"]["installed_bytes_match_tarball"] = False
    elif mutation == "malformed_digest":
        summary["goppi_package"]["cli_sha256"] = "e" * 64
    elif mutation == "extra_field":
        summary["goppi_package"]["package_name"] = "example-producer"
    elif mutation == "package_tree":
        summary["package_tree"]["tarball_matches_installed_tree"] = False
    else:
        del summary[mutation]
    with pytest.raises(check.Failure):
        check.validate_success_summary(summary)


def test_failure_summary_is_structured_and_path_free(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "private-artifact"
    code = check.main(
        [
            "--goppi-tarball",
            str(missing),
            "--underwrite-wheel",
            str(missing),
            "--underwrite-core-wheel",
            str(missing),
            "--node",
            str(missing),
            "--goppi-tarball-sha256",
            "sha256:" + "0" * 64,
        ]
    )
    streams = capsys.readouterr()
    report = json.loads(streams.err)

    assert code == 1 and streams.out == ""
    assert report["failure"] == {
        "phase": "snapshot",
        "reason": "INPUT_ARTIFACT_UNAVAILABLE",
        "location": "/goppi_tarball",
        "next_action": "CORRECT_ARGUMENTS",
    }
    assert str(tmp_path) not in streams.err


CLI = b"#!/usr/bin/env node\nconsole.log('synthetic producer');\n"
Members = list[tuple[str, bytes | None]]


SYMLINK, HARDLINK = b"\x00symlink:", b"\x00hardlink:"


def _tarball(path: Path, members: Members) -> Path:
    """Write an npm-pack-shaped archive; None is a directory, a link prefix names a target."""
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            elif data.startswith((SYMLINK, HARDLINK)):
                info.type = tarfile.SYMTYPE if data.startswith(SYMLINK) else tarfile.LNKTYPE
                info.linkname = data.split(b":", 1)[1].decode()
                archive.addfile(info)
            else:
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return path


def test_pinned_dir_hash_vector_excludes_only_top_level_node_modules(tmp_path: Path) -> None:
    vector_path = ROOT / "fixtures/conformance/goppi-execution-v2/build-dir-hash/vector.json"
    vector = json.loads(vector_path.read_bytes())
    vector_files = {
        item["name"]: (len(item["content"].encode()), item["sha256"])
        for item in vector["files"]
    }
    assert check._dir_hash(vector_files) == vector["dir_hash"]

    manifest = _manifest(name="goppi", bin="dist/cli.js")
    members: Members = [
        ("package/package.json", manifest),
        ("package/dist/cli.js", CLI),
        ("package/dist/node_modules/bundled.mjs", b"nested dependency"),
        ("package/node_modules/ignored.mjs", b"top-level excluded"),
    ]
    tarball = _tarball(tmp_path / "goppi.tgz", members)
    package = check.package_cli(tarball)
    installed = tmp_path / "installed"
    (installed / "dist/node_modules").mkdir(parents=True)
    (installed / "node_modules").mkdir()
    (installed / "package.json").write_bytes(manifest)
    (installed / "dist/cli.js").write_bytes(CLI)
    (installed / "dist/node_modules/bundled.mjs").write_bytes(b"nested dependency")
    (installed / "node_modules/ignored.mjs").write_bytes(b"top-level excluded")

    compared = check.compare_package_trees(tarball, installed, package, "a" * 64)
    names = {item["path"] for item in compared["tarball"]["files"]}
    assert "node_modules/ignored.mjs" not in names
    assert "dist/node_modules/bundled.mjs" in names
    assert compared["tarball_matches_installed_tree"] is True
    assert compared["tarball"]["matches_export_build_digest"] is False
    assert compared["installed"]["dir_hash"] == compared["tarball"]["dir_hash"]


def _manifest(**fields: object) -> bytes:
    return json.dumps({"name": "example-producer", "version": "1.0.0", **fields}).encode()


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    ("name", "declared", "target"),
    [
        ("example-producer", {"example-producer": "./dist/cli.js"}, "dist/cli.js"),
        ("example-producer", "dist/cli.js", "dist/cli.js"),
        (
            "@example/producer",
            {"helper": "bin/helper.js", "producer": "dist/cli.js"},
            "dist/cli.js",
        ),
        ("example-producer", {"differently-named": "dist/cli.js"}, "dist/cli.js"),
    ],
)
def test_cli_comes_from_the_tarball_manifest_not_a_fixed_path(
    tmp_path: Path, name: str, declared: object, target: str
) -> None:
    manifest = _manifest(name=name, bin=declared)
    tarball = _tarball(
        tmp_path / "producer.tgz",
        [
            ("package/package.json", manifest),
            ("package/dist/cli.js", CLI),
            ("package/bin/helper.js", b"helper"),
        ],
    )
    package = check.package_cli(tarball)
    assert (package.name, package.target) == (name, target)
    assert package.manifest_sha256 == _sha256(manifest)
    assert package.cli_sha256 == _sha256(CLI)
    assert dict(package.files) == {
        "package.json": _sha256(manifest),
        "dist/cli.js": _sha256(CLI),
        "bin/helper.js": _sha256(b"helper"),
    }


def test_checker_source_names_no_installed_package_layout() -> None:
    source = (ROOT / "scripts/check_goppi_installed_pair.py").read_text(encoding="utf-8")
    assert "node_modules/" not in source
    assert ".mjs" not in source


PACKAGE_REFUSALS: dict[str, tuple[Members, str]] = {
    "missing_manifest": ([("package/dist/cli.js", CLI)], "GOPPI_PACKAGE_MANIFEST_MISSING"),
    "manifest_outside_package": (
        [("other/package.json", _manifest(bin="dist/cli.js")), ("package/dist/cli.js", CLI)],
        "GOPPI_PACKAGE_MEMBER_OUTSIDE_ROOT",
    ),
    "duplicate_manifest": (
        [
            ("package/package.json", _manifest(bin="dist/cli.js")),
            ("package/package.json", _manifest(bin="dist/other.js")),
            ("package/dist/cli.js", CLI),
        ],
        "GOPPI_PACKAGE_MEMBER_AMBIGUOUS",
    ),
    "manifest_not_json": (
        [("package/package.json", b"{"), ("package/dist/cli.js", CLI)],
        "GOPPI_PACKAGE_MANIFEST_INVALID",
    ),
    "manifest_not_object": (
        [("package/package.json", b"[]"), ("package/dist/cli.js", CLI)],
        "GOPPI_PACKAGE_MANIFEST_INVALID",
    ),
    "traversal_name": (
        [("package/package.json", _manifest(name="../escape", bin="dist/cli.js"))],
        "GOPPI_PACKAGE_NAME_INVALID",
    ),
    "uppercase_name": (
        [("package/package.json", _manifest(name="Producer", bin="dist/cli.js"))],
        "GOPPI_PACKAGE_NAME_INVALID",
    ),
    "non_text_name": (
        [("package/package.json", _manifest(name=7, bin="dist/cli.js"))],
        "GOPPI_PACKAGE_NAME_INVALID",
    ),
    "missing_bin": (
        [("package/package.json", _manifest()), ("package/dist/cli.js", CLI)],
        "GOPPI_CLI_UNDECLARED",
    ),
    "empty_bin": (
        [("package/package.json", _manifest(bin={})), ("package/dist/cli.js", CLI)],
        "GOPPI_CLI_UNDECLARED",
    ),
    "ambiguous_bin": (
        [
            ("package/package.json", _manifest(bin={"one": "dist/cli.js", "two": "dist/b.js"})),
            ("package/dist/cli.js", CLI),
            ("package/dist/b.js", CLI),
        ],
        "GOPPI_CLI_AMBIGUOUS",
    ),
    "absolute_bin": (
        [("package/package.json", _manifest(bin="/usr/bin/env"))],
        "GOPPI_CLI_PATH_INVALID",
    ),
    "parent_bin": (
        [("package/package.json", _manifest(bin="../../outside.js"))],
        "GOPPI_CLI_PATH_INVALID",
    ),
    "backslash_bin": (
        [("package/package.json", _manifest(bin="dist\\cli.js")), ("package/dist/cli.js", CLI)],
        "GOPPI_CLI_PATH_INVALID",
    ),
    "bin_not_in_tarball": (
        [("package/package.json", _manifest(bin="dist/other.js")), ("package/dist/cli.js", CLI)],
        "GOPPI_CLI_MISSING",
    ),
    "bin_is_directory": (
        [("package/package.json", _manifest(bin="dist")), ("package/dist", None)],
        "GOPPI_CLI_MISSING",
    ),
    "duplicate_cli": (
        [
            ("package/package.json", _manifest(bin="dist/cli.js")),
            ("package/dist/cli.js", CLI),
            ("package/dist/cli.js", b"replacement"),
        ],
        "GOPPI_PACKAGE_MEMBER_AMBIGUOUS",
    ),
    "case_variant_shadows_cli": (
        [
            ("package/package.json", _manifest(bin="dist/cli.js")),
            ("package/dist/cli.js", CLI),
            ("package/dist/CLI.js", b"replacement"),
        ],
        "GOPPI_PACKAGE_MEMBER_AMBIGUOUS",
    ),
    "member_outside_root": (
        [
            ("package/package.json", _manifest(bin="dist/cli.js")),
            ("package/dist/cli.js", b"import './impl.js';\n"),
            ("package/dist/impl.js", b"verified"),
            ("shadow/dist/impl.js", b"installed instead by root stripping"),
        ],
        "GOPPI_PACKAGE_MEMBER_OUTSIDE_ROOT",
    ),
    "dot_prefixed_root": (
        [
            ("./package/package.json", _manifest(bin="dist/cli.js")),
            ("./package/dist/cli.js", CLI),
        ],
        "GOPPI_PACKAGE_MEMBER_OUTSIDE_ROOT",
    ),
    "symlinked_cli": (
        [
            ("package/package.json", _manifest(bin="dist/cli.js")),
            ("package/dist/real.js", CLI),
            ("package/dist/cli.js", SYMLINK + b"real.js"),
        ],
        "GOPPI_PACKAGE_MEMBER_TYPE_UNSUPPORTED",
    ),
    "hardlinked_manifest": (
        [
            ("package/real.json", _manifest(bin="dist/cli.js")),
            ("package/package.json", HARDLINK + b"package/real.json"),
            ("package/dist/cli.js", CLI),
        ],
        "GOPPI_PACKAGE_MEMBER_TYPE_UNSUPPORTED",
    ),
    "deeply_nested_manifest": (
        [("package/package.json", b"[" * 200_000), ("package/dist/cli.js", CLI)],
        "GOPPI_PACKAGE_MANIFEST_INVALID",
    ),
}


@pytest.mark.parametrize("case", sorted(PACKAGE_REFUSALS))
def test_package_identity_refuses_any_other_shape(tmp_path: Path, case: str) -> None:
    members, reason = PACKAGE_REFUSALS[case]
    tarball = _tarball(tmp_path / "producer.tgz", members)
    with pytest.raises(check.Failure) as caught:
        check.package_cli(tarball)
    assert caught.value.reason == reason
    assert caught.value.location == "/goppi_tarball"


def test_unreadable_tarball_is_refused(tmp_path: Path) -> None:
    tarball = tmp_path / "producer.tgz"
    tarball.write_bytes(b"not a gzip archive")
    with pytest.raises(check.Failure) as caught:
        check.package_cli(tarball)
    assert caught.value.reason == "GOPPI_PACKAGE_UNREADABLE"


def _installed(tmp_path: Path) -> tuple[Any, Path, Path]:
    manifest = _manifest(bin={"example-producer": "./dist/cli.js"})
    tarball = _tarball(
        tmp_path / "producer.tgz",
        [("package/package.json", manifest), ("package/dist/cli.js", CLI)],
    )
    npm_root = tmp_path / "npm"
    directory = npm_root / "node_modules" / "example-producer"
    (directory / "dist").mkdir(parents=True)
    (directory / "package.json").write_bytes(manifest)
    (directory / "dist/cli.js").write_bytes(CLI)
    return check.package_cli(tarball), npm_root, directory


def test_installed_cli_is_the_verified_tarball_member(tmp_path: Path) -> None:
    package, npm_root, directory = _installed(tmp_path)
    assert check.installed_cli(npm_root, package) == (directory / "dist/cli.js").resolve()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("cli_bytes", "GOPPI_CLI_INSTALL_MISMATCH"),
        ("manifest_bytes", "GOPPI_CLI_INSTALL_MISMATCH"),
        ("missing", "GOPPI_CLI_INSTALL_MISSING"),
        ("cli_symlink", "GOPPI_CLI_INSTALL_REDIRECTED"),
        ("package_symlink", "GOPPI_CLI_INSTALL_REDIRECTED"),
        ("scope_symlink", "GOPPI_CLI_INSTALL_REDIRECTED"),
        ("extra_file", "GOPPI_CLI_INSTALL_MISMATCH"),
        ("symlinked_subdirectory", "GOPPI_CLI_INSTALL_REDIRECTED"),
    ],
)
def test_installed_cli_refuses_other_bytes_or_redirected_paths(
    tmp_path: Path, mutation: str, reason: str
) -> None:
    package, npm_root, directory = _installed(tmp_path)
    cli = directory / "dist/cli.js"
    if mutation == "cli_bytes":
        cli.write_bytes(b"different program")
    elif mutation == "manifest_bytes":
        (directory / "package.json").write_bytes(_manifest(bin="dist/cli.js"))
    elif mutation == "missing":
        cli.unlink()
    elif mutation == "cli_symlink":
        outside = tmp_path / "outside.js"
        outside.write_bytes(CLI)
        cli.unlink()
        cli.symlink_to(outside)
    elif mutation == "package_symlink":
        moved = tmp_path / "elsewhere"
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    elif mutation == "extra_file":
        (directory / "dist/injected.js").write_bytes(b"not in the tarball")
    elif mutation == "symlinked_subdirectory":
        (tmp_path / "outside").mkdir()
        (directory / "lib").symlink_to(tmp_path / "outside", target_is_directory=True)
    else:
        manifest = _manifest(name="@example/producer", bin="dist/cli.js")
        tarball = _tarball(
            tmp_path / "scoped.tgz",
            [("package/package.json", manifest), ("package/dist/cli.js", CLI)],
        )
        package = check.package_cli(tarball)
        real_scope = tmp_path / "real-scope"
        (real_scope / "producer/dist").mkdir(parents=True)
        (real_scope / "producer/package.json").write_bytes(manifest)
        (real_scope / "producer/dist/cli.js").write_bytes(CLI)
        (npm_root / "node_modules/@example").symlink_to(real_scope, target_is_directory=True)
    with pytest.raises(check.Failure) as caught:
        check.installed_cli(npm_root, package)
    assert caught.value.reason == reason


ARGUMENTS = [
    "--goppi-tarball",
    "producer.tgz",
    "--underwrite-wheel",
    "underwrite.whl",
    "--underwrite-core-wheel",
    "underwrite_core.whl",
    "--node",
    "node",
]


def test_expected_tarball_digest_accepts_only_the_exact_form() -> None:
    valid = "sha256:" + "a" * 64
    parsed = check.parser().parse_args([*ARGUMENTS, "--goppi-tarball-sha256", valid])
    assert parsed.goppi_tarball_sha256 == valid
    for value in ("abc", "sha256:" + "A" * 64, "sha256:" + "a" * 63, "a" * 64):
        with pytest.raises(check.argparse.ArgumentTypeError):
            check.expected_sha256(value)


@pytest.mark.parametrize(
    ("value", "message"),
    [(None, "required: --goppi-tarball-sha256"), ("sha256:" + "a" * 63, "expected sha256:")],
)
def test_expected_tarball_digest_is_required_on_the_command_line(
    value: str | None, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = [*ARGUMENTS] if value is None else [*ARGUMENTS, "--goppi-tarball-sha256", value]
    with pytest.raises(SystemExit) as caught:
        check.parser().parse_args(argv)
    assert caught.value.code == 2  # noqa: PLR2004
    assert message in capsys.readouterr().err


def test_different_tarball_is_refused_before_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*_: object, **__: object) -> None:
        pytest.fail("an unexpected tarball must be refused before installation")

    monkeypatch.setattr(check, "install", forbidden)
    inputs: dict[str, Path] = {}
    for role in check.ARTIFACT_ROLES:
        inputs[role] = tmp_path / f"{role}.input"
        inputs[role].write_bytes(role.encode())
    expected = "sha256:" + "0" * 64
    code = check.main(
        [
            "--goppi-tarball",
            str(inputs["goppi_tarball"]),
            "--underwrite-wheel",
            str(inputs["underwrite_wheel"]),
            "--underwrite-core-wheel",
            str(inputs["underwrite_core_wheel"]),
            "--node",
            str(inputs["node_executable"]),
            "--goppi-tarball-sha256",
            expected,
        ]
    )
    report = json.loads(capsys.readouterr().err)
    assert code == 1
    assert report["failure"] == {
        "phase": "snapshot",
        "reason": "GOPPI_TARBALL_DIGEST_MISMATCH",
        "location": "/goppi_tarball",
        "next_action": "CORRECT_ARGUMENTS",
    }
    assert report["goppi_package"] == {"tarball_sha256_expected": expected}
    assert report["artifacts"]["goppi_tarball"]["sha256"] == _sha256(b"goppi_tarball")


def test_member_size_limit_is_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check, "PACKAGE_MEMBER_MAX_BYTES", len(CLI) - 1)
    tarball = _tarball(
        tmp_path / "producer.tgz",
        [("package/package.json", _manifest(bin="dist/cli.js")), ("package/dist/cli.js", CLI)],
    )
    with pytest.raises(check.Failure) as caught:
        check.package_cli(tarball)
    assert caught.value.reason == "GOPPI_PACKAGE_MEMBER_LIMIT"


def test_package_identity_is_read_before_any_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_: object, **__: object) -> None:
        pytest.fail("a tarball without a valid manifest must be refused before npm runs")

    def which(name: str) -> str:
        return f"/usr/bin/{name}"

    monkeypatch.setattr(check.shutil, "which", which)
    monkeypatch.setattr(check, "run", forbidden)
    tarball = _tarball(tmp_path / "producer.tgz", [("package/dist/cli.js", CLI)])
    with pytest.raises(check.Failure) as caught:
        check.install(
            tmp_path / "work",
            {"goppi_tarball": tarball},
            Path("/usr/bin/node"),
            {},
            {"goppi_package": {"tarball_sha256_expected": _sha256(tarball.read_bytes())}},
        )
    assert caught.value.reason == "GOPPI_PACKAGE_MANIFEST_MISSING"


def test_summary_failures_are_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def accepted_without_package_identity(_: object, summary: dict[str, Any]) -> None:
        summary["contracts"] = dict(check.CONTRACTS)

    monkeypatch.setattr(check, "accept", accepted_without_package_identity)
    code = check.main([*ARGUMENTS, "--goppi-tarball-sha256", "sha256:" + "a" * 64])
    report = json.loads(capsys.readouterr().err)
    assert code == 1 and report["status"] == "failed"
    assert report["failure"]["reason"] == "SUCCESS_PACKAGE_IDENTITY_MISSING"


@pytest.mark.parametrize("nested", ["node_modules/dep/index.js", "node_modules/.bin/dep"])
def test_files_only_npm_extracted_under_the_package_are_refused(
    tmp_path: Path, nested: str
) -> None:
    """A member another reader cannot see would otherwise shadow an import resolution."""
    package, npm_root, directory = _installed(tmp_path)
    hidden = directory / nested
    hidden.parent.mkdir(parents=True)
    hidden.write_bytes(b"export default 'not audited';\n")
    with pytest.raises(check.Failure) as caught:
        check.installed_cli(npm_root, package)
    assert caught.value.reason == "GOPPI_CLI_INSTALL_MISMATCH"


def test_bundled_files_are_compared_like_any_other_member(tmp_path: Path) -> None:
    manifest = _manifest(bin="dist/cli.js")
    bundled = b"module.exports = 'bundled';\n"
    tarball = _tarball(
        tmp_path / "producer.tgz",
        [
            ("package/package.json", manifest),
            ("package/dist/cli.js", CLI),
            ("package/node_modules/bundled/index.js", bundled),
        ],
    )
    package = check.package_cli(tarball)
    directory = tmp_path / "npm/node_modules/example-producer"
    (directory / "dist").mkdir(parents=True)
    (directory / "node_modules/bundled").mkdir(parents=True)
    (directory / "package.json").write_bytes(manifest)
    (directory / "dist/cli.js").write_bytes(CLI)
    (directory / "node_modules/bundled/index.js").write_bytes(bundled)
    assert check.installed_cli(tmp_path / "npm", package) == (directory / "dist/cli.js").resolve()
    (directory / "node_modules/bundled/index.js").write_bytes(b"changed")
    with pytest.raises(check.Failure) as caught:
        check.installed_cli(tmp_path / "npm", package)
    assert caught.value.reason == "GOPPI_CLI_INSTALL_MISMATCH"


def test_non_regular_installed_entries_are_refused_without_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, npm_root, directory = _installed(tmp_path)
    os.mkfifo(directory / "dist/pipe")
    original = check.digest

    def guarded(path: Path) -> str:
        # Opening a FIFO blocks; fail fast instead of hanging if the type check regresses.
        assert path.name != "pipe", "a non-regular entry was opened for hashing"
        return cast(str, original(path))

    monkeypatch.setattr(check, "digest", guarded)
    with pytest.raises(check.Failure) as caught:
        check.installed_cli(npm_root, package)
    assert caught.value.reason == "GOPPI_CLI_INSTALL_MISMATCH"


@pytest.mark.skipif(os.geteuid() == 0, reason="permission bits do not restrict root")
def test_unlistable_installed_directory_is_refused(tmp_path: Path) -> None:
    package, npm_root, directory = _installed(tmp_path)
    hidden = directory / "hidden"
    hidden.mkdir()
    (hidden / "extra.js").write_bytes(b"not in the tarball")
    hidden.chmod(0)
    try:
        with pytest.raises(check.Failure) as caught:
            check.installed_cli(npm_root, package)
    finally:
        hidden.chmod(0o700)
    assert caught.value.reason == "GOPPI_CLI_INSTALL_UNREADABLE"


def test_total_member_size_limit_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(bin="dist/cli.js")
    monkeypatch.setattr(check, "PACKAGE_TOTAL_MAX_BYTES", len(manifest) + len(CLI) - 1)
    tarball = _tarball(
        tmp_path / "producer.tgz",
        [("package/package.json", manifest), ("package/dist/cli.js", CLI)],
    )
    with pytest.raises(check.Failure) as caught:
        check.package_cli(tarball)
    assert caught.value.reason == "GOPPI_PACKAGE_MEMBER_LIMIT"


@pytest.mark.parametrize(
    ("variant", "installed_as"),
    [
        ("package//dist/impl.js", "dist/impl.js"),
        ("package/./dist/impl.js", "dist/impl.js"),
        ("package/dist/" + "e\u0301.js", "dist/" + "\u00e9.js"),
    ],
)
def test_reader_normalization_differences_cannot_hide_an_installed_file(
    tmp_path: Path, variant: str, installed_as: str
) -> None:
    """npm may install two differently written members at one path; the tree must still match."""
    manifest = _manifest(bin="dist/cli.js")
    audited = installed_as.split("/", 1)[1]
    tarball = _tarball(
        tmp_path / "producer.tgz",
        [
            ("package/package.json", manifest),
            ("package/dist/cli.js", CLI),
            ("package/dist/" + audited, b"audited"),
            (variant, b"installed instead"),
        ],
    )
    package = check.package_cli(tarball)
    directory = tmp_path / "npm/node_modules/example-producer"
    (directory / "dist").mkdir(parents=True)
    (directory / "package.json").write_bytes(manifest)
    (directory / "dist/cli.js").write_bytes(CLI)
    (directory / installed_as).write_bytes(b"installed instead")
    with pytest.raises(check.Failure) as caught:
        check.installed_cli(tmp_path / "npm", package)
    assert caught.value.reason == "GOPPI_CLI_INSTALL_MISMATCH"


@pytest.mark.parametrize("tamper", [False, True])
def test_installed_files_are_verified_again_after_the_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: bool
) -> None:
    package, npm_root, directory = _installed(tmp_path)
    cli = directory / "dist/cli.js"
    pair = check.InstalledPair(
        tmp_path,
        tmp_path / "node",
        cli,
        tmp_path / "underwrite",
        {},
        package,
        npm_root,
    )
    checks = [{"name": "complete_exchange", "status": "passed"}]

    def exercise(*_: object) -> list[dict[str, str]]:
        if tamper:
            (directory / "dist/cli.js").write_bytes(b"replaced by an executed program")
        return checks

    monkeypatch.setattr(check, "exercise", exercise)
    if not tamper:
        assert check.checked_exercise(tmp_path, tmp_path, pair.node, pair, {}) == checks
        return
    with pytest.raises(check.Failure) as caught:
        check.checked_exercise(tmp_path, tmp_path, pair.node, pair, {})
    assert (caught.value.reason, caught.value.phase) == (
        "GOPPI_CLI_CHANGED_DURING_CHECKS",
        "verification",
    )

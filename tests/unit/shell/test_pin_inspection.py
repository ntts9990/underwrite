"""Read-only pin admission and drift report must share one bounded capture."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from underwrite.cli import commands, render
from underwrite.cli.main import main
from underwrite.fleet import pins
from underwrite.fleet.pins import PinState, refresh_pins, verify_pins
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = json.loads((ROOT / "contracts/cli_result.v1.schema.json").read_text())
REFUSAL = 2
FIXTURE_MANIFESTS = 2
COPY_SMALL_LIMIT = 10


def _cli(pins_root: Path, siblings_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "underwrite.cli.main",
            "drift",
            "check",
            "--pins-root",
            str(pins_root),
            "--siblings-root",
            str(siblings_root),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _refusal(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.returncode == REFUSAL and result.stderr == ""
    payload: dict[str, Any] = json.loads(result.stdout)
    assert payload["state"] == "UNCONFIGURED" and payload["exit_code"] == REFUSAL
    assert "active_population" not in payload["details"]
    Draft202012Validator(CONTRACT).validate(payload)  # pyright: ignore[reportUnknownMemberType]
    packaged_validator("cli_result.v1").validate(payload)  # pyright: ignore[reportUnknownMemberType]
    return payload


def test_duplicate_manifest_repo_cannot_be_coerced_into_pass(
    pins_tree: Path, hermetic_siblings: Callable[..., Path]
) -> None:
    sibling_root = hermetic_siblings()
    manifest = pins_tree / "active/sample-tool/PINS.json"
    raw = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        raw.replace('"repo": "sample-tool"', '"repo": "sample-tool", "repo": "sample-tool"')
    )
    payload = _refusal(_cli(pins_tree, sibling_root))
    assert payload["details"]["reason"] == "INVALID_PIN_MANIFEST"


def test_nonfinite_manifest_cannot_be_coerced_into_pass(
    pins_tree: Path, hermetic_siblings: Callable[..., Path]
) -> None:
    sibling_root = hermetic_siblings()
    manifest = pins_tree / "active/sample-tool/PINS.json"
    raw = manifest.read_text(encoding="utf-8")
    manifest.write_text(raw.replace('"source_commit": "local-fixture"', '"source_commit": NaN'))
    payload = _refusal(_cli(pins_tree, sibling_root))
    assert payload["details"]["reason"] == "INVALID_PIN_MANIFEST"


def test_missing_selected_pin_is_drift_not_traceback(
    pins_tree: Path, hermetic_siblings: Callable[..., Path]
) -> None:
    sibling_root = hermetic_siblings()
    selected = pins_tree / "active/sample-tool/contracts/sample.schema.json"
    selected.unlink()
    report = verify_pins(pins_tree, sibling_root)
    result = next(item for item in report.results if item.repo == "sample-tool")
    assert result.state is PinState.DRIFT
    assert result.detail == "pin file missing or nonregular"
    cli = _cli(pins_tree, sibling_root)
    assert cli.returncode == 1 and cli.stderr == ""


def test_cli_uses_one_manifest_capture_for_verdict_and_table(
    pins_tree: Path,
    hermetic_siblings: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sibling_root = hermetic_siblings()
    original = pins._regular_bytes  # pyright: ignore[reportPrivateUsage]
    reads: dict[Path, int] = {}

    def once(
        path: Path, limit: int, remaining: int, *, missing_ok: bool = False
    ) -> tuple[bytes | None, int]:
        captured = original(path, limit, remaining, missing_ok=missing_ok)
        if path.name == "PINS.json":
            reads[path] = reads.get(path, 0) + 1
            if reads[path] > 1:
                raise AssertionError("second manifest capture")
            if path.parent.name == "sample-tool":
                document = json.loads(path.read_bytes())
                document["source_commit"] = "changed-after-first-capture"
                path.write_text(json.dumps(document))
        return captured

    monkeypatch.setattr(pins, "_regular_bytes", once)
    code = main(
        [
            "drift",
            "check",
            "--pins-root",
            str(pins_tree),
            "--siblings-root",
            str(sibling_root),
            "--json",
        ]
    )
    output = capsys.readouterr()
    assert code == 0 and output.err == ""
    assert len(reads) == FIXTURE_MANIFESTS and all(count == 1 for count in reads.values())
    payload = json.loads(output.out)
    assert payload["details"]["repos"]["sample-tool"]["source_commit"] == "local-fixture"
    assert payload["details"]["repos"]["sample-tool"]["state"] == "PASS"
    assert payload["details"]["repos"]["sample-tool"]["pins"] == 1


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("wrong-repo", "INVALID_PIN_MANIFEST"),
        ("wrong-source-type", "INVALID_PIN_MANIFEST"),
        ("wrong-hash-type", "INVALID_PIN_MANIFEST"),
        ("bad-path", "INVALID_PIN_PATH"),
        ("bad-source-path", "INVALID_PIN_PATH"),
    ],
)
def test_manifest_shape_and_path_failures_are_finite(
    pins_tree: Path, hermetic_siblings: Callable[..., Path], mutation: str, reason: str
) -> None:
    sibling_root = hermetic_siblings()
    selected = pins_tree / "active/sample-tool/PINS.json"
    document = json.loads(selected.read_bytes())
    key = next(iter(document["files"]))
    if mutation == "wrong-repo":
        document["repo"] = "sample-metrics"
    elif mutation == "wrong-source-type":
        document["files"][key]["source_path"] = 1
    elif mutation == "wrong-hash-type":
        document["files"][key]["sha256"] = 1
    elif mutation == "bad-path":
        document["files"]["../escape.json"] = document["files"].pop(key)
    else:
        document["files"][key]["source_path"] = "../escape.json"
    selected.write_text(json.dumps(document))
    assert _refusal(_cli(pins_tree, sibling_root))["details"]["reason"] == reason


def test_unknown_stale_hash_is_a_repairable_drift(
    stale_sample_pins: tuple[Path, Path], hermetic_git_sibling: Callable[[str], Path]
) -> None:
    pins_root, manifest_path = stale_sample_pins
    sibling_dir = hermetic_git_sibling("sample-tool")
    document = json.loads(manifest_path.read_bytes())
    next(iter(document["files"].values()))["sha256"] = "unknown historical hash"
    manifest_path.write_text(json.dumps(document))
    assert verify_pins(pins_root, sibling_dir.parent).failures()
    refreshed = refresh_pins(pins_root, sibling_dir.parent, "sample-tool")
    assert next(iter(refreshed.files.values())).sha256.startswith("sha256:")


def test_missing_registry_keeps_empty_population_semantics(tmp_path: Path) -> None:
    result = _cli(tmp_path / "missing-pins", tmp_path)
    assert result.returncode == 1 and result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["state"] == "EMPTY_POPULATION"
    assert payload["details"]["active_population"] == 0


def test_missing_and_nonregular_copy_or_source_is_drift(
    pins_tree: Path, hermetic_siblings: Callable[..., Path]
) -> None:
    sibling_root = hermetic_siblings()
    pin = pins_tree / "active/sample-tool/contracts/sample.schema.json"
    source = sibling_root / "sample-metrics/contracts/sample.schema.json"
    pin.unlink()
    source.unlink()
    os.mkfifo(pin)
    os.mkfifo(source)
    report = verify_pins(pins_tree, sibling_root)
    assert {item.repo for item in report.failures()} == {"sample-tool", "sample-metrics"}
    assert not report.unconfigured()


def test_readonly_directory_manifest_pin_and_source_links_are_supported(
    pins_tree: Path, hermetic_siblings: Callable[..., Path], tmp_path: Path
) -> None:
    sibling_root = hermetic_siblings()
    repo = pins_tree / "active/sample-tool"
    real_repo = tmp_path / "sample-tool-real"
    repo.rename(real_repo)
    repo.symlink_to(real_repo, target_is_directory=True)
    manifest = real_repo / "PINS.json"
    outside_manifest = tmp_path / "manifest-copy.json"
    outside_manifest.write_bytes(manifest.read_bytes())
    manifest.unlink()
    manifest.symlink_to(outside_manifest)
    pin = real_repo / "contracts/sample.schema.json"
    outside_pin = tmp_path / "pin-copy.json"
    outside_pin.write_bytes(pin.read_bytes())
    pin.unlink()
    pin.symlink_to(outside_pin)
    source = sibling_root / "sample-tool/contracts/sample.schema.json"
    outside_source = tmp_path / "source-copy.json"
    outside_source.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(outside_source)
    result = _cli(pins_tree, sibling_root)
    assert result.returncode == 0 and result.stderr == ""
    assert json.loads(result.stdout)["state"] == "PASS"


def _inprocess_refusal(
    pins_root: Path, siblings_root: Path, capsys: pytest.CaptureFixture[str]
) -> dict[str, Any]:
    code = main(
        [
            "drift",
            "check",
            "--pins-root",
            str(pins_root),
            "--siblings-root",
            str(siblings_root),
            "--json",
        ]
    )
    output = capsys.readouterr()
    assert code == REFUSAL and output.err == ""
    payload: dict[str, Any] = json.loads(output.out)
    Draft202012Validator(CONTRACT).validate(payload)  # pyright: ignore[reportUnknownMemberType]
    packaged_validator("cli_result.v1").validate(payload)  # pyright: ignore[reportUnknownMemberType]
    assert "active_population" not in payload["details"]
    return payload


@pytest.mark.parametrize(
    "case",
    [
        ("MANIFESTS_MAX", 1, "manifests"),
        ("DECLARED_FILES_MAX", 1, "declared_files"),
        ("VISITED_ENTRIES_MAX", 1, "directory_entries"),
    ],
)
def test_registry_population_limits_refuse_without_partial_counts(
    pins_tree: Path,
    hermetic_siblings: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: tuple[str, int, str],
) -> None:
    sibling_root = hermetic_siblings()
    name, limit, kind = case
    monkeypatch.setattr(pins, name, limit)
    payload = _inprocess_refusal(pins_tree, sibling_root, capsys)
    assert payload["details"]["limit_kind"] == kind
    assert payload["details"]["limit"] == limit


def test_copy_byte_limit_is_bounded_by_opened_regular_descriptor(
    pins_tree: Path,
    hermetic_siblings: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sibling_root = hermetic_siblings()
    monkeypatch.setattr(pins, "PIN_FILE_MAX_BYTES", COPY_SMALL_LIMIT)
    payload = _inprocess_refusal(pins_tree, sibling_root, capsys)
    assert payload["details"]["limit_kind"] == "bytes"
    assert payload["details"]["limit"] == COPY_SMALL_LIMIT


def test_manifest_byte_limit_and_depth_are_finite(
    pins_tree: Path,
    hermetic_siblings: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sibling_root = hermetic_siblings()
    monkeypatch.setattr(pins, "MANIFEST_MAX_BYTES", COPY_SMALL_LIMIT)
    limited = _inprocess_refusal(pins_tree, sibling_root, capsys)
    assert limited["details"]["limit_kind"] == "bytes"
    assert limited["details"]["limit"] == COPY_SMALL_LIMIT
    monkeypatch.setattr(pins, "MANIFEST_MAX_BYTES", 1024 * 1024)
    selected = pins_tree / "active/sample-tool/PINS.json"
    selected.write_bytes(b"[" * 65 + b"0" + b"]" * 65)
    invalid = _inprocess_refusal(pins_tree, sibling_root, capsys)
    assert invalid["details"]["reason"] == "INVALID_PIN_MANIFEST"


def test_total_capture_includes_exact_exemption_bytes(
    pins_tree: Path,
    hermetic_siblings: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sibling_root = hermetic_siblings()
    config = tmp_path / "exemptions.json"
    config.write_text('{"schema":"exemptions.v1","exemptions":{}}')
    selected = pins_tree / "active/sample-tool/PINS.json"
    cap = config.stat().st_size + selected.stat().st_size - 1
    monkeypatch.setattr(pins, "PIN_TOTAL_MAX_BYTES", cap)
    original = commands.read_bytes
    config_reads = 0

    def counted(path: Path, maximum: int, location: str) -> bytes:
        nonlocal config_reads
        if path == config:
            config_reads += 1
        return original(path, maximum, location)

    monkeypatch.setattr(commands, "read_bytes", counted)
    code = main(
        [
            "drift",
            "check",
            "--pins-root",
            str(pins_tree),
            "--siblings-root",
            str(sibling_root),
            "--exemptions",
            str(config),
            "--json",
        ]
    )
    output = capsys.readouterr()
    assert code == REFUSAL and output.err == ""
    payload: dict[str, Any] = json.loads(output.out)
    Draft202012Validator(CONTRACT).validate(payload)  # pyright: ignore[reportUnknownMemberType]
    assert payload["details"]["limit_kind"] == "captured_bytes"
    assert payload["details"]["limit"] == cap
    assert config_reads == 1


@pytest.mark.parametrize("remaining", [True, 0.5, -1, pins.PIN_TOTAL_MAX_BYTES + 1])
def test_internal_remaining_budget_rejects_programming_values_before_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remaining: object
) -> None:
    def no_io(_root: Path) -> list[tuple[Path, str, bool]]:
        raise AssertionError("opened registry before validating remaining budget")

    monkeypatch.setattr(pins, "_candidate_paths", no_io)
    with pytest.raises(ValueError, match="^INVALID_PIN_READ_BUDGET$"):
        pins._inspect_pins(tmp_path, tmp_path, cast(int, remaining))  # pyright: ignore[reportPrivateUsage]


def test_exemption_alone_over_total_budget_is_a_typed_resource_refusal(
    pins_tree: Path,
    hermetic_siblings: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sibling_root = hermetic_siblings()
    config = tmp_path / "exemptions.json"
    config.write_text('{"schema":"exemptions.v1","exemptions":{}}')
    total_limit = config.stat().st_size - 1
    monkeypatch.setattr(pins, "PIN_TOTAL_MAX_BYTES", total_limit)
    code = main(
        [
            "drift",
            "check",
            "--pins-root",
            str(pins_tree),
            "--siblings-root",
            str(sibling_root),
            "--exemptions",
            str(config),
            "--json",
        ]
    )
    output = capsys.readouterr()
    assert code == REFUSAL and output.err == ""
    payload: dict[str, Any] = json.loads(output.out)
    Draft202012Validator(CONTRACT).validate(payload)  # pyright: ignore[reportUnknownMemberType]
    assert payload["details"] == {
        "reason": "INPUT_LIMIT_EXCEEDED",
        "location": "/pins",
        "limit_kind": "captured_bytes",
        "limit": total_limit,
    }


def test_repo_without_manifest_is_ignored_but_discovered_manifest_disappearance_refuses(
    pins_tree: Path,
    hermetic_siblings: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sibling_root = hermetic_siblings()
    skipped = pins_tree / "active/initially-empty"
    skipped.mkdir()
    (skipped / "note.txt").write_text("ignored")
    assert _cli(pins_tree, sibling_root).returncode == 0
    original = pins._regular_bytes  # pyright: ignore[reportPrivateUsage]

    def disappear(
        path: Path, maximum: int, remaining: int, *, missing_ok: bool = False
    ) -> tuple[bytes | None, int]:
        if path.name == "PINS.json" and path.parent.name == "sample-tool":
            path.unlink()
        return original(path, maximum, remaining, missing_ok=missing_ok)

    monkeypatch.setattr(pins, "_regular_bytes", disappear)
    payload = _inprocess_refusal(pins_tree, sibling_root, capsys)
    assert payload["details"] == {"reason": "PIN_READ_FAILED", "location": "/pins"}


def test_drift_render_budget_is_atomic_and_changes_exit(
    pins_tree: Path,
    hermetic_siblings: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sibling_root = hermetic_siblings()
    monkeypatch.setattr(render, "OUTPUT_MAX_BYTES", 400)
    payload = _inprocess_refusal(pins_tree, sibling_root, capsys)
    assert payload["details"] == {
        "reason": "INPUT_LIMIT_EXCEEDED",
        "location": "/output",
        "limit_kind": "output_bytes",
        "limit": 400,
    }


def test_exact_manifest_copy_and_total_byte_limits_admit_complete_capture(
    pins_tree: Path, hermetic_siblings: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    sibling_root = hermetic_siblings()
    manifests = list((pins_tree / "active").glob("*/PINS.json"))
    manifest_total = sum(path.stat().st_size for path in manifests)
    copies = [
        pins_tree / "active/sample-tool/contracts/sample.schema.json",
        pins_tree / "active/sample-metrics/contracts/sample.schema.json",
        sibling_root / "sample-tool/contracts/sample.schema.json",
        sibling_root / "sample-metrics/contracts/sample.schema.json",
    ]
    copy_limit = max(path.stat().st_size for path in copies)
    total = manifest_total + sum(path.stat().st_size for path in copies)
    monkeypatch.setattr(pins, "MANIFEST_MAX_BYTES", max(path.stat().st_size for path in manifests))
    monkeypatch.setattr(pins, "PIN_FILE_MAX_BYTES", copy_limit)
    monkeypatch.setattr(pins, "PIN_TOTAL_MAX_BYTES", total)
    assert verify_pins(pins_tree, sibling_root).ok
    monkeypatch.setattr(pins, "PIN_TOTAL_MAX_BYTES", total - 1)
    with pytest.raises(pins.PinReadError) as caught:
        verify_pins(pins_tree, sibling_root)
    assert (caught.value.limit_kind, caught.value.limit) == ("captured_bytes", total - 1)
    assert not hasattr(caught.value, "publication")


def test_exact_manifest_and_declared_file_counts_admit_complete_registry(
    pins_tree: Path, hermetic_siblings: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    sibling_root = hermetic_siblings()
    monkeypatch.setattr(pins, "MANIFESTS_MAX", FIXTURE_MANIFESTS)
    monkeypatch.setattr(pins, "DECLARED_FILES_MAX", FIXTURE_MANIFESTS)
    report = verify_pins(pins_tree, sibling_root)
    assert report.ok and report.active_population == FIXTURE_MANIFESTS


def test_source_root_remains_untrusted_metadata(
    pins_tree: Path, hermetic_siblings: Callable[..., Path]
) -> None:
    sibling_root = hermetic_siblings()
    selected = pins_tree / "active/sample-tool/PINS.json"
    document = json.loads(selected.read_bytes())
    document["source_root"] = "/outside/unreadable/metadata-only"
    selected.write_text(json.dumps(document))
    assert verify_pins(pins_tree, sibling_root).ok


def test_selected_copy_permission_error_is_finite(
    pins_tree: Path,
    hermetic_siblings: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sibling_root = hermetic_siblings()
    selected = sibling_root / "sample-tool/contracts/sample.schema.json"
    original = os.open

    def refuse(
        path: str | os.PathLike[str], flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if Path(path) == selected:
            raise PermissionError("private host path")
        return original(path, flags, mode, dir_fd=dir_fd)

    with monkeypatch.context() as patch:
        patch.setattr(pins.os, "open", refuse)
        payload = _inprocess_refusal(pins_tree, sibling_root, capsys)
    assert payload["details"] == {"reason": "PIN_READ_FAILED", "location": "/pins"}


def test_default_drift_output_budget_refuses_large_manifest_metadata(tmp_path: Path) -> None:
    root = tmp_path / "pins"
    for repo in ("alpha", "beta"):
        selected = root / "active" / repo
        selected.mkdir(parents=True)
        (selected / "PINS.json").write_text(
            json.dumps(
                {
                    "schema": "pins.v1",
                    "repo": repo,
                    "source_root": repo,
                    "source_commit": "x" * 600_000,
                    "captured_at": "stale",
                    "files": {},
                }
            )
        )
    payload = _refusal(_cli(root, tmp_path))
    assert payload["details"] == {
        "reason": "INPUT_LIMIT_EXCEEDED",
        "location": "/output",
        "limit_kind": "output_bytes",
        "limit": 1024 * 1024,
    }

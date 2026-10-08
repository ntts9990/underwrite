"""Standalone behavior and boundary checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import TypedDict, TypeGuard

RECORD_NAME = "observations.json"
MANIFEST_NAME = "manifest.json"


class Entry(TypedDict):
    format: str
    version: str
    path: str
    raw_sha256: str


class ManifestError(ValueError):
    """A capture record is missing, malformed, or disagrees with the bytes it names."""


def _is_object(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def _text(value: object) -> str:
    """``value`` when it is a string, else ``""`` — absent and non-string read as empty."""
    return value if isinstance(value, str) else ""


def _entry(record_path: Path, name: str, item: object, repo_root: Path) -> Entry:
    """The manifest entry for the file ``name`` captured beside ``record_path``."""
    if not _is_object(item):
        raise ManifestError(f"{record_path}: {name}: entry is not an object")
    source = item.get("source")
    if not _is_object(source):
        raise ManifestError(f"{record_path}: {name}: missing source selector")
    fmt, version = _text(source.get("format")), _text(source.get("format_version"))
    if not fmt or not version:
        raise ManifestError(f"{record_path}: {name}: incomplete source selector")
    target = record_path.parent / name
    if not target.is_file():
        raise ManifestError(f"{record_path}: {name}: captured file is missing")
    raw_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    digest = f"sha256:{raw_sha256}"
    if item.get("raw_hash") != digest or item.get("ref") != digest:
        raise ManifestError(f"{record_path}: {name}: raw digest does not match the bytes")
    return Entry(
        format=fmt,
        version=version,
        path=target.relative_to(repo_root).as_posix(),
        raw_sha256=raw_sha256,
    )


def derive(external_root: Path, repo_root: Path) -> list[Entry]:
    """Read every ``<vendor>/observations.json`` under ``external_root`` into entries."""
    entries: list[Entry] = []
    for record_path in sorted(external_root.glob(f"*/{RECORD_NAME}")):
        try:
            record: object = json.loads(record_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ManifestError(f"{record_path}: not JSON ({error.msg})") from error
        files = record.get("files") if _is_object(record) else None
        if not _is_object(files) or not files:
            raise ManifestError(f"{record_path}: no files recorded")
        for name, item in sorted(files.items()):
            entries.append(_entry(record_path, name, item, repo_root))
    if not entries:
        raise ManifestError(f"{external_root}: no capture records found")
    return sorted(entries, key=lambda entry: entry["path"])


def render(entries: list[Entry]) -> str:
    document = {"formats": entries}
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def check(external_root: Path, repo_root: Path) -> list[str]:
    """Return the problems with the committed manifest; an empty list means it is derived."""
    try:
        derived = render(derive(external_root, repo_root))
    except ManifestError as error:
        return [str(error)]
    manifest = external_root / MANIFEST_NAME
    if not manifest.is_file():
        return [f"{manifest}: missing"]
    if manifest.read_text(encoding="utf-8") != derived:
        return [f"{manifest}: differs from the derivation; regenerate with --write"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive or check fixtures/golden/external/manifest.json."
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--write", action="store_true", help="Write the derived manifest.")
    args = parser.parse_args(argv)
    repo_root = args.repo.resolve()
    external_root = repo_root / "fixtures" / "golden" / "external"
    if args.write:
        manifest = external_root / MANIFEST_NAME
        try:
            manifest.write_text(render(derive(external_root, repo_root)), encoding="utf-8")
        except ManifestError as error:
            print(f"external_manifest: {error}", file=sys.stderr)
            return 1
        print(f"external_manifest: wrote {manifest}")
        return 0
    problems = check(external_root, repo_root)
    for problem in problems:
        print(f"external_manifest: {problem}", file=sys.stderr)
    print("external_manifest: OK" if not problems else "external_manifest: FAIL")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

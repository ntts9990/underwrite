"""Standalone behavior and boundary checks."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest
from underwrite_core import chain as core_chain
from underwrite_core.bundle import Blob, plan_layout
from underwrite_core.canonical import canonical_bytes
from underwrite_core.receipt import Artifact, Ladder, Limitations, build_statement

REPO_ROOT = Path(__file__).resolve().parents[3]
VERIFY_PY = REPO_ROOT / "packages" / "underwrite-core" / "build" / "verify.py"
_CHAIN_LEN = 10
_TAMPERED_SEQ = 4
_EXPECTED_ROLES = ("--expected-roles", "receipt,ledger_chain,anchor")


def _build_chain(length: int) -> list[core_chain.ChainRow]:
    rows: list[core_chain.ChainRow] = []
    for i in range(length):
        rows.append(core_chain.append_row(rows, kind="seal", payload_digest=f"sha256:payload-{i}"))
    return rows


def _chain_row_dict(row: core_chain.ChainRow) -> dict[str, object]:
    return {
        "seq": row.seq,
        "kind": row.kind,
        "payload_digest": row.payload_digest,
        "prev_hash": row.prev_hash,
        "row_hash": row.row_hash,
    }


def _receipt_bytes(*, raw_included: str, ledger_anchored: str) -> bytes:
    statement = build_statement(
        subjects=[Artifact(role="run", digest="sha256:" + "ab" * 32)],
        artifacts=[],
        limitations=Limitations(
            signature_verification="not_measured",
            raw_included=raw_included,
            ledger_anchored=ledger_anchored,
            ladder=Ladder(
                selfcheck="pass", mock_replay="pass", aa_vacuous_rate=0.0, mutation_kill_rate=0.9
            ),
        ),
        run_id="run-0001",
        produced_at="2026-09-04T00:00:00Z",
    )
    return canonical_bytes(statement)


def _write_tar(path: Path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, mode="w:") as tar:
        for member_path, data in files.items():
            info = tarfile.TarInfo(name=member_path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _good_bundle_files(rows: list[core_chain.ChainRow]) -> dict[str, bytes]:
    anchor = core_chain.anchor_for(rows)
    anchor_bytes = json.dumps(
        {"upto_seq": anchor.upto_seq, "chain_hash": anchor.chain_hash}
    ).encode("utf-8")
    chain_bytes = json.dumps([_chain_row_dict(row) for row in rows]).encode("utf-8")
    blobs = [
        Blob(
            role="receipt",
            media_type="application/json",
            data=_receipt_bytes(raw_included="no", ledger_anchored="anchored"),
        ),
        Blob(role="ledger_chain", media_type="application/json", data=chain_bytes),
        Blob(role="anchor", media_type="application/json", data=anchor_bytes),
    ]
    return plan_layout(blobs)


def _run_verify_json(tar_path: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(VERIFY_PY), str(tar_path), "--json", *extra_args],
        capture_output=True,
        text=True,
    )


def _report_of(result: subprocess.CompletedProcess[str]) -> Any:
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "attack", ["size", "missing_media_type", "duplicate_role", "duplicate_key", "utf8"]
)
def test_standalone_verifier_rejects_malformed_bundle(tmp_path: Path, attack: str) -> None:
    files = _good_bundle_files(_build_chain(2))
    if attack == "utf8":
        files["manifest-sha256.txt"] = b"\xff"
    elif attack == "duplicate_key":
        files["index.json"] = files["index.json"].replace(b'"size":', b'"si\\u007ae":999,"size":')
    else:
        index = json.loads(files["index.json"])
        if attack == "size":
            index["descriptors"][0]["size"] += 1
        elif attack == "missing_media_type":
            del index["descriptors"][0]["mediaType"]
        else:
            index["descriptors"].append(dict(index["descriptors"][0]))
        files["index.json"] = json.dumps(index).encode()
    target = tmp_path / "invalid.tar"
    _write_tar(target, files)
    result = _run_verify_json(target, *_EXPECTED_ROLES)
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    report = _report_of(result)
    assert report["integrity"] == "FAIL"
    code = "MANIFEST_MISMATCH" if attack == "utf8" else "INDEX_INVALID"
    assert code in {reason[0] for reason in report["reasons"]}


# --- clean bundle ------------------------------------------------------------


def test_clean_bundle_passes_with_anchored_ledger(tmp_path: Path) -> None:
    tar_path = tmp_path / "bundle.tar"
    _write_tar(tar_path, _good_bundle_files(_build_chain(_CHAIN_LEN)))

    result = _run_verify_json(tar_path, *_EXPECTED_ROLES)

    assert result.returncode == 0, result.stdout + result.stderr
    report = _report_of(result)
    assert report["schema"] == "verify_report.v1"
    assert report["integrity"] == "PASS"
    assert report["reasons"] == []
    assert report["signature_verification"] == "not_measured"
    assert report["ledger_anchored"] == "anchored"
    assert report["raw_included"] == "no"
    assert report["authenticity"] == "not asserted"


# --- 1-byte tamper of a blob -------------------------------------------------


def test_one_byte_tamper_of_a_blob_is_digest_mismatch(tmp_path: Path) -> None:
    files = dict(_good_bundle_files(_build_chain(_CHAIN_LEN)))
    blob_path = next(p for p in files if p.startswith("blobs/sha256/"))
    mutated = bytearray(files[blob_path])
    mutated[0] ^= 0xFF
    files[blob_path] = bytes(mutated)
    tar_path = tmp_path / "bundle.tar"
    _write_tar(tar_path, files)

    result = _run_verify_json(tar_path, *_EXPECTED_ROLES)

    assert result.returncode == 1
    report = _report_of(result)
    assert report["integrity"] == "FAIL"
    codes = {code for code, _detail in report["reasons"]}
    assert "DIGEST_MISMATCH" in codes


# --- drop attack: blob + manifest line + index descriptor, together --------


def test_drop_attack_removing_ledger_chain_is_missing_role(tmp_path: Path) -> None:
    files = dict(_good_bundle_files(_build_chain(_CHAIN_LEN)))
    index = json.loads(files["index.json"])
    dropped = next(d for d in index["descriptors"] if d["role"] == "ledger_chain")
    dropped_path = f"blobs/sha256/{dropped['digest'].removeprefix('sha256:')}"

    del files[dropped_path]
    index["descriptors"] = [d for d in index["descriptors"] if d["role"] != "ledger_chain"]
    files["index.json"] = json.dumps(index).encode("utf-8")
    manifest_lines = files["manifest-sha256.txt"].decode("utf-8").splitlines(keepends=True)
    files["manifest-sha256.txt"] = "".join(
        line for line in manifest_lines if dropped_path not in line
    ).encode("utf-8")

    tar_path = tmp_path / "bundle.tar"
    _write_tar(tar_path, files)

    result = _run_verify_json(tar_path, *_EXPECTED_ROLES)

    assert result.returncode == 1
    report = _report_of(result)
    assert report["integrity"] == "FAIL"
    assert ["MISSING_ROLE", "ledger_chain"] in report["reasons"]
    assert report["ledger_anchored"] == "not_measured"


# --- edited chain row: layout-consistent, chain-semantically broken --------


def test_edited_chain_row_is_chain_edit(tmp_path: Path) -> None:
    rows = _build_chain(_CHAIN_LEN)
    stale = rows[_TAMPERED_SEQ]
    # Same (now stale) row_hash as a valid row, but payload_digest changed --
    # the row no longer recomputes to its own row_hash (mirrors
    # tests/unit/pure/test_chain.py's own CHAIN_EDIT fixture). Baked into a
    # FRESH bundle via plan_layout so digest/manifest/index are internally
    # consistent -- the only broken thing is the chain's own semantics.
    edited_rows = list(rows)
    edited_rows[_TAMPERED_SEQ] = core_chain.ChainRow(
        seq=stale.seq,
        kind=stale.kind,
        payload_digest="TAMPERED",
        prev_hash=stale.prev_hash,
        row_hash=stale.row_hash,
    )
    tar_path = tmp_path / "bundle.tar"
    _write_tar(tar_path, _good_bundle_files(edited_rows))

    result = _run_verify_json(tar_path, *_EXPECTED_ROLES)

    assert result.returncode == 1
    report = _report_of(result)
    assert report["integrity"] == "FAIL"
    assert ["CHAIN_EDIT", str(_TAMPERED_SEQ)] in report["reasons"]
    assert report["ledger_anchored"] == "not_measured"


# --- unsafe tar member -------------------------------------------------------


def test_unsafe_path_member_is_rejected(tmp_path: Path) -> None:
    tar_path = tmp_path / "bundle.tar"
    _write_tar(tar_path, {"../evil": b"nope"})

    result = _run_verify_json(tar_path, *_EXPECTED_ROLES)

    assert result.returncode == 1
    report = _report_of(result)
    assert report["integrity"] == "FAIL"
    assert ["UNSAFE_PATH", "../evil"] in report["reasons"]


# --- malformed receipt content: RECEIPT_UNPARSEABLE, not a silent PASS -----


def test_malformed_receipt_content_yields_receipt_unparseable_and_fails(tmp_path: Path) -> None:
    # Valid JSON, but missing the "limitations"/"raw_included" shape build_statement
    # always produces -- a real receipt blob that cannot be VALIDATED, not just
    # unparseable bytes. Bundle-layer integrity (digest/manifest/index) stays
    # clean since plan_layout derives the digest from these exact bytes; the
    # only finding must come from the receipt-content parse/validate step, so a
    # future refactor cannot silently drop RECEIPT_UNPARSEABLE while leaving

    malformed_receipt = json.dumps({"predicate": {"limitations": {}}}).encode("utf-8")
    blob = Blob(role="receipt", media_type="application/json", data=malformed_receipt)
    files = plan_layout([blob])
    tar_path = tmp_path / "bundle.tar"
    _write_tar(tar_path, files)

    result = _run_verify_json(tar_path, "--expected-roles", "receipt")

    assert result.returncode == 1
    report = _report_of(result)
    assert report["integrity"] == "FAIL"
    assert ["RECEIPT_UNPARSEABLE", "receipt"] in report["reasons"]
    assert report["raw_included"] == "not_measured"


def test_receipt_blob_that_is_not_a_json_object_yields_receipt_unparseable(
    tmp_path: Path,
) -> None:
    not_an_object = json.dumps(["not", "an", "object"]).encode("utf-8")
    files = plan_layout([Blob(role="receipt", media_type="application/json", data=not_an_object)])
    tar_path = tmp_path / "bundle.tar"
    _write_tar(tar_path, files)

    result = _run_verify_json(tar_path, "--expected-roles", "receipt")

    assert result.returncode == 1
    report = _report_of(result)
    assert report["integrity"] == "FAIL"
    assert ["RECEIPT_UNPARSEABLE", "receipt"] in report["reasons"]
    assert report["raw_included"] == "not_measured"


# --- honesty note: present in both JSON and human-readable output ----------


def test_report_never_asserts_issuer_authenticity(tmp_path: Path) -> None:
    tar_path = tmp_path / "bundle.tar"
    _write_tar(tar_path, _good_bundle_files(_build_chain(_CHAIN_LEN)))

    result = _run_verify_json(tar_path, *_EXPECTED_ROLES)

    report = _report_of(result)
    assert report["authenticity"] == "not asserted"
    assert "does not assert issuer authenticity" in report["honesty_note"]


def test_human_readable_summary_states_the_honesty_note(tmp_path: Path) -> None:
    tar_path = tmp_path / "bundle.tar"
    _write_tar(tar_path, _good_bundle_files(_build_chain(_CHAIN_LEN)))

    result = subprocess.run(
        [sys.executable, "-I", str(VERIFY_PY), str(tar_path), *_EXPECTED_ROLES],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "integrity: PASS" in result.stdout
    assert "does not assert issuer authenticity" in result.stdout

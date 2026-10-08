"""Standalone behavior and boundary checks."""

import hashlib
import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from underwrite_core.bundle import (
    Blob,
    BundleError,
    BundleReason,
    BundleVerdict,
    Descriptor,
    ExpectedSet,
    _check_manifest,  # pyright: ignore[reportPrivateUsage]
    _parse_index,  # pyright: ignore[reportPrivateUsage]
    plan_layout,
    verify_layout,
)


def test_golden_layout_two_blobs_exact_paths_and_manifest() -> None:
    blob_a = Blob(role="receipt", media_type="application/json", data=b'{"a":1}')
    blob_b = Blob(role="index", media_type="text/plain", data=b"hello world")
    hex_a = hashlib.sha256(blob_a.data).hexdigest()
    hex_b = hashlib.sha256(blob_b.data).hexdigest()
    path_a, path_b = f"blobs/sha256/{hex_a}", f"blobs/sha256/{hex_b}"

    files = plan_layout([blob_a, blob_b])

    assert set(files) == {path_a, path_b, "manifest-sha256.txt", "index.json"}
    assert files[path_a] == blob_a.data
    assert files[path_b] == blob_b.data

    expected_manifest = "".join(
        f"{h}  {p}\n" for p, h in sorted({path_a: hex_a, path_b: hex_b}.items())
    )
    assert files["manifest-sha256.txt"].decode("utf-8") == expected_manifest

    index = json.loads(files["index.json"])
    assert index == {
        "schema": "bundle_index.v1",
        "descriptors": [
            {
                "role": "index",
                "mediaType": "text/plain",
                "digest": f"sha256:{hex_b}",
                "size": len(blob_b.data),
            },
            {
                "role": "receipt",
                "mediaType": "application/json",
                "digest": f"sha256:{hex_a}",
                "size": len(blob_a.data),
            },
        ],
    }


def test_verify_layout_ok_on_freshly_planned_bundle() -> None:
    blobs = [
        Blob(role="receipt", media_type="application/json", data=b'{"a":1}'),
        Blob(role="index", media_type="text/plain", data=b"hello world"),
    ]
    files = plan_layout(blobs)
    expected = ExpectedSet(required_roles=frozenset({"receipt", "index"}))

    assert verify_layout(files, expected) == BundleVerdict(ok=True, reasons=())


def test_one_byte_mutation_of_a_blob_is_caught_by_digest_and_manifest() -> None:
    blob = Blob(role="receipt", media_type="application/json", data=b"hello world")
    files = dict(plan_layout([blob]))
    path = f"blobs/sha256/{hashlib.sha256(blob.data).hexdigest()}"
    mutated = bytearray(files[path])
    mutated[0] ^= 0xFF
    files[path] = bytes(mutated)

    verdict = verify_layout(files, ExpectedSet(required_roles=frozenset({"receipt"})))

    assert verdict.ok is False
    assert (BundleReason.DIGEST_MISMATCH, path) in verdict.reasons
    assert (BundleReason.MANIFEST_MISMATCH, path) in verdict.reasons


def test_drop_attack_removing_blob_manifest_line_and_descriptor_yields_missing_role() -> None:
    blobs = [
        Blob(role="receipt", media_type="application/json", data=b'{"a":1}'),
        Blob(role="evidence", media_type="application/json", data=b'{"b":2}'),
    ]
    files = dict(plan_layout(blobs))
    dropped_path = f"blobs/sha256/{hashlib.sha256(blobs[1].data).hexdigest()}"

    # Remove the blob, its manifest line, and its index descriptor -- the
    # attack recomputes nothing else (bundle_verifier_standalone.py:10-12 style).
    del files[dropped_path]
    manifest_lines = files["manifest-sha256.txt"].decode("utf-8").splitlines(keepends=True)
    files["manifest-sha256.txt"] = "".join(
        line for line in manifest_lines if dropped_path not in line
    ).encode("utf-8")
    index = json.loads(files["index.json"])
    index["descriptors"] = [d for d in index["descriptors"] if d["role"] != "evidence"]
    files["index.json"] = json.dumps(index).encode("utf-8")

    expected = ExpectedSet(required_roles=frozenset({"receipt", "evidence"}))
    verdict = verify_layout(files, expected)

    assert verdict == BundleVerdict(ok=False, reasons=((BundleReason.MISSING_ROLE, "evidence"),))


def test_extra_unreferenced_blob_is_flagged() -> None:
    blob = Blob(role="receipt", media_type="application/json", data=b'{"a":1}')
    files = dict(plan_layout([blob]))
    extra_data = b"nobody points at me"
    extra_hex = hashlib.sha256(extra_data).hexdigest()
    extra_path = f"blobs/sha256/{extra_hex}"
    files[extra_path] = extra_data
    # Keep the manifest truthful about the extra blob (no descriptor though) so
    # the only finding is EXTRA_BLOB, not also a MANIFEST_MISMATCH.
    files["manifest-sha256.txt"] += f"{extra_hex}  {extra_path}\n".encode()

    verdict = verify_layout(files, ExpectedSet(required_roles=frozenset({"receipt"})))

    assert verdict == BundleVerdict(ok=False, reasons=((BundleReason.EXTRA_BLOB, extra_path),))


def test_missing_index_is_flagged() -> None:
    blob = Blob(role="receipt", media_type="application/json", data=b'{"a":1}')
    files = dict(plan_layout([blob]))
    del files["index.json"]

    verdict = verify_layout(files, ExpectedSet(required_roles=frozenset({"receipt"})))

    assert verdict == BundleVerdict(ok=False, reasons=((BundleReason.MISSING_INDEX, "index.json"),))


def test_missing_manifest_is_flagged() -> None:
    blob = Blob(role="receipt", media_type="application/json", data=b'{"a":1}')
    files = dict(plan_layout([blob]))
    del files["manifest-sha256.txt"]

    verdict = verify_layout(files, ExpectedSet(required_roles=frozenset({"receipt"})))

    assert verdict == BundleVerdict(
        ok=False, reasons=((BundleReason.MISSING_MANIFEST, "manifest-sha256.txt"),)
    )


def test_index_descriptor_with_wrong_digest_is_index_invalid() -> None:
    blob = Blob(role="receipt", media_type="application/json", data=b'{"a":1}')
    files = dict(plan_layout([blob]))
    index = json.loads(files["index.json"])
    index["descriptors"][0]["digest"] = "sha256:" + "0" * 64
    files["index.json"] = json.dumps(index).encode("utf-8")

    verdict = verify_layout(files, ExpectedSet(required_roles=frozenset({"receipt"})))

    assert verdict.ok is False
    assert (BundleReason.INDEX_INVALID, "receipt") in verdict.reasons


def test_duplicate_role_raises_bundle_error() -> None:
    blobs = [
        Blob(role="receipt", media_type="application/json", data=b"a"),
        Blob(role="receipt", media_type="application/json", data=b"b"),
    ]
    with pytest.raises(BundleError, match="DUPLICATE_ROLE"):
        plan_layout(blobs)


@pytest.mark.parametrize("field", ["role", "mediaType", "digest", "size"])
def test_verify_rejects_missing_descriptor_fields(field: str) -> None:
    files = plan_layout([Blob("receipt", "application/json", b"{}")])
    index = json.loads(files["index.json"])
    del index["descriptors"][0][field]
    files["index.json"] = json.dumps(index).encode()
    assert not verify_layout(files, ExpectedSet(frozenset({"receipt"}))).ok


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", ""),
        ("mediaType", ""),
        ("mediaType", []),
        ("size", True),
        ("size", -1),
        ("size", "2"),
        ("size", 2.5),
        ("size", 99999),
        ("digest", "sha256:" + "0" * 64 + "\n"),
        ("extra", "unrecognized"),
    ],
)
def test_verify_rejects_invalid_descriptor_fields(field: str, value: object) -> None:
    files = plan_layout([Blob("receipt", "application/json", b"{}")])
    index = json.loads(files["index.json"])
    index["descriptors"][0][field] = value
    files["index.json"] = json.dumps(index).encode()
    assert not verify_layout(files, ExpectedSet(frozenset({"receipt"}))).ok


def test_verify_rejects_duplicate_roles_and_extra_index_fields() -> None:
    for duplicate in (True, False):
        files = plan_layout([Blob("receipt", "application/json", b"{}")])
        index = json.loads(files["index.json"])
        if duplicate:
            index["descriptors"].append(dict(index["descriptors"][0]))
        else:
            index["extra"] = "unrecognized"
        files["index.json"] = json.dumps(index).encode()
        assert not verify_layout(files, ExpectedSet(frozenset({"receipt"}))).ok


def test_invalid_utf8_manifest_preserves_independent_findings() -> None:
    files = plan_layout([Blob("receipt", "application/json", b"{}")])
    files["manifest-sha256.txt"] = b"\xff"
    del files["index.json"]
    verdict = verify_layout(files, ExpectedSet(frozenset({"receipt"})))
    assert not verdict.ok
    assert (BundleReason.MANIFEST_MISMATCH, "manifest-sha256.txt") in verdict.reasons
    assert (BundleReason.MISSING_INDEX, "index.json") in verdict.reasons


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        (b'"size":2', b'"size":999,"size":2'),
        (b'"size":2', b'"si\\u007ae":999,"size":2'),
        (b'"schema":', b'"schema":"wrong","schema":'),
    ],
)
def test_index_duplicate_keys_are_invalid(original: bytes, replacement: bytes) -> None:
    files = plan_layout([Blob("receipt", "application/json", b"{}")])
    assert original in files["index.json"]
    files["index.json"] = files["index.json"].replace(original, replacement)
    verdict = verify_layout(files, ExpectedSet(frozenset({"receipt"})))
    assert not verdict.ok
    assert (BundleReason.INDEX_INVALID, "index.json") in verdict.reasons


# --- _parse_index: direct coverage of every malformed-index shape ----------
# (verify_layout only ever reaches these through a well-formed index.json,
# so the shapes below -- unparseable JSON, wrong top-level type, wrong
# schema, non-list descriptors, and non-dict/partial descriptor entries --
# are otherwise never exercised.)


def test_parse_index_rejects_unparseable_json() -> None:
    descriptors, findings = _parse_index(b"not json at all")
    assert descriptors == []
    assert findings == [(BundleReason.INDEX_INVALID, "index.json")]


def test_parse_index_rejects_non_object_top_level() -> None:
    descriptors, findings = _parse_index(b"[1, 2, 3]")
    assert descriptors == []
    assert findings == [(BundleReason.INDEX_INVALID, "index.json")]


def test_parse_index_rejects_wrong_schema() -> None:
    data = json.dumps({"schema": "wrong.v1", "descriptors": []}).encode("utf-8")
    descriptors, findings = _parse_index(data)
    assert descriptors == []
    assert findings == [(BundleReason.INDEX_INVALID, "index.json")]


def test_parse_index_rejects_non_list_descriptors() -> None:
    data = json.dumps({"schema": "bundle_index.v1", "descriptors": "nope"}).encode("utf-8")
    descriptors, findings = _parse_index(data)
    assert descriptors == []
    assert findings == [(BundleReason.INDEX_INVALID, "index.json")]


def test_parse_index_flags_non_dict_descriptor_entry_by_position() -> None:
    valid_entry = {
        "role": "a",
        "digest": "sha256:" + "0" * 64,
        "mediaType": "text/plain",
        "size": 0,
    }
    data = json.dumps(
        {"schema": "bundle_index.v1", "descriptors": [valid_entry, "not-a-dict"]}
    ).encode("utf-8")
    descriptors, findings = _parse_index(data)
    assert descriptors == [Descriptor("a", "text/plain", "sha256:" + "0" * 64, 0)]
    assert findings == [(BundleReason.INDEX_INVALID, "descriptors[1]")]


def test_parse_index_flags_descriptor_missing_role_or_digest() -> None:
    data = json.dumps(
        {
            "schema": "bundle_index.v1",
            "descriptors": [{"digest": "sha256:" + "0" * 64}, {"role": "b"}],
        }
    ).encode("utf-8")
    descriptors, findings = _parse_index(data)
    assert descriptors == []
    assert findings == [
        (BundleReason.INDEX_INVALID, "descriptors[0]"),
        (BundleReason.INDEX_INVALID, "descriptors[1]"),
    ]


def test_missing_index_via_verify_layout_short_circuits_before_parse_index() -> None:
    # Documents the MISSING_INDEX/INDEX_INVALID boundary that _check_index owns
    # before ever calling _parse_index (see test_missing_index_is_flagged too).
    blob = Blob(role="receipt", media_type="application/json", data=b'{"a":1}')
    files = dict(plan_layout([blob]))
    del files["index.json"]
    verdict = verify_layout(files, ExpectedSet(required_roles=frozenset({"receipt"})))
    assert verdict == BundleVerdict(ok=False, reasons=((BundleReason.MISSING_INDEX, "index.json"),))


# --- _check_manifest: declared-vs-actual asymmetry and separator direction -


def test_check_manifest_flags_a_file_present_but_undeclared() -> None:
    blob = Blob(role="receipt", media_type="application/json", data=b'{"a":1}')
    files = dict(plan_layout([blob]))
    # Add a file to the layout without adding its manifest line: `actual` now
    # has a path `declared` does not. Only the union (not the intersection)
    # of both key sets catches this.
    undeclared_path = "blobs/sha256/" + "1" * 64
    files[undeclared_path] = b"sneaked in"

    findings = _check_manifest(files)

    assert (BundleReason.MANIFEST_MISMATCH, undeclared_path) in findings


def test_check_manifest_uses_first_double_space_as_the_hex_path_separator() -> None:
    # A manifest line is "<hex>  <path>\n"; if a path itself later contained
    # "  " (unusual but not forbidden by the parser), the separator must be
    # the FIRST "  ", not the last, so the hex column never gets sourced from
    # inside the path.
    hex_digest = "0" * 64
    path = "blobs/sha256/weird  path  with  spaces"
    manifest = f"{hex_digest}  {path}\n".encode()
    files = {path: b"content", "manifest-sha256.txt": manifest}

    findings = _check_manifest(files)

    # partition("  ") on "<hex>  <path>" splits at the FIRST "  ", giving
    # exactly hex_digest as the declared hash for the full path (including
    # its internal double spaces) -- content's real digest never matches
    # this placeholder hex, so it is correctly flagged, at the FULL path.
    assert findings == [(BundleReason.MANIFEST_MISMATCH, path)]


_ROLE_POOL: tuple[str, ...] = tuple(f"role-{i}" for i in range(6))


def _blobs_from_role_data(items: list[tuple[str, bytes]]) -> list[Blob]:
    return [
        Blob(role=role, media_type="application/octet-stream", data=data) for role, data in items
    ]


def _unique_role_blob_lists() -> st.SearchStrategy[list[Blob]]:
    return st.lists(
        st.tuples(st.sampled_from(_ROLE_POOL), st.binary(min_size=1, max_size=32)),
        min_size=1,
        max_size=len(_ROLE_POOL),
        unique_by=lambda item: item[0],
    ).map(_blobs_from_role_data)


@settings(max_examples=40, deadline=None)
@given(_unique_role_blob_lists(), st.data())
def test_property_plan_then_verify_is_ok_and_single_byte_mutation_is_detected(
    blobs: list[Blob], data: st.DataObject
) -> None:
    files = plan_layout(blobs)
    expected = ExpectedSet(required_roles=frozenset(blob.role for blob in blobs))
    assert verify_layout(files, expected) == BundleVerdict(ok=True, reasons=())

    target = data.draw(st.sampled_from(blobs))
    path = f"blobs/sha256/{hashlib.sha256(target.data).hexdigest()}"
    byte_index = data.draw(st.integers(min_value=0, max_value=len(target.data) - 1))
    tampered = dict(files)
    mutated = bytearray(tampered[path])
    mutated[byte_index] ^= 0xFF
    tampered[path] = bytes(mutated)

    verdict = verify_layout(tampered, expected)
    assert verdict.ok is False
    assert (BundleReason.DIGEST_MISMATCH, path) in verdict.reasons

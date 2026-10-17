# ADR-022: Validate packaged README rendering in the existing release gate

Status: accepted for the usage-guidance slice.

The `underwrite` 0.1.0 PyPI description exposed repository-relative image and
document links. A later source check missed the outer `LICENSE` href in a nested
Markdown badge; actual package rendering found it. An ad-hoc preview also used
the email parser's implicit text payload and corrupted Korean characters. The
release check therefore needs to inspect the bytes and HTML that a package
reader sees, not just grep the authored Markdown.

Use `readme-renderer[md]==46.0` **only in the development group**. The existing
`test_packaged_contracts.py` build fixture already produces both app and core
wheel/sdist artifacts. The gate decodes each artifact's raw `METADATA` or
`PKG-INFO` payload with strict UTF-8, compares it with its authored README,
renders Markdown through PyPA's renderer, and uses stdlib `HTMLParser` to check
rendered `href`, image `src`, and resolved in-page fragment targets. It also
renders the Korean repository README separately, since it is not the wheel's
long description. The test does not fetch links, implement Markdown parsing,
add a CI job, or change runtime output.

This dependency is justified by the demonstrated nested-link and Unicode
failures. A hand-written Markdown regex already missed the badge, while a
stdlib-only check cannot reliably model PyPA's rendered headings, percent-
encoded Korean fragments, or sanitized nested HTML. The pinned renderer accepts
`str` and may return `None`; the test handles that explicitly. Renderer output
is a local preview, not proof of the exact Warehouse deployment, browser layout,
image proxy, or permanent external URL availability.

The isolated CPython 3.12 dependency check contained renderer 46.0 (Apache-2.0),
Comrak 0.0.16 (MIT classifier), nh3 0.3.7 (MIT metadata), Docutils 0.23
(multiple license notices/classifiers including public-domain, BSD and GPL),
and Pygments 2.21.0 (BSD-2-Clause). Their installed package directories totaled
about 13 MiB; Pygments was already present in the development lock, while the
other four packages are new. These are test-only costs and metadata observations,
not a claim that all transitive material has one uniform license. Runtime and
pure dependency groups remain unchanged; wheels and sdists must not gain this
renderer as an install requirement.

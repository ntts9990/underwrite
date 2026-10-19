# ADR-023: Bound full preflight to two file-grouped pytest workers

Status: accepted for the local and protected CI cost slice.

The shared full preflight runs all 6,156 tests before building and exercising
installed distributions. On unchanged source, local serial pytest passed in
182.00 seconds; the protected Linux CI run spent 485.93 seconds in pytest out of
about 613 seconds in the shared preflight. Dependency sync took under a second,
so caching packages would not address the measured bottleneck.

Use `pytest-xdist==3.8.0` in the **development group only**, with two workers
and `--dist loadfile` for the full pytest step. A disposable local invocation
passed the same 6,156 tests in 103.77 seconds, versus the matched 182.00-second
serial run. File-level grouping keeps one test module in one worker. This is an
observed local reduction, not a promise of the same Linux speed or absence of
cross-worker races. The first protected CI run must pass before the optimization
is considered delivered; compare its actual test and whole-step durations with
the previous run. If it fails or is materially slower, revert the scheduling
change rather than omit tests.

Preserve all full preflight stages and the separate pure-only serial test run.
The documentation lane and built-wheel smoke stay as they are. The existing
`test_preflight.py` command-shape regression asserts which step is parallel and
that pure/docs verification remains serial. No app/core runtime dependency,
matrix job, scheduled run, package version, or output contract changes.

The isolated trial resolved pytest-xdist 3.8.0 and execnet 2.1.2; both report
MIT license metadata. They are development tools, not distribution requirements.

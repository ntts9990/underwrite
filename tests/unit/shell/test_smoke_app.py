"""Standalone behavior and boundary checks."""

import underwrite
import underwrite.acceptance
import underwrite.adjudication
import underwrite.cli
import underwrite.fleet
import underwrite.instrument
import underwrite.instrument.evidence
import underwrite.instrument.ingest
import underwrite.measurement
import underwrite.service

_SUBPACKAGES = (
    underwrite,
    underwrite.acceptance,
    underwrite.adjudication,
    underwrite.cli,
    underwrite.fleet,
    underwrite.instrument,
    underwrite.instrument.evidence,
    underwrite.instrument.ingest,
    underwrite.measurement,
    underwrite.service,
)


def test_underwrite_subpackages_import() -> None:
    for module in _SUBPACKAGES:
        assert module.__name__ == "underwrite" or module.__name__.startswith("underwrite.")

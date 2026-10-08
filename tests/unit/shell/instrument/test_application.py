"""Application behavior without CLI imports or an assumed installed resource layout."""

import pytest
from _inspection_cases import inspection_contracts as inspection_contracts
from _instrument_errors import ERROR_CASES
from underwrite_core.canonical import CanonicalizationError

from underwrite.instrument.ingest import application
from underwrite.instrument.ingest.project import ProjectionError, Source
from underwrite.instrument.ingest.schema import SchemaConfigurationError
from underwrite.instrument.ingest.transport import DecodeError, TransportError


@pytest.mark.parametrize(("error", "code", "reason", "exit_code"), ERROR_CASES)
def test_safe_diagnostic_contract(error: Exception, code: str, reason: str, exit_code: int) -> None:
    assert application.instrument_diagnostic(error, "project") == {
        "schema": "instrument_error.v1",
        "command": "project",
        "code": code,
        "reason": reason,
        "exit_code": exit_code,
    }


@pytest.mark.parametrize(
    "cause,code,reason",
    [
        ("INT_OUT_OF_SAFE_RANGE", "CANONICAL_NUMBER_INCOMPATIBLE", "INT_OUT_OF_SAFE_RANGE"),
        ("UNSUPPORTED_TYPE", "INVALID_INPUT", "INVALID_PROJECTION"),
    ],
)
def test_canonical_numeric_cause_is_preserved(cause: str, code: str, reason: str) -> None:
    error = ProjectionError("INVALID_PROJECTION")
    error.__cause__ = CanonicalizationError(cause)
    result = application.instrument_diagnostic(error, "ingest")
    assert result["code"] == code and result["reason"] == reason


@pytest.mark.parametrize("error", [ValueError("secret"), ProjectionError("NEW_BUG")])
def test_unknown_errors_do_not_become_registered_findings(error: Exception) -> None:
    with pytest.raises(KeyError):
        application.instrument_diagnostic(error, "ingest")


@pytest.mark.usefixtures("inspection_contracts")
def test_factory_forwards_limits_and_trusted_registry() -> None:
    ingestor = application.packaged_ingestor(max_bytes=4, max_depth=1)
    with pytest.raises(TransportError, match="^BYTE_LIMIT_EXCEEDED$"):
        ingestor.http_body(b"12345", "local", Source("underwrite.evidence-bundle", "v1", None))
    with pytest.raises(DecodeError, match="^DEPTH_LIMIT_EXCEEDED$"):
        ingestor.http_body(b"[[]]", "local", Source("underwrite.evidence-bundle", "v1", None))


def test_factory_materialization_failure_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args: object, **kwargs: object) -> None:
        raise OSError("secret resource path")

    monkeypatch.setattr(application, "as_file", unavailable)
    with pytest.raises(SchemaConfigurationError, match="^INVALID_OBSERVATION_SCHEMA$"):
        application.packaged_ingestor(max_bytes=4, max_depth=1)

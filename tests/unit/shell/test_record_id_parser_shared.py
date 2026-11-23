"""Record CLI argument adapters preserve their distinct user-facing error contracts."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import cast

import pytest

from underwrite.cli import (
    record_baseline,
    record_cross_artifact,
    record_deepeval,
    record_langfuse,
    record_measure,
    record_promptfoo,
    record_trajectory,
)
from underwrite.cli.evidence import EvidenceError
from underwrite.cli.record_capture import new_record_id_argument


def _id_type(configure: Callable[[argparse.ArgumentParser], None]) -> Callable[[str], str]:
    parser = argparse.ArgumentParser()
    configure(parser)
    action = next(row for row in parser._actions if "--id" in row.option_strings)  # noqa: SLF001
    assert action.type is not None
    return cast(Callable[[str], str], action.type)


@pytest.mark.parametrize(
    "configure",
    [
        record_promptfoo.configure,
        record_cross_artifact.configure,
        record_deepeval.configure,
        record_measure.configure,
        record_baseline.configure,
        record_langfuse.configure,
    ],
)
def test_new_record_id_parser_keeps_exact_error(
    configure: Callable[[argparse.ArgumentParser], None],
) -> None:
    convert = _id_type(configure)
    assert convert is new_record_id_argument
    assert convert("valid.001") == "valid.001"
    with pytest.raises(argparse.ArgumentTypeError, match="^valid new record ID required$"):
        convert("bad id")


def test_trajectory_record_id_keeps_different_error_owner() -> None:
    convert = _id_type(record_trajectory.configure)
    assert convert("valid.001") == "valid.001"
    with pytest.raises(EvidenceError) as caught:
        convert("bad id")
    assert caught.value.payload["code"] == "USAGE_ERROR"
    assert caught.value.payload["reason"] == "INVALID_ARGUMENTS_SEE_HELP"

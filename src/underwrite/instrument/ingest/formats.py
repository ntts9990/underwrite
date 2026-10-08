"""Aggregate code-owned codec selectors without replacing any owner."""

from collections.abc import Iterable, Mapping

from underwrite.instrument.ingest.codecs import (
    deepeval,
    evidence_bundle,
    inspect,
    langfuse,
    openinference,
    otlp_json,
    promptfoo,
)
from underwrite.instrument.ingest.transport import Codec


class CodecConfigurationError(ValueError):
    """Two trusted codec owners registered the same selector."""


def _merge(registries: Iterable[Mapping[tuple[str, str], Codec]]) -> dict[tuple[str, str], Codec]:
    codecs: dict[tuple[str, str], Codec] = {}
    for registry in registries:
        for key, codec in registry.items():
            if key in codecs:
                raise CodecConfigurationError("DUPLICATE_CODEC_SELECTOR")
            codecs[key] = codec
    return codecs


def native_codecs() -> dict[tuple[str, str], Codec]:
    """Return an owned registry; ambiguous selectors are configuration failures."""
    return _merge((evidence_bundle.CODECS,))


def external_codecs() -> dict[tuple[str, str], Codec]:
    """Vendor-captured external profiles, one exact version each."""
    return _merge(
        (
            deepeval.CODECS,
            inspect.CODECS,
            langfuse.CODECS,
            openinference.CODECS,
            otlp_json.CODECS,
            promptfoo.CODECS,
        )
    )


def trusted_codecs() -> dict[tuple[str, str], Codec]:
    """Every selector the installed CLI dispatches; native and external never overlap."""
    return _merge((native_codecs(), external_codecs()))

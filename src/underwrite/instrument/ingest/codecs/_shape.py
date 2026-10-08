"""JSON shape checks shared by the v1 external readers; every reason stays its codec's own.

Each reader refuses the same handful of shape faults — not an object, an unknown key, not
an array, a blank identifier, a non-string, a non-bool, a non-number. Only the raised type
and the refusal text differ, and that text is contract, not implementation. So
``Shape`` owns the predicates and nothing else: a codec binds it once to its own error
class and reason prefix, and each call site still names the detail that follows the colon.
A check whose rule is one format's own (Langfuse's exact key match, DeepEval's float-only
number, the OTLP proto scalars) stays in that codec, built on ``fail``."""

from __future__ import annotations

from typing import NoReturn, TypeGuard


def is_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


class Shape:
    """Shared structural predicates bound to one codec's error and reason prefix.

    Instances are configured once as module constants. Format-specific rules
    stay in their owning codec.
    """

    __slots__ = ("error", "prefix")

    error: type[ValueError]
    prefix: str

    def __init__(self, error: type[ValueError], prefix: str) -> None:
        self.error = error
        self.prefix = prefix

    def fail(self, kind: str, detail: str) -> NoReturn:
        """Raise this codec's error as ``<PREFIX>_<kind>: <detail>`` — the pinned bytes."""
        raise self.error(f"{self.prefix}_{kind}: {detail}")

    def as_object(self, value: object, detail: str) -> dict[str, object]:
        if not is_dict(value):
            self.fail("OBJECT_REQUIRED", detail)
        return value

    def as_closed_object(
        self, value: object, keys: frozenset[str], detail: str
    ) -> dict[str, object]:
        """The object, refused when it carries a key outside the pinned set."""
        payload = self.as_object(value, detail)
        if not keys.issuperset(payload):
            self.fail("UNKNOWN_FIELD", detail)
        return payload

    def as_array(self, value: object, detail: str) -> list[object]:
        if not is_list(value):
            self.fail("ARRAY_REQUIRED", detail)
        return value

    # `detail` defaults to the key itself: the readers that name only the faulty key say
    # nothing twice, and the readers that qualify it ("<owner> <key>") pass the full text.
    def identifier(self, payload: dict[str, object], key: str, detail: str | None = None) -> None:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            self.fail("IDENTIFIER_REQUIRED", key if detail is None else detail)

    def text(self, payload: dict[str, object], key: str, detail: str | None = None) -> None:
        if not isinstance(payload.get(key), str):
            self.fail("TEXT_REQUIRED", key if detail is None else detail)

    def text_or_null(self, payload: dict[str, object], key: str, detail: str | None = None) -> None:
        # `string | null` in the producer's own types: the key is written, null is a value.
        if key not in payload or not isinstance(payload[key], str | None):
            self.fail("TEXT_OR_NULL_REQUIRED", key if detail is None else detail)

    def flag(self, payload: dict[str, object], key: str, detail: str | None = None) -> None:
        if type(payload.get(key)) is not bool:
            self.fail("FLAG_REQUIRED", key if detail is None else detail)

    def count(self, payload: dict[str, object], key: str, detail: str | None = None) -> None:
        if type(payload.get(key)) is not int:
            self.fail("COUNT_REQUIRED", key if detail is None else detail)

    def number(self, payload: dict[str, object], key: str, detail: str | None = None) -> None:
        # JSON numbers only; a bool is not a number.
        if type(payload.get(key)) not in (int, float):
            self.fail("NUMBER_REQUIRED", key if detail is None else detail)

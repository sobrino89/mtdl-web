"""Conditions of a subscription: field, operator, value, regex switch, enabled switch."""

import datetime as dt
import functools
from dataclasses import dataclass
from enum import StrEnum

import regex

from mtdl_web.text import normalize


class Field(StrEnum):
    TOPIC = "topic"
    TITLE = "title"
    DESCRIPTION = "description"
    CHANNEL = "channel"
    DATE = "date"
    DURATION = "duration"


class Operator(StrEnum):
    IS = "is"
    IS_NOT = "is_not"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    ANY_OF = "any_of"
    NONE_OF = "none_of"
    AFTER = "after"
    BEFORE = "before"
    AT_LEAST = "at_least"
    AT_MOST = "at_most"


TEXT_FIELDS = frozenset({Field.TOPIC, Field.TITLE, Field.DESCRIPTION})
OPERATORS = {
    **dict.fromkeys(TEXT_FIELDS, frozenset({Operator.IS, Operator.IS_NOT, Operator.CONTAINS, Operator.NOT_CONTAINS})),
    Field.CHANNEL: frozenset({Operator.ANY_OF, Operator.NONE_OF}),
    Field.DATE: frozenset({Operator.AFTER, Operator.BEFORE}),
    Field.DURATION: frozenset({Operator.AT_LEAST, Operator.AT_MOST}),
}
# With regex only "matches" / "does not match", search semantics; anchors in the pattern decide the rest.
REGEX_OPERATORS = frozenset({Operator.IS, Operator.IS_NOT})

# Case-insensitive with full case folding ("ß" matches "SS"), as without regex.
FLAGS = regex.V0 | regex.IGNORECASE | regex.FULLCASE

Value = str | tuple[str, ...] | dt.date | int


class RuleError(ValueError):
    """An invalid condition. ``code`` and ``params`` let the UI explain it in its own language."""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(f"{code}: {params}")
        self.code = code
        self.params = params


@functools.lru_cache(maxsize=1024)
def compile_pattern(value: str, is_regex: bool) -> regex.Pattern[str]:
    """The pattern a text condition matches with; plain text is compared as literal comparison text."""
    source = value if is_regex else regex.escape(normalize(value))
    try:
        return regex.compile(source, FLAGS)
    except regex.error as exc:
        # The stubs lack msg and pos; the runtime error has both.
        message, position = getattr(exc, "msg", str(exc)), getattr(exc, "pos", None)
        raise RuleError("invalid_regex", message=message, position=position) from exc


@dataclass(frozen=True, slots=True)
class Condition:
    field: Field
    operator: Operator
    value: Value
    regex: bool = False
    enabled: bool = True

    def __post_init__(self) -> None:
        try:
            field, operator = Field(self.field), Operator(self.operator)
        except ValueError as exc:
            raise RuleError("unknown_field_or_operator", field=self.field, operator=self.operator) from exc
        object.__setattr__(self, "field", field)
        object.__setattr__(self, "operator", operator)
        if operator not in OPERATORS[field]:
            raise RuleError("operator_not_allowed", field=field, operator=operator)
        if self.regex and field not in TEXT_FIELDS:
            raise RuleError("regex_not_allowed", field=field)
        if self.regex and operator not in REGEX_OPERATORS:
            raise RuleError("regex_operator", operator=operator)
        value = self.value
        if field in TEXT_FIELDS:
            if not isinstance(value, str) or not value.strip():
                raise RuleError("invalid_value", field=field, expected="text")
            compile_pattern(value, self.regex)
        elif field is Field.CHANNEL:
            if isinstance(value, list):
                value = tuple(value)
                object.__setattr__(self, "value", value)
            if not isinstance(value, tuple) or not value or not all(isinstance(v, str) and v for v in value):
                raise RuleError("invalid_value", field=field, expected="list of channels")
        elif field is Field.DATE:
            if not isinstance(value, dt.date) or isinstance(value, dt.datetime):
                raise RuleError("invalid_value", field=field, expected="date")
        elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuleError("invalid_value", field=field, expected="minutes")

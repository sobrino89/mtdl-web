"""Evaluates a flat AND list of conditions against films, with a reason per condition for the preview."""

import datetime as dt
import zoneinfo
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from mtdl_web.models import Film
from mtdl_web.rules.model import Condition, Field, Operator, compile_pattern
from mtdl_web.text import fold, normalize

BERLIN = zoneinfo.ZoneInfo("Europe/Berlin")
REGEX_TIMEOUT = 0.05  # seconds per match; titles are short, a sane pattern needs microseconds

Code = Literal["ok", "value_missing", "regex_timeout", "disabled"]


@dataclass(frozen=True, slots=True)
class ConditionResult:
    passed: bool
    code: Code
    # The film's value as compared: comparison text, channel, local date (ISO) or duration in seconds.
    actual: str | int | None = None
    # Where the text or pattern was found in ``actual``; for negated operators that is why it failed.
    span: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class Evaluation:
    film: Film
    passed: bool
    results: tuple[ConditionResult, ...]  # in the order of the conditions

    @property
    def failed(self) -> list[int]:
        """Indexes of the conditions that excluded the film."""
        return [i for i, r in enumerate(self.results) if not r.passed]


class RuleSet:
    def __init__(
        self,
        conditions: Sequence[Condition],
        *,
        timezone: dt.tzinfo = BERLIN,
        regex_timeout: float = REGEX_TIMEOUT,
    ) -> None:
        self.conditions = tuple(conditions)
        self._tz = timezone
        self._timeout = regex_timeout

    def evaluate(self, film: Film) -> Evaluation:
        results = tuple(self._check(c, film) for c in self.conditions)
        return Evaluation(film, all(r.passed for r in results), results)

    def matching(self, films: Iterable[Film]) -> list[Film]:
        return [f for f in films if self.evaluate(f).passed]

    def _check(self, condition: Condition, film: Film) -> ConditionResult:
        if not condition.enabled:
            return ConditionResult(True, "disabled")
        field, operator, value = condition.field, condition.operator, condition.value
        if field is Field.CHANNEL:
            assert isinstance(value, tuple)
            found = fold(film.channel) in {fold(v) for v in value}
            return ConditionResult(found == (operator is Operator.ANY_OF), "ok", film.channel)
        if field is Field.DATE:
            assert isinstance(value, dt.date)
            if film.timestamp is None:
                return ConditionResult(False, "value_missing")
            start = dt.datetime.fromtimestamp(film.timestamp, self._tz)
            day_begins = dt.datetime.combine(value, dt.time(0), self._tz)
            after = start >= day_begins
            return ConditionResult(after == (operator is Operator.AFTER), "ok", start.date().isoformat())
        if field is Field.DURATION:
            assert isinstance(value, int)
            if film.duration is None:
                return ConditionResult(False, "value_missing")
            limit = value * 60
            ok = film.duration >= limit if operator is Operator.AT_LEAST else film.duration <= limit
            return ConditionResult(ok, "ok", film.duration)

        assert isinstance(value, str)
        text = normalize(getattr(film, field.value))
        pattern = compile_pattern(value, condition.regex)
        try:
            if operator in (Operator.IS, Operator.IS_NOT) and not condition.regex:
                match = pattern.fullmatch(text, timeout=self._timeout)
            else:
                match = pattern.search(text, timeout=self._timeout)
        except TimeoutError:
            return ConditionResult(False, "regex_timeout", text)
        positive = operator in (Operator.IS, Operator.CONTAINS)
        span = match.span() if match else None
        return ConditionResult((match is not None) == positive, "ok", text, span)

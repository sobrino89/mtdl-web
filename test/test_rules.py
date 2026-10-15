import dataclasses
import datetime as dt
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import FULL_EXTRACT, mvw_results

from mtdl_web.filmlist.parser import open_filmlist
from mtdl_web.filmlist.store import FilmStore
from mtdl_web.models import Film
from mtdl_web.rules.engine import RuleSet
from mtdl_web.rules.model import Condition, Field, Operator, RuleError
from mtdl_web.rules.templates import has_episode_number, only_new
from mtdl_web.sources.local import LocalSource
from mtdl_web.sources.mvw import film_from_mvw

# Reference case: search "das gipfeltreffen" gives 35 hits, these two rules leave episodes 39 to 58.
TOPIC_IS = Condition(Field.TOPIC, Operator.IS, "Das Gipfeltreffen")
TITLE_NUMBERED = Condition(Field.TITLE, Operator.IS, r"\(\d+\)$", regex=True)
EPISODES = list(range(39, 59))


def episode(film: Film) -> int:
    m = re.search(r"\((\d+)\)$", film.title)
    assert m, film.title
    return int(m[1])


@pytest.fixture(scope="module")
def mvw_films() -> list[Film]:
    return [film_from_mvw(r) for r in mvw_results()]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[FilmStore]:
    with FilmStore(tmp_path / "films.sqlite", index_description=True) as s, open_filmlist(FULL_EXTRACT) as reader:
        s.import_full(reader)
        yield s


def by_title(films: list[Film], title: str) -> Film:
    (film,) = [f for f in films if f.title == title]
    return film


def test_reference_case_mediathekviewweb(mvw_films: list[Film]) -> None:
    rules = RuleSet([TOPIC_IS, TITLE_NUMBERED])
    assert len(mvw_films) == 35
    matched = rules.matching(mvw_films)
    assert sorted(episode(f) for f in matched) == EPISODES


@pytest.mark.anyio
async def test_reference_case_local_film_list(store: FilmStore, mvw_films: list[Film]) -> None:
    hits = await LocalSource(store).search("das gipfeltreffen", limit=100)
    assert hits.total == 35
    matched = RuleSet([TOPIC_IS, TITLE_NUMBERED]).matching(hits.films)
    assert sorted(episode(f) for f in matched) == EPISODES
    assert {f.id for f in matched} == {f.id for f in RuleSet([TOPIC_IS, TITLE_NUMBERED]).matching(mvw_films)}


def test_reference_case_reasons(mvw_films: list[Film]) -> None:
    rules = RuleSet([TOPIC_IS, TITLE_NUMBERED])
    evaluations = [rules.evaluate(f) for f in mvw_films]
    other_topic = [e for e in evaluations if e.film.topic != "Das Gipfeltreffen"]
    no_number = [e for e in evaluations if e.film.topic == "Das Gipfeltreffen" and not e.passed]
    assert len(other_topic) == 10
    assert all(0 in e.failed for e in other_topic)
    assert len(no_number) == 5
    assert all(e.failed == [1] and "Best of" in e.film.title for e in no_number)
    for e in evaluations:
        if e.passed:
            topic, title = e.results
            assert (topic.code, topic.span, topic.actual) == ("ok", (0, 17), "Das Gipfeltreffen")
            assert isinstance(title.actual, str) and title.span is not None
            assert title.actual[slice(*title.span)] == f"({episode(e.film)})"


def test_episode_template_gives_the_same_result(mvw_films: list[Film]) -> None:
    assert RuleSet([TOPIC_IS, has_episode_number()]).matching(mvw_films) == RuleSet(
        [TOPIC_IS, TITLE_NUMBERED]
    ).matching(mvw_films)


@pytest.mark.anyio
async def test_werwoelfe_subscription(store: FilmStore) -> None:
    # The old MediathekView subscription: channel BR, topic "Werwölfe · Das Spiel von List und Täuschung".
    hits = await LocalSource(store).search("werwölfe", limit=100)
    topic = Condition(Field.TOPIC, Operator.IS, "Werwölfe · Das Spiel von List und Täuschung")
    channel = Condition(Field.CHANNEL, Operator.ANY_OF, ("BR",))
    assert len(RuleSet([topic, channel]).matching(hits.films)) == 30
    episodes = RuleSet([topic, channel, has_episode_number()]).matching(hits.films)
    codes = sorted(re.findall(r"\((S\d+/E\d+)\)$", f.title)[0] for f in episodes)
    assert codes == [f"S0{s}/E{e:02d}" for s in (1, 2) for e in range(1, 13)]


def test_plain_text_ignores_case_dashes_and_spacing(mvw_films: list[Film]) -> None:
    lower = Condition(Field.TOPIC, Operator.IS, "das gipfeltreffen")
    hyphen = Condition(Field.TITLE, Operator.CONTAINS, "gipfeltreffen  -  best of")  # the titles use "–" and "-"
    best_of = RuleSet([lower, hyphen]).matching(mvw_films)
    assert len(best_of) == 5
    sat3 = RuleSet([Condition(Field.TITLE, Operator.CONTAINS, "Gipfeltreffen – Best of")]).matching(mvw_films)
    assert {f.channel for f in sat3} == {"MDR", "3Sat"}


def test_negations(mvw_films: list[Film]) -> None:
    not_best_of = Condition(Field.TITLE, Operator.NOT_CONTAINS, "Best of")
    assert sorted(episode(f) for f in RuleSet([TOPIC_IS, not_best_of]).matching(mvw_films)) == EPISODES
    best_of = by_title(mvw_films, "Das Gipfeltreffen – Best of 2025")
    result = RuleSet([not_best_of]).evaluate(best_of).results[0]
    assert not result.passed
    assert isinstance(result.actual, str) and result.span is not None
    assert result.actual[slice(*result.span)] == "Best of"
    regex_not = Condition(Field.TITLE, Operator.IS_NOT, r"best of \d{4}", regex=True)
    assert len(RuleSet([TOPIC_IS, regex_not]).matching(mvw_films)) == 20
    other_topic = Condition(Field.TOPIC, Operator.IS_NOT, "Das Gipfeltreffen")
    assert len(RuleSet([other_topic]).matching(mvw_films)) == 10


def test_channels_ignore_case(mvw_films: list[Film]) -> None:
    mdr = RuleSet([TOPIC_IS, TITLE_NUMBERED, Condition(Field.CHANNEL, Operator.ANY_OF, ("mdr",))])
    assert sorted(episode(f) for f in mdr.matching(mvw_films)) == list(range(44, 59))
    not_ard = RuleSet([TOPIC_IS, TITLE_NUMBERED, Condition(Field.CHANNEL, Operator.NONE_OF, ("ARD", "3sat"))])
    assert len(not_ard.matching(mvw_films)) == 15


def test_date_conditions_use_local_days(mvw_films: list[Film]) -> None:
    base = [TOPIC_IS, TITLE_NUMBERED]
    since_2026 = RuleSet([*base, only_new(dt.date(2026, 1, 1))])
    assert sorted(episode(f) for f in since_2026.matching(mvw_films)) == list(range(51, 59))
    before_2026 = RuleSet([*base, Condition(Field.DATE, Operator.BEFORE, dt.date(2026, 1, 1))])
    assert len(before_2026.matching(mvw_films)) == 12
    # Episode 47 started 20.02.2025 00:45 in Berlin, which is still the 19th in UTC.
    film = by_title(mvw_films, "Pumpen ist ein Tu-Wort (47)")
    after_20th = Condition(Field.DATE, Operator.AFTER, dt.date(2025, 2, 20))
    berlin = RuleSet([after_20th]).evaluate(film).results[0]
    assert (berlin.passed, berlin.actual) == (True, "2025-02-20")
    utc = RuleSet([after_20th], timezone=dt.UTC).evaluate(film).results[0]
    assert (utc.passed, utc.actual) == (False, "2025-02-19")


def test_duration_in_minutes(mvw_films: list[Film]) -> None:
    film = by_title(mvw_films, "MPU für Hänsel und Gretel (45)")  # exactly 29:00
    assert film.duration == 1740
    at_most = RuleSet([TOPIC_IS, TITLE_NUMBERED, Condition(Field.DURATION, Operator.AT_MOST, 29)])
    assert [f.title for f in at_most.matching(mvw_films)] == [film.title]
    at_least = RuleSet([TOPIC_IS, TITLE_NUMBERED, Condition(Field.DURATION, Operator.AT_LEAST, 29)])
    assert len(at_least.matching(mvw_films)) == 20


def test_missing_values_fail_with_a_reason(extract_films: list[Film]) -> None:
    livestream = by_title(extract_films, "3Sat Livestream")
    rules = RuleSet([only_new(dt.date(2020, 1, 1)), Condition(Field.DURATION, Operator.AT_LEAST, 1)])
    evaluation = rules.evaluate(livestream)
    assert not evaluation.passed
    assert [r.code for r in evaluation.results] == ["value_missing", "value_missing"]


def test_disabled_conditions_are_ignored(mvw_films: list[Film]) -> None:
    disabled = dataclasses.replace(TITLE_NUMBERED, enabled=False)
    evaluations = [RuleSet([TOPIC_IS, disabled]).evaluate(f) for f in mvw_films]
    assert sum(e.passed for e in evaluations) == 25
    assert all(e.results[1].code == "disabled" for e in evaluations)


def test_regex_timeout_fails_the_condition(mvw_films: list[Film]) -> None:
    film = dataclasses.replace(mvw_films[0], title="a" * 60 + "!")  # catastrophic backtracking for (a|aa)+$
    result = RuleSet([Condition(Field.TITLE, Operator.IS, r"(a|aa)+$", regex=True)]).evaluate(film).results[0]
    assert (result.passed, result.code) == (False, "regex_timeout")


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"field": "title", "operator": "is", "value": r"\(\d+)$", "regex": True}, "invalid_regex"),
        ({"field": "title", "operator": "contains", "value": "x", "regex": True}, "regex_operator"),
        ({"field": "channel", "operator": "is", "value": ("BR",)}, "operator_not_allowed"),
        ({"field": "channel", "operator": "any_of", "value": ("BR",), "regex": True}, "regex_not_allowed"),
        ({"field": "title", "operator": "is", "value": "  "}, "invalid_value"),
        ({"field": "channel", "operator": "any_of", "value": ()}, "invalid_value"),
        ({"field": "date", "operator": "after", "value": "2026-01-01"}, "invalid_value"),
        ({"field": "duration", "operator": "at_least", "value": True}, "invalid_value"),
        ({"field": "genre", "operator": "is", "value": "x"}, "unknown_field_or_operator"),
    ],
)
def test_invalid_conditions(kwargs: dict[str, object], code: str) -> None:
    with pytest.raises(RuleError) as caught:
        Condition(**kwargs)  # type: ignore[arg-type]
    assert caught.value.code == code


def test_invalid_regex_reports_the_position() -> None:
    with pytest.raises(RuleError) as caught:
        Condition(Field.TITLE, Operator.IS, r"\(\d+)$", regex=True)  # unbalanced ")"
    assert caught.value.params["position"] == 5


def test_conditions_accept_plain_strings_and_lists() -> None:
    condition = Condition("channel", "any_of", ["BR", "ARD"])  # type: ignore[arg-type]
    assert (condition.field, condition.operator, condition.value) == (Field.CHANNEL, Operator.ANY_OF, ("BR", "ARD"))

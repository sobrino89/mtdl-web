"""Templates create ordinary, visible conditions that the user can inspect and change (no magic)."""

import datetime as dt

from mtdl_web.rules.model import Condition, Field, Operator

# "(39)" or "(S02/E03)" at the end of the title.
EPISODE_NUMBER = r"\((S\d+/E)?\d+\)$"


def has_episode_number() -> Condition:
    return Condition(Field.TITLE, Operator.IS, EPISODE_NUMBER, regex=True)


def only_new(today: dt.date) -> Condition:
    return Condition(Field.DATE, Operator.AFTER, today)

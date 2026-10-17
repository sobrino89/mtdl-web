"""Film sources behind one interface. Today there is one, the local film list; the interface stays so that another
(an official MediathekViewWeb API, say) could join without touching search and subscriptions."""

import datetime as dt
from typing import Protocol

from mtdl_web.models import Film, Scope, SearchResult

__all__ = ["Scope", "Source", "SourceError"]


class SourceError(Exception):
    """The source could not answer (network, server error, unexpected response)."""


class Source(Protocol):
    name: str

    @property
    def description_search(self) -> bool:
        """Whether ``search(include_description=True)`` is available; the UI shows the toggle only then."""
        ...

    async def search(
        self,
        query: str,
        *,
        include_description: bool = False,
        scope: Scope = "topic_title",
        limit: int = 50,
        offset: int = 0,
    ) -> SearchResult:
        """Every word as a word prefix, all words required, in topic or title (``scope`` narrows or widens);
        newest broadcast first."""
        ...

    async def new_since(self, since: dt.datetime, *, limit: int = 1000) -> list[Film]:
        """Films that are new since ``since``; see each source for what "new" can mean there."""
        ...

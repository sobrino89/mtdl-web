"""The local film list index as a source, the default."""

import asyncio
import datetime as dt

from mtdl_web.filmlist.store import FilmStore
from mtdl_web.models import Film, SearchResult
from mtdl_web.sources import Scope, SourceError


class LocalSource:
    name = "filmlist"

    def __init__(self, store: FilmStore) -> None:
        self._store = store

    @property
    def description_search(self) -> bool:
        return self._store.index_description

    async def search(
        self,
        query: str,
        *,
        include_description: bool = False,
        scope: Scope = "topic_title",
        limit: int = 50,
        offset: int = 0,
    ) -> SearchResult:
        try:
            return await asyncio.to_thread(
                self._store.search,
                query,
                include_description=include_description,
                scope=scope,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:  # descriptions asked for, but not indexed: a subscription check reports it
            raise SourceError(str(exc)) from exc

    async def new_since(self, since: dt.datetime, *, limit: int = 1000) -> list[Film]:
        """Films that entered the index after ``since`` (creation time of the list that brought them)."""
        return await asyncio.to_thread(self._store.new_since, since, limit=limit)

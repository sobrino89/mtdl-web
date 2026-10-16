"""MediathekViewWeb's public search API as an optional source.

Every call queries a community-run server, so the local film list stays the default. The API returns the same
values as the film list with relative urls already resolved (checked field by field, docs/filmliste-format.md);
it has no geo restriction field.
"""

import datetime as dt
import json
from typing import Any

import httpx

from mtdl_web.filmlist.parser import clean_url, website_url
from mtdl_web.models import Film, SearchResult, film_id
from mtdl_web.query import Query, parse
from mtdl_web.sources import Scope, SourceError

API_URL = "https://mediathekviewweb.de/api/query"


def film_from_mvw(result: dict[str, Any]) -> Film:
    def url(key: str) -> str | None:
        return clean_url(result.get(key) or "")

    return Film(
        id=film_id(result["channel"], result["topic"], result["title"], result["url_video"]),
        channel=result["channel"],
        topic=result["topic"],
        title=result["title"],
        description=result.get("description") or "",
        timestamp=result.get("timestamp"),
        duration=result.get("duration") or None,
        size=result.get("size") or None,
        url=url("url_video"),
        url_low=url("url_video_low"),
        url_hd=url("url_video_hd"),
        url_subtitle=url("url_subtitle"),
        url_website=website_url(result.get("url_website") or ""),
        geo=(),
    )


class MvwSource:
    name = "mediathekviewweb"
    description_search = True
    PAGE = 50
    MAX_NEW_PAGES = 10  # new_since pages through the newest broadcasts; stay polite

    def __init__(self, client: httpx.AsyncClient, url: str = API_URL) -> None:
        self._client = client
        self._url = url

    async def _query(
        self, queries: list[dict[str, Any]], limit: int, offset: int, q: Query | None = None
    ) -> SearchResult:
        body: dict[str, Any] = {
            "queries": queries,
            "sortBy": "timestamp",
            "sortOrder": "desc",
            "future": True,
            "offset": offset,
            "size": limit,
        }
        if q is not None and q.min_seconds is not None:
            body["duration_min"] = q.min_seconds
        if q is not None and q.max_seconds is not None:
            body["duration_max"] = q.max_seconds
        try:
            # The API expects JSON sent as text/plain.
            response = await self._client.post(
                self._url, content=json.dumps(body), headers={"Content-Type": "text/plain"}
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(f"MediathekViewWeb: {exc!r}") from exc
        if data.get("err"):
            raise SourceError(f"MediathekViewWeb: {data['err']}")
        result = data["result"]
        return SearchResult([film_from_mvw(r) for r in result["results"]], result["queryInfo"]["totalResults"])

    async def search(
        self,
        query: str,
        *,
        include_description: bool = False,
        scope: Scope = "topic_title",
        limit: int = 50,
        offset: int = 0,
    ) -> SearchResult:
        """The query split like MediathekViewWeb's own client does it: one part per selector, the parts of one field
        are alternatives on the server."""
        q = parse(query)
        selected = (("channel", q.channels), ("topic", q.topics), ("title", q.titles), ("description", q.descriptions))
        queries = [{"fields": [field], "query": " ".join(group)} for field, groups in selected for group in groups]
        if q.words:
            fields = {"topic_title": ["topic", "title"], "title": ["title"], "topic": ["topic"]}.get(scope)
            if include_description or fields is None:
                fields = ["topic", "title", "description"]
            elif scope == "topic_title" and q.topics:
                fields = ["title"]
            queries.append({"fields": fields, "query": " ".join(q.words)})
        return await self._query(queries, limit, offset, q)

    async def new_since(self, since: dt.datetime, *, limit: int = 1000) -> list[Film]:
        """MVW does not know when an entry appeared; this returns broadcasts starting after ``since``."""
        cutoff = since.timestamp()
        films: list[Film] = []
        for page in range(self.MAX_NEW_PAGES):
            batch = (await self._query([], self.PAGE, page * self.PAGE)).films
            films.extend(f for f in batch if f.timestamp is not None and f.timestamp > cutoff)
            if len(films) >= limit or len(batch) < self.PAGE or (batch[-1].timestamp or 0) <= cutoff:
                break
        return films[:limit]

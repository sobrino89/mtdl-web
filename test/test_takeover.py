"""Takeover from MediathekView. The history lines are built in MediathekView's format from real film list entries
(the fixture extract), not taken from anyone's real history."""

import datetime as dt
from typing import Any
from urllib.parse import urlsplit

from conftest import FULL_EXTRACT, load_films
from fastapi.testclient import TestClient
from test_downloads import api  # noqa: F401 - fixture

from mtdl_web.models import Film
from mtdl_web.takeover import convert, parse_history, parse_subscriptions

FILMS = load_films(FULL_EXTRACT)
WERWOELFE = "Werwölfe · Das Spiel von List und Täuschung"


def film(title: str, channel: str | None = None) -> Film:
    (found,) = [f for f in FILMS if f.title == title and (channel is None or f.channel == channel)]
    return found


def line(day: str, topic: str, title: str, url: str) -> str:
    """As MediathekView writes history.txt: topic cut to 25, title to 40 characters, padded."""
    return f"{day} |#| {topic[:25]:<25} |#| {title[:40]:<41} |###|  {url}"


NEWEST = film("Das Beste kommt noch! (58)")
FIRST = film('Folge 1: "Ich bin ein Dorfbewohner" (S01/E01)')
SECOND = film("Folge 2: Das kleine Mädchen (S01/E02)")
THIRD = film("Folge 3: Vertrauenssache (S01/E03)")
# Another host and a quality the list does not name, in the same folder: as MediathekView's history has it after the
# broadcaster moved its CDN (rbbmediapmdp-a.akamaihd.net → rbb-progressive.ard-mcdn.de).
THIRD_ELSEWHERE = "https://old-cdn.example" + urlsplit(THIRD.url or "").path.rsplit("/", 1)[0] + "/folge3_avc720.mp4"
MOVED = "https://cdn-storage.br.de/geo/old/path/"
HISTORY = "\n".join(
    [
        line("20.02.2026", NEWEST.topic, NEWEST.title, NEWEST.url_hd or ""),
        line("12.10.2026", NEWEST.topic, NEWEST.title, NEWEST.url_hd or ""),  # loaded twice
        # The CDN moved: the url is unknown, topic and title still lead to the broadcast ...
        line("05.10.2025", WERWOELFE, SECOND.title, MOVED + "folge2.mp4"),
        # ... unless the cut title fits two (as in a real history: "… Dorfbewohner" (S0", S01/E01 and S02/E01).
        line("05.10.2025", WERWOELFE, FIRST.title[:39], MOVED + "folge1.mp4"),
        line(
            "20.02.2026",
            "Die Heiland - Wir sind Anwalt",
            "Folge 6: Gefahr im Verzug (S01/E06)",
            "https://x.example/6.mp4",
        ),
        line("01.01.2025", "Das Gipfeltreffen", "Das Gipfeltreffen – Best of 2023 (2/2)", "https://x.example/b.mp4"),
        line("05.10.2025", WERWOELFE, THIRD.title, THIRD_ELSEWHERE),
        "not a history line",
    ]
)
ABO_HISTORY = line("05.10.2025", WERWOELFE, SECOND.title, MOVED + "folge2.mp4")
SETTINGS = f"""<?xml version="1.0" encoding="UTF-8"?>
<Mediathek>
<Abonnement><aktiv>true</aktiv><Name>Werwoelfe_Das_Spiel_von_List_und_Taeuschung</Name><Sender>BR</Sender>
<Thema>{WERWOELFE}</Thema><Mindestdauer>0</Mindestdauer><min_max>true</min_max>
<Zielpfad>Werwoelfe__Das_Spiel_von_List_und_Taeuschung</Zielpfad><letztes_Abo>12.10.2026</letztes_Abo>
<Programmset>Linux Speichern</Programmset></Abonnement>
<Abonnement><aktiv>false</aktiv><Name>Gipfel</Name><Titel>#:\\(\\d+\\)$</Titel><Thema-Titel>gipfeltreffen</Thema-Titel>
<Mindestdauer>20</Mindestdauer><min_max>true</min_max></Abonnement>
</Mediathek>"""


def test_history_format() -> None:
    lines = parse_history(HISTORY)
    assert len(lines) == 7
    assert lines[0].day == dt.date(2026, 2, 20) and lines[0].topic == "Das Gipfeltreffen"
    assert lines[2].topic == "Werwölfe · Das Spiel von" and lines[2].title == SECOND.title


def test_subscriptions_become_visible_conditions() -> None:
    werwoelfe, gipfel = (convert(old) for old in parse_subscriptions(SETTINGS))
    assert werwoelfe.draft is not None and gipfel.draft is not None
    assert werwoelfe.draft.name == WERWOELFE and werwoelfe.draft.query == WERWOELFE
    assert [(str(c.field), str(c.operator), c.value) for c in werwoelfe.draft.conditions] == [
        ("channel", "any_of", ("BR",)),
        ("topic", "is", WERWOELFE),
    ]
    assert werwoelfe.draft.naming.folder == "Werwoelfe__Das_Spiel_von_List_und_Taeuschung"
    assert werwoelfe.draft.state == "paused" and werwoelfe.notes == ()
    assert gipfel.draft.query == "gipfeltreffen" and set(gipfel.notes) == {"regex", "query_from"}
    title, duration = gipfel.draft.conditions
    assert (title.value, title.regex, duration.value) == (r"\(\d+\)$", True, 20)


def takeover(client: TestClient, apply: bool) -> dict[str, Any]:
    body = {"history": HISTORY, "abo_history": ABO_HISTORY, "settings": SETTINGS, "apply": apply}
    response = client.post("/api/v1/takeover/mediathekview", json=body)
    assert response.status_code == 200, response.text
    report: dict[str, Any] = response.json()
    return report


def test_dry_run_then_apply(api: TestClient) -> None:  # noqa: F811
    report = takeover(api, apply=False)
    assert report["lines"] == 7
    counts = {"new": 4, "ambiguous": 1, "missing": 1, "by_url": 1, "by_folder": 1, "by_title": 2}
    assert report["counts"] == counts | {"by_ambiguous": 1, "by_missing": 1}
    newest, second, first, heiland, best_of, third = report["items"]
    assert (third["how"], third["film"]["title"]) == ("folder", THIRD.title)
    assert (newest["how"], newest["lines"], newest["day"]) == ("url", [1, 2], "2026-10-12")
    assert (second["how"], second["film"]["title"], second["subscription"]) == ("title", SECOND.title, 0)
    assert first["status"] == "ambiguous" and first["film"] is None
    assert heiland["status"] == "missing" and heiland["film"] is None
    # Only one "Best of 2023 (2/2)" has the topic "Das Gipfeltreffen" (3sat files it under "Kabarett & Comedy").
    assert (best_of["how"], best_of["film"]["channel"]) == ("title", "MDR")
    assert [s["name"] for s in report["subscriptions"]] == [WERWOELFE, "Gipfel"]
    assert api.get("/api/v1/downloads").json()["counts"] == {}  # a dry run changes nothing

    applied = takeover(api, apply=True)
    assert (applied["imported_downloads"], applied["imported_subscriptions"]) == (4, 2)
    subs = {s["name"]: s for s in api.get("/api/v1/subscriptions").json()}
    assert subs[WERWOELFE]["state"] == "paused" and subs["Gipfel"]["state"] == "paused"
    done = {d["film"]["title"]: d for d in api.get("/api/v1/downloads", params={"state": "done"}).json()["items"]}
    assert done[SECOND.title]["subscription_id"] == subs[WERWOELFE]["id"]
    assert done[NEWEST.title]["finished_at"] == "2026-10-12T10:00:00Z"  # noon in Berlin on the last download day
    assert done[NEWEST.title]["path"] == ""

    again = takeover(api, apply=True)  # nothing twice
    assert (again["imported_downloads"], again["imported_subscriptions"]) == (0, 0)
    assert again["counts"]["known"] == 4 and all(s["exists"] for s in again["subscriptions"])
    preview = api.post(
        "/api/v1/subscriptions/preview",
        json={"query": WERWOELFE, "conditions": [{"field": "topic", "operator": "is", "value": WERWOELFE}]},
    ).json()
    states = {i["film"]["title"]: i["status"] for i in preview["items"]}
    assert (states[SECOND.title], states[FIRST.title]) == ("exists", "load")


def test_unreadable_settings(api: TestClient) -> None:  # noqa: F811
    response = api.post("/api/v1/takeover/mediathekview", json={"settings": "<Mediathek><Abonnement>"})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "unreadable"

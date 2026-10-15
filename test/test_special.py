"""Special versions (special.py): audio description, sign language, "Hörfassung" and "klare
Sprache" stay out of subscriptions by default; each subscription may say otherwise. Titles and topics are real, from
the film list of 15.10.2026 and the test extract."""

import dataclasses
from pathlib import Path
from typing import Any

from test_downloads import MediaServer, running_app

from mtdl_web.models import Film
from mtdl_web.special import excluded, special_version

VERSIONS = [  # (topic, title) -> marker
    ("37 Grad", "37°: Klicks um jeden Preis (Audiodeskription)", "Audiodeskription"),
    ("Babylon Berlin", "Babylon Berlin II (14/16) (Audiodeskription)", "Audiodeskription"),
    ("SRF bi de Lüt - Hüttengeschichten", "Hochsaison, Stromausfall und ein Gipfeltreffen – (Audiodeskription)", None),
    ("ZDF spezial", "Gipfeltreffen in Alaska - was heißt das für die Ukraine? (S2025/E44) (Gebärdensprache)", None),
    ("100percentme", "Behindert feiern gehen | Audiodeskription | 100percentme", "Audiodeskription"),
    ("Coronavirus-Update NDR Info", "Gebärdensprache: Coronavirus-Update #94", "Gebärdensprache"),
    ("ARD-Buffet", "Die Sendung vom 23.09.2022 mit Deutscher Gebärdensprache (DGS)", "mit Deutscher Gebärdensprache"),
    ("Tagesschau in Gebärdensprache", "Tagesschau vom 14.10.2026", "in Gebärdensprache"),
    ("tagesschau (mit Gebärdensprache)", "tagesschau 20:00 Uhr", "mit Gebärdensprache"),
    ("Audiodeskription", "Barrierefreie Programmangebote", "Audiodeskription"),
    ("Löwenzahn", "Peter in Bärstadt (Hörfassung)", "Hörfassung"),
    ("Die Kinderdoku", "Der Freund (3) (klare Sprache)", "klare Sprache"),
    ("Kika", "Etage (Video in klarer Sprache)", "Video in klarer Sprache"),
]
ABOUT = [  # programmes about the subject: no versions
    ("Sehen statt Hören", "Geschichte der Deutschen Gebärdensprache"),
    ("Die Sendung mit dem Elefanten", "Was ist Gebärdensprache?"),
    ("Gesellschaft", "Ersatz für das Hören – Gebärdensprache und Implantat"),
    ("100percentme", "Wahr oder falsch? 6 Fakten über Gehörlosigkeit und Gebärdensprache | 100percentme"),
    ("tagesschau24", "tagesschau in Einfacher Sprache"),  # its own format, not one of the versions
]


def film(topic: str, title: str) -> Film:
    return Film(1, "ARD", topic, title, "", None, None, None, None, None, None, None, None, ())


def test_the_markers_of_special_versions() -> None:
    for topic, title, marker in VERSIONS:
        found = special_version(film(topic, title))
        assert found is not None, title
        if marker:
            assert found == marker, (title, found)
    for topic, title in ABOUT:
        assert special_version(film(topic, title)) is None, title
    assert [excluded(c, True) for c in ("setting", "exclude", "include")] == [True, True, False]
    assert [excluded(c, False) for c in ("setting", "exclude", "include")] == [False, True, False]


DRAFT: dict[str, Any] = {"name": "37 Grad", "query": "#37,grad", "conditions": [], "naming": {"scheme": "date"}}


def test_subscriptions_leave_special_versions_out_unless_told(tmp_path: Path) -> None:
    with running_app(tmp_path, MediaServer()) as api:

        def preview(**changes: object) -> Any:
            return api.post("/api/v1/subscriptions/preview", json={**DRAFT, **changes}).json()

        default = preview()
        specials = [i for i in default["items"] if i["special"]]
        assert len(specials) == 6 and {i["special"] for i in specials} == {"Audiodeskription"}
        (step,) = default["steps"]
        assert (step["kind"], step["index"], step["excluded"]) == ("special_versions", 0, 6)
        assert all((i["status"], i["excluded_by"]) == ("excluded", 0) for i in specials)

        included = preview(special_versions="include")
        assert included["steps"] == []
        assert not [i for i in included["items"] if i["status"] == "excluded"]
        assert len([i for i in included["items"] if i["special"]]) == 6

        api.patch("/api/v1/settings", json={"exclude_special_versions": False})
        assert preview()["steps"] == []  # the setting is off
        assert len(preview(special_versions="exclude")["steps"]) == 1  # this subscription leaves them out anyway

        api.patch("/api/v1/settings", json={"exclude_special_versions": True})
        saved = api.post("/api/v1/subscriptions", json={**DRAFT, "state": "paused"}).json()
        assert saved["special_versions"] == "setting"
        title = specials[0]["film"]["title"]

        def owner() -> object:
            hits = api.get("/api/v1/search", params={"q": DRAFT["query"], "status": True, "limit": 100}).json()
            return next(f for f in hits["films"] if f["title"] == title)["status"]["subscription_id"]

        assert owner() is None  # the search does not claim a special version for the subscription
        changed = {**saved, "special_versions": "include"}
        api.put(f"/api/v1/subscriptions/{saved['id']}", json={k: changed[k] for k in [*DRAFT, "special_versions"]})
        assert owner() == saved["id"]


def test_one_definition_for_twins_and_the_filter() -> None:
    """The twin logic prefers the plain version by the same rule (subscriptions._representatives)."""
    plain = film("37 Grad", "37°: Klicks um jeden Preis")
    version = dataclasses.replace(plain, id=2, title=plain.title + " (Audiodeskription)")
    assert special_version(plain) is None and special_version(version) == "Audiodeskription"

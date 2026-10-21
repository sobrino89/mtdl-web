---
title: Datenquellen
description: Woher mtdl-web weiß, was die Mediatheken anbieten, und wie es die Dienste dahinter schont.
---

mtdl-web greift auf Dienste anderer zu: die Filmliste des MediathekView-Projekts, optional TMDB, dazu die Server
der Sender. Es hält sich an deren Vorgaben und nennt sich in jeder Anfrage mit dem
`User-Agent` `mtdl-web/<version> (+https://github.com/sobrino89/mtdl-web)`.

## Filmliste

Die Filmliste des [MediathekView-Projekts](https://mediathekview.de) verzeichnet alles, was die Sender in ihren
Mediatheken anbieten, rund 720.000 Einträge. mtdl-web lädt sie und baut daraus einen lokalen Suchindex (SQLite mit
Volltextsuche). Suche und Abos laufen damit auf dem eigenen Server, schnell und auch, wenn die Quelle kurz nicht
erreichbar ist.

Der Server des Projekts erzeugt von etwa 5 bis 23 Uhr jede Stunde eine neue Liste, vollständig (rund 75 MB
komprimiert) und als Änderungsliste. Die Änderungsliste enthält alles, was seit der ersten Liste des Tages neu
hinzukam; abgelaufene Sendungen verschwinden nur mit einer vollständigen Liste. mtdl-web richtet sich danach, wie
die Kodi-Erweiterung des Projekts:

| | |
|---|---|
| Erster Start | die vollständige Liste, ein bis zwei Minuten samt Index |
| Stündlich | zu einer festen Minute je Installation die Änderungsliste (wenige hundert KB), auch nachts |
| Einmal am Tag | die vollständige Liste, beim ersten Abruf nach 07:00 UTC (9 Uhr Sommerzeit, 8 Uhr Winterzeit) |
| Neustart | nichts zusätzlich, es gilt dieser Rhythmus; eine geladene, noch nicht eingelesene Liste liest mtdl-web von der Platte |
| Adresse | aus dem Server-Verzeichnis des Projekts, wie beim MediathekView-Programm, mit Rückfall auf die offizielle |
| Schonung | Bedingungs-Anfragen (`ETag`, `If-Modified-Since`), Pausen nach Fehlern, `Retry-After` wird beachtet |

So fallen am Tag rund 80 MB an. Den Stand zeigen die Statusleiste und **Einstellungen**, **Datenquelle**; dort lädt **Jetzt aktualisieren** sofort.
Schlägt ein Abruf fehl, gilt der alte Stand, und die Oberfläche sagt es.

## TMDB (optional)

Für Bibliotheken holt mtdl-web Serien, Staffeln, Folgen und Filme von [TMDB](https://www.themoviedb.org), mit
deinem eigenen, kostenlosen Zugang:

- höchstens 10 Anfragen je Sekunde, `429` und `Retry-After` werden beachtet,
- eine Serie samt aller Staffeln in einer Anfrage, sechs Stunden zwischengespeichert,
- das Token geht als Kopfzeile, nie in der Adresse, und erscheint nie im Log.

Dieses Produkt nutzt die TMDB-API, ist aber nicht von TMDB unterstützt oder zertifiziert.

## Senderlogos (optional)

Ab Werk zeigt mtdl-web Sendernamen als Text. Unter **Einstellungen**, **System** lassen sich die Logos des
MediathekView-Programms einschalten: Der Server lädt sie einmal aus dessen Repository (fester Stand, GPL-3.0), legt
sie im Cache ab und liefert sie selbst aus. Der Browser lädt nichts von dort. Die Logos sind Marken der Sender.

## Videos

Die Videos selbst kommen von den Servern der Sender, wie beim Abspielen im Browser. Alle Downloads teilen sich das
Bandbreitenlimit, siehe [Downloads](../../usage/downloads/).

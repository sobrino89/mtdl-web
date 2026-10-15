---
title: Datenquellen
description: Woher mtdl-web weiß, was die Mediatheken anbieten, und wie es die Dienste dahinter schont.
---

mtdl-web greift auf Dienste anderer zu: die Filmliste des MediathekView-Projekts, optional MediathekViewWeb und
TMDB, dazu die Server der Sender. Es hält sich an deren Vorgaben und nennt sich in jeder Anfrage mit dem
`User-Agent` `mtdl-web/<version> (+https://github.com/sobrino89/mtdl-web)`.

## Filmliste (Standard)

Die Filmliste des [MediathekView-Projekts](https://mediathekview.de) verzeichnet alles, was die Sender in ihren
Mediatheken anbieten, rund 720.000 Einträge. mtdl-web lädt sie und baut daraus einen lokalen Suchindex (SQLite mit
Volltextsuche). Suche und Abos laufen damit auf dem eigenen Server, schnell und auch, wenn die Quelle kurz nicht
erreichbar ist.

| | |
|---|---|
| Erster Start | die vollständige Liste (rund 75 MB komprimiert), ein bis zwei Minuten samt Index |
| Danach | stündlich, zu einer festen Minute je Installation, meist nur die Änderungsliste (wenige hundert KB) |
| Einmal am Tag | die vollständige Liste, nach der Regel des MediathekView-Programms |
| Adresse | aus dem Server-Verzeichnis des Projekts, wie beim MediathekView-Programm, mit Rückfall auf die offizielle |
| Schonung | Bedingungs-Anfragen (`ETag`, `If-Modified-Since`), Pausen nach Fehlern, `Retry-After` wird beachtet |

Den Stand zeigen die Statusleiste und **Einstellungen**, **Datenquelle**; dort lädt **Jetzt aktualisieren** sofort.
Schlägt ein Abruf fehl, gilt der alte Stand, und die Oberfläche sagt es.

## MediathekViewWeb (optional)

Statt der lokalen Liste kann mtdl-web bei jeder Suche die öffentliche Such-API von
[MediathekViewWeb](https://mediathekviewweb.de) fragen. Dann braucht es keinen Cache, hängt aber von der
Erreichbarkeit dieses Dienstes ab und belastet ihn mit jeder Suche und jeder Abo-Prüfung. Sinnvoll für schwache
Hardware oder zum Ausprobieren; umschalten unter **Einstellungen**, **Datenquelle** oder je Abo.

## TMDB (optional)

Für Bibliotheken holt mtdl-web Serien, Staffeln, Folgen und Filme von [TMDB](https://www.themoviedb.org), mit
deinem eigenen, kostenlosen Zugang:

- höchstens 10 Anfragen je Sekunde, `429` und `Retry-After` werden beachtet,
- eine Serie samt aller Staffeln in einer Anfrage, sechs Stunden zwischengespeichert,
- das Token geht als Kopfzeile, nie in der Adresse, und erscheint nie im Log.

Dieses Produkt nutzt die TMDB-API, ist aber nicht von TMDB unterstützt oder zertifiziert.

## Senderlogos (optional)

Ab Werk zeigt mtdl-web Sendernamen als Text. Unter **Einstellungen**, **System** lassen sich die Logos des
MediathekView-Programms einschalten: Der Server lädt sie einmal aus dessen Repository, legt sie im Cache ab und
liefert sie selbst aus. Der Browser lädt nichts von dort. Die Logos sind Marken der Sender.

## Videos

Die Videos selbst kommen von den Servern der Sender, wie beim Abspielen im Browser. Alle Downloads teilen sich das
Bandbreitenlimit, siehe [Downloads](../../usage/downloads/).

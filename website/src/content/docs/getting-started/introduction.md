---
title: Was ist mtdl-web?
description: Was mtdl-web kann, für wen es gedacht ist und was es bewusst nicht tut.
---

mtdl-web (Langform „Mediathek Downloader Web“) durchsucht die Mediatheken von ARD, ZDF, arte, 3sat und den übrigen
öffentlich-rechtlichen Sendern, lädt Sendungen herunter und abonniert Serien. Es läuft als Web-App in einem
Container auf dem eigenen Server, etwa einem NAS oder einem anderen gängigen Heimserver, und wird im Browser
bedient. Optional lässt es sich an die Medienserver Plex und Jellyfin anbinden: Serien und Filme landen dann
direkt in deren Bibliotheken.

Was die Mediatheken anbieten, weiß mtdl-web aus der Filmliste des [MediathekView-Projekts](https://mediathekview.de),
die das Projekt öffentlich bereitstellt. mtdl-web ist eigenständig und hat mit dem MediathekView-Team nichts zu tun.

Entstanden ist es als schlanke Alternative zum bewährten Desktop-Client
[MediathekView](https://github.com/mediathekview/MediathekView): Den gibt es für Server zwar schon als Container,
aber samt Desktop-Oberfläche im Browserfenster, und das ist für einen Heimserver schwerer als nötig. Programmiert
ist mtdl-web zum großen Teil mit KI; wer dahintersteht und wie das abläuft, steht unter
[Über das Projekt](../../project/about/).

## Funktionen

**Suche.** Über die Filmliste des MediathekView-Projekts (rund 720.000 Einträge, lokaler Volltextindex). Filter
für Sender, Thema, Titel, Dauer, Zeitraum, HD und Untertitel, Gruppierung nach Thema, Vorschau im Browser. Die
Suchsyntax von MediathekViewWeb funktioniert auch hier (`!ard #tatort >80`). Mehr unter [Suche](../../usage/search/).

**Abos.** Ein Abo lädt neue Folgen einer Sendung von selbst. Seine Regeln sind eine flache Liste von Bedingungen
(Thema, Titel, Beschreibung, Sender, Datum, Dauer), die alle zutreffen müssen; Textfelder wahlweise als regulärer
Ausdruck. Die Vorschau zeigt für jeden Treffer, ob er geladen wird und welche Regel ihn sonst ausschließt. Mehr unter
[Abos](../../usage/subscriptions/).

**Downloads.** MP4 und HLS, mit Wiederaufnahme nach Abbruch oder Neustart, Untertiteln als SRT, einer Warteschlange
mit Reihenfolge und einem gemeinsamen Bandbreitenlimit, das sofort wirkt, auch nach Zeitplan. Fehlermeldungen
erklären die Ursache und die Lösung. Mehr unter [Downloads](../../usage/downloads/).

**Plex und Jellyfin.** Dateinamen mit Staffel und Folge (`Serie/Season 01/Serie - S01E39 - Titel.mp4`), nach Datum
oder nach eigener Vorlage. Auf Wunsch lädt ein Abo direkt in die Serienbibliothek des Mediaservers, benannt nach
TMDB; einzelne Filme gehen in eine Filmbibliothek. Mehr unter [Bibliotheken](../../usage/libraries/).

**Integrationen.** REST-API mit OpenAPI-Beschreibung, signierte Webhooks (auch direkt an Sonarr, Gotify, ntfy oder
Discord), Skripte je Ereignis für die Nachbearbeitung und ein MCP-Server für KI-Assistenten wie Claude.

**Umzug.** Download-Historie und Abos der Desktop-App MediathekView lassen sich übernehmen, damit nichts doppelt
geladen wird.

## Leitlinien

- **Ein Container, wenig Ressourcen.** Python mit FastAPI und SQLite, die Oberfläche ist mit Preact gebaut. Nach dem
  ersten Import belegt die Filmliste samt Index rund ein GB im Cache-Ordner.
- **Keine fremden Inhalte zur Laufzeit.** Die Oberfläche lädt keine Schriften, Skripte oder Bilder von anderen
  Servern; nur die Vorschau im Browser spielt das Video direkt vom Sender. Der Server holt die Filmliste und die
  Videos, alles Weitere nur, wenn du es einrichtest (TMDB, Plex, Senderlogos).
- **Höflich zu den Quellen.** Die Filmliste kommt stündlich als kleine Änderungsliste und einmal am Tag vollständig,
  mit Bedingungs-Anfragen und eigenem `User-Agent`; siehe [Datenquellen](../../background/data-sources/).
- **Sicher ab Werk.** Anmeldung mit Passwort ist Standard, ein Einrichtungscode aus dem Log schützt den ersten
  Aufruf, API-Schlüssel dürfen nur lesen oder auch ändern. Siehe [Sicherheit](../../background/security/).
- **Erklärt statt rätselt.** Jede Abo-Regel nennt ihren Grund, jeder Fehler seine Lösung, jeder Ordner wird beim
  Start auf Schreibrechte geprüft.

## Was mtdl-web nicht tut

- **Kein Livestream-Mitschnitt.** Geladen wird, was die Sender in ihren Mediatheken anbieten.
- **Keine Umgehung von Sperren.** Geo-gesperrte Sendungen lädt mtdl-web nur, wenn der Server sie abrufen darf; die
  Fehlermeldung sagt, aus welchem Land.
- **Kein Mediaserver.** Abspielen, Metadaten und Bilder übernehmen Plex, Jellyfin und Co.; mtdl-web legt die
  Dateien so ab, dass sie dort richtig erscheinen.

## Stand

mtdl-web ist jung: Version 0.1 ist die erste veröffentlichte Fassung. Die Oberfläche gibt es auf Deutsch und
Englisch, diese Dokumentation zunächst auf Deutsch. Fehler und Wünsche gehen an die
[Issues auf GitHub](https://github.com/sobrino89/mtdl-web/issues).

# mtdl-web

[![CI](https://github.com/sobrino89/mtdl-web/actions/workflows/ci.yml/badge.svg)](https://github.com/sobrino89/mtdl-web/actions/workflows/ci.yml)
[![Lizenz: GPL-3.0-or-later](https://img.shields.io/badge/Lizenz-GPL--3.0--or--later-blue)](LICENSE)

**Mediathek Downloader Web**: Mediatheken der öffentlich-rechtlichen Sender durchsuchen, Sendungen herunterladen
und Serien abonnieren, als schlanke, selbst gehostete Web-App in einem Container.

*English: search the German public broadcasters' media libraries, download broadcasts and subscribe to shows, as a
small self-hosted web app. The interface is available in German and English; the documentation starts in German.*

**Dokumentation:** [sobrino89.github.io/mtdl-web](https://sobrino89.github.io/mtdl-web/)

## Warum

Der bewährte Desktop-Client [MediathekView](https://github.com/mediathekview/MediathekView) läuft auf Servern meist
als Java-Anwendung in einem VNC-Container: über ein GB Image, mehrere GB Arbeitsspeicher, Desktop-Bedienung im
Browserfenster. mtdl-web bietet die Kernfunktionen als Web-App in einem kleinen Container, bedienbar vom Rechner wie
vom Handy.

## Funktionen

- **Suche** über die Filmliste des MediathekView-Projekts (rund 720.000 Einträge, lokal indexiert, Treffer in
  Millisekunden), mit Filtern, Vorschau im Browser und der Suchsyntax von MediathekViewWeb (`!ard #tatort >80`).
- **Abos** mit einfachen, sichtbaren Regeln. Die Vorschau zeigt vor dem Speichern, welche Folgen geladen werden und
  welche Regel die übrigen ausschließt.
- **Downloads** als MP4 und HLS mit Wiederaufnahme, Untertiteln als SRT, einem gemeinsamen Bandbreitenlimit (auch
  nach Zeitplan) und Fehlermeldungen, die die Lösung nennen.
- **Plex und Jellyfin:** Dateinamen mit Staffel und Folge, oder direkt in die Bibliothek des Mediaservers, benannt
  nach TMDB.
- **Integrationen:** REST-API mit OpenAPI, signierte Webhooks, Skripte je Ereignis und ein MCP-Server für
  KI-Assistenten.
- **Umzug von MediathekView:** Historie und Abos übernehmen, damit nichts doppelt geladen wird.
- **Sicher ab Werk:** Anmeldung mit Passwort, API-Schlüssel mit Leserecht, nach dem Start kein Prozess als root,
  keine externen Ressourcen in der Oberfläche.

## Schnellstart

```sh
curl -LO https://raw.githubusercontent.com/sobrino89/mtdl-web/main/compose.yaml
# Pfade und PUID/PGID in compose.yaml anpassen, dann:
docker compose up -d
docker logs mtdl-web 2>&1 | grep "setup code"
```

Danach `http://<server>:8080` öffnen, mit dem Einrichtungscode aus dem Log ein Passwort festlegen, und ein kurzer
Assistent führt durch die Ersteinrichtung. Vorlagen: [`compose.yaml`](compose.yaml) für die meisten Systeme,
[`compose.unraid.yaml`](compose.unraid.yaml) für Unraid, [`.env.example`](.env.example) mit allen weiteren
Einstellungen. Ausführlich: [Schnellstart](https://sobrino89.github.io/mtdl-web/getting-started/quickstart/) und
[Installation](https://sobrino89.github.io/mtdl-web/install/docker-compose/).

## Roadmap

Geplant, ohne feste Reihenfolge und ohne Termin:

- **Benachrichtigungen** aufs Handy oder per Mail über Apprise (ntfy, Telegram, Mail und viele mehr).
- **Aufbewahrungsfrist:** geladene Dateien nach einer Frist löschen, ab dem Download oder ab „gesehen“ in Plex oder
  Jellyfin, für Abos, einzelne Downloads und Filme; mit einer Übersicht der anstehenden Löschungen und einem
  Papierkorb.
- **Film-Abos:** einzelne Filme nach Regeln vormerken, mit Bestätigung je Eintrag.
- **MKV** mit eingebetteten Untertiteln (heute per Skript nach dem Download).
- **Unraid:** eine App für Community Applications.
- **Dokumentation auf Englisch.**

Wünsche und Vorschläge gern als [Issue](https://github.com/sobrino89/mtdl-web/issues).

## Über das Projekt

Ein privates Projekt von [sobrino89](https://github.com/sobrino89). Den Großteil des Codes hat eine KI geschrieben
(Claude von Anthropic); Anforderungen, Entscheidungen und Prüfung liegen bei mir: Hintergrund in der
Anwendungsentwicklung, Studium der angewandten Informatik, seit 2014 IT-Systemadministrator. Jede Änderung läuft
durch über 350 automatische Tests mit echten Daten aus Filmliste und TMDB. Mehr unter
[Über das Projekt](https://sobrino89.github.io/mtdl-web/project/about/).

## Mitwirken und Sicherheit

Fehler und Wünsche als [Issue](https://github.com/sobrino89/mtdl-web/issues), Beiträge nach
[CONTRIBUTING.md](CONTRIBUTING.md). Sicherheitslücken bitte vertraulich melden, siehe [SECURITY.md](SECURITY.md).
Alle Prüfungen laufen im Container:

```sh
docker build --target test -t mtdl-web:test .
docker run --rm mtdl-web:test
```

## Dank

Die Daten stammen aus der Filmliste des [MediathekView](https://mediathekview.de)-Projekts, die Suchsyntax folgt
[MediathekViewWeb](https://github.com/mediathekview/mediathekviewweb). mtdl-web ist kein Projekt des
MediathekView-Teams und nicht mit ihm verbunden. Die Sendungen gehören den Sendern und ihren Urhebern.

Serien-, Staffel-, Folgen- und Filmdaten für die Bibliotheken kommen von [TMDB](https://www.themoviedb.org). Dieses
Produkt nutzt die TMDB-API, ist aber nicht von TMDB unterstützt oder zertifiziert.

## Lizenz

GNU General Public License, Version 3 oder neuer (`GPL-3.0-or-later`), siehe [LICENSE](LICENSE). Mitgelieferte
Bibliotheken behalten ihre eigenen Lizenzen (in der Oberfläche Preact MIT, hls.js und Swagger UI Apache-2.0).

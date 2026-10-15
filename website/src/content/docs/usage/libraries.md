---
title: Bibliotheken, Plex und Jellyfin
description: Serien und Filme direkt in die Ordner des Mediaservers laden, benannt nach TMDB.
---

Bibliotheken sind die Medienordner deines Mediaservers, angelegt wie die Stammordner von Sonarr und Radarr. Ein Abo
lädt in eine **Serienbibliothek** und gehört dort zu einer Serie bei TMDB: Jede Folge bekommt Staffel, Nummer und
Titel wie dort, der Ordner trägt die ID, an der Plex die Serie erkennt. Einzelne Filme aus der Suche gehen in eine
**Filmbibliothek**, benannt nach Titel und Jahr bei TMDB.

```text
Die Anstalt {tmdb-73860}/Season 13/Die Anstalt - S13E08 - <Folgentitel bei TMDB>.mp4
Nikita (1990) {tmdb-9322}/Nikita (1990).mp4
```

„(S2026/E08)“ der Mediathek wird so zu Staffel 13, Folge 8, wie TMDB und Plex die Sendung zählen.

## Voraussetzungen

1. **TMDB-Zugang.** Ein kostenloses Konto auf [themoviedb.org](https://www.themoviedb.org), dort unter
   **Einstellungen**, **API** das **API-Lesezugriffstoken** kopieren. Eintragen unter **Einstellungen**,
   **Bibliotheken und Plex**, **TMDB**, oder als `MTDL_TMDB_TOKEN`.
2. **Ein eingebundener Ordner je Bibliothek**, etwa `/library/shows` für Serien und `/library/movies` für Filme:

   ```yaml
       volumes:
         - /pfad/zu/serien:/library/shows
         - /pfad/zu/filme:/library/movies
   ```

   mtdl-web legt Bibliotheksordner nie selbst an und lädt nicht hinein, solange der Mount fehlt: Dateien im
   Container selbst wären mit dem nächsten Update weg.

## Bibliothek anlegen

Unter **Einstellungen**, **Bibliotheken und Plex**, **Bibliothek hinzufügen**:

| Feld | Bedeutung |
|---|---|
| Name | frei, erscheint bei Abos und in der Suche |
| Art | Serien (für Abos) oder Filme (für einzelne Filme aus der Suche) |
| Ordner im Container | der eingebundene Ordner; beim Speichern prüft mtdl-web, ob es dort schreiben darf |
| Benennung | eine Vorlage, siehe unten |
| Namen vereinfachen | Umlaute und Sonderzeichen ersetzen wie im Download-Ordner; aus (Standard) heißen Dateien wie bei TMDB |
| Ordner aus Sicht von Plex | nur nötig, wenn Plex den Ordner unter einem anderen Pfad sieht |
| Nach jedem Download neu einlesen | Plex liest den Ordner der Serie neu ein; aus lassen, wenn Plex Änderungen selbst bemerkt |

Ein neuer Pfad zieht die Bibliothek um: Gespeicherte Downloads folgen, die Dateien selbst verschiebst du. Löschen
lässt sich eine Bibliothek erst, wenn kein Abo und kein offener Download sie mehr nutzt; die Dateien bleiben.

## Vorlagen

| Vorlage | Serien | Filme |
|---|---|---|
| Plex, TMDB-ID (Standard) | `{serie} {tmdb}/{staffelordner}/{serie} - S{staffel}E{folge} - {folgentitel}` | `{titel} ({jahr}) {tmdb}/{titel} ({jahr})` |
| Plex, TVDB-ID | `{serie} {tvdb}/{staffelordner}/…` | |
| Plex, Jahr und IMDb-ID | `{serie} ({serienjahr}) {imdb}/{staffelordner}/…` | `{titel} ({jahr}) {imdb}/{titel} ({jahr})` |
| Ohne ID | `{serie}/{staffelordner}/…` | `{titel} ({jahr})/{titel} ({jahr})` |

Platzhalter für Serien: `{serie}`, `{serienjahr}`, `{tmdb}`, `{tvdb}`, `{imdb}`, `{staffelordner}` (`Season 13`,
Sonderfolgen in `Specials`), `{staffel}`, `{folge}`, `{folgentitel}`, dazu `{titel}`, `{datum}` und `{sender}` aus
der Mediathek. Für Filme: `{titel}`, `{originaltitel}`, `{jahr}`, `{tmdb}`, `{imdb}`, `{sender}`, `{datum}`. Die
Dateiendung hängt mtdl-web selbst an.

Trägt eine Folge bei TMDB nur einen Platzhalternamen wie „Folge 9“, steht der Titel aus der Mediathek an seiner
Stelle.

:::tip[Jellyfin]
Plex liest die ID im Ordnernamen in geschweiften Klammern. Für Jellyfin passt die Vorlage **Ohne ID** oder eine
eigene Vorlage in der Schreibweise, die Jellyfin erwartet. Vorhandene Ordner erkennt mtdl-web in beiden
Schreibweisen (`{tmdb-…}` und `[tmdbid-…]`).
:::

## Abo in eine Serienbibliothek

Im Editor des Abos unter **Wohin und wie benannt**: **Direkt in eine Serienbibliothek** wählen, die Bibliothek und
die Serie bei TMDB. Stimmt der Name überein, ordnet mtdl-web die Serie selbst zu.

Die Vorschau zeigt dann je Folge Staffel, Nummer und TMDB-Titel samt Grund der Zuordnung, etwa „Nummer und Titel“
oder „Sendetag und Länge“. Einträge, die zu keiner TMDB-Folge passen, stehen unter **Ohne TMDB-Folge** und werden
nicht geladen; mtdl-web gleicht sie bei jeder Prüfung neu ab. Wie die Zuordnung funktioniert, erklärt
[Folgenerkennung](../../background/episodes/).

Kommt eine Folge in der Mediathek, bevor TMDB sie führt, lädt mtdl-web sie, sobald TMDB nachzieht: Die Daten einer
Serie frischt es alle sechs Stunden auf, die Folge kommt also spätestens rund sieben Stunden, nachdem TMDB sie
aufnimmt.

## Schon vorhanden

Ordner einer Serie oder eines Films erkennt mtdl-web an der ID im Namen (`{tmdb-…}`, `{tvdb-…}`, `{imdb-…}`, auch
`[tmdbid-…]`), Folgen an `S02E03` im Namen von Videodateien. Neue Folgen kommen in den vorhandenen Ordner; was schon
da ist, zeigt die Vorschau als „in der Bibliothek“ und lädt es nicht noch einmal. So lässt sich eine Bibliothek, die
bisher FileBot oder Sonarr gefüllt hat, einfach weiterführen.

## Plex

Unter **Einstellungen**, **Bibliotheken und Plex**, **Plex** stehen Adresse und Token. Beides ist nur nötig, wenn eine
Bibliothek nach jedem Download neu einlesen soll; das Token kann leer bleiben, wenn Plex Anfragen aus dem eigenen
Netz ohne Anmeldung erlaubt. **Verbindung prüfen** zeigt die Version und die Bibliotheken von Plex.

## Einzelne Filme

In den Details eines Treffers öffnet **In die Filmbibliothek** die Suche bei TMDB, vorbelegt mit dem Titel. Nach der
Wahl des Films zeigt mtdl-web das Ziel und sagt, ob der Film schon in der Bibliothek liegt; laden legt dann eine
zweite Fassung daneben.

Unfertiges liegt bis zum Schluss im versteckten Ordner `.mtdl-incoming` der Bibliothek, den Plex und Jellyfin übergehen. Der
Mediaserver sieht nur fertige Dateien.

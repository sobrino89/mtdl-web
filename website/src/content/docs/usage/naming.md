---
title: Dateinamen
description: Wie Downloads im Download-Ordner heißen, mit Schemas für Plex und Jellyfin, nach Datum oder nach eigener Vorlage.
---

mtdl-web benennt Dateien auf zwei Wegen, je nach Ziel:

| | Download-Ordner (diese Seite) | Bibliothek |
|---|---|---|
| Namen aus | dem Titel in der Mediathek | TMDB |
| Staffel und Folge | aus der Marke im Titel, etwa `(S02/E03)` oder `(39)` | wie bei TMDB gezählt, so wie Plex und Jellyfin sie zuordnen |
| Ordner | Name der Sendung | Serie oder Film samt ID, an der Plex sie erkennt |
| Braucht | nichts | einen TMDB-Zugang und einen eingebundenen Bibliotheksordner |
| Für | einzelne Downloads und Abos in den Download-Ordner | Abos in eine Serienbibliothek, einzelne Filme in eine Filmbibliothek |

Wer Namen wie bei TMDB möchte, lädt in eine Bibliothek, siehe [Bibliotheken](../libraries/). Im Download-Ordner
benennt mtdl-web einzelne Downloads nach der Einstellung unter **Einstellungen**, **Ziel und Benennung**; jedes Abo
hat seine eigene Benennung.

## Schemas

| Schema | Titel in der Mediathek | Datei |
|---|---|---|
| Staffel und Folge | `Folge 3: Was für ein Theater (S02/E03)` | `Werwoelfe/Season 02/Werwoelfe - S02E03 - Folge 3 - Was fuer ein Theater.mp4` |
| Folgennummer | `Menschenjagd in gut (39)` | `Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E39 - Menschenjagd in gut.mp4` |
| Datum | `Die Anstalt vom 7. Oktober 2026` | `Die Anstalt/Die Anstalt - 2026-10-07 - Die Anstalt vom 7. Oktober 2026.mp4` |
| Eigene Vorlage | | Ordner und Name aus Platzhaltern |

Die Beispiele zeigen die Voreinstellung mit vereinfachten Dateinamen (unten).

Für einzelne Downloads ist **Serien (Plex/Jellyfin)** der Standard: Staffel und Folge aus dem Titel, sonst die
Folgennummer als Staffel 1, sonst das Datum. In Abos legst du beim Schema Folgennummer die Staffel selbst fest; dort
lassen sich auch der Serienname und der Ordner ändern (leer heißt: das Thema der Sendung).

Untertitel liegen mit demselben Namen und der Endung `.de.srt` daneben.

## Eigene Vorlage

Teile mit `/` trennen Ordner. Die Platzhalter gibt es auf Deutsch und Englisch:

| Platzhalter | Inhalt |
|---|---|
| `{thema}`, `{topic}` | Name der Sendung |
| `{titel}`, `{title}` | Titel ohne Folgennummer |
| `{staffel}`, `{season}` | Staffel, zweistellig |
| `{folge}`, `{episode}` | Folge, zweistellig |
| `{datum}`, `{date}` | Sendedatum `JJJJ-MM-TT` |
| `{jahr}`, `{year}` | Jahr der Sendung |
| `{sender}`, `{channel}` | Sender |
| `{ext}` | Dateiendung |

Beispiel: `{thema}/Staffel {staffel}/{thema} {folge} {titel}.{ext}` ergibt
`Das Gipfeltreffen/Staffel 01/Das Gipfeltreffen 39 Menschenjagd in gut.mp4`. Ein unbekannter Platzhalter wird beim
Speichern gemeldet. Das Live-Beispiel in den Einstellungen zeigt das Ergebnis für eine Folge mit Nummer, eine mit Staffel und
Folge und eine Sendung ohne Nummer.

## Dateinamen vereinfachen

Ab Werk schreibt mtdl-web Namen ohne Sonderzeichen: Umlaute werden umschrieben (ä → ae, ß → ss), Akzente, Emojis
und andere Symbole entfallen, typografische Striche und Anführungszeichen werden einfach. Ausgeschaltet bleiben die
Namen, wie die Sender sie schreiben.

Zeichen, die Dateisysteme oder Windows-Freigaben ablehnen (`: ? * " < > |`), ersetzt mtdl-web immer.

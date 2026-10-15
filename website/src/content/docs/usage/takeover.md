---
title: Umzug von MediathekView
description: Download-Historie und Abos der Desktop-App MediathekView übernehmen, damit nichts doppelt geladen wird.
---

Wer bisher MediathekView genutzt hat, übernimmt dessen Historie und Abos. Danach wissen die Abos von mtdl-web, was
schon geladen ist, und laden es nicht noch einmal.

## Dateien finden

MediathekView speichert alles im Ordner `.mediathek3` im Benutzerverzeichnis, im Docker-Container von MediathekView
im Konfigurationsordner:

| Datei | Inhalt | Nötig? |
|---|---|---|
| `history.txt` | alle Downloads | ja |
| `downloadAbos.txt` | Downloads der Abos | optional |
| `mediathek.xml` | Einstellungen samt Abos | optional, für die Abos |

## Übernehmen

Unter **Einstellungen**, **System**, **Aus MediathekView übernehmen** die Dateien wählen und **Prüfen**. Die Prüfung
ändert noch nichts und dauert gut zehn Sekunden. Sie zeigt:

- wie viele Zeilen gelesen und Folgen gefunden wurden, und wie: über die Adresse, über den Ordner der Datei oder über
  Thema und Titel,
- was schon in mtdl-web ist, was nicht mehr in der Filmliste steht und was nicht eindeutig ist,
- welche Abos entstehen und welche es schon gibt.

**Übernehmen** trägt die gefundenen Folgen als geladen in den Verlauf ein und legt die Abos an.

## Was dabei passiert

- **Folgen** findet mtdl-web meist über die Adresse in der Historie, sonst über den Anfang von Thema und Titel, wenn
  er genau eine Sendung meint. Was die Filmliste nicht mehr kennt, ließe sich ohnehin nicht neu laden und wird nur
  gezählt. Im Verlauf tragen übernommene Folgen den Vermerk „aus MediathekView übernommen“.
- **Abos** kommen pausiert an, damit du sie in Ruhe prüfst. Ihre Bedingungen sind gewöhnliche, sichtbare Regeln.
  Werte mit dem Präfix `#:` werden zu regulären Ausdrücken; „Thema oder Titel“ und „irgendwo“ werden zum Suchbegriff
  des Abos. Ein Abo, das es schon gibt, legt mtdl-web nicht doppelt an.

Danach im Abo die Vorschau ansehen, Ziel und Benennung prüfen und das Abo fortsetzen.

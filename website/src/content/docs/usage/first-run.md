---
title: Ersteinrichtung
description: Was der Assistent beim ersten Start abfragt und wo sich alles später ändern lässt.
---

Beim ersten Aufruf führt ein kurzer Assistent durch die Einrichtung. Jeder Schritt lässt sich später in den
Einstellungen ändern; **Später einrichten** überspringt den Rest.

## 1. Passwort

Der Assistent beginnt mit dem Passwort und dem Einrichtungscode aus dem Log des Containers, siehe
[Anmeldung](../../install/access/#erstes-passwort). Mit `MTDL_PASSWORD` und in den Modi `local`, `proxy` und
`none` entfällt der Schritt.

## 2. Zielordner

mtdl-web zeigt den Download-Ordner, wie ihn der Container sieht, und legt dort zur Probe eine Datei an. Klappt das
nicht, nennt es den Grund, den Benutzer, unter dem es läuft, und einen Befehl, der den Besitz richtet. Mehr unter
[Ordner und Rechte](../../install/folders/).

## 3. Filmliste

Die erste Filmliste hat rund 720.000 Einträge. Der Assistent zeigt den Fortschritt: erst das Herunterladen, dann
das Einlesen samt Suchindex. Das dauert ein bis zwei Minuten; **Im Hintergrund weiterladen** geht schon zum nächsten
Schritt. Danach holt mtdl-web stündlich nur die Änderungen. Scheitert der Abruf, versucht es mtdl-web später von
selbst wieder.

## 4. Benennung

Wie einzelne Downloads im Download-Ordner heißen:

- **Serien mit Staffel und Folge** für Plex und Jellyfin: `Serie/Season 01/Serie - S01E39 - Titel.mp4`
- **Nach Sendedatum**: `Serie/Serie - 2026-10-07 - Titel.mp4`

Eigene Vorlagen und vereinfachte Dateinamen gibt es in den Einstellungen unter **Ziel und Benennung**, siehe
[Dateinamen](../naming/). Abos haben eine eigene Benennung.

Am Ende verweist der Assistent auf die Bibliotheken: Wer Serien und Filme direkt in die Ordner von Plex oder
Jellyfin laden will, richtet sie unter **Einstellungen**, **Bibliotheken und Plex** ein, siehe
[Bibliotheken](../libraries/).

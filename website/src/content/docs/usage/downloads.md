---
title: Downloads
description: Warteschlange, Bandbreitenlimit mit Zeitplan, Wiederaufnahme, Fehler mit Lösung und der Verlauf.
---

Die Ansicht **Downloads** zeigt, was gerade lädt, was wartet und was nicht geklappt hat. Die Statusleiste unten
fasst das auf jeder Seite zusammen.

## Warteschlange

Downloads starten in der Reihenfolge der Warteschlange. Die Reihenfolge änderst du per Ziehen oder mit den Pfeilen,
**Nach vorne** zieht einen Download an die Spitze. Wie viele gleichzeitig laden (1 bis 10), stellst du in der
Ansicht oder unter **Einstellungen**, **Downloads** ein; die Änderung wirkt sofort.

**Abbrechen** beendet einen Download und löscht seine Teildatei, **Alle abbrechen** alle wartenden und laufenden.
Beides lässt sich kurz rückgängig machen. Abgebrochenes lädt kein Abo von selbst wieder.

## Bandbreitenlimit

Ein Limit gilt für alle Downloads zusammen und wirkt sofort auf laufende Downloads, ohne Neustart. Die Einheit ist
wählbar, Mbit/s oder MB/s, und gilt auch für alle Geschwindigkeitsanzeigen. `0` oder leer heißt unbegrenzt. Das
Limit änderst du über die Statusleiste oder unter **Einstellungen**, **Downloads**.

Ein **Zeitplan** setzt ein zweites Limit für bestimmte Tage und Uhrzeiten, etwa werktags tagsüber gedrosselt und
nachts frei. Zeitfenster über Mitternacht gehen auch (22:00 bis 06:00), gleiche Zeiten heißen ganzer Tag. Im
Zeitfenster öffnet der Schalter in der Statusleiste das Limit bis zum Ende des Fensters.

Jedes Byte läuft durch dieses eine Limit, auch bei HLS: mtdl-web lädt die Segmente selbst, ffmpeg setzt sie nur
lokal zusammen.

## Formate und Qualität

| | |
|---|---|
| Video | MP4; HLS-Sendungen setzt ffmpeg ohne Neukodierung zu MP4 zusammen |
| Qualität | HD, Hoch oder Klein; fehlt die gewünschte, lädt mtdl-web die nächstniedrigere, sonst die nächsthöhere |
| Untertitel | als `.de.srt` neben dem Video, wenn der Sender welche anbietet (abschaltbar) |

Die Voreinstellungen stehen unter **Einstellungen**, **Qualität**; Abos können eigene haben.

## Wiederaufnahme und Wiederholung

Bricht eine Verbindung ab, setzt mtdl-web den Download fort, statt neu zu beginnen. Auch nach einem Neustart des
Containers laufen Downloads dort weiter, wo sie waren. Bei vorübergehenden Fehlern des Senders versucht es mtdl-web
nach 5 und nach 10 Minuten erneut; die Zeile zeigt den nächsten Versuch.

## Fehler mit Lösung

Scheitert ein Download endgültig, steht er unter **Fehlgeschlagen**, mit Ursache, Lösung und technischen Details
zum Kopieren:

| Fehler | Was dahintersteckt |
|---|---|
| Geo-gesperrt | Der Sender liefert die Sendung nur in bestimmte Länder, der Server ist über ein anderes im Netz. |
| Zugriff verweigert | Der Sender lehnt ab, oft wegen einer Geo-Sperre, die in der Filmliste fehlt. |
| Nicht mehr verfügbar | Die Sendung wurde aus der Mediathek entfernt. |
| Zielordner nicht beschreibbar | Rechte passen nicht, mit dem passenden Befehl; siehe [Ordner und Rechte](../../install/folders/). |
| Kein Platz mehr | Das Ziellaufwerk ist voll; nach dem Aufräumen geht es weiter, wo es war. |
| Zusammensetzen fehlgeschlagen | ffmpeg konnte die HLS-Teile nicht verbinden. |

**Erneut versuchen** stellt einen Download wieder ein. Ein nicht beschreibbarer Zielordner ist kein Fehler im
engeren Sinn: Downloads dorthin warten, bis der Ordner wieder geht.

## Verlauf

Der **Verlauf** listet jede geladene Sendung mit Zielpfad, Sendedatum und Größe, nach Tagen gruppiert, durchsuchbar
und nach Abo filterbar. **Entfernen** löscht nur den Eintrag, nicht die Datei; danach darf ein Abo die Sendung
erneut laden. Abgebrochene Downloads stehen ebenfalls hier; **Erneut laden** stellt sie wieder ein.

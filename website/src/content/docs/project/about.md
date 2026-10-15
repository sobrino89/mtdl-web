---
title: Über das Projekt
description: Wer hinter mtdl-web steht, warum es entstanden ist und wie es mit KI entwickelt wird.
---

## Warum

Für die Mediatheken gibt es seit vielen Jahren den Desktop-Client
[MediathekView](https://github.com/mediathekview/MediathekView). Auf einem Server läuft er meist als Container, der
die Desktop-Anwendung samt Oberfläche in ein Browserfenster bringt: über ein GB Image, mehrere GB Arbeitsspeicher,
Bedienung wie am Desktop. Für meinen Heimserver war mir das zu schwer. Ich wollte eine schlanke Web-App für
dieselbe Aufgabe und habe deshalb mtdl-web gebaut.

## Wer

mtdl-web ist ein privates Projekt von [sobrino89](https://github.com/sobrino89). Ich komme aus der
Anwendungsentwicklung, habe angewandte Informatik studiert und arbeite seit 2014 als IT-Systemadministrator.

## Mit KI entwickelt

Den Großteil des Codes hat eine KI geschrieben: [Claude](https://claude.com) von Anthropic. Ich lege fest, was
mtdl-web können soll und wie es sich verhält, entscheide bei Abwägungen, prüfe die Ergebnisse und betreibe es
täglich selbst. Damit das trägt, gelten feste Regeln:

- Jede Änderung läuft durch automatische Prüfungen: über 350 Tests im Backend, Typprüfung im strengen Modus,
  Tests der Oberfläche. Die Tests arbeiten mit echten Daten aus der Filmliste und von TMDB, nicht mit ausgedachten.
- Änderungen am Abgleich und an der Folgenerkennung werden vorher über echte Bestände alt gegen neu verglichen.

Fehler sind trotzdem möglich, wie in jeder Software. Meldungen helfen: [Issues auf
GitHub](https://github.com/sobrino89/mtdl-web/issues).

## Was als Nächstes kommt

Geplant sind unter anderem Benachrichtigungen über Apprise, eine Aufbewahrungsfrist für geladene Dateien, Film-Abos
und eine App für Unraid. Die Liste steht in der
[Roadmap im README](https://github.com/sobrino89/mtdl-web#roadmap).

## Unabhängig

mtdl-web nutzt die Filmliste des MediathekView-Projekts, ist aber eigenständig und nicht mit dem
MediathekView-Team verbunden, ebenso wenig mit den Sendern oder mit TMDB. Dank und Lizenzen stehen unter
[Lizenz und Dank](../license/).

---
title: Ordner und Rechte
description: Wie mtdl-web Zielordner prüft, wem neue Dateien gehören und was bei fehlenden Rechten passiert.
---

Die häufigste Ursache für scheiternde Downloads in Containern sind Ordnerrechte. mtdl-web prüft sie deshalb selbst
und sagt, wie man sie behebt, statt erst beim hundertsten Download zu scheitern.

## Wem die Dateien gehören

mtdl-web läuft als `PUID`:`PGID` und legt jede Datei und jeden Ordner als dieser Benutzer an, mit den Rechten aus
`UMASK` (Standard `022`: Dateien `644`, Ordner `755`). Am besten setzt du `PUID` und `PGID` auf den Besitzer des
Download-Ordners; `id` auf dem Server zeigt deine Werte, `ls -ln /pfad/zu/downloads` den Besitzer des Ordners.

Beim Start übergibt der Container nur `/config` und den Cache an diesen Benutzer, und nur deren oberste Ebene. In
Download-Ordnern und Bibliotheken ändert er nie Besitzer oder Rechte.

## Schreibtest

Beim Start, in der Ersteinrichtung und auf Knopfdruck in den Einstellungen (**Ziel und Benennung**, **Jetzt
prüfen**) legt mtdl-web im Download-Ordner und in jeder Bibliothek eine leere Probedatei an und löscht sie wieder.
Klappt das nicht, zeigt die Oberfläche einen Hinweis mit dem Grund und einem Befehl zum Kopieren, der den Besitz
im Container auf den Benutzer von mtdl-web setzt:

```sh
docker exec -u 0 mtdl-web chown -R 1000:1000 /downloads
```

Alternativ startest du den Container mit `PUID` und `PGID` des Ordnerbesitzers.

## Downloads warten, statt zu scheitern

Ist ein Ziel nicht beschreibbar oder fehlt es, warten seine Downloads in der Warteschlange: etwa wenn ein Array
gestoppt ist, eine Netzwerkfreigabe fehlt oder die Rechte nicht passen. Abos prüfen weiter, laden aber erst, wenn der
Ordner wieder geht. mtdl-web prüft ihn dann jede Minute neu und macht von selbst weiter; **Erneut prüfen** im Hinweis
prüft sofort. Solange alles in Ordnung ist, prüft mtdl-web nichts, schlafende Platten bleiben schlafen.

## Nur eingebundene Ordner

Ein Ordner, der nicht als Volume eingebunden ist, liegt im Container selbst: Alles darin wäre beim nächsten Update
des Containers verloren. mtdl-web erkennt solche Ordner und schreibt nicht hinein. Bibliotheken legt es nie selbst
an, ein fehlender Bibliotheksordner bleibt ein Fehler, bis er eingebunden ist.

Die Erkennung vergleicht das Laufwerk des Ordners mit dem des Containers. Bei sehr ungewöhnlichen Einbindungen kann
sie sich irren; dann schaltet `MTDL_CHECK_MOUNTS=false` sie ab.

## Unfertige Dateien

| Ziel | Wo Unfertiges liegt |
|---|---|
| Download-Ordner | neben der Zieldatei, als `.part` (HLS-Segmente in einem Ordner `.parts`) |
| Bibliothek | im versteckten Ordner `.mtdl-incoming` der Bibliothek, mit `.plexignore` für Plex und `.ignore` für Jellyfin und Emby |

In einer Bibliothek sieht der Mediaserver so nie eine halbe Datei. Die fertige Datei kommt durch Umbenennen an ihren
Platz, auf demselben Laufwerk und damit in einem Schritt. Ist `.mtdl-incoming` leer, entfernt mtdl-web den Ordner
wieder. Ein abgebrochener Download löscht seine Teile; ein Neustart setzt laufende Downloads dort fort, wo sie waren.

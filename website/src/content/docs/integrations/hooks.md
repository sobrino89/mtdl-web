---
title: Skripte nach Ereignissen
description: Eigene Programme im Container nach jedem Download oder jeder neuen Filmliste, etwa zum Umwandeln, Verschieben oder Taggen.
---

Für die Nachbearbeitung führt mtdl-web eigene Skripte aus. Jede ausführbare Datei in
`/config/hooks/<ereignis>.d/` läuft bei diesem Ereignis: eine nach der anderen in Namensreihenfolge, als
`PUID`:`PGID` mit `UMASK` und im Download-Ordner.

| Ordner | läuft, wenn |
|---|---|
| `download.queued.d/` | ein Download eingereiht wurde |
| `download.started.d/` | ein Download beginnt |
| `download.completed.d/` | ein Download fertig ist |
| `download.failed.d/` | ein Download gescheitert ist |
| `filmlist.updated.d/` | eine neue Filmliste eingelesen ist |
| `subscription.expired.d/` | ein Abo seinen letzten Tag erreicht hat |

## Eingabe

Das Skript bekommt das Ereignis als JSON auf stdin, im selben Format wie ein [Webhook](../webhooks/), und als
Umgebungsvariablen:

| Variable | Inhalt |
|---|---|
| `MTDL_EVENT`, `MTDL_TIME` | Ereignis und Zeitpunkt (UTC) |
| `MTDL_FILE`, `MTDL_SUBTITLE_FILE` | absoluter Pfad des Videos und der Untertitel (bei Downloads) |
| `MTDL_PATH`, `MTDL_LIBRARY_ID` | Pfad relativ zur Bibliothek `MTDL_LIBRARY_ID`, ohne Bibliothek zum Download-Ordner |
| `MTDL_TITLE`, `MTDL_TOPIC`, `MTDL_CHANNEL`, `MTDL_SIZE`, `MTDL_ERROR` … | jedes Feld des Ereignisses, Name in Großbuchstaben |

In den Variablen steht jeder Wert in einer Zeile: Zeilenumbrüche und andere Steuerzeichen werden zu Leerzeichen. Das
JSON auf stdin enthält die Felder unverändert.

## Beispiel

Video und Untertitel zusätzlich als MKV ablegen:

```sh title="/config/hooks/download.completed.d/50-mkv.sh"
#!/bin/sh
[ -n "$MTDL_SUBTITLE_FILE" ] || exit 0
exec ffmpeg -nostdin -loglevel error -y -i "$MTDL_FILE" -i "$MTDL_SUBTITLE_FILE" -map 0:v -map 0:a -map 1 \
  -c copy -c:s srt -metadata:s:s:0 language=ger "${MTDL_FILE%.*}.mkv"
```

```sh
chmod +x config/hooks/download.completed.d/50-mkv.sh
```

## Regeln

- Nach `MTDL_HOOK_TIMEOUT` Sekunden (Standard 600) wird ein Skript samt Kindprozessen beendet.
- Ausgabe und Exit-Code stehen im Log und unter **Einstellungen**, **Benachrichtigungen**, **Skripte**, beim
  jeweiligen Skript mit seinem letzten Lauf.
- Neue Skripte gelten ohne Neustart. `chmod -x` schaltet eines ab.
- Im Image gibt es `sh`, `bash`, `python3`, `ffmpeg` und die Grundwerkzeuge, aber kein `curl`. Andere Dienste
  erreichst du besser über [Webhooks](../webhooks/).

:::caution[Wer hier schreiben darf, führt Code aus]
Die Ordner unter `/config/hooks/` legt mtdl-web unabhängig von `UMASK` nur für `PUID` beschreibbar an (`755`). Gib
anderen keinen Schreibzugriff darauf. Die API zeigt Skripte nur an; sie kann keine anlegen, ändern oder starten.
:::

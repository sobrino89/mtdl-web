---
title: Updates und Sicherung
description: Neue Versionen einspielen, was zu sichern ist und wie man zurückkehrt.
---

## Update

```sh
docker compose pull
docker compose up -d
```

Beim Start bringt mtdl-web seine Datenbank selbst auf den neuen Stand. Laufende Downloads setzen nach dem Neustart
dort fort, wo sie waren. Ein offenes Browserfenster verbindet sich von selbst wieder; die neue Oberfläche bekommt es
mit dem nächsten Neuladen der Seite.

Welche Version läuft, zeigen **Einstellungen**, **System** und die Kopfzeile des Logs. Was sich geändert hat, steht
in den [Releases auf GitHub](https://github.com/sobrino89/mtdl-web/releases).

Welche Tags es gibt, steht unter [Image und Versionen](../docker-compose/#image-und-versionen).

## Sichern

| Was | Sichern? |
|---|---|
| `/config/mtdl-web.sqlite` (mit `-wal` und `-shm`) | ja: Einstellungen, Abos, Verlauf, Bibliotheken, API-Schlüssel, Webhooks |
| `/config/hooks/` | ja, falls du eigene Skripte nutzt |
| `/config/cache/` | nein: Filmliste und Suchindex lädt mtdl-web jederzeit neu |
| Downloads und Bibliotheken | wie deine übrigen Medien |

Am sichersten sicherst du bei gestopptem Container. Im laufenden Betrieb hilft die Sicherungsfunktion von SQLite,
denn eine einfache Dateikopie einer offenen Datenbank kann einen halben Schreibvorgang erwischen. `-u` mit deinen
Werten für `PUID`:`PGID`, damit die Kopie mtdl-web gehört:

```sh
docker exec -u 1000:1000 mtdl-web python -c "import sqlite3; sqlite3.connect('/config/mtdl-web.sqlite').backup(sqlite3.connect('/config/backup.sqlite'))"
```

Die Datei `/config/backup.sqlite` kommt dann in die Sicherung. Passwort, Sitzungen und API-Schlüssel liegen dort nur
als Hash, Tokens für TMDB und Plex dagegen lesbar: Behandle die Sicherung wie ein Passwort.

## Zurück zu einer älteren Version

Eine neuere Version passt die Datenbank beim Start an; eine ältere kennt diese Änderungen nicht. Vor einem Update
lohnt deshalb eine Sicherung von `mtdl-web.sqlite`. Zurück geht es dann mit dem alten Image-Tag und der Sicherung
von vorher.

## Umziehen

Container stoppen, `/config` ohne `cache/` auf den neuen Rechner kopieren, dort mit denselben Volumes starten. Liegen
Download-Ordner oder Bibliotheken unter anderen Pfaden im Container, passt du die Bibliotheken in den Einstellungen
an; ihre Downloads ziehen mit, die Dateien selbst verschiebst du.

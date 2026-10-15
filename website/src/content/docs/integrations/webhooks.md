---
title: Webhooks
description: Ereignisse als HTTP-Anfragen an Home Assistant, n8n, Sonarr, Gotify, ntfy, Discord oder eigene Dienste, auf Wunsch signiert.
---

Ein Webhook schickt bei jedem gewählten Ereignis eine `POST`-Anfrage an eine Adresse. Webhooks legst du unter
**Einstellungen**, **Benachrichtigungen** an: Adresse, Ereignisse, optional ein Geheimnis, eine eigene Vorlage für
den Inhalt und eigene Kopfzeilen. **Test senden** schickt ein Beispiel und zeigt die Antwort.

## Ereignisse

| Ereignis | Wann |
|---|---|
| `download.queued` | ein Download wurde eingereiht |
| `download.started` | ein Download beginnt |
| `download.completed` | ein Download ist fertig |
| `download.failed` | ein Download ist gescheitert, mit `will_retry`, ob mtdl-web es erneut versucht |
| `filmlist.updated` | eine neue Filmliste oder Änderungsliste ist eingelesen |
| `subscription.expired` | ein Abo hat seinen letzten Tag erreicht und pausiert oder löscht sich |

Die Namen der Ereignisse sind Teil der öffentlichen Schnittstelle: Sie ändern sich nicht, neue kommen hinzu.

## Standardformat

Ohne Vorlage ist der Inhalt JSON mit Ereignis, Zeitpunkt (UTC) und den Feldern des Ereignisses:

```json
{
  "event": "download.completed",
  "time": "2026-10-15T18:42:07.512094Z",
  "data": {
    "download_id": 42,
    "film_id": "1234567890123456789",
    "channel": "MDR",
    "topic": "Das Gipfeltreffen",
    "title": "Das Beste kommt noch! (58)",
    "path": "Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E58 - Das Beste kommt noch!.mp4",
    "subscription_id": 3,
    "file": "/downloads/Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E58 - Das Beste kommt noch!.mp4",
    "library_id": null,
    "size": 512000000,
    "subtitle_path": "Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E58 - Das Beste kommt noch!.de.srt",
    "subtitle_file": "/downloads/Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E58 - Das Beste kommt noch!.de.srt"
  }
}
```

| Feld | Bei | Inhalt |
|---|---|---|
| `download_id`, `film_id`, `channel`, `topic`, `title` | allen `download.*` | der Download und seine Sendung |
| `path`, `library_id` | allen `download.*` | Pfad relativ zur Bibliothek `library_id`, ohne Bibliothek zum Download-Ordner |
| `file` | allen `download.*` | absoluter Pfad des Videos im Container |
| `subscription_id` | allen `download.*` | das Abo, `null` bei einzelnen Downloads |
| `size`, `subtitle_path`, `subtitle_file` | `download.completed` | Größe in Byte, Untertitel relativ und absolut |
| `error`, `will_retry` | `download.failed` | Fehlertext; ob ein weiterer Versuch folgt |
| `kind`, `list_id`, `created`, `added`, `changed`, `removed`, `total` | `filmlist.updated` | `full` oder `diff`, Stand und Zahlen der Liste |
| `subscription_id`, `subscription_name`, `valid_until`, `action` | `subscription.expired` | `action` ist `paused` oder `deleted` |

Jede Anfrage trägt die Kopfzeile `X-Mtdl-Event` mit dem Namen des Ereignisses.

## Signatur

Mit einem Geheimnis signiert mtdl-web jeden Inhalt: `X-Mtdl-Signature: sha256=<HMAC-SHA256(geheimnis, inhalt)>`,
als Hex über die rohen Bytes des Inhalts. Prüfen etwa in Python:

```python
import hashlib, hmac

def valid(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
```

## Eigene Vorlage

Manche Empfänger erwarten ein eigenes Format. Eine Vorlage ersetzt dann den Inhalt; Platzhalter sind `{{event}}`,
`{{time}}` und `{{data.<feld>}}`. In Anführungszeichen setzt mtdl-web einen Wert als Text ein (passend maskiert),
frei stehend als JSON-Wert (`"size": {{data.size}}`). Für reinen Text setzt du die Kopfzeile
`Content-Type: text/plain`. Fehlt einem Ereignis ein Feld, bleibt der Platzhalter leer bzw. `null`.

Eigene Kopfzeilen tragen etwa API-Schlüssel des Empfängers. Gespeicherte Werte zeigt mtdl-web nie wieder an.

Beispiele, die die Oberfläche fertig anbietet:

**Sonarr: Import anstoßen** an `http://<sonarr>:8989/api/v3/command`, Kopfzeile `X-Api-Key` mit dem Schlüssel aus
Sonarr, Ereignis `download.completed`. `/downloads/` ersetzt du durch den Pfad, unter dem Sonarr den Download-Ordner
sieht:

```json
{
  "name": "DownloadedEpisodesScan",
  "path": "/downloads/{{data.path}}",
  "importMode": "Move"
}
```

**Gotify** an `http://<gotify>/message`, Kopfzeile `X-Gotify-Key` mit dem Token einer Gotify-Anwendung:

```json
{
  "title": "{{data.topic}}",
  "message": "{{data.title}} ({{data.channel}})",
  "priority": 5
}
```

**ntfy** an `https://ntfy.sh/<thema>` oder den eigenen Server, Kopfzeilen `Content-Type: text/plain; charset=utf-8`
und `Title: mtdl-web`:

```text
{{data.topic}}: {{data.title}} ({{data.channel}})
```

**Discord** an die Webhook-Adresse des Kanals:

```json
{
  "content": "**{{data.topic}}**: {{data.title}} ({{data.channel}})"
}
```

## Zustellung

mtdl-web versucht jede Nachricht bis zu dreimal: sofort, nach 5 und nach 30 Sekunden. Antwortet der Empfänger mit
einem Fehler der Art „so nicht“ (`4xx` außer `429`) oder einer Weiterleitung, versucht es mtdl-web nicht erneut;
Weiterleitungen folgt es nie, damit Kopfzeilen nicht bei einem anderen Server landen. Das letzte Ergebnis steht beim
Webhook in den Einstellungen. Zeitüberschreitung: 10 Sekunden je Versuch.

Ein Webhook ist eine Benachrichtigung, keine Warteschlange: Ist der Empfänger länger nicht erreichbar, gehen
Nachrichten verloren. Was zählt, steht weiter in mtdl-web und lässt sich über die [REST-API](../rest-api/)
abfragen.

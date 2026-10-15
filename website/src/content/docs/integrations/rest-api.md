---
title: REST-API
description: Alles, was die Oberfläche kann, als HTTP-API mit API-Schlüssel, OpenAPI-Beschreibung und maschinenlesbaren Fehlern.
---

Die Oberfläche von mtdl-web nutzt selbst nur die REST-API. Was sie kann, können Skripte und andere Dienste also
auch: suchen, Downloads einreihen, Abos anlegen und prüfen, Einstellungen ändern.

- **Basis:** `http://<server>:8080/api/v1`, JSON hinein und heraus.
- **Referenz:** auf dieser Seite unter [API-Referenz](/mtdl-web/api/) und in jeder Instanz unter `/api/docs`
  (Swagger UI, zum Ausprobieren), die Beschreibung als OpenAPI unter `/api/v1/openapi.json`.
- **Version:** Der Pfad trägt die Version. Felder kommen hinzu, ohne dass `v1` endet; was wegfällt oder sich ändert,
  kommt in eine neue Version.

## Anmelden mit Schlüssel

API-Schlüssel erzeugst du unter **Einstellungen**, **Zugang**. Sie gelten in jedem Anmeldemodus und gehen in die
Kopfzeile `X-Api-Key`:

```sh
curl -H "X-Api-Key: mtdl_…" http://mtdl-web:8080/api/v1/status
```

| Recht | Darf |
|---|---|
| Nur lesen (Standard) | alle `GET`-Anfragen und die Vorschauen (`POST`, die nichts ändern: Abo-, Benennungs- und Bibliotheksvorschau, Ziel eines Downloads) |
| Lesen und ändern | alles |

Ohne Schlüssel entscheidet der Anmeldemodus. Das Sitzungs-Cookie des Browsers ist für die Oberfläche gedacht, nicht
für Skripte.

## Beispiele

Suchen, mit derselben Syntax wie in der Oberfläche:

```sh
curl -G -H "X-Api-Key: $KEY" http://mtdl-web:8080/api/v1/search \
  --data-urlencode "q=#tatort >80" --data-urlencode "limit=5"
```

Einen Film einreihen (die `id` stammt aus der Suche):

```sh
curl -H "X-Api-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"film_id": "1234567890123456789"}' http://mtdl-web:8080/api/v1/downloads
```

Ein Abo als Entwurf prüfen, ohne es zu speichern:

```sh
curl -H "X-Api-Key: $KEY" -H "Content-Type: application/json" http://mtdl-web:8080/api/v1/subscriptions/preview -d '{
  "name": "Das Gipfeltreffen",
  "query": "gipfeltreffen",
  "conditions": [
    {"field": "topic", "operator": "is", "value": "Das Gipfeltreffen"},
    {"field": "title", "operator": "is", "value": "\\(\\d+\\)$", "regex": true}
  ]
}'
```

Was gerade läuft, samt Bandbreitenlimit in Kraft:

```sh
curl -H "X-Api-Key: $KEY" http://mtdl-web:8080/api/v1/status
```

## Fehler

Fehler tragen in `detail` ein Objekt mit einem maschinenlesbaren `code`, manchmal mit weiteren Feldern:

```json
{"detail": {"code": "subscription_not_found"}}
{"detail": {"code": "library_in_use", "subscriptions": [3], "downloads": []}}
```

Eingaben, die nicht zum Schema passen, beantwortet die API mit `422` und der Liste der Prüffehler von FastAPI in
`detail`, je Feld mit Ort und Grund.

| Status | Bedeutung |
|---|---|
| `400` | Anfrage passt nicht, etwa eine Suche in Beschreibungen ohne Index |
| `401` | kein oder ein ungültiger Schlüssel (`api_key_invalid`, `login_required`) |
| `403` | Schlüssel darf nur lesen (`api_key_read_only`) oder Anfrage von einer fremden Seite (`cross_site`) |
| `404` | nicht gefunden (`film_not_found`, `download_not_found`, `subscription_not_found` …) |
| `409` | passt nicht zum Zustand, etwa ein Download, der schon fertig ist |
| `422` | ungültige Eingabe, mit Angaben zum Feld |
| `502` | die Datenquelle antwortet nicht |

## Fremde Webseiten

Ein Browser kennzeichnet Anfragen, die eine andere Webseite auslöst. Solche Änderungen lehnt mtdl-web mit `403` und
dem Code `cross_site` ab. Skripte senden diese Kennzeichen nicht und sind nicht betroffen.

## Ereignisse

Statt die API abzufragen, kann mtdl-web selbst melden, wenn etwas passiert: per [Webhook](../webhooks/) an andere
Dienste oder per [Skript](../hooks/) im Container. KI-Assistenten nutzen den [MCP-Server](../mcp/).

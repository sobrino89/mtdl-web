---
title: Anmeldung
description: Die vier Anmeldemodi, das erste Passwort, ein vergessenes Passwort und API-Schlüssel.
---

Wer mtdl-web erreicht, kann Downloads starten, Abos und Webhooks ändern. Deshalb fragt es ab Werk nach einem
Passwort. Den Modus legt `MTDL_AUTH` beim Start fest.

## Modi

| `MTDL_AUTH` | Verhalten | Für wen |
|---|---|---|
| `password` (Standard) | Ein Konto mit Passwort. Jeder Browser bleibt 30 Tage nach der letzten Nutzung angemeldet; nach mehreren falschen Versuchen muss man warten. | alle |
| `local` | Im eigenen Netz ohne Passwort, von außen mit Passwort | nur erfahrene Nutzer, siehe Warnung |
| `proxy` | Die Anmeldung übernimmt ein Reverse Proxy (Authelia, Authentik, oauth2-proxy) und übergibt den Benutzer in einer Kopfzeile | wer schon einen Identitätsdienst betreibt, siehe [Reverse Proxy](../reverse-proxy/) |
| `none` | Keine Anmeldung. Kommt eine Anfrage von außerhalb des eigenen Netzes, zeigt die Oberfläche eine rote Warnung. | nur abgeschottete Netze |

## Erstes Passwort

Ohne `MTDL_PASSWORD` fragt die Oberfläche beim ersten Aufruf nach einem Passwort (mindestens 8 Zeichen) und nach
einem Einrichtungscode, den nur das Log des Containers zeigt:

```sh
docker logs mtdl-web 2>&1 | grep "setup code"
```

So kann niemand, der bloß die Adresse erreicht, die Instanz vor dir übernehmen. Nach jedem Neustart gilt ein neuer
Code, bis ein Passwort festgelegt ist.

Mit `MTDL_PASSWORD` (oder `MTDL_PASSWORD_FILE` für ein Docker-Secret) steht das Passwort fest. Die Oberfläche lässt
es dann nicht ändern.

Unter **Einstellungen**, **Zugang** änderst du das Passwort (alle anderen Sitzungen enden) und meldest dich auf
Wunsch überall sonst ab, etwa nach einem verlorenen Handy.

## Passwort vergessen

Im Container zurücksetzen. Der Befehl gibt einen neuen Einrichtungscode aus und beendet alle Sitzungen:

```sh
docker exec -it mtdl-web python -m mtdl_web.auth reset
```

Danach wählst du das neue Passwort in der Oberfläche wie beim ersten Start.

**Ohne Konsole**, wie auf manchen NAS-Oberflächen, geht es über die Umgebung: `MTDL_PASSWORD_RESET=true` setzen und
den Container neu starten. mtdl-web vergisst dann das Passwort, beendet alle Sitzungen und schreibt einen neuen
Einrichtungscode ins Log. API-Schlüssel bleiben gültig.

Die Variable wirkt nur einmal: Bleibt sie stehen, behalten weitere Neustarts das neue Passwort, und das Log erinnert
daran, sie zu entfernen. Erst nach einem Start ohne sie wirkt sie wieder. Neben `MTDL_PASSWORD` tut sie nichts, das
vorgegebene Passwort gilt weiter.

## Im eigenen Netz ohne Passwort

Mit `MTDL_AUTH=local` fragt mtdl-web im eigenen Netz nicht nach dem Passwort, von außen schon. Ohne Passwort kommt
nur rein, wer eindeutig aus deinem Netz kommt:

- Die Verbindung kommt von einer Adresse aus deinem Netz oder aus Tailscale.
- Reicht ein Proxy die Anfrage weiter, nennt er die Adresse des Besuchers, und auch die liegt in deinem Netz.
- Der Browser hat mtdl-web über eine IP-Adresse oder einen lokalen Namen aufgerufen, etwa `nas`, `nas.lan`,
  `nas.local` oder `nas.home.arpa`, nicht über eine Domain aus dem Internet.

Fehlt auch nur eine dieser Bedingungen, fragt mtdl-web nach dem Passwort. Namen unter `ts.net` zählen nicht als
lokal, weil Tailscale Funnel sie öffentlich machen kann.

:::caution[Modus local nur mit sauber eingerichtetem Reverse Proxy]
Ein falsch eingestellter Reverse Proxy lässt Besucher aus dem Internet wie Geräte aus deinem Netz aussehen: wenn er
ihre Adresse nicht weitergibt und zugleich die aufgerufene Adresse umschreibt. Dann wäre mtdl-web ohne Passwort
öffentlich. Von unterwegs deshalb am besten über ein VPN wie Tailscale, oder über einen sauber eingerichteten Reverse
Proxy, der die Adresse des Besuchers weitergibt (siehe [Reverse Proxy](../reverse-proxy/)). Ob es stimmt, zeigt ein
Aufruf von unterwegs: Fragt mtdl-web dann nach dem Passwort, passt die Einrichtung.
:::

:::danger[Modus none nie ins Internet]
Ohne Anmeldung kann jeder, der die Adresse kennt, Downloads starten und Abos, Webhooks und Skripte ändern. `none`
gehört nie hinter einen Reverse Proxy oder eine Portfreigabe.
:::

## API-Schlüssel

Für Skripte, andere Dienste (Home Assistant, n8n) und KI-Assistenten erzeugst du API-Schlüssel unter
**Einstellungen**, **Zugang**. Sie gelten in jedem Modus und gehen in die Kopfzeile `X-Api-Key`:

```sh
curl -H "X-Api-Key: mtdl_…" http://mtdl-web:8080/api/v1/downloads
```

Ein Schlüssel darf entweder **nur lesen** (alle `GET`-Anfragen und die Vorschauen, Standard für neue Schlüssel)
oder **lesen und ändern**. Mit einem lesenden Schlüssel antwortet jede Änderung mit `403` und dem Code
`api_key_read_only`. mtdl-web speichert nur einen Hash: Der Schlüssel erscheint einmal beim Erzeugen und nie wieder.
Mehr unter [REST-API](../../integrations/rest-api/).

## Schutz vor fremden Webseiten

Unabhängig vom Modus nimmt mtdl-web Änderungen nur von seinen eigenen Seiten an: Eine fremde Webseite, die du gerade
offen hast, kann keine Downloads auslösen, auch wenn dein Browser angemeldet ist. Ein Reverse Proxy muss dafür den
`Host` durchreichen oder `X-Forwarded-Host` setzen, wie es Nginx Proxy Manager, Traefik und Caddy tun.

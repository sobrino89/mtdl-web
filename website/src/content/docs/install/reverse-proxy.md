---
title: Reverse Proxy
description: mtdl-web hinter Caddy, Nginx oder Traefik, mit eigener Domain und HTTPS, auf Wunsch mit Anmeldung über Authelia oder Authentik.
---

Ein Reverse Proxy gibt mtdl-web eine eigene Adresse mit HTTPS, etwa `https://mtdl.example.org`. Für den Zugriff von
unterwegs ist ein VPN wie Tailscale oder WireGuard meist die bessere Wahl: Dann steht mtdl-web gar nicht erst im
Internet. Steht es dort doch, gehört es in den Modus `password` oder `proxy`, siehe [Anmeldung](../access/).

## Was der Proxy weitergeben muss

| Kopfzeile | Wozu |
|---|---|
| `Host` (oder `X-Forwarded-Host`) | Schutz vor fremden Webseiten: mtdl-web vergleicht die Herkunft einer Änderung mit der aufgerufenen Adresse |
| `X-Forwarded-For` | Adresse des Besuchers, für die Modi `local` und `none` |
| `X-Forwarded-Proto` | bei `https` bekommt das Sitzungs-Cookie das Attribut `Secure` |

Caddy, Traefik und Nginx Proxy Manager tun das von selbst. WebSockets braucht mtdl-web nicht.

## Beispiele

### Caddy

```caddy title="Caddyfile"
mtdl.example.org {
    reverse_proxy mtdl-web:8080
}
```

### Nginx

```nginx
server {
    listen 443 ssl;
    server_name mtdl.example.org;
    # ssl_certificate und ssl_certificate_key wie gewohnt

    client_max_body_size 64m;  # für den Umzug von MediathekView (Dateien bis 20 MB)

    location / {
        proxy_pass http://mtdl-web:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

In Nginx Proxy Manager gehört `client_max_body_size 64m;` in die eigene Konfiguration des Proxy-Hosts.

### Traefik

```yaml title="compose.yaml (Ausschnitt)"
    labels:
      - traefik.enable=true
      - traefik.http.routers.mtdl.rule=Host(`mtdl.example.org`)
      - traefik.http.routers.mtdl.entrypoints=websecure
      - traefik.http.routers.mtdl.tls.certresolver=letsencrypt
      - traefik.http.services.mtdl.loadbalancer.server.port=8080
```

## Anmeldung über den Proxy

Mit `MTDL_AUTH=proxy` übernimmt der Proxy die Anmeldung, etwa mit Authelia, Authentik oder oauth2-proxy. Dort gibt
es Zwei-Faktor, Passkeys und Single Sign-on. Der Proxy nennt mtdl-web den angemeldeten Benutzer in einer Kopfzeile:

```yaml
    environment:
      MTDL_AUTH: proxy
      MTDL_AUTH_HEADER: Remote-User          # so heißt sie bei Authelia und Authentik
      MTDL_TRUSTED_PROXIES: 172.18.0.0/16    # Adresse oder Netz des Proxys, Pflicht
```

mtdl-web glaubt die Kopfzeile nur Anfragen von `MTDL_TRUSTED_PROXIES`. Ohne diese Variable startet der Modus nicht.
Kommt eine Anfrage ohne Benutzer, zeigt die Oberfläche einen Hinweis statt einer Anmeldeseite.

:::caution[Die Kopfzeile darf nur vom Proxy kommen]
Der Proxy muss die Kopfzeile mit dem Benutzer bei jeder Anfrage selbst setzen oder entfernen, nie die des Besuchers
durchreichen. Sonst könnte sich jeder mit `Remote-User: admin` selbst anmelden. Forward-Auth-Einrichtungen von
Authelia und Authentik tun das für die Pfade, die sie schützen. Lass deshalb keinen Pfad außer `/mcp` (unten) an der
Anmeldung vorbei.
:::

### Skripte und KI-Assistenten

Mit `proxy` laufen alle Anfragen der Oberfläche über die Anmeldung des Proxys, auch die an `/api/`. Skripte, Home
Assistant und KI-Assistenten melden sich dagegen mit einem API-Schlüssel an und können die Anmeldeseite des Proxys
nicht bedienen. Dafür gibt es zwei Wege:

- **Direkt im eigenen Netz.** Sie rufen mtdl-web unter seiner Adresse im LAN oder im Docker-Netz auf, etwa
  `http://mtdl-web:8080`, nicht über den Proxy. Dort zählt nur der Schlüssel.
- **`/mcp` am Proxy freigeben.** Der MCP-Server nimmt ausschließlich API-Schlüssel an; Cookies und die
  Benutzer-Kopfzeile zählen dort nicht. `/mcp` darf deshalb ohne Proxy-Anmeldung durch. `/api/` dagegen nicht, denn
  dort braucht die Oberfläche die Anmeldung des Proxys.

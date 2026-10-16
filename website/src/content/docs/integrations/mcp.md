---
title: KI-Assistenten (MCP)
description: mtdl-web als MCP-Server für Claude und andere Assistenten, mit API-Schlüssel und getrennten Rechten für Lesen und Ändern.
---

Auf Wunsch bietet mtdl-web einen MCP-Server an (Model Context Protocol). Ein Assistent wie Claude sucht dann in der
Filmliste, prüft und legt Abos an und reiht Downloads ein, etwa auf „Abonniere die nummerierten Folgen vom
Gipfeltreffen“.

## Einschalten

Unter **Einstellungen**, **Zugang**, **MCP-Server** einschalten und einen API-Schlüssel erzeugen. Der Server
antwortet unter `/mcp` (Streamable HTTP, zustandslos, Antworten als JSON) und nur mit API-Schlüssel, in der
Kopfzeile `X-Api-Key` oder als `Authorization: Bearer`. Das gilt in jedem Anmeldemodus; eine Anmeldung im Browser
zählt dort nicht.

Ein Schlüssel, der nur lesen darf, bekommt nur die lesenden Werkzeuge, einer, der ändern darf, alle. Die Liste steht
unter [MCP-Werkzeuge](../mcp-tools/).

:::note[Ein Assistent handelt ohne Rückfrage]
Er tut alles, was sein Schlüssel darf. Gib ihm im Zweifel einen Schlüssel, der nur lesen darf, und lege Abos selbst
an. Löschen kann keines der Werkzeuge: weder Dateien noch Abos noch Bibliotheken.

Titel und Beschreibungen schreiben die Sender, und darin könnte auch eine Anweisung an Assistenten stehen. mtdl-web
sagt dem Assistenten deshalb, dass es Daten sind, und kennzeichnet Werkzeuge, die etwas überschreiben oder abbrechen
(Einstellungen, Abos ändern, Downloads abbrechen), so dass Clients wie Claude vorher nachfragen können.
:::

## Claude Code

```sh
claude mcp add --transport http mtdl-web http://mtdl-web:8080/mcp --header "X-Api-Key: mtdl_…"
```

Andere Clients mit HTTP-Transport bekommen dieselbe Adresse und dieselbe Kopfzeile.

## Claude Desktop

Claude Desktop startet MCP-Server als lokale Programme. Eine Brücke wie
[mcp-remote](https://github.com/geelen/mcp-remote) verbindet es mit mtdl-web; für eine Adresse mit `http://` statt
`https://` braucht sie `--allow-http`. Die Brücke läuft mit Node.js. Unter Windows prüfst und installierst du es in
der PowerShell:

```powershell
# Ist Node.js installiert? Zeigt die Version, sonst eine Fehlermeldung.
node --version

# Falls nicht: Node.js (LTS) installieren, danach Claude Desktop ganz beenden und neu starten.
winget install OpenJS.NodeJS.LTS
```

Auf dem Mac geht es mit `brew install node` oder mit dem Installationsprogramm von
[nodejs.org](https://nodejs.org). Der Eintrag für mtdl-web gehört in `claude_desktop_config.json` (Einstellungen,
Entwickler, Konfiguration bearbeiten):

```json title="claude_desktop_config.json"
{
  "mcpServers": {
    "mtdl-web": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://mtdl-web:8080/mcp", "--allow-http", "--header", "X-Api-Key:${MTDL_KEY}"],
      "env": { "MTDL_KEY": "mtdl_…" }
    }
  }
}
```

Die Datei enthält meist schon andere Einstellungen: den Eintrag `mtdl-web` in einen vorhandenen Block `mcpServers`
setzen, sonst den Block mit einem Komma zum vorigen Eintrag ergänzen, nie die ganze Datei ersetzen. Danach Claude
Desktop ganz beenden und neu starten.

| Meldung | Ursache und Lösung |
|---|---|
| `spawn npx ENOENT` | Node.js fehlt; unter Windows etwa `winget install OpenJS.NodeJS.LTS`, danach Claude neu starten |
| `npx` trotz Installation nicht gefunden | unter Windows `"command": "cmd"` und `"/c", "npx"` vor die übrigen Argumente |
| Server startet, aber `401` | Schlüssel falsch oder gelöscht; einen neuen erzeugen |
| `404` unter `/mcp` | MCP-Server in den Einstellungen nicht eingeschaltet |

## Warum kein Connector?

Der Dialog „Benutzerdefinierten Connector hinzufügen“ in Claude (Web, Desktop, App) passt nicht: Solche Connectoren
verbindet Anthropics Cloud, nicht dein Rechner. Sie erreichen nur Server, die öffentlich per `https://` im Internet
stehen, also keine Adresse im eigenen Netz.

## Wie die Werkzeuge arbeiten

Die Werkzeuge rufen intern die [REST-API](../rest-api/) mit dem Schlüssel des Assistenten auf: Sie dürfen genau, was
ein Skript mit diesem Schlüssel darf, mit denselben Prüfungen und Fehlercodes. Antworten sind für Assistenten
gekürzt (keine Stream-Adressen, Beschreibungen in Listen gekürzt).

- `cancel_downloads` bricht nur ab; `requeue_subscription` ersetzt abgebrochene und fehlgeschlagene Einträge eines
  Abos im Verlauf durch neue Downloads.
- Ein Abo zu pausieren hält seine schon eingereihten Downloads nicht an, dafür gibt es `cancel_downloads`.
- Hinter einem Reverse Proxy mit eigener Anmeldung darf `/mcp` an der Anmeldung vorbei, siehe
  [Reverse Proxy](../../install/reverse-proxy/#skripte-und-ki-assistenten).

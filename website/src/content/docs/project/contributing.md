---
title: Mitwirken
description: Fehler melden, Wünsche einbringen und am Code oder an dieser Dokumentation mitarbeiten.
---

Fehlerberichte, Wünsche und Beiträge sind willkommen, auf Deutsch oder Englisch.

## Fehler melden

Ein [Issue auf GitHub](https://github.com/sobrino89/mtdl-web/issues) mit:

- dem Text von **Einstellungen**, **System**, **Diagnose kopieren** (Version, Ordner, Zustand, ohne Zugangsdaten),
- bei einem Download den **technischen Details** aus der Fehlermeldung,
- bei einem Abo der Suche und den Regeln, am besten mit einem Titel, der falsch behandelt wird.

Sicherheitslücken bitte vertraulich melden, siehe [Sicherheit](../../background/security/#sicherheitslücken-melden).

## Entwickeln

Lokal ist nur Docker nötig; alle Prüfungen laufen im Container:

```sh
docker build --target test -t mtdl-web:test .
docker run --rm mtdl-web:test
```

Der Bau prüft die Oberfläche (TypeScript, Tests, Bundle mit esbuild), der Test-Container das Backend mit `ruff`,
`mypy --strict` und `pytest`. Der langsame Test gegen die echte Filmliste erwartet `data/filmliste/Filmliste-akt.xz`:

```sh
docker run --rm -v "$PWD/data:/app/data:ro" mtdl-web:test pytest -m full
```

| Ordner | Inhalt |
|---|---|
| `src/mtdl_web/` | Backend: Python, FastAPI, SQLite mit FTS5 |
| `ui/` | Oberfläche: Preact, TypeScript, esbuild |
| `test/` | Tests, mit echten Daten als Fixtures |
| `tools/` | Werkzeuge, etwa die Auswertung der Folgenmarken |
| `website/` | diese Dokumentation (Starlight) |
| `docs/` | Analysen für Entwickler: Filmlisten-Format, Folgenerkennung |

## Leitlinien

- **Nichts fremdes im Repository.** Abhängigkeiten kommen beim Bau aus ihren offiziellen Quellen, mit festen
  Versionen (Lockfiles, Basis-Images per Digest). Zur Laufzeit lädt mtdl-web nur Daten, nie Code.
- **Echte Daten in Tests.** Fixtures stammen aus der Filmliste und von TMDB, nicht ausgedacht.
- **Abgleich und Heuristiken** (Folgenerkennung, Regeln) halten den Referenzfall „Das Gipfeltreffen“ und werden vor
  einer Änderung über echte Daten alt gegen neu verglichen.
- **Keine fest verdrahteten Texte** in der Oberfläche: Jeder Text steht im Sprachkatalog, Deutsch und Englisch.
- **Keine externen Ressourcen** in der Oberfläche; der Standort steht im URL-Hash (`#/<ansicht>[/<auswahl>]`).
- Commits mit Präfix nach Bereich: `Backend:`, `UI:`, `Docs:`, `Build:`.

## An der Dokumentation mitarbeiten

Jede Seite hat unten einen Link **Seite bearbeiten**. Lokal startet die Seite mit Node.js 22 oder neuer:

```sh
cd website
npm ci
npm run dev
```

Die API-Referenz und die Liste der MCP-Werkzeuge kommen aus dem Code: `tools/docs_data.py` schreibt sie nach
`website/src/data/`, ein Test schlägt an, wenn sie veraltet sind:

```sh
docker run --rm -v "$PWD:/w" -w /w -e PYTHONPATH=/w/src mtdl-web:test python tools/docs_data.py
```

Übersetzungen ins Englische sind besonders willkommen: Eine Seite unter `website/src/content/docs/en/` mit demselben
Pfad ersetzt dort den deutschen Text.

## Lizenz von Beiträgen

Beiträge stehen wie das Projekt unter der GPL-3.0-or-later, siehe [Lizenz und Dank](../license/).

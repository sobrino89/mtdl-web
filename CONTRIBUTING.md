# Mitwirken

*English below.*

Fehlerberichte, Wünsche und Beiträge sind willkommen, auf Deutsch oder Englisch. Die ausführliche Anleitung steht
auf der Projektseite unter [Mitwirken](https://sobrino89.github.io/mtdl-web/project/contributing/).

- **Fehler und Wünsche:** ein [Issue](https://github.com/sobrino89/mtdl-web/issues), bei Fehlern mit dem Text von
  **Einstellungen**, **System**, **Diagnose kopieren**.
- **Sicherheitslücken:** vertraulich, siehe [SECURITY.md](SECURITY.md).
- **Code:** Alle Prüfungen laufen im Container, lokal ist nur Docker nötig:

  ```sh
  docker build --target test -t mtdl-web:test .
  docker run --rm mtdl-web:test
  ```

- **Leitlinien:** nichts Fremdes ins Repository (Abhängigkeiten fest versioniert, aus offiziellen Quellen), echte
  Daten in Tests, keine fest verdrahteten Texte in der Oberfläche (Sprachkatalog Deutsch und Englisch), keine
  externen Ressourcen zur Laufzeit. Änderungen an Abgleich und Folgenerkennung halten den Referenzfall „Das
  Gipfeltreffen“.
- **Commits** mit Präfix nach Bereich: `Backend:`, `UI:`, `Docs:`, `Build:`.

Beiträge stehen wie das Projekt unter der GPL-3.0-or-later.

---

Bug reports, ideas and contributions are welcome, in German or English. Open an
[issue](https://github.com/sobrino89/mtdl-web/issues); report security problems privately (see
[SECURITY.md](SECURITY.md)). All checks run in Docker: `docker build --target test -t mtdl-web:test .` and
`docker run --rm mtdl-web:test`. Contributions are licensed under GPL-3.0-or-later, like the project.

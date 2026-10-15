# Sicherheit

*English below.*

## Sicherheitslücken melden

Bitte nicht als öffentliches Issue, sondern vertraulich über
[GitHub Security Advisories](https://github.com/sobrino89/mtdl-web/security/advisories/new) („Report a
vulnerability“). Hilfreich sind: betroffene Version, Anmeldemodus (`MTDL_AUTH`), Schritte zum Nachstellen und was ein
Angreifer damit erreicht.

Du bekommst innerhalb einer Woche eine Antwort. Behobene Lücken werden mit der neuen Version veröffentlicht und im
Advisory beschrieben; wer meldet, wird auf Wunsch genannt.

## Unterstützte Versionen

Fehlerbehebungen gibt es für die neueste veröffentlichte Version. mtdl-web ist vor 1.0: Ein Update auf die neueste
Version ist der empfohlene Weg.

## Was zählt

Besonders interessant sind Umgehungen der Anmeldung oder des Schutzes vor fremden Webseiten, API-Schlüssel, die mehr
dürfen als ihr Recht, Zugriff auf Dateien außerhalb der eingebundenen Ordner und Wege, Code im Container
auszuführen. Wie mtdl-web sich absichert, steht unter
[Sicherheit](https://sobrino89.github.io/mtdl-web/background/security/).

Kein Fehler im engeren Sinn: Was in den Modi `local` oder `none` ohne Passwort erreichbar ist, solange es den
dokumentierten Regeln folgt.

---

## Reporting a vulnerability

Please do not open a public issue. Report it privately through
[GitHub Security Advisories](https://github.com/sobrino89/mtdl-web/security/advisories/new) with the affected
version, the access mode (`MTDL_AUTH`), steps to reproduce and the impact. You will get an answer within a week.
Fixes are published with the next version; only the latest release is supported.

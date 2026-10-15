---
title: Häufige Fragen
description: Antworten auf typische Fragen zu Betrieb, Abos, Downloads und Datenquellen.
---

## Was darf ich mit den Downloads machen?

mtdl-web lädt nur, was die Sender frei zugänglich in ihren Mediatheken anbieten, so wie der Browser beim Abspielen.
Die Sendungen bleiben urheberrechtlich geschützt: Kopien sind für den privaten Gebrauch gedacht, nicht zum
Weitergeben oder Veröffentlichen. Im Zweifel gilt das Recht deines Landes.

## Warum findet die Suche eine Sendung nicht?

Die Filmliste enthält nur, was die Sender gerade anbieten. Sendungen verschwinden nach Ablauf ihrer
Verweildauer (depubliziert), manche erscheinen erst nach der Ausstrahlung. Prüfe die Schreibweise und Filter; mit
**auch künftige Sendungen** erscheinen Einträge, die erst später abrufbar sind.

## Ein Download sagt „Geo-gesperrt“

Der Sender liefert die Sendung nur an Anschlüsse in bestimmten Ländern, meist Deutschland, Österreich oder die
Schweiz. Läuft der Server über einen Anschluss in einem anderen Land, etwa über ein VPN, scheitert der Abruf. Abhilfe
schafft eine Verbindung aus dem passenden Land.

## Warum lädt ein Abo eine Folge nicht?

Die Vorschau des Abos sagt es: Jeder ausgeschlossene Treffer nennt die Regel und den Grund. Häufig sind es ein
abweichendes Thema, eine fehlende Folgennummer oder eine Sonderfassung. Bei Abos in eine Serienbibliothek steht ein
Eintrag ohne passende TMDB-Folge unter **Ohne TMDB-Folge**. Siehe [Abos](../../usage/subscriptions/) und
[Folgenerkennung](../../background/episodes/).

## Eine Folge wurde abgebrochen und soll doch geladen werden

Im Verlauf **Erneut laden**, oder im Abo **Erneut laden** für alle abgebrochenen und fehlgeschlagenen Folgen auf
einmal. **Entfernen** im Verlauf gibt eine Folge für die nächste Prüfung frei.

## Downloads stehen still, „Zielordner nicht beschreibbar“

Rechte oder Mount passen nicht. Die Oberfläche zeigt den Ordner, den Grund und einen Befehl; die Downloads warten und
laufen weiter, sobald der Ordner wieder geht. Siehe [Ordner und Rechte](../../install/folders/).

## Brauche ich MediathekView noch?

Nein. mtdl-web nutzt dieselbe Filmliste, lädt selbst und übernimmt auf Wunsch Historie und Abos, siehe
[Umzug](../../usage/takeover/).

## Wie viel Platz braucht mtdl-web?

Rund 1 GB für Filmliste und Suchindex im Cache-Ordner, dazu die Downloads. Ohne Beschreibungen im Index
(`MTDL_INDEX_DESCRIPTION=false`) sind es gut 100 MB weniger. Mit MediathekViewWeb als Datenquelle entfällt der Cache.

## Läuft mtdl-web auf einem Raspberry Pi?

Das Image gibt es auch für `linux/arm64`, etwa für einen Raspberry Pi 4 oder 5 mit 64-Bit-System. Erprobt ist
mtdl-web bisher vor allem auf x86-Servern; das erste Einlesen der Filmliste dauert auf kleiner Hardware länger.

## Gibt es eine englische Oberfläche?

Ja, unter **Einstellungen**, **System**, **Oberfläche**; ab Werk folgt sie der Sprache des Browsers. Diese
Dokumentation gibt es zunächst auf Deutsch.

## Wo melde ich Fehler oder Wünsche?

In den [Issues auf GitHub](https://github.com/sobrino89/mtdl-web/issues). Hilfreich ist der Text von
**Einstellungen**, **System**, **Diagnose kopieren**: Er nennt Version, Ordner und Zustand ohne Zugangsdaten.

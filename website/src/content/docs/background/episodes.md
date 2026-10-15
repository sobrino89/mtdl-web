---
title: Folgenerkennung
description: Wie mtdl-web aus einem Mediathek-Eintrag eine Folge macht, für Dateinamen und für den Abgleich mit TMDB.
---

Die Sender schreiben Staffel und Folge in den Titel, aber nicht einheitlich. Diese Seite erklärt, was mtdl-web
erkennt, wie es Einträge den Folgen bei TMDB zuordnet und wo die Grenzen liegen. Die ausführliche Auswertung aller
Titel der Filmliste steht im Repository unter
[`docs/folgenerkennung.md`](https://github.com/sobrino89/mtdl-web/blob/main/docs/folgenerkennung.md).

## Folgenmarken im Titel

mtdl-web liest vier Formen, und nur am Ende des Titels:

| Form | Bedeutung | Beispiel |
|---|---|---|
| `(S02/E03)` | Staffel 2, Folge 3 | „Folge 3: Was für ein Theater (S02/E03)“ |
| `(S2026/E08)` | Staffel als Jahr: die Staffel, die in diesem Jahr begann | Die Anstalt 2026 ist bei TMDB Staffel 13 |
| `(Staffel 2, Folge 3)` | wie `(S02/E03)`, Schreibweise des SRF | |
| `(58)` | fortlaufend über alle Staffeln gezählt | Das Gipfeltreffen 58 ist Staffel 8, Folge 8 |

Ein Titelanfang wie „Folge 3:“, „Teil 3.“ oder „Episode 3:“ wird vor dem Vergleich mit TMDB entfernt; seine Nummer
nutzt mtdl-web nicht.

**Im Download-Ordner** wird aus `(S02/E03)` der Name `S02E03` und aus `(39)` Folge 39 in der Staffel, die das Abo
angibt (sonst 1). Ohne Marke heißt die Datei nach dem Sendetag. Die Regelvorlage **Hat Folgennummer** legt eine
sichtbare Regel an: Titel passt auf `\((S\d+/E)?\d+\)$`.

## Abgleich mit TMDB

Ein Abo in eine Serienbibliothek gehört zu einer Serie bei TMDB. Für jeden Eintrag, den die Regeln durchlassen,
sucht mtdl-web die passende Folge über drei Signale, das stärkste zuerst:

1. **Nummer** aus der Folgenmarke.
2. **Titel:** der Name der TMDB-Folge am Anfang des Eintragstitels, ohne Groß- und Kleinschreibung und Satzzeichen,
   gleich oder als längster Anfang mit mindestens 8 Zeichen. Platzhalter von TMDB wie „Folge 1“ zählen nicht.
3. **Sendetag** gleich dem Ausstrahlungstag bei TMDB. Allein entscheidet er nur, wenn auch die Länge passt: Der
   Eintrag darf um ein Viertel der Laufzeit bei TMDB davon abweichen, mindestens aber um 5 Minuten. Eine Folge von
   45 Minuten darf also rund 34 bis 56 Minuten lang sein, eine von 15 Minuten 10 bis 20. Der Grund für die Vorsicht:
   Sender stellen Folgen teils Wochen vor dem TMDB-Datum online, und Ausschnitte tragen den Tag ihrer Folge.

Die Vorschau des Abos nennt für jeden Eintrag den Grund:

| Grund in der Vorschau | Lage |
|---|---|
| Nummer im Titel, Nummer und Titel, Nummer und Sendetag | die Nummer passt, der Titel passt oder fehlt |
| Titel, Titel und Sendetag | nur der Titel passt, zu genau einer Folge |
| Sendetag und Länge | nur Tag und Länge passen, zu genau einer Folge |
| Nummer und Titel nennen verschiedene Folgen | Widerspruch: keine Zuordnung |
| Mehrere TMDB-Folgen kommen in Frage | der Titel passt auf mehrere, und weder Tag noch Nummer entscheiden |
| Viel kürzer als die Folge, wohl ein Ausschnitt | kürzer als die halbe Laufzeit bei TMDB |
| Keine passende TMDB-Folge | nichts davon |

Einträge ohne Zuordnung lädt mtdl-web nicht und gleicht sie bei jeder Prüfung neu ab. Geladene Folgen sind über
ihren Download festgelegt: Sie werden nie erneut geladen, ihr Name bleibt.

**Doppelte Einträge.** Zwei Einträge für eine Folge, etwa eine Wiederholung oder ein Clip unter gleichem Namen: Der
längere lädt, der andere zählt als Duplikat. Dieselbe Videodatei auf mehreren Sendern am selben Tag ist eine Folge
und lädt einmal.

**TMDB-Daten** hält mtdl-web je Serie sechs Stunden vor. Eine Auffrischung ist eine Anfrage, eine neu zugeordnete
Serie zwei. Ist TMDB nicht erreichbar, gilt die ältere Antwort. Eine neue Folge, die TMDB noch nicht führt, lädt die
erste Prüfung nach der nächsten Auffrischung.

## Grenzen

Manche Formen erkennt mtdl-web bisher nicht als Folge:

| Form | Beispiel | Ausweg |
|---|---|---|
| Marke vor einer Fassungsangabe | „… (S2026/E09) (Audiodeskription)“ | Sonderfassungen lassen Abos ohnehin weg |
| Mehrteiler | „Abenteuer Linienbus (1/4)“ | Benennung nach Datum oder eigene Vorlage |
| Nummer am Titelanfang | „Folge 1311: …“, „27. Der Taschengeldtrick“, „Episode 656“ | Abgleich über Titel und Sendetag |
| seltene Schreibweisen | „(Folge 5)“, „(F. 328)“, „(S2024E81)“ | Abgleich über Titel und Sendetag |

Auch Zahlen, die keine Folge meinen, stehen in Klammern: Datumsangaben, Zeiträume, „(Clip 31)“. Eine Jahreszahl wie
„(2024)“ liest mtdl-web als fortlaufende Nummer. Im Download-Ordner wird daraus `S01E2024`; hier hilft eine Regel,
die solche Titel ausschließt, oder die Benennung nach Datum. Eine einfache Regel „19xx und 20xx sind Jahre“ ginge
nicht: Manche Serien zählen ihre Folgen tatsächlich bis in diesen Bereich.

## Wenn das Thema wechselt

Dieselbe Sendung steht nicht immer unter demselben Thema. Beim ZDF Magazin Royale etwa stehen fast alle Folgen unter
„ZDF Magazin Royale - Late-Night mit Jan Böhmermann“, einzelne aber nur unter „ZDF Magazin Royale“, dann auch ohne
Folgenmarke. Für Abos heißt das: „Thema enthält“ statt „Thema ist“. Eine Folge ohne Marke findet ihre TMDB-Folge
über Titel und Sendetag.

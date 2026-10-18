---
title: Abos
description: Serien abonnieren mit einfachen, sichtbaren Regeln, einer Vorschau vor dem Speichern und klaren Gründen für jeden ausgeschlossenen Treffer.
---

Ein Abo lädt neue Folgen einer Sendung von selbst. Es besteht aus einer Suche, die Kandidaten findet, und Regeln,
die sie eingrenzen. mtdl-web prüft jedes aktive Abo nach jeder Aktualisierung der Filmliste, also stündlich, und
reiht neue Folgen in die Warteschlange ein.

## Ein Abo anlegen

Am schnellsten aus der Suche: Sendung suchen, **Abo daraus machen** wählen. mtdl-web schlägt Regeln vor, meist das
Thema und, wenn die Folgen nummeriert sind, die Folgennummer im Titel. Der Hinweis oben sagt, wie viele der Treffer
der Vorschlag lädt. Alles daran lässt sich ändern.

Der Editor hat drei Abschnitte: **Welche Folgen** (Suche und Regeln), **Wohin und wie benannt** (Ziel) und
**Optionen**. Rechts zeigt die Vorschau, was das Abo jetzt laden würde.

## Regeln

Alle Bedingungen müssen zutreffen, es gibt keine verschachtelten Gruppen. Jede Bedingung zeigt, wie viele der bis
dahin verbliebenen Treffer sie ausschließt, und lässt sich vorübergehend ausschalten.

| Feld | Vergleiche | Wert |
|---|---|---|
| Thema, Titel, Beschreibung | ist, ist nicht, enthält, enthält nicht | Text |
| Sender | ist eines von, ist keines von | eine Auswahl |
| Datum | ab, vor | ein Tag |
| Dauer | mindestens, höchstens | Minuten |

**Reguläre Ausdrücke.** Textfelder lassen sich als regulärer Ausdruck auswerten. Dann gibt es nur „ist“ (passt) und
„ist nicht“ (passt nicht); ein Teiltreffer genügt, `^` und `$` verankern am Anfang und Ende, Groß- und
Kleinschreibung zählt nicht. Ein ungültiger Ausdruck wird markiert, mit der Stelle des Fehlers.

**Vorlagen** fügen fertige Bedingungen ein, die danach ganz normale, sichtbare Regeln sind:

| Vorlage | Bedingung |
|---|---|
| Hat Folgennummer | Titel ist regex `\((S\d+/E)?\d+\)$` |
| Staffel und Folge im Titel | Titel passt auf eine Marke wie `(S02/E03)` |
| Nur neue Folgen ab heute | Datum ab heute |
| Ohne Clips unter 10 min | Dauer mindestens 10 Minuten |

### Beispiel

Die Suche „Gipfeltreffen“ findet über hundert Einträge: die nummerierten Folgen von „Das Gipfeltreffen“, aber
auch Best-of-Ausgaben, Beiträge anderer Sendungen und Fassungen mit Gebärdensprache. Zwei Regeln lassen genau die
nummerierten Folgen übrig (im Oktober 2026 waren es 20):

1. Thema ist „Das Gipfeltreffen“
2. Titel ist regex `\(\d+\)$`, also eine Zahl in Klammern am Ende

## Sonderfassungen

Fassungen mit Audiodeskription, Gebärdensprache, Hörfassung oder klarer Sprache lassen Abos ab Werk weg, ebenso die
Fassungen mit Untertiteln, die ARTE neben der normalen anbietet („(mit Untertitel)“). In der Vorschau steht dafür ein
fester letzter Schritt nach den Regeln, mit Grund für jeden ausgeschlossenen Treffer.
Je Abo lässt sich unter **Optionen** wählen: wie in den Einstellungen, nicht laden oder mitladen. Ein Abo für genau
eine solche Fassung braucht „mitladen“. Die Voreinstellung steht unter **Einstellungen**, **Qualität**.

## Vorschau

Die Vorschau aktualisiert sich bei jeder Änderung und teilt die Treffer in drei Gruppen:

- **Wird geladen**: erfüllt alle Bedingungen und ist noch nicht da, mit Zielpfad.
- **Schon vorhanden**: schon geladen oder in der Bibliothek.
- **Ausgeschlossen**: mit der Regel, die den Treffer ausschließt, und dem Grund („anderes Thema“, „zu kurz“).

**Nur von Regel n ausgeschlossen** zeigt, was eine einzelne Regel kostet. Dieselbe Sendung auf mehreren Sendern
oder als Wiederholung erscheint nur einmal; geladen wird die längere Fassung.

**Beispiele markieren.** Mit den Daumen markierst du Treffer als „gehört dazu“ oder „gehört nicht dazu“. mtdl-web
sucht dann ein Merkmal, das nur die abgewählten Treffer haben, und schlägt eine Regel vor, etwa „Titel enthält nicht
Hörfassung“ oder „Dauer mindestens 20 min“. Schließt eine Regel einen Treffer aus, der dazugehört, zeigt es diese
Regel.

## Ziel und Benennung

| Ziel | Wirkung |
|---|---|
| Download-Ordner | Ordner und Dateinamen nach einem Schema: Folgennummer → `S01E39` (die Staffel legst du fest), Staffel und Folge aus dem Titel, Datum oder eigene Vorlage. Siehe [Dateinamen](../naming/). |
| Serienbibliothek | Ordner, Staffeln, Nummern und Folgentitel wie bei TMDB; Plex erkennt die Serie an der ID im Ordnernamen. Siehe [Bibliotheken](../libraries/). |

## Optionen

| Option | Wirkung |
|---|---|
| Qualität | HD bevorzugt, Hoch oder Klein; Standard wie in den Einstellungen |
| Untertitel | als `.de.srt` neben die Datei, wenn vorhanden |
| Auch in Beschreibungen suchen | die Suche des Abos findet auch Wörter aus der Beschreibung |
| Gültig bis | letzter Tag, an dem das Abo sucht, etwa bis eine Staffel endet; danach pausiert es oder löscht sich (Dateien und Verlauf bleiben) |
| Sonderfassungen | siehe oben |
| Abo aktiv | pausiert lädt es nichts, bis es fortgesetzt wird |

## Speichern

Unten steht, was das Speichern auslöst: wie viele Folgen jetzt geladen werden, wie groß sie ungefähr sind und
wohin sie gehen. Ein Abo lädt beim Speichern alles, was es findet und noch nicht da ist. Wer nur künftige Folgen
will, nimmt die Vorlage **Nur neue Folgen ab heute**.

## Im Betrieb

Die Liste der Abos zeigt Zustand, letzte Folge, Zahl der geladenen Folgen und die nächste Prüfung. Je Abo gibt es
**Pausieren**, **Fortsetzen** und **Jetzt prüfen**, für alle **Alle jetzt prüfen**.

Im Editor eines gespeicherten Abos:

- **Erneut laden** stellt abgebrochene und fehlgeschlagene Folgen des Abos wieder ein.
- **Alle abbrechen** bricht die wartenden und laufenden Downloads des Abos ab. Pausieren allein hält schon
  eingereihte Downloads nicht an.
- **Löschen** entfernt das Abo; geladene Dateien und der Verlauf bleiben.

Woran mtdl-web erkennt, dass es eine Folge schon hat: nie an der Adresse des Videos allein, denn die Sender wechseln
ihre Server auch innerhalb einer Serie. Es vergleicht Thema, Titel und Sendetag, dazu die Länge; dieselbe Videodatei
am selben Tag gilt auch unter anderem Sender oder Titel als dieselbe Sendung. Details unter
[Folgenerkennung](../../background/episodes/).

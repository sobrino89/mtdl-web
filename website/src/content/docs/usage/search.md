---
title: Suche
description: Sendungen finden, mit Filtern und der Suchsyntax von MediathekViewWeb, und aus Treffern Downloads oder Abos machen.
---

Die Suche durchsucht die Filmliste, also alles, was die Sender gerade in ihren Mediatheken anbieten. Treffer kommen
in Millisekunden, sortiert mit den neuesten zuerst.

## Wörter und Syntax

Freie Wörter suchen in Thema und Titel. Groß- und Kleinschreibung zählt nicht, jedes Wort zählt als Wortanfang
(`gipfel` findet „Gipfeltreffen“). Akzente zählen nicht (`cafe` findet „Café“), ä, ö und ü sind eigene Buchstaben,
ß lässt sich auch als `ss` oder `s` tippen (`strasse` findet „Straße“). Ein Apostroph darf fehlen: `girls` findet
„A Good Girl's Guide to Murder“, `kudamm` findet „Ku'damm“.

Wer MediathekViewWeb kennt, kann dessen Syntax verwenden. Ein Klick auf **Suchsyntax** neben dem Suchfeld zeigt
Beispiele zum Einfügen.

| Angabe | Bedeutung | Beispiel |
|---|---|---|
| `!` | Sender | `!zdf` |
| `#` | Thema | `#tatort` |
| `+` | Titel | `+mord` |
| `*` | Beschreibung | `*berlin` |
| `>` | mindestens so viele Minuten | `>80` |
| `<` | höchstens so viele Minuten | `<10` |

Verschiedene Angaben gelten zusammen. Wörter hinter einer Angabe, mit Kommas verbunden, müssen alle vorkommen
(`#sturm,der,liebe`). Dieselbe Angabe zweimal heißt „eines von beiden“ (`!ard !zdf`). Mit einer `#`-Angabe suchen
freie Wörter nur noch im Titel. Abos verstehen dieselbe Syntax.

## Filter

| Filter | Wirkung |
|---|---|
| Suchen in | Titel und Thema, nur Titel, nur Thema oder überall, auch in der Beschreibung |
| Sender | einer oder mehrere |
| Thema | eines oder mehrere der Themen unter den Treffern |
| Titel | Titel enthält einen Text |
| Dauer | von bis, in Minuten |
| Zeitraum | letzte 7 Tage, 30 Tage, 12 Monate oder alles |
| nur HD, mit Untertiteln | nur Sendungen, die das anbieten |
| nur Deutsch | ohne Sendungen, die vermutlich nicht deutsch sind (siehe unten) |
| ohne Sonderfassungen | ohne Fassungen mit Audiodeskription, Gebärdensprache, als Hörfassung oder in klarer Sprache, so wie Abos sie erkennen |
| auch künftige Sendungen | Einträge, die erst ab einem späteren Tag abrufbar sind |

Die Suche in Beschreibungen fehlt, wenn `MTDL_INDEX_DESCRIPTION=false` gesetzt ist.

Eine Sprachangabe hat die Filmliste nicht. **nur Deutsch** blendet deshalb aus, was sich verrät: die fremdsprachigen
Ausgaben von ARTE (französisch, englisch, spanisch, polnisch, italienisch), Kennzeichen wie „(Originalversion)“,
„(OV)“, „(OmU)“, eine Sprache in Klammern wie „(Englisch)“ oder „(engl.)“, Titel in kyrillischer oder arabischer
Schrift und die sorbischen Magazine. Fassungen mit deutschem Ton und Untertiteln bleiben, ebenso Sendungen, die eine
Sprache nur im Titel nennen („Englisch lernen“). Wer etwas vermisst, schaltet den Filter kurz aus.

## Treffer

Jede Zeile zeigt Sender, Thema, Titel, Datum, Dauer, Größe und Merkmale wie HD, Untertitel und Geo-Sperre. Ein
Status sagt, ob eine Sendung schon geladen ist, in der Warteschlange steht oder zu einem Abo gehört. **Nach Thema
gruppieren** fasst Treffer zu Sendungen zusammen; aus einer Gruppe wird mit einem Klick ein Abo für dieses Thema.

Dieselbe Datei auf mehreren Sendern (etwa ZDF und 3sat) erscheint als eine Zeile, die übrigen Sender stehen im
Detail. Bei sehr vielen Treffern lädt die Liste die neuesten und bietet **Weitere Treffer anzeigen** an.

## Details

Ein Klick auf einen Treffer öffnet die Details: Beschreibung, Qualitäten (eine Größe nennt die Filmliste nur für
„Hoch“), Untertitel, Geo-Sperre und die Seite des Senders. Die **Vorschau** spielt die Sendung im Browser, direkt vom
Sender und ohne Bandbreitenlimit, in der bevorzugten Qualität aus **Einstellungen**, **Qualität**. Darunter lässt
sich eine andere wählen; die Wiedergabe läuft an derselben Stelle weiter. Manche HLS-Sendungen lassen sich im Browser
nicht abspielen, weil der Sender das Abspielen auf fremden Seiten nicht erlaubt; geladen werden können sie trotzdem.

## Herunterladen und abonnieren

- **Herunterladen** reiht eine Sendung in der bevorzugten Qualität ein; eine andere wählst du daneben oder in den
  Details.
  Mehrere Treffer wählst du über die Kästchen aus und lädst sie gemeinsam.
- **Abo daraus machen** öffnet ein neues Abo mit einem Vorschlag aus der Suche, siehe [Abos](../subscriptions/).
- **In die Filmbibliothek** lädt einen Film benannt nach TMDB in eine Filmbibliothek, siehe
  [Bibliotheken](../libraries/).

Ist eine Sendung schon geladen oder eingereiht, auch bei einem anderen Sender, fragt mtdl-web nach; **Trotzdem
laden** lädt sie ein zweites Mal.

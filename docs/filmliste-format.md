# Filmlisten-Format

Geprüft am 12.10.2026 an der echten Liste: `Filmliste-akt.xz` (77,3 MB xz, 476 MB JSON, 719.925 Einträge) und
`Filmliste-diff.xz` (216 KB, 1.720 Einträge), beide erzeugt am 12.10.2026 um 20:19 UTC. Zahlen unten beziehen sich
auf diesen Stand. Testdaten daraus: `test/fixtures/README.md`.

## Abruf

- `https://liste.mediathekview.de/Filmliste-akt.xz` (Vollliste) und `…/Filmliste-diff.xz` (Diff).
- Der Server (nginx) liefert `Last-Modified`, `ETag` und `Accept-Ranges: bytes`. Mit `304` antwortet er nur, wenn
  `If-None-Match` das ETag trifft und `If-Modified-Since` genau das Datum der Datei nennt; ein späteres Datum
  bringt `200` (Voreinstellung `if_modified_since exact`, geprüft am 17.10.2026). Die Wurzel `/` liefert `403`.
- Hinter dem Namen stehen zwei Server (`88.99.10.178`, `.179`, 13.10.2026). Dieselbe Datei hatte dort zeitweise
  verschiedene `Last-Modified` und ETags (21:55:02 gegen 21:55:19, gleiche Größe): Ein bedingter Request trifft nur
  beim selben Server. Folge nur Bandbreite, kein Doppelimport, weil mtdl-web die Listen-ID prüft.
- Der Server (Crawler MServer, `Crawler.java`) schreibt von etwa 5 bis 23 Uhr (deutsche Zeit) stündlich eine neue
  Liste, jeweils als Vollliste und als Diff derselben Erzeugung. Die Diff enthält alle Einträge, die nicht in der
  „Org-Liste“ stehen, laut Code „typ. erste Liste am Tag“; sie wächst also über den Tag und beginnt nach der
  Nachtpause neu. Gelöschte Sendungen enthält sie nie (Forum, Beiträge 4042 vom 05.10.2017 und 38221 vom
  11.01.2025; empirisch: alle 1.720 Diff-Einträge stehen feldgleich in der Vollliste derselben Erzeugung).
- Regel des Java-Clients (MediathekView, `FilmListMetaData.canUseDiffList()`, `ListeFilme.updateFromFilmList()`,
  `AutomaticFilmlistUpdate`):
  - Diff nur, wenn die lokale Liste **nach heute 07:00 UTC** erzeugt wurde, sonst Vollliste (früher 9 Uhr Ortszeit,
    „Uhrzeit ab der die Diffliste alle Änderungen abdeckt“, MLib `Const.TIME_MAX_AGE_FOR_DIFF`).
  - Einsortieren: lokale Einträge mit gleicher Identität (Sender, Thema, URL, Website) entfernen, Diff-Einträge
    hinzufügen, Metadaten der Diff übernehmen. Gelöschte Sendungen verschwinden erst mit der nächsten Vollliste.
  - Lädt beim Start ab 3 Stunden Alter, danach alle 12 Stunden; für diesen Takt ist die Regel gebaut.
- mtdl-web fragt stündlich und macht es wie die Kodi-Erweiterung des Projekts (`updater.py`): die Vollliste einmal
  am Tag, sonst die Diff, auch nachts (`filmlist/fetch.py`, `plan`). Fällig ist die Vollliste, wenn mtdl-web seit
  der letzten 07:00 UTC keine mehr hatte; was der Server dann schickt, gilt für den Tag, auch eine schon bekannte
  Liste. Ob es die Liste des Tages schon hat, merkt sich mtdl-web selbst (`fetched_at` neben der Datei, bei `200`
  und `304`), denn der Server kann es nicht sagen: `If-Modified-Since` nur exakt, und die zwei Server dahinter
  datieren und taggen dieselbe Datei verschieden. Eine geladene, aber nicht importierte Vollliste (Neustart während
  des Imports) wird von der Platte importiert, nicht neu geladen. Die 07:00-Regel des Java-Clients stündlich
  angewandt hätte zwischen Mitternacht und 07:00 UTC jede neue Vollliste geladen (in den Tagen bis 0.1.0.dev36:
  fünf bis sechs je Nacht).

## Aufbau

Ein JSON-Objekt mit **doppelten Schlüsseln**: zweimal `"Filmliste"`, danach je Eintrag ein `"X"`. Parser, die bei
doppelten Schlüsseln den letzten Wert behalten (Standard in fast allen Sprachen), sind unbrauchbar; die Liste wird
als Strom gelesen. UTF-8, keine Zeilenumbrüche zwischen den Einträgen.

- `Filmliste` (erstes Vorkommen), Metadaten: `[Erzeugt Ortszeit "TT.MM.JJJJ, HH:MM" (Europe/Berlin), Erzeugt UTC,
  Formatversion "3", Crawler "MSearch [Vers.: 3.1.294]", Listen-ID (32 Hex-Zeichen)]`. Diff und Vollliste derselben
  Erzeugung tragen dieselbe Zeit und dieselbe ID.
- `Filmliste` (zweites Vorkommen): die 20 Spaltennamen.
- `X`: ein Eintrag, 20 Strings, alle Einträge haben genau 20 Felder.

| # | Spalte | Befund |
|---|---|---|
| 0 | Sender | Leer = wie Vorgänger. Nur gesetzt, wenn er sich ändert (33 Blöcke, 32 Werte; `rbtv` und `RBTV` sind verschiedene Werte, die Blöcke sind ohne Groß/Klein sortiert). |
| 1 | Thema | Leer = wie Vorgänger. Bei Senderwechsel immer gesetzt, nie wiederholt. 9.423 Themen. |
| 2 | Titel | Nie leer. 60 mit Leerzeichen am Rand, 7 mit Zeilenumbruch, 387 mit geschütztem Leerzeichen, 24.570 mit „–". |
| 3 | Datum | `TT.MM.JJJJ`, Ortszeit Europe/Berlin; 253 leer (dann auch DatumL leer). |
| 4 | Zeit | `HH:MM:SS` (557.659), `HH:MM` (153.275) oder leer (8.991). |
| 5 | Dauer | `HH:MM:SS`; 136 leer. |
| 6 | Größe [MB] | Ganze MiB (MVW: `32` → 33.554.432 Byte); 128.213 leer. |
| 7 | Beschreibung | 63.766 mit Zeilenumbruch. |
| 8 | Url | Haupt-URL (mittlere Qualität), absolut. 592.299 mp4, 127.609 m3u8 (HLS), 16 mp3; 32.536 mit `http://`; 2 kaputt (`https://cdn-storage.br.denull`, eine ohne Host). |
| 9 | Website | Seite in der Mediathek. |
| 10 | Url Untertitel | Formate siehe unten. |
| 11 | Url RTMP | RTMP gibt es nicht mehr. Aber: 1.185 KiKA-Einträge tragen hier die Untertitel-URL (`…/subtitle`, `…/webvtt`), `Url Untertitel` ist dann leer. |
| 12 | Url Klein | Relativ zur Haupt-URL: `n\|suffix` = erste `n` Zeichen der Haupt-URL + `suffix`. `n` ist oft klein (`4\|s://nrodl…` macht aus `http://tvdl…` eine `https://nrodl…`-URL). In der echten Liste nie absolut; 27.345 leer. |
| 13 | Url RTMP Klein | Immer leer. |
| 14 | Url HD | Wie Url Klein; 90.110 leer. |
| 15 | Url RTMP HD | Immer leer. |
| 16 | DatumL | Unix-Sekunden, maßgeblich. Passt in allen 719.672 Fällen zu Datum und Zeit in Europe/Berlin. Spanne 1925 bis 2204: 35.213 vor 2000 (Archiv), 15 mehr als 60 Tage in der Zukunft. |
| 17 | Url History | Immer leer. |
| 18 | Geo | Leer (88,6 %), `DE`, `AT`, `DE-AT-CH`, `DE-AT-CH-EU`, `DE-AT-CH-FR`, `DE-FR`. |
| 19 | neu | Immer `false`, auch in der Diff. Der Java-Client markiert „neu" selbst (URL nicht in der vorigen Liste). |

### Untertitel

269.137 Einträge haben eine Untertitel-URL. Das Format ist an der Endung nicht sicher zu erkennen, der Downloader
muss es am Inhalt erkennen (TTML/EBU-TT oder WebVTT).

| Form | Quelle (Host) | Anzahl |
|---|---|---|
| ohne Endung (EBU-TT) | `api.ardmediathek.de/…/ebutt/urn:ard:subtitle:…` | 166.184 |
| ohne Endung | `www.ardmediathek.de` | 20.373 |
| ohne Endung (KiKA) | `www.kika.de/_next-api/…/subtitle` bzw. `…/webvtt`, teils im Feld Url RTMP | 15.112 + 1.185 |
| `.xml` (TTML) | ZDF `utstreaming.zdf.de` 25.490, SRF 12.474, weitere | 38.241 |
| `.vtt` | SRF | 15.637 |
| `.ttml` | ORF 11.695, BR 19 | 11.714 |
| `ut.php?file=…`, protokollrelativ `//…` | SR | 1.446 |
| `.html` | NDR | 414 |
| kaputt | `https://www.ardmediathek.dehttps:…` 12, ohne Schema mit Leerzeichen am Ende 1 | 13 |
| nur Pfad ohne Host | `/static/avportal/untertitel_mediathek_preview/….xml` (ARD 2014) | 10 |

### URL-Reparaturen beim Import

Gemessen mit `mtdl_web.filmlist.parser.clean_url` an der ganzen Liste:

| Fall | Behandlung | Anzahl |
|---|---|---|
| protokollrelativ `//host/…` | `https:` davor | 1.446 |
| doppeltes Schema `http://hosthttps://…` | ab dem zweiten Schema | 12 |
| ohne Schema `host.tld/pfad` | `https://` davor | 1 |
| nur Pfad, kein Host | verworfen (leer) | 10 Untertitel |
| Haupt-URL kaputt (`…br.denull`, ohne Host) | verworfen; Klein- und HD-URL werden trotzdem gegen den Rohwert aufgelöst und einzeln geprüft, eine davon bleibt gültig | 2 Haupt-, 1 Klein-, 2 HD-URLs |

Livestreams stehen mit in der Liste: Thema `Livestream` je Sender, ohne Datum, Dauer und Größe, Haupt-URL ist
ein HLS-Livestream. Der Downloader muss sie ausschließen.

## Identität und Dubletten

- (Sender, Thema, Titel, Url) ist in der Vollliste eindeutig.
- (Sender, Thema, Url, Website) ist die Identität des Java-Clients beim Einsortieren der Diff; in der Vollliste
  2 Dubletten (KiKA, „Löwenzahn mit Peter Lustig").
- 348 Haupt-URLs kommen mehrfach vor (733 Einträge). Dieselbe Sendung bei mehreren Sendern hat verschiedene URLs
  (ARD/NDR bei „extra 3"), und innerhalb einer Serie wechselt der Host. Für „schon geladen?" taugt die URL
  deshalb nicht.

## Abgleich mit MediathekViewWeb

Das Fixture `mvw-query-gipfeltreffen-2026-10-12.json` stammt aus dem MVW-Index derselben Liste
(`filmlisteTimestamp` = 12.10.2026 20:19 UTC). Alle 35 Treffer stehen per Haupt-URL in der Filmliste. Feldgenau
gleich: Sender, Thema, Titel, Beschreibung, Website, Untertitel, aufgelöste Klein- und HD-URL,
`timestamp` = DatumL, `duration` = Dauer in Sekunden, `size` = Größe × 2²⁰.

Suchsemantik: Die MVW-Suche „das gipfeltreffen" (Titel und Thema) liefert genau die Einträge, bei denen jedes
Suchwort als Wortanfang (oder Teilstring) in Thema oder Titel vorkommt: 35 von 121 Einträgen mit „gipfeltreffen".
Als ganze Wörter wären es 34 (MVW findet „das" auch in „dass").

## Normalisierung (Daten zum offenen Punkt)

- Themen: Groß/Klein-Unterschiede fassen 82 Paare zusammen (`Extra 3`/`extra 3`, `Scobel`/`scobel`). Striche,
  Leerraum und NFKC kommen nur 3 weitere hinzu (`...` gegen `…`).
- Titel: „–" gegen „-" trennt dieselbe Sendung über Sender hinweg (MDR „Das Gipfeltreffen – Best of 2023 (1/2)",
  3sat „Das Gipfeltreffen - Best of 2023 (1/2)"). Das betrifft die Dubletten-Erkennung, nicht die Abo-Regeln.

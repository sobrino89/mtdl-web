# Folgenerkennung

Wie mtdl-web aus einem Mediathek-Eintrag eine Folge macht: für die Dateinamen im Downloadordner, für Abos in eine
Serienbibliothek (Abgleich mit TMDB) und für die Regelvorlage „Hat Folgennummer“. Stand 0.1.0.dev22; Zahlen aus der
Filmliste vom 13.10.2026. Code: `src/mtdl_web/library.py` (Bibliothek), `src/mtdl_web/naming.py` (Downloadordner),
`src/mtdl_web/rules/templates.py` (Vorlage); Tests mit echten Daten in `test/test_library.py`.

## 1. Folgenmarken im Titel

Die Sender schreiben Staffel und Folge in den Titel, meist in Klammern am Ende. mtdl-web liest heute vier Formen,
und nur am Titelende:

| Form | Bedeutung | Beispiel |
|---|---|---|
| `(S02/E03)` | Staffel 2, Folge 3 | Werwölfe: „Folge 3: Was für ein Theater (S02/E03)“ |
| `(S2026/E08)` | Staffel als Jahr: die Staffel, die in diesem Jahr begann (oder die einzige, die darin lief) | Die Anstalt 2026 = TMDB-Staffel 13 |
| `(Staffel 2, Folge 3)` | wie `(S02/E03)`, Schreibweise des SRF | |
| `(58)` | fortlaufend über alle regulären Staffeln gezählt (ohne Staffel 0) | Das Gipfeltreffen 58 = Staffel 8, Folge 8 |

Ein Titelanfang wie „Folge 3:“, „Teil 3.“ oder „Episode 3:“ wird vor dem Titelvergleich entfernt; seine Nummer
nutzt mtdl-web nicht.

**Downloadordner** (Abo-Ziel „Downloadordner“, Einzeldownloads): `(S02/E03)` ergibt `S02E03`, `(39)` ergibt Folge 39
in der Staffel, die das Abo angibt (sonst 1). Ohne Marke heißt die Datei nach dem Sendetag
(`Serie - 2026-10-12 - Titel.mp4`).

**Regelvorlage „Hat Folgennummer“** legt eine sichtbare Regel an: Titel passt auf `\((S\d+/E)?\d+\)$`.

## 2. Abgleich mit TMDB (Abo-Ziel Serienbibliothek)

Ein Abo in eine Serienbibliothek gehört zu einer Serie bei TMDB. Für jeden Eintrag, den die Regeln durchlassen,
sucht mtdl-web die TMDB-Folge über drei Signale, das stärkste zuerst:

1. **Nummer** aus der Folgenmarke (Abschnitt 1).
2. **Titel:** der TMDB-Folgenname am Anfang des Eintragstitels, ohne Groß- und Kleinschreibung und Satzzeichen
   („Die Anstalt – Jetzt erst rechts! - Politsatire …“ = „Die Anstalt - Jetzt erst rechts!“). Gleich oder als
   längster Anfang mit mindestens 8 Zeichen; auch nach dem Seriennamen („Werwölfe · Das Spiel …: <Folge>“).
   Platzhalter von TMDB („Folge 1“, „Episode 1“) zählen nicht.
3. **Sendetag** gleich dem TMDB-Ausstrahlungstag. Allein entscheidet er nur, wenn beide Längen bekannt sind und
   die Länge des Eintrags um höchstens ein Viertel der TMDB-Laufzeit davon abweicht, mindestens aber 5 Minuten
   Abweichung erlaubt sind (`max(5, 0.25 * Laufzeit)`; 45 Minuten: rund 34 bis 56, 15 Minuten: 10 bis 20): Sender
   stellen Folgen teils Wochen vor dem TMDB-Datum online, und Ausschnitte tragen den Tag ihrer Folge.

Entscheidung:

| Lage | Ergebnis (Grund in der Vorschau) |
|---|---|
| Nummer passt, Titel passt oder fehlt | Folge der Nummer (`number_title`, `number_date`, `number`) |
| Nummer `(S02/E03)` und Titel nennen verschiedene Folgen | keine Zuordnung (`conflict`) |
| nur der Titel, eine Folge | diese (`title`, `title_date`) |
| Titel passt auf mehrere | der Sendetag entscheidet, sonst eine fortlaufende Nummer, sonst keine (`ambiguous`) |
| nur der Sendetag, genau eine passende Folge | diese (`date`) |
| nichts davon | keine (`no_match`) |
| Eintrag kürzer als die halbe TMDB-Laufzeit | keine, wohl ein Ausschnitt (`too_short`) |

Weitere Regeln:

- Zwei Einträge für eine Folge (Wiederholung, Clip unter gleichem Namen): der längere lädt, der andere zählt als
  Duplikat. Dieselbe Videodatei auf mehreren Sendern am selben Tag ist eine Folge und lädt einmal.
- **Ohne Zuordnung** („Ohne TMDB-Folge“) lädt nichts und wird bei jeder Prüfung neu abgeglichen. Treffer werden
  nicht festgeschrieben: Der Abgleich läuft lokal gegen die zwischengespeicherte Serie. Geladene Folgen sind über ihren
  Download fest (nie erneut geladen, der Name bleibt), ungeladene folgen dem aktuellen Stand bei TMDB.
- **TMDB-Daten:** je Serie sechs Stunden in der Zustandsdatenbank. Eine Auffrischung ist eine Anfrage (die Staffeln
  hängen an der Serienabfrage, höchstens 20 Teile), eine neu zugeordnete Serie zwei: vier Anfragen am Tag je Serie,
  gleich wie viele Einträge auf eine Folge warten. Höchstens 10 Anfragen je
  Sekunde, eine 429-Antwort wird respektiert; ist TMDB nicht erreichbar, gilt die ältere Antwort.
- **Folge erst später bei TMDB:** Steht eine neue Folge in der Mediathek, bevor TMDB sie führt, lädt sie die erste
  Prüfung nach der nächsten Auffrischung, also spätestens rund 7 Stunden, nachdem TMDB sie aufnimmt.
- **Dateiname** nach der Vorlage der Bibliothek, Standard
  `{serie} {tmdb}/{staffelordner}/{serie} - S{staffel}E{folge} - {folgentitel}` (Staffel 0 in `Specials`).
  Trägt die Folge bei TMDB nur einen Platzhalter („Folge 9“), steht der Mediathek-Titel an seiner Stelle.
- **Schon in der Bibliothek:** Ordner der Serie werden an der ID im Namen erkannt (`{tmdb-…}`, `{tvdb-…}`,
  `{imdb-…}`, auch `[tmdbid-…]`), Folgen an `S02E03` im Namen von Videodateien. Neue Folgen kommen in den
  vorhandenen Ordner mit der ID-Art der Vorlage.

## 3. Folgenmarken in der Filmliste

Alle 720.003 Titel der Filmliste vom 13.10.2026 (11.348 Themen), erzeugt mit `tools/folgenmarken.py`. Ein Titel kann
in mehreren Zeilen stehen; „davon erkannt“ zählt Titel, in denen mtdl-web heute eine Marke liest (oft eine zweite,
etwa `(1/4)` zusammen mit `(S01/E01)`).

#### Erkennt mtdl-web heute (Marke am Titelende)

| Form | Titel | Themen | Sender (häufigste) | davon erkannt | Beispiel |
|---|---:|---:|---|---:|---|
| (S02/E03) | 29743 | 1742 | ZDF 11889, ZDF-tivi 4935, MDR 2322 | 29743 | 3Sat: Südspanien von oben (S01/E17) |
| (S2026/E08), Staffel als Jahr | 15479 | 267 | ZDF 12831, ZDFinfo 940, ZDF-tivi 831 | 15479 | 3Sat: 37°: Stresstest gemeinsame Wohnung (S2025/E17) |
| (58), fortlaufend | 5285 | 334 | MDR 1394, ARD 1147, ONE 987 | 5285 | 3Sat: Vier Saiten (9) |
| (Staffel 2, Folge 3) | 3740 | 211 | SRF 3740 | 3740 | SRF: «Felicità, Bonheur, Glück, Fortuna» (Staffel 2024, Folge 1) |

#### Folgenmarken, die mtdl-web heute nicht erkennt

| Form | Titel | Themen | Sender (häufigste) | davon erkannt | Beispiel |
|---|---:|---:|---|---:|---|
| (S01/E06) vor einer Fassungsangabe | 13227 | 647 | ZDF 6733, MDR 1638, ZDF-tivi 1137 | 0 | 3Sat: 37°: Die Landklinik: Patient Krankenhaus (S2026/E09) (Audiodeskription) |
| (1/4), Teil 1 von 4 | 11652 | 777 | ARTE.FR 2653, ARTE.DE 2110, 3Sat 1353 | 811 | 3Sat: Abenteuer Linienbus (1/4) |
| Folge 1311: am Titelanfang | 10981 | 568 | MDR 3913, SWR 1626, ARD 1443 | 5962 | 3Sat: Folge 6: Bonn – Alte Freunde, neue Feinde (S01/E06) (Audiodeskription) |
| 27. Der Taschengeldtrick (KiKA) | 5157 | 217 | KiKA 5157 | 54 | KiKA: 4. Janne schwimmt um die Landesmeisterschaft |
| Episode 656 | 1101 | 31 | ONE 982, ZDF 54, Funk.net 22 | 24 | 3Sat: Wilder: Spur, Episode 4 |
| (Folge 5) | 920 | 139 | SRF 723, SWR 52, SR 44 | 2 | 3Sat: Davos 1917 (Folge 5) |
| (Teil 2) | 368 | 111 | ARD 94, SRF 83, SR 29 | 27 | 3Sat: Ottilie von Faber-Castell - Eine mutige Frau (Teil 2) |
| (F. 328) | 164 | 2 | BR 163, ARD 1 | 0 | ARD: Fit in den Frühling (F. 266) |
| (S2024E81), ohne Schrägstrich | 146 | 27 | ZDF 110, SRF 18, ARTE.PL 14 | 0 | 3Sat: "Kulturzeit" vom 02.05.2024: Wie demokratisch sind die USA? (S2024E81) |
| Staffel 4, Folge 3 im Titel | 57 | 9 | SRF 31, 3Sat 16, ZDF 7 | 0 | 3Sat: Traumhafte Bahnstrecken der Schweiz (Staffel 2, Folge 4/4) |
| (Staffel 2, 3/6) | 41 | 5 | SRF 35, 3Sat 6 | 0 | 3Sat: Kunst hoch 2 mit Pascale Wiedemann und Daniel Mettler (Staffel 2, 3/6) |
| (E68) | 25 | 6 | ZDF 22, 3Sat 3 | 0 | 3Sat: Honigfrauen (3/3) - Hochzeit im Paradies (E03) |
| S2026 E6, S1E5 ohne Klammer | 1 | 1 | SWR 1 | 0 | SWR: Heimat entdecken - Mit Leidenschaft fürs Ehrenamt/ Doku & Reportage S2026 E6 |

#### Zahlen, die keine Folge meinen

| Form | Titel | Themen | Sender (häufigste) | davon erkannt | Beispiel |
|---|---:|---:|---|---:|---|
| (14.03.2003), Datum | 27208 | 280 | ARD 15387, SR 11703, ARTE.DE 54 | 0 | ARD: Schröder zur „Agenda 2010“ (14.03.2003) |
| (23/10/2020), Datum (ARTE.FR) | 1250 | 5 | ARTE.FR 1247, ARTE.DE 2, DW 1 | 0 | ARTE.DE: ARTE Journal (12/10/2026) - Ist ein AfD-Verbotsverfahren möglich? |
| (2024), ein Jahr | 1079 | 101 | ORF 270, ARD 211, SRF 178 | 1079 | 3Sat: Das Geheimnis der Meister - Kees van Dongens "Finger an der Wange" (1910) |
| (1933-1935), Zeitraum | 150 | 26 | ARTE.FR 43, ARTE.DE 30, RBB 11 | 6 | ARD: Folge 1: Macht & Terror (1933-1935) |
| (Clip 31) | 139 | 2 | ZDF 70, KiKA 69 | 0 | KiKA: Liebesgeständnisse (Clip 31) |
| (Staffel 2), nur Staffel | 40 | 15 | SRF 30, ARD 4, BR 2 | 0 | ARD: ALLE Staffeln SUPERNATURAL in 120 Sekunden? / Der große RECAP fürs FINALE (Staffel 15) |
| (S2022), nur Staffel | 40 | 4 | ZDF-tivi 35, NDR 3, ZDF 1 | 0 | NDR: Entfernte Szenen „Die Kanzlei“ (S6) – Teil 2 |

Befunde:

- **Fassungen:** Hörfassung, Gebärdensprache, Sprachfassungen und Originalversion hängen ihre Angabe hinter die Marke
  („… (S2026/E09) (Audiodeskription)“). Diese 13.227 Titel erkennt mtdl-web heute nicht als Folge; in Abos fallen sie
  auf Titel und Sendetag zurück.
- **Mehrteiler** zählen „(1/4)“ (11.652 Titel, vor allem Dokumentationen bei 3sat und ARTE): Teil 1 von 4.
- **Titelanfang „Folge 1311:“:** rund 5.000 Titel tragen nur diese Marke, etwa Daily Soaps und Doku-Serien
  (Die Fallers, Eisbär, Affe & Co., Schloss Einstein). KiKA schreibt „27. Der Taschengeldtrick“, ONE bei „Sturm der
  Liebe“ „Episode 656“.
- **Seltener:** „(Folge 5)“ (SRF), „(F. 328)“ (BR, Zoogeschichten), „(S2024E81)“ ohne Schrägstrich (ZDF),
  „Staffel 4, Folge 3“ im Fließtext (SRF, 3sat), „(Staffel 2, 3/6)“ (SRF), „(E68)“ (ZDF).
- **Fallen:** Datumsangaben in Klammern (ARD, SR; ARTE.FR mit Schrägstrichen), Zeiträume, „(Clip 31)“ und reine
  Staffelangaben bei Trailern. Jahreszahlen wie „(1910)“ oder „(2026)“ (Tatort) liest mtdl-web heute als fortlaufende
  Nummer: im Bibliotheksabgleich harmlos, solange eine Serie weniger Folgen hat, im Downloadordner wird daraus aber
  `S01E1910`. Eine einfache Regel „19xx/20xx ist ein Jahr“ geht nicht: ONE zählt „Rote Rosen“ mit „(1460)“ bis
  „(1964)“, das sind Folgen.

#### Thema wechselt innerhalb einer Sendung

Dieselbe Sendung steht nicht immer unter demselben Thema. Beispiel ZDF Magazin Royale (Filmliste vom 14.10.2026): 34
Folgen seit März 2024 unter „ZDF Magazin Royale - Late-Night mit Jan Böhmermann“, mit Marke „(S2026/E08)“ und der
Zeit der Online-Premiere (freitags 18:00). Die Folge vom 9.10.2026 steht dagegen unter „ZDF Magazin Royale“, ohne
Marke und mit der Zeit der Ausstrahlung (23:10). Beide Einträge zeigen auf dieselbe Seite der Sendung bei zdf.de, und
an keinem Tag stehen beide Themen nebeneinander: kein Doppel, sondern ein Eintrag aus anderer Quelle, vermutlich dem
Sendetermin. Für Abos heißt das „Thema enthält“ statt „Thema ist“; eine Folge ohne Marke findet ihre TMDB-Folge über
Titel und Datum (Abschnitt 2).

## 4. Offen

Nicht entschieden; jede Erweiterung des Abgleichs muss den Referenzfall halten und paarweise ALT gegen NEU über die
Bestandsdaten geprüft werden.

- Marke vor einer Fassungsangabe lesen: geringes Risiko, dieselbe Marke; betrifft auch Downloadordner und Vorlage.
- `(S2024E81)`, `(Folge 5)`, `(F. 328)`, `(Staffel 2, 3/6)`: geringes Risiko, wenige Titel.
- `(1/4)` als Folge 1 einer Staffel: passt zu Miniserien bei TMDB, nicht zu Dokumentationen, die TMDB nicht führt.
- Titelanfang „Folge 1311:“, KiKA „27.“, „Episode 656“: fortlaufende Nummern langer Serien; ob TMDB sie ebenso zählt,
  vorher je Serie prüfen.
- Jahreszahlen nicht als Nummer lesen: nur mit Blick auf die übrigen Einträge des Themas (zählt es fortlaufend bis
  in diesen Bereich, wie „Rote Rosen“, ist es eine Folge).

## 5. Nachprüfen

```sh
docker run --rm -v /pfad/zu/films.sqlite:/data/films.sqlite:ro mtdl-web:test \
    python tools/folgenmarken.py /data/films.sqlite
```

Die Filmdatenbank liegt im Cache-Ordner von mtdl-web (`cache/films.sqlite`); eine Kopie genügt. Der Abgleich ist
durch `test/test_library.py` belegt (Die Anstalt, Werwölfe, Das Gipfeltreffen gegen TMDB vom 13.10.2026).

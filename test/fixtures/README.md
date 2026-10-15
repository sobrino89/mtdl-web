# Testdaten

Echte Daten, nicht nachgebaut. Formatbefunde: `docs/filmliste-format.md`.

| Datei | Inhalt |
|---|---|
| `mvw-query-gipfeltreffen-2026-10-12.json` | Antwort der MediathekViewWeb-API auf „das gipfeltreffen" (Titel und Thema), 35 Treffer, Index-Stand 12.10.2026 20:19 UTC. Referenzfall: Regeln *Thema ist „Das Gipfeltreffen"* + *Titel ist regex `\(\d+\)$`* → 20 Folgen (39–58). |
| `filmliste-auszug-2026-10-12.json` | 220 Einträge der Vollliste (`Filmliste-akt.xz`, erzeugt 12.10.2026 20:19 UTC, Listen-ID `b1587d20…`). |
| `filmliste-diff-auszug-2026-10-12-2019.json` | 21 Einträge der Diff derselben Erzeugung (20:19 UTC): 11 stehen gleich im Auszug (Einsortieren ohne Dublette), 10 fehlen dort (Hinzufügen). |
| `filmliste-diff-auszug-2026-10-12-2133.json` | 19 Einträge der nächsten Diff (21:33 UTC, Listen-ID `91cccfc3…`, kumulativ seit der Tagesbasis, 4.460 Einträge): 8 ändern Einträge des Auszugs (Größe nachgetragen oder entfernt, ZDF-HD-Variante `6628k_p61v17` → `6660k_p37v17`), 11 sind neu, darunter ein neuer „Gipfeltreffen"-Eintrag (ZDF, Thema Politik). |
| `films-die-anstalt-2026-10-13.json` | 3 Einträge „Die Anstalt" der Filmliste vom 13.10.2026: E08 bei ZDF und 3sat (dieselbe Videodatei unter anderem Titel), E07 als andere Folge. |
| `films-serien-2026-10-13.json` | Mediathek-Einträge je Serie aus der Filmliste vom 13.10.2026: Die Anstalt 28, Werwölfe 30, Das Gipfeltreffen 25 (Feldnamen wie `models.Film`). |
| `films-filme-2026-10-13.json` | Ein Spielfilm der Filmliste vom 13.10.2026: „Nikita - Spielfilm, Frankreich/Italien 1990" (3sat, gesendet 11.10.2026). |
| `tmdb-die-anstalt-2026-10-13.json`, `tmdb-werwoelfe-2026-10-13.json`, `tmdb-das-gipfeltreffen-2026-10-13.json` | TMDB-Antworten vom 13.10.2026 (`de-DE`) je Serie: `tv/{id}` mit `external_ids` und jede Staffel `tv/{id}/season/{n}`, gekürzt auf die Felder, die mtdl-web liest. |
| `tmdb-search-2026-10-13.json` | TMDB `search/tv` vom 13.10.2026 für die drei Serien. |
| `tmdb-movie-nikita-2026-10-13.json`, `tmdb-search-movie-nikita-2026-10-13.json` | TMDB `movie/9322` und `search/movie` („Nikita", Jahr 1990) vom 13.10.2026 (`de-DE`), ungekürzt. |
| `res-mediathekview-akt-2026-10-13.xml`, `res-mediathekview-diff-2026-10-13.xml` | Server-Verzeichnis von MediathekView (`res.mediathekview.de/akt.xml`, `diff.xml`) vom 13.10.2026. |

## Filmlisten-Auszug

Erzeugt mit `tools/fixture_filmliste.py` aus der Vollliste und den beiden Diffs vom 12.10.2026 (Aufruf im
Docstring). Ausgewählt, in der Reihenfolge der Originalliste:

- alle 121 Einträge mit „gipfeltreffen" in Thema oder Titel (darunter die 35 MVW-Treffer und 86, die die Suche
  „das gipfeltreffen" nicht finden darf),
- alle 36 Einträge mit „Werwölfe" in Thema oder Titel (30 aus dem BR-Thema „Werwölfe · Das Spiel von List und
  Täuschung"),
- die ersten 25 Einträge der Liste,
- 5 Einträge, die auch in der ersten Diff stehen,
- 5 Einträge, die die zweite Diff ändert,
- je der erste Eintrag mit einer Formatbesonderheit (Liste in `features()` des Werkzeugs): Zeit `HH:MM` oder leer,
  Datum leer, Dauer leer, Größe leer, HLS, Klein- oder HD-URL leer, einstelliger URL-Offset, kaputte Haupt-URL,
  Untertitel als WebVTT, TTML, ARD-EBU-TT, HTML, protokollrelativ, mit doppeltem Schema, ohne Schema, nur als Pfad,
  im Feld Url RTMP (KiKA), jeder Geo-Wert, Titel mit geschütztem Leerzeichen, Zeilenumbruch oder Rand-Leerzeichen,
  Datum vor 1970 und in ferner Zukunft, Sender `rbtv` und `RBTV`.

Diff-Auszüge: alle Diff-Einträge, deren Identität (Sender, Thema, URL, Website) im Auszug vorkommt, die ersten 10,
die dort fehlen, und alle fehlenden mit „gipfeltreffen" oder „Werwölfe".

Abweichung vom Original, damit jeder Auszug selbst eine gültige Filmliste ist: Sender und Thema sind nach der Regel
des Servers neu kodiert (gesetzt, wenn sie sich gegenüber dem vorigen Eintrag des Auszugs ändern, sonst leer). Alle
anderen Felder sind unverändert, die Metadaten sind die des Originals. Ein Eintrag je Zeile (JSON-Leerraum; das
Original hat keine Zeilenumbrüche). Geprüft: Jeder Eintrag ist nach dem Auflösen von Sender und Thema feldgleich mit
der Originalliste.

## Untertitel (`subtitles/`)

Echte Untertitel vom 12./13.10.2026, je Quelle ein Beispiel, auf die ersten 12 Einträge gekürzt (Rest der Datei
abgeschnitten, schließende Tags ergänzt). Ausgewählt mit dem Index der Vollliste: je Untertitel-Variante der neueste
kurze Eintrag.

| Datei | Quelle | Besonderheit |
|---|---|---|
| `ard-ebutt-mdr.xml` | `api.ardmediathek.de/…/ebutt/…` (MDR) | EBU-TT-D mit Präfix `tt:`, eingerückt |
| `zdf-ebutt.xml` | `utstreaming.zdf.de` | EBU-TT-D |
| `kika-ebutt.xml` | `www.kika.de/…/subtitle` | EBU-TT-D |
| `sr-ebutt-timecode.xml` | `www.sr-mediathek.de/sr_player/ut.php` | Zeiten ab Sende-Timecode 10:00:00 |
| `srf-ttml.xml` | `subtitles.eai-general.aws.srf.ch/…/hbbtv.xml` | TTML ohne Präfix, `dur` |
| `orf-ttml.ttml` | `api-tvthek.orf.at` | Zeiten in Sekunden, Umbrüche als maskiertes `<br/>` |
| `srf.vtt` | `subtitles.eai-general.aws.srf.ch/…/vod.vtt` (älterer Eintrag) | WebVTT mit `<font>` |
| `ndr-not-subtitles.html` | `www.ndr.de/…/ut….html` | keine Untertitel, eine HTML-Seite (erste 2.000 Zeichen) |

## HLS-Playlists (`hls/`)

Echte Playlists vom 12./13.10.2026; die Ursprungs-URLs stehen in `test/test_hls.py` (relative Segmente lösen sich
dagegen auf). `funk-media-30.m3u8` ist auf die ersten 30 Segmente gekürzt (Original 538).

| Datei | Quelle | Besonderheit |
|---|---|---|
| `srf-media.m3u8` | SRF „Meteo vom 12.10.2026" | relative Segmente mit Query, TS mit Ton |
| `orf-chunklist.m3u8` | ORF „ZIB Flash 4 vom 12.10.2026" | Chunklist (Medien-Playlist) einer Bitrate |
| `wdr-media.m3u8` | WDR | Segmente absolut auf anderem Host (`wdrvod-pckgr`) |
| `funk-media-30.m3u8` | funk | gekürzt |
| `orf-master.m3u8` | ORF „Universum: Lungau" | Master nach Umleitung auf `vod-ww.mdn.ors.at`, Bild-Varianten plus Ton-Playlist |
| `funk-master.m3u8` | funk | Master mit reinen Ton-Varianten und I-Frame-Streams |

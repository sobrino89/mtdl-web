---
title: Lizenz und Dank
description: Lizenz von mtdl-web, Lizenzen der Bestandteile und Dank an die Projekte, auf denen es aufbaut.
---

## Lizenz

mtdl-web steht unter der GNU General Public License, Version 3 oder neuer (`GPL-3.0-or-later`). Du darfst es nutzen,
ändern und weitergeben. Wer eine veränderte Fassung weitergibt, legt deren Quellcode unter derselben Lizenz offen.
Den Wortlaut enthält die Datei
[`LICENSE`](https://github.com/sobrino89/mtdl-web/blob/main/LICENSE) im Repository.

Die Bestandteile behalten ihre eigenen Lizenzen: in der Oberfläche Preact (MIT), hls.js und Swagger UI
(Apache-2.0), im Backend die Python-Pakete aus `uv.lock`, im Image ffmpeg und die übrigen Pakete von Debian. Diese
Dokumentation ist mit [Starlight](https://starlight.astro.build) gebaut.

## Dank

**MediathekView.** Die Daten stammen aus der Filmliste des [MediathekView](https://mediathekview.de)-Projekts, das
sie seit vielen Jahren pflegt und frei verteilt. Die Suchsyntax folgt
[MediathekViewWeb](https://github.com/mediathekview/mediathekviewweb). mtdl-web ist kein Projekt des
MediathekView-Teams und nicht mit ihm verbunden. Wer Senderlogos einschaltet, bekommt die Grafiken des
MediathekView-Programms, die der Server einmal von dort lädt: aus dem
[Repository](https://github.com/mediathekview/MediathekView) unter `src/main/resources/icons/sender`, das wie das
ganze Programm unter der GPL-3.0 steht. mtdl-web selbst liefert keine Logos mit; sie sind Marken der Sender.

**TMDB.** Serien-, Staffel-, Folgen- und Filmdaten für die Bibliotheken kommen von
[TMDB](https://www.themoviedb.org). Dieses Produkt nutzt die TMDB-API, ist aber nicht von TMDB unterstützt oder
zertifiziert.

**Die Sender.** Die Sendungen gehören den öffentlich-rechtlichen Sendern und ihren Urhebern. mtdl-web ist mit keinem
Sender verbunden.

---
title: Sicherheit
description: Wie mtdl-web Zugang, Schlüssel, Oberfläche und Container absichert, und wie man Sicherheitslücken meldet.
---

Wer mtdl-web erreicht, kann Downloads starten, Abos, Webhooks und Einstellungen ändern. Ein Webhook kann Anfragen an
Dienste im eigenen Netz schicken, ein Skript im Container läuft mit den Rechten von mtdl-web. Deshalb ist mtdl-web ab
Werk geschlossen und erklärt, was jede Öffnung bedeutet.

## Zugang

- **Passwort ab Werk** (`MTDL_AUTH=password`). Den ersten Aufruf schützt ein Einrichtungscode, den nur das Log des
  Containers zeigt; nach jedem Neustart gilt ein neuer, bis ein Passwort feststeht.
- **Gespeichert wird nichts, was sich entschlüsseln ließe:** das Passwort als scrypt-Hash, Sitzungen, API-Schlüssel
  und Einrichtungscode als SHA-256-Hash.
- **Sitzungen** gelten 30 Tage ab der letzten Nutzung. Das Cookie ist `HttpOnly` und `SameSite=Lax`, über HTTPS auch
  `Secure`. Ein neues Passwort beendet alle anderen Sitzungen; **Überall sonst abmelden** ebenso.
- **Falsche Versuche** bremsen: Nach mehreren muss eine Adresse warten. Raten viele Adressen zugleich, kommt nach
  jedem Fehlversuch eine Pause von bis zu 30 Sekunden für alle, aber keine Sperre, die dich selbst aussperrt.
- **Andere Modi** (`local`, `proxy`, `none`) sind bewusste Entscheidungen mit Warnungen in Doku und Oberfläche, siehe
  [Anmeldung](../../install/access/).

## API-Schlüssel und MCP

- Ein Schlüssel erscheint einmal beim Erzeugen; mtdl-web kennt danach nur seinen Hash.
- Schlüssel dürfen **nur lesen** (Standard) oder **lesen und ändern**. Ein lesender Schlüssel bekommt auch im
  MCP-Server nur die lesenden Werkzeuge.
- Der MCP-Server ist ab Werk aus und nimmt nur Schlüssel an, keine Cookies: Eine Webseite kann keinen angemeldeten
  Browser dafür ausleihen. Keines seiner Werkzeuge löscht Dateien, Abos oder Bibliotheken.

## Schutz vor fremden Webseiten

Eine Webseite, die jemand gerade offen hat, könnte versuchen, über dessen Browser Anfragen an mtdl-web im eigenen Netz
zu schicken. mtdl-web nimmt Änderungen deshalb in jedem Modus nur von seinen eigenen Seiten an: Es prüft die
Kennzeichnung, die Browser mitschicken (`Sec-Fetch-Site`), bei älteren Browsern `Origin` gegen die aufgerufene
Adresse. Skripte senden diese Kopfzeilen nicht und sind nicht betroffen.

Eine fremde Webseite kann auch ihren eigenen Namen auf die Adresse von mtdl-web zeigen lassen (DNS-Rebinding). Ihre
Anfragen gelten dem Browser dann als eigene, kommen aber unter ihrem Namen an. In den Modi `password` und `proxy`
fehlt ihr die Anmeldung, im Modus `local` gilt ihr Name nicht als lokal. Im Modus `none` antwortet mtdl-web deshalb
nur unter einer IP-Adresse oder einem lokalen Namen.

## Oberfläche

- Keine Inhalte von anderen Servern: Schriften, Skripte und Bilder kommen von mtdl-web selbst.
- Eine strenge Content Security Policy erlaubt nur eigene Skripte; dazu `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff` und `Referrer-Policy: no-referrer`, damit Links zu Senderseiten nicht die
  Adresse eines Dienstes im eigenen Netz verraten.

## Zugangsdaten anderer Dienste

Tokens für TMDB und Plex, Geheimnisse und Kopfzeilen von Webhooks zeigt die Oberfläche nach dem Speichern nie wieder
an, und die API gibt sie nicht heraus; sie nennt nur, was gesetzt ist. Im Log erscheinen sie nicht, von einer
Webhook-Adresse nur der Hostname, denn Adressen tragen oft selbst ein Token. Ein API-Schlüssel, der nur lesen darf,
sieht ebenso nur Schema und Host einer Webhook-Adresse und statt einer Vorlage `***`. Webhooks folgen keinen
Weiterleitungen, damit Kopfzeilen nicht bei einem anderen Server landen.

In der Datenbank liegen diese Tokens lesbar, denn mtdl-web braucht sie im Klartext. Behandle Sicherungen von
`/config` deshalb wie ein Passwort.

## Downloads

Die Adressen von Sendungen, Playlists, Untertiteln und Filmlisten-Spiegeln stammen von anderen: aus der Filmliste,
von MediathekViewWeb, von den Servern der Sender. mtdl-web lädt von ihnen nur aus dem Internet, nie aus dem eigenen
Netz. Es prüft die Adresse, mit der die Verbindung tatsächlich besteht, nicht nur den Namen davor; so kann auch eine
manipulierte Liste keine Geräte im eigenen Netz abfragen. Webhooks und Plex sind ausgenommen, sie zeigen gewollt
dorthin.

## Skripte

Skripte kommen nur aus dem Ordner `/config/hooks/`. mtdl-web legt dessen Unterordner nur für `PUID` beschreibbar an.
Die API zeigt Skripte und ihren letzten Lauf, kann aber keine anlegen, ändern oder starten.

## Container

- Mit `PUID`/`PGID` braucht nur der Start einen Moment root, um `/config` zu übergeben. Danach läuft kein Prozess
  mehr als root, auch nicht der Init-Prozess `tini` oder der Healthcheck: alles als `PUID`:`PGID`, ohne weitere
  Gruppen und ohne Capabilities. Mit `user:` in der Compose-Datei startet der Container ganz ohne root, siehe
  [Docker Compose](../../install/docker-compose/#ganz-ohne-root).
- Die Vorlagen setzen `no-new-privileges`: Kein Prozess im Container kann sich über ein setuid-Programm wieder mehr
  Rechte holen.
- Download-Ordner und Bibliotheken ändert er nie in Besitz oder Rechten.
- Alle Abhängigkeiten sind fest versioniert (Lockfiles, Basis-Images per Digest) und kommen beim Bau aus ihren
  offiziellen Quellen. Zur Laufzeit lädt mtdl-web nur Daten, nie Code.

## Sicherheitslücken melden

Bitte nicht als öffentliches Issue, sondern vertraulich über
[GitHub Security Advisories](https://github.com/sobrino89/mtdl-web/security/advisories/new). Details stehen in
der Datei `SECURITY.md` im Repository.

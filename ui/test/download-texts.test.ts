import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import type { Download, Film, Info, SearchResult } from "../src/api";
import { failure, origin, progress, targetFile, targetFolder } from "../src/download-texts";
import { setLocale } from "../src/format";

setLocale("de-DE");
const films = (JSON.parse(readFileSync("test/fixtures/search-das-gipfeltreffen.json", "utf8")) as SearchResult).films;
const geoFilm = films.find((f) => f.geo.length === 1) as Film;
const info: Info = { version: "0", description_search: true, timezone: "Europe/Berlin", download_dir: "/downloads", uid: 99, gid: 100, workers: 2 };

function download(patch: Partial<Download> = {}): Download {
  return {
    id: 7,
    state: "failed",
    film: geoFilm,
    path: "Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E39 - Menschenjagd in gut.mp4",
    file: "", // servers before 0.1.0.dev20 did not send it
    library_id: null,
    quality: "hd",
    subtitles: true,
    subscription_id: null,
    bytes_done: 0,
    bytes_total: null,
    speed: null,
    phase: null,
    error: "HTTP 403 for https://example.invalid/a.mp4 (geo restriction: DE)",
    error_code: "geo",
    attempts: 1,
    retry_at: null,
    position: 7,
    created_at: "2026-10-13T10:00:00Z",
    started_at: null,
    finished_at: "2026-10-13T10:01:00Z",
    ...patch,
  };
}

test("target paths as the container sees them", () => {
  assert.equal(targetFolder(download(), info), "/downloads/Das Gipfeltreffen/Season 01");
  assert.equal(targetFile(download({ path: "a.mp4" }), info), "/downloads/a.mp4");
  assert.equal(targetFolder(download({ path: "a.mp4" }), { ...info, download_dir: "/media/" }), "/media");
  // Library downloads of servers before 0.1.0.dev20 kept their absolute path: no downloads folder in front.
  const library = "/data/shows/Die Anstalt {tmdb-73860}/Season 13/Die Anstalt - S13E01 - Die Prüf-Anstalt.mp4";
  assert.equal(targetFile(download({ path: library }), info), library);
  assert.equal(targetFolder(download({ path: library }), info), "/data/shows/Die Anstalt {tmdb-73860}/Season 13");
  // Since then the server says where the file is; the path is relative to the library.
  const relative = "Die Anstalt {tmdb-73860}/Season 13/Die Anstalt - S13E01 - Die Prüf-Anstalt.mp4";
  assert.equal(targetFile(download({ path: relative, file: library, library_id: 1 }), info), library);
});

test("a folder that is not mounted is explained, and trying again helps once it is", () => {
  const notMounted = failure(download({ error_code: "not_mounted", error: "/data/shows is not mounted" }), info);
  assert.equal(notMounted.title, "Zielordner nicht eingebunden");
  assert.ok(notMounted.text.startsWith("{path} liegt nicht auf einem eingebundenen Laufwerk"));
  assert.equal(notMounted.retryable, true);
});

test("failures explain what happened and what helps", () => {
  const geo = failure(download(), info);
  assert.equal(geo.title, "Geo-gesperrt: nur abrufbar in DE");
  assert.equal(geo.retryable, true);
  assert.equal(geo.command, null);
  const perm = failure(download({ error_code: "permission", error: "PermissionError: [Errno 13]" }), info);
  assert.equal(perm.text, "{path} ist für mtdl-web nicht beschreibbar; mtdl-web läuft als 99:100.");
  assert.equal(perm.command, 'docker exec -u 0 mtdl-web chown -R 99:100 "/downloads/Das Gipfeltreffen/Season 01"');
  const gone = failure(download({ error_code: "gone" }), info);
  assert.equal(gone.retryable, false);
  assert.equal(failure(download({ error_code: "not_public" }), info).title, "Adresse im eigenen Netz");
  assert.equal(failure(download({ error_code: null }), info).code, "unknown");
  assert.equal(failure(download({ error_code: "something new" }), info).title, "Download fehlgeschlagen");
});

test("origin and progress", () => {
  assert.equal(origin(download(), () => undefined), "Einzeldownload");
  assert.equal(origin(download({ subscription_id: 3 }), (id) => (id === 3 ? "Das Gipfeltreffen" : undefined)), "Abo „Das Gipfeltreffen“");
  const live = { id: 7, channel: "MDR", topic: "", title: "", done: 115_000_000, total: 267_000_000, speed: 2_500_000, phase: "download" };
  const p = progress(download({ state: "running" }), live, "Mbit/s");
  assert.equal(p.text, "43 % · 115 MB von 267 MB");
  assert.equal(p.speed, "20,0 Mbit/s");
  assert.equal(p.eta, "noch 1:01 min");
  const mux = progress(download({ state: "running" }), { ...live, done: 267_000_000, phase: "mux", speed: 0 }, "MB/s");
  assert.equal(mux.text, "100 % · 267 MB von 267 MB · wird zusammengesetzt");
  assert.equal(mux.eta, "–");
});

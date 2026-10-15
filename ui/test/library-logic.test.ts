// Real TMDB search answers of 13.10.2026 (the same file as the backend's test/fixtures).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import type { Library, SeriesCandidate } from "../src/api";
import { autoSeries, episodeCode, folder, libraryFor, MOVIE_PRESETS, movieQuery, PRESETS, presetOf, reasonKey } from "../src/library-logic";

type Answer = { results: (SeriesCandidate & { id: number; first_air_date: string })[] };
const searches = JSON.parse(readFileSync("test/fixtures/tmdb-search-2026-10-13.json", "utf8")) as Record<string, Answer>;
const candidates = (query: string): SeriesCandidate[] =>
  (searches[query]?.results ?? []).map((r) => ({
    tmdb_id: r.id,
    name: r.name,
    original_name: r.original_name,
    year: r.first_air_date ? Number(r.first_air_date.slice(0, 4)) : null,
    overview: r.overview,
  }));

// Titles of films in the film list of 13.10.2026 (3sat, ZDF, ZDFneo, ARTE, ZDF-tivi).
test("what to ask TMDB for a film of the Mediathek", () => {
  assert.deepEqual(movieQuery("Nikita - Spielfilm, Frankreich/Italien 1990"), { query: "Nikita", year: 1990 });
  assert.deepEqual(movieQuery("Magic Mike (Audiodeskription)"), { query: "Magic Mike", year: null });
  assert.deepEqual(movieQuery("Survive - Gestrandet im Ozean (Englisch)"), { query: "Survive - Gestrandet im Ozean", year: null });
  assert.deepEqual(movieQuery("John Wayne - Amerika um jeden Preis (mit Untertitel)"), { query: "John Wayne - Amerika um jeden Preis", year: null });
  assert.deepEqual(movieQuery("Arrietty und die wundersame Welt der Borger"), { query: "Arrietty und die wundersame Welt der Borger", year: null });
  // A year in the title is the title, not the year of release.
  assert.deepEqual(movieQuery("1917 - Spielfilm, Großbritannien/USA 2019"), { query: "1917", year: 2019 });
});

const nikita = JSON.parse(readFileSync("test/fixtures/tmdb-search-movie-nikita-2026-10-13.json", "utf8")) as {
  results: { id: number; title: string; original_title: string; release_date: string; overview: string }[];
};

test("a movie is taken without asking only when one of that title and year exists", () => {
  const found: SeriesCandidate[] = nikita.results.map((r) => ({
    tmdb_id: r.id,
    name: r.title,
    original_name: r.original_title,
    year: Number(r.release_date.slice(0, 4)),
    overview: r.overview,
  }));
  assert.equal(autoSeries(found, movieQuery("Nikita - Spielfilm, Frankreich/Italien 1990").query)?.tmdb_id, 9322);
});

test("the library of a subscription: the one named, else the only one of its kind", () => {
  const lib = (id: number, kind: Library["kind"]): Library => ({
    id,
    name: `L${id}`,
    kind,
    path: `/library/${id}/`,
    template: "",
    normalize: false,
    plex_path: null,
    plex_refresh: false,
    problem: null,
    subscriptions: [],
  });
  const one = [lib(1, "series"), lib(2, "movies")];
  assert.equal(libraryFor(one, null, "series")?.id, 1); // subscriptions of dev16 to dev19 name none
  assert.equal(libraryFor(one, 2, "series")?.id, 2); // named: that one (the server refuses the wrong kind)
  assert.equal(libraryFor([...one, lib(3, "series")], null, "series"), null); // two: the person chooses
  assert.equal(libraryFor(null, 1, "series"), null);
  assert.equal(folder("/library/1/"), "/library/1");
  assert.equal(folder("/"), "/");
});

test("a series is taken without asking only when its name is the query", () => {
  assert.equal(autoSeries(candidates("Die Anstalt"), "Die Anstalt")?.tmdb_id, 73860);
  assert.equal(autoSeries(candidates("Das Gipfeltreffen"), "das gipfeltreffen")?.tmdb_id, 119293);
  // The Mediathek's topic ("Werwölfe · Das Spiel …") is TMDB's name ("Werwölfe – Das Spiel …") but for punctuation.
  assert.equal(autoSeries(candidates("Werwölfe"), "Werwölfe · Das Spiel von List und Täuschung")?.tmdb_id, 300292);
  assert.equal(autoSeries(candidates("Werwölfe"), "Werwölfe"), null); // a word of the name is not the name
  assert.equal(autoSeries(candidates("Die Anstalt"), ""), null);
});

test("presets, codes and reasons", () => {
  assert.equal(presetOf(PRESETS[0].template), "tmdb");
  assert.equal(presetOf("{serie}/x"), null);
  assert.equal(presetOf(MOVIE_PRESETS[0].template, "movies"), "movie_tmdb");
  assert.equal(presetOf(MOVIE_PRESETS[0].template), null); // not a series preset
  assert.equal(MOVIE_PRESETS[0].template, "{titel} ({jahr}) {tmdb}/{titel} ({jahr})"); // the backend default
  assert.equal(episodeCode({ season: 13, number: 8 }), "S13E08");
  assert.equal(episodeCode({ season: 0, number: 1 }), "S00E01");
  assert.equal(reasonKey("number_title"), "lib.reason.number_title");
  assert.equal(reasonKey("conflict"), "lib.none.conflict");
  assert.equal(reasonKey(undefined), "lib.none.no_match");
});

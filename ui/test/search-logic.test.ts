// Real search response (production film list, 13.10.2026): "das gipfeltreffen", 35 hits. Expected numbers were
// counted with jq on the fixture, not derived from the code under test.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import type { Film, SearchResult } from "../src/api";
import {
  activeFilters,
  apiScope,
  applyFilters,
  facets,
  fold,
  groupByTopic,
  hasFilters,
  marks,
  nextSort,
  parseFilters,
  pickQuality,
  qualities,
  sortFilms,
  topicCount,
  urlOf,
} from "../src/search-logic";

const result = JSON.parse(readFileSync("test/fixtures/search-das-gipfeltreffen.json", "utf8")) as SearchResult;
const films: Film[] = result.films;
const NOW = Date.parse("2026-10-13T12:00:00Z");
const filters = (query: Record<string, string> = {}) => parseFilters({ q: "das gipfeltreffen", ...query });
const count = (query: Record<string, string>, now = NOW) => applyFilters(films, filters(query), now).length;

test("the fixture is the reference query", () => {
  assert.equal(result.total, 35);
  assert.equal(films.length, 35);
});

test("defaults and parsing of the hash query", () => {
  const f = parseFilters({});
  assert.equal(f.scope, "titelthema");
  assert.equal(f.sort, "datum");
  assert.equal(f.desc, true);
  assert.equal(hasFilters(f), false);
  const g = parseFilters({ sender: "MDR,ARD", thema: "Das Gipfeltreffen|extra 3", sort: "titel-asc", in: "titel", dmin: "20", zeit: "30" });
  assert.deepEqual(g.sender, ["MDR", "ARD"]);
  assert.deepEqual(g.thema, ["Das Gipfeltreffen", "extra 3"]);
  assert.equal(g.sort, "titel");
  assert.equal(g.desc, false);
  assert.equal(g.dmin, 20);
  assert.equal(g.zeit, "30");
  assert.equal(apiScope(g.scope, true), "title");
  assert.equal(parseFilters({ sort: "nonsense-asc", zeit: "12", in: "irgendwo", dmin: "-3" }).sort, "datum");
  assert.equal(parseFilters({ zeit: "12" }).zeit, "");
  assert.equal(parseFilters({ in: "irgendwo" }).scope, "titelthema");
  assert.equal(parseFilters({ dmin: "-3" }).dmin, null);
});

test("description search falls back when the index has no descriptions", () => {
  assert.equal(apiScope("alles", true), "all");
  assert.equal(apiScope("alles", false), "topic_title");
});

test("filters narrow the loaded hits", () => {
  assert.equal(count({}), 35);
  assert.equal(count({ thema: "Das Gipfeltreffen" }), 25);
  assert.equal(count({ sender: "MDR" }), 20);
  assert.equal(count({ sender: "MDR,ARD" }), 27);
  assert.equal(count({ titel: "BEST OF" }), 7);
  assert.equal(count({ dmin: "40" }), 6);
  assert.equal(count({ dmax: "5" }), 2);
  assert.equal(count({ hd: "1" }), 34);
  assert.equal(count({ ut: "1" }), 27);
  assert.equal(count({ hd: "1", ut: "1" }), 27);
  assert.equal(count({ zeit: "365" }), 9);
  assert.equal(count({ zeit: "30" }), 0);
});

test("future broadcasts are hidden unless asked for", () => {
  const before = Date.parse("2026-08-01T00:00:00Z");
  assert.equal(count({}, before), 32);
  assert.equal(count({ kuenftig: "1" }, before), 35);
});

test("facets count with all other filters", () => {
  const f = filters({ thema: "Das Gipfeltreffen" });
  const { senders, topics } = facets(films, f, NOW);
  assert.deepEqual(senders, [["ARD", 5], ["MDR", 20]]);
  assert.equal(topics.length, 6);
  assert.deepEqual(topics[0], ["Das Gipfeltreffen", 25]);
  const mdr = facets(films, filters({ sender: "MDR", thema: "extra 3" }), NOW);
  assert.deepEqual(mdr.topics, [["Das Gipfeltreffen", 20], ["extra 3", 0]]);
});

test("sorting by column, ties newest first", () => {
  const byTitle = sortFilms(films, "titel", false);
  assert.equal(byTitle[0]?.title, "Asche macht keine Geräusche (53)");
  const byDate = sortFilms(films, "datum", true);
  assert.equal(byDate[0]?.title, "Das Beste kommt noch! (58)");
  const oldest = sortFilms(films, "datum", false);
  assert.equal(oldest[0]?.start, "2018-05-30T20:50:00Z");
  const longest = sortFilms(films, "dauer", true);
  assert.equal(longest[0]?.title, "Das Gipfeltreffen – Best of 2025");
  assert.equal(nextSort(filters(), "datum"), "datum-asc");
  assert.equal(nextSort(filters(), "titel"), "titel-asc");
  assert.equal(nextSort(filters(), "dauer"), "dauer-desc");
  assert.equal(nextSort(filters({ sort: "titel-asc" }), "titel"), "titel-desc");
});

test("groups by topic, largest first", () => {
  const groups = groupByTopic(sortFilms(films, "datum", true));
  assert.deepEqual(
    groups.map((g) => [g.topic, g.films.length]),
    [
      ["Das Gipfeltreffen", 25],
      ["Kurzstrecke mit Pierre M. Krause", 3],
      ["extra 3", 2],
      ["Kabarett & Comedy", 2],
      ["ZDF spezial", 2],
      ["phoenix der tag", 1],
    ],
  );
  assert.deepEqual(groups[0]?.channels, [["MDR", 20], ["ARD", 5]]);
  assert.equal(topicCount(films), 6);
});

test("chips to remove single filters", () => {
  const chips = activeFilters(filters({ sender: "MDR,ARD", hd: "1", dmin: "10" }));
  assert.deepEqual(
    chips.map((c) => [c.kind, c.value]),
    [["sender", "MDR"], ["sender", "ARD"], ["dauer", ""], ["hd", ""]],
  );
  assert.deepEqual(chips[0]?.patch, { sender: "ARD" });
});

test("comparison text like the backend", () => {
  assert.equal(fold("Das Gipfeltreffen – Best of"), "das gipfeltreffen - best of");
  assert.equal(fold("STRASSE  Straße"), "strasse strasse");
});

test("marks and qualities of a hit", () => {
  const newest = films[0] as Film;
  assert.deepEqual(marks(newest), ["HD", "UT"]);
  const geo = films.find((f) => f.geo.length === 3) as Film;
  assert.ok(marks(geo).includes("DE-AT-CH"));
  const q = qualities(newest);
  assert.deepEqual(q.map((x) => [x.quality, x.available]), [["hd", true], ["normal", true], ["low", true]]);
  assert.equal(q[1]?.size, 267386880);
});

test("German only and without special versions go by what the server marks", () => {
  // The fixture predates both fields (0.1.0.dev40); the marks are the server's answers for such titles.
  const [a, b, c] = films as [Film, Film, Film];
  const marked = [{ ...a, other_language: "Originalversion" }, { ...b, special_version: "Audiodeskription" }, c];
  const shown = (query: Record<string, string>) =>
    applyFilters(marked, parseFilters({ q: "das gipfeltreffen", ...query }), NOW).map((film) => film.id);
  assert.deepEqual(shown({}), [a.id, b.id, c.id]);
  assert.deepEqual(shown({ deutsch: "1" }), [b.id, c.id]);
  assert.deepEqual(shown({ ohnesonder: "1" }), [a.id, c.id]);
  assert.deepEqual(shown({ deutsch: "1", ohnesonder: "1" }), [c.id]);
  const chips = activeFilters(parseFilters({ deutsch: "1", ohnesonder: "1" }));
  assert.deepEqual(chips.map((chip) => [chip.kind, chip.patch]), [["deutsch", { deutsch: "" }], ["ohnesonder", { ohnesonder: "" }]]);
});

test("download button and preview take the preferred quality like the server picks it", () => {
  const all = films[0] as Film;
  assert.equal(pickQuality(all, "hd"), "hd");
  assert.equal(pickQuality(all, "normal"), "normal");
  assert.equal(pickQuality(all, "low"), "low");
  assert.equal(urlOf(all, "low"), all.url_low);
  // The one hit of the fixture without HD (jq: 1 of 35): HD preferred, the next lower one plays.
  const noHd = films[11] as Film;
  assert.equal(noHd.url_hd, null);
  assert.equal(pickQuality(noHd, "hd"), "normal");
  // Nothing lower: the next higher one.
  const onlyHd: Film = { ...all, url: null, url_low: null };
  assert.equal(pickQuality(onlyHd, "low"), "hd");
  assert.equal(pickQuality(onlyHd, "normal"), "hd");
  assert.equal(pickQuality({ ...onlyHd, url_hd: null }, "hd"), null);
});

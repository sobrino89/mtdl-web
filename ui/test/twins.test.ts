// Real search responses (production film list, 13.10.2026). "die anstalt": 32 hits, 3sat lists ZDF's S2026/E08 under
// a title of its own with the same file. "maus-spezial frankreich": ARD and WDR share the HD and low files of the
// audio-described version (the normal files differ), the plain version is a file of its own. The pairs were found
// with jq on the fixtures (films sharing a video URL), not with the code under test.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import type { Film, SearchResult } from "../src/api";
import { applyFilters, broadcastDay, groupByTopic, mergeTwins, parseFilters, sameFile } from "../src/search-logic";

const load = (name: string) => (JSON.parse(readFileSync(`test/fixtures/${name}`, "utf8")) as SearchResult).films;
const ANSTALT = load("search-die-anstalt.json");
const MAUS = load("search-maus-spezial-frankreich.json");
const BERLIN = "Europe/Berlin";
const NOW = Date.parse("2026-10-13T12:00:00Z");

const byId = (films: Film[], id: string) => {
  const film = films.find((x) => x.id === id);
  assert.ok(film, id);
  return film;
};
const ZDF_E08 = byId(ANSTALT, "316634119111183984");
const SAT_E08 = byId(ANSTALT, "6231108083154161763");
const ZDF_E07 = byId(ANSTALT, "5178004720710945336");

test("one file on two channels is one broadcast", () => {
  assert.notEqual(ZDF_E08.title, SAT_E08.title);
  assert.ok(sameFile(ZDF_E08, SAT_E08, BERLIN));
  assert.ok(!sameFile(ZDF_E08, ZDF_E07, BERLIN));
  const aWeekLater = { ...SAT_E08, start: new Date(Date.parse(SAT_E08.start ?? "") + 7 * 86_400_000).toISOString() };
  assert.ok(!sameFile(aWeekLater, ZDF_E08, BERLIN)); // an address kept for the "newest episode" is another broadcast
});

test("the broadcast day is the server's", () => {
  assert.equal(broadcastDay(ZDF_E08, BERLIN), "2026-10-06"); // 20:15 UTC, 22:15 in Berlin
  assert.equal(broadcastDay(ZDF_E08, "Pacific/Kiritimati"), "2026-10-07"); // UTC+14
  assert.equal(broadcastDay({ ...ZDF_E08, start: null }, BERLIN), "");
});

test("the list shows the file once, on the home channel", () => {
  const twins = mergeTwins(ANSTALT, ANSTALT, BERLIN);
  assert.equal(twins.shown.length, 31);
  assert.ok(twins.shown.includes(ZDF_E08) && !twins.shown.includes(SAT_E08)); // ZDF has 27 hits on the topic, 3sat 1
  assert.deepEqual(twins.of.get(SAT_E08.id), [ZDF_E08, SAT_E08]);
  assert.equal(twins.of.get(ZDF_E08.id), twins.of.get(SAT_E08.id));
  assert.equal(twins.of.size, 2);
  const anstalt = groupByTopic(twins.shown).find((g) => g.topic === "Die Anstalt");
  assert.deepEqual(anstalt?.channels, [["ZDF", 27]]); // was "ZDF 27 · 3Sat 1"
});

test("filters see every channel, the merge only what passes", () => {
  const sat = applyFilters(ANSTALT, parseFilters({ q: "die anstalt", sender: "3Sat" }), NOW);
  assert.deepEqual(mergeTwins(sat, ANSTALT, BERLIN).shown, [SAT_E08]);
  const e08 = applyFilters(ANSTALT, parseFilters({ q: "die anstalt", titel: "S2026/E08" }), NOW);
  assert.deepEqual(mergeTwins(e08, ANSTALT, BERLIN).shown, [ZDF_E08]);
});

test("a shared HD file is enough; the plain version stays a row of its own", () => {
  const twins = mergeTwins(MAUS, MAUS, BERLIN);
  assert.deepEqual(
    twins.shown.map((x) => `${x.channel}: ${x.title}`),
    // WDR has two hits on the topic, ARD one: WDR's title names the audio-described file.
    ["WDR: Maus-Spezial: Frankreich (Audiodeskription)", "WDR: Maus-Spezial: Frankreich"],
  );
  assert.deepEqual(
    twins.of.get("1765258727088297454")?.map((x) => x.channel),
    ["WDR", "ARD"],
  );
});

test("a plain version names the file before a special one", () => {
  // Derived from the real entry: the same file with a longer, audio-described title (the plain one must win).
  const described = { ...ZDF_E08, id: "1", title: `${ZDF_E08.title} (Audiodeskription)` };
  assert.deepEqual(mergeTwins([described, ZDF_E08], ANSTALT, BERLIN).shown, [ZDF_E08]);
  // One hit each on the topic: the longer title, which names the episode ("Die Anstalt – Jetzt erst rechts! …").
  assert.deepEqual(mergeTwins([SAT_E08, ZDF_E08], [SAT_E08, ZDF_E08], BERLIN).shown, [ZDF_E08]);
});

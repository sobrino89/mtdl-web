import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import type { PreviewItem, SearchResult } from "../src/api";
import { comparePreview, nextPreviewSort, parsePreviewSort } from "../src/preview-sort";

// The reference case: the 35 hits of "das gipfeltreffen", the numbered episodes as files to load.
const films = (JSON.parse(readFileSync("test/fixtures/search-das-gipfeltreffen.json", "utf8")) as SearchResult).films;
const items: PreviewItem[] = films.map((film) => {
  const number = /\((\d+)\)$/.exec(film.title)?.[1];
  return {
    film,
    status: number ? "load" : "excluded",
    excluded_by: number ? null : 1,
    path: number ? `Das Gipfeltreffen/Season 01/Das Gipfeltreffen - S01E${number} - ${film.title}.mp4` : null,
    duplicate_of: null,
    download_id: null,
    results: [],
  };
});
const sorted = (value: string) => [...items].sort(comparePreview(parsePreviewSort(value)));

test("newest first unless a column was chosen", () => {
  assert.deepEqual(parsePreviewSort(undefined), { key: "datum", desc: true });
  assert.deepEqual(parsePreviewSort("unsinn-asc"), { key: "datum", desc: true });
  const starts = sorted("").map((i) => i.film.start ?? "");
  assert.deepEqual(starts, [...starts].sort().reverse());
});

test("a click flips the column, another column starts in its natural order", () => {
  assert.equal(nextPreviewSort({ key: "datum", desc: true }, "datum"), "datum-asc");
  assert.equal(nextPreviewSort({ key: "datum", desc: true }, "titel"), "titel-asc");
  assert.equal(nextPreviewSort({ key: "titel", desc: false }, "dauer"), "dauer-desc");
});

test("columns sort as their values: names alphabetically, numbers numerically", () => {
  const byTarget = sorted("ziel-asc").filter((i) => i.path);
  const episodes = byTarget.map((i) => Number(/S01E(\d+)/.exec(i.path ?? "")?.[1]));
  assert.deepEqual(episodes, [...episodes].sort((a, b) => a - b)); // "S01E9" would come before "S01E10"
  const durations = sorted("dauer-desc").map((i) => i.film.duration ?? 0);
  assert.deepEqual(durations, [...durations].sort((a, b) => b - a));
  const titles = sorted("titel-asc").map((i) => i.film.title);
  assert.deepEqual(titles, [...titles].sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" })));
  const channels = sorted("sender-desc").map((i) => i.film.channel);
  assert.deepEqual(channels, [...channels].sort((a, b) => b.localeCompare(a, undefined, { sensitivity: "base" })));
});

// Suggestions against real search results (production film list, 13.10.2026). Expected values counted by hand and
// with jq on the fixtures, not derived from the code under test.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import type { Film, SearchResult } from "../src/api";
import { learn, NUMBERED, passes, suggestFromSearch } from "../src/suggest";

const load = (name: string) => (JSON.parse(readFileSync(`test/fixtures/${name}`, "utf8")) as SearchResult).films;
const gipfel = load("search-das-gipfeltreffen.json");
const werwoelfe = load("search-werwoelfe.json");
const byTitle = (films: Film[], title: string) => films.find((f) => f.title === title) as Film;

test("reference case: topic and episode number from the search", () => {
  const newest = byTitle(gipfel, "Das Beste kommt noch! (58)");
  for (const from of [newest, null]) {
    const start = suggestFromSearch(gipfel, "das gipfeltreffen", from);
    assert.deepEqual(
      start.conditions.map((c) => [c.field, c.operator, c.value, c.regex]),
      [
        ["topic", "is", "Das Gipfeltreffen", false],
        ["title", "is", NUMBERED, true],
      ],
    );
    assert.equal(start.name, "Das Gipfeltreffen");
    assert.equal(start.scheme, "episode_number");
  }
  // The 20 episodes 39 to 58 pass both rules.
  const start = suggestFromSearch(gipfel, "das gipfeltreffen", newest);
  const kept = gipfel.filter((f) => start.conditions.every((c) => passes(c, f)));
  assert.equal(kept.length, 20);
});

test("season and episode in the title: Werwölfe and Kurzstrecke", () => {
  const start = suggestFromSearch(werwoelfe, "werwölfe", byTitle(werwoelfe, "Folge 3: Was für ein Theater (S02/E03)"));
  assert.deepEqual(
    start.conditions.map((c) => [c.field, c.value]),
    [
      ["topic", "Werwölfe · Das Spiel von List und Täuschung"],
      ["title", String.raw`\(S\d+/E\d+\)$`],
    ],
  );
  assert.equal(start.scheme, "season_episode");
  assert.equal(werwoelfe.filter((f) => start.conditions.every((c) => passes(c, f))).length, 24);
  const kurz = suggestFromSearch(gipfel, "das gipfeltreffen", byTitle(gipfel, "Kurzstrecke mit Pierre M. Krause - Johann König braucht das Gipfeltreffen nicht"));
  assert.equal(kurz.name, "Kurzstrecke mit Pierre M. Krause");
  assert.equal(kurz.scheme, "season_episode");
});

test("thumbs down on a Best of suggests 'title does not contain Best of'", () => {
  const topic = gipfel.filter((f) => f.topic === "Das Gipfeltreffen");
  const down = [byTitle(gipfel, "Das Gipfeltreffen – Best of 2025")];
  const lesson = learn(down, topic.filter((f) => !down.includes(f)), ["Das Gipfeltreffen"], []);
  assert.ok(lesson && lesson.kind === "text");
  assert.equal(lesson.value, "Best of");
  assert.equal(lesson.many, false);
  const left = topic.filter((f) => passes(lesson.condition, f));
  assert.equal(topic.length - left.length, 5); // every Best of in the topic
  assert.equal(left.length, 20);
  // A thumbs-up hit that contains the words is a counterexample.
  const up = [byTitle(gipfel, "Das Gipfeltreffen – Best of 2024 (2/2)")];
  assert.equal(learn(down, topic.filter((f) => !down.includes(f)), ["Das Gipfeltreffen"], up)?.kind, "none");
});

test("thumbs down on trailers and clips", () => {
  const topic = werwoelfe.filter((f) => f.topic === "Werwölfe · Das Spiel von List und Täuschung");
  const trailers = topic.filter((f) => f.title.startsWith("Trailer"));
  const text = learn(trailers, topic.filter((f) => !trailers.includes(f)), ["Werwölfe · Das Spiel von List und Täuschung"], []);
  assert.ok(text && text.kind === "text" && text.value === "Trailer" && text.many);
  const short = topic.filter((f) => (f.duration ?? 0) < 120);
  assert.equal(short.length, 4);
  const clips = learn(short, topic.filter((f) => !short.includes(f)), ["Werwölfe · Das Spiel von List und Täuschung"], []);
  assert.ok(clips && clips.kind === "duration");
  assert.equal(clips.minutes, 8); // between 1 min (longest clip) and 15 min (shortest kept)
  assert.equal(clips.shortest, 15);
});

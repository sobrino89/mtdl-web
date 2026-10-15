import assert from "node:assert/strict";
import { test } from "node:test";

import { build, parse } from "../src/router";

test("parse splits view, selection and filters", () => {
  const route = parse("#/suche?q=das+gipfeltreffen&in=titel");
  assert.equal(route.view, "suche");
  assert.deepEqual(route.params, []);
  assert.deepEqual(route.query, { q: "das gipfeltreffen", in: "titel" });
  assert.equal(route.path, "/suche");

  const editor = parse("#/abos/12");
  assert.equal(editor.view, "abos");
  assert.deepEqual(editor.params, ["12"]);
});

test("empty hash is the start view", () => {
  assert.equal(parse("").view, "");
  assert.equal(parse("#/").path, "/");
});

test("build drops empty filters and round-trips", () => {
  const hash = build("/suche", { q: "das gipfeltreffen", hd: true, ut: false, sender: "", id: null });
  assert.equal(hash, "#/suche?q=das+gipfeltreffen&hd=1");
  assert.deepEqual(parse(hash).query, { q: "das gipfeltreffen", hd: "1" });
});

test("special characters survive the hash", () => {
  const hash = build("/suche", { titel: "\\(\\d+\\)$", thema: "Werwölfe · Das Spiel" });
  const route = parse(hash);
  assert.equal(route.query.titel, "\\(\\d+\\)$");
  assert.equal(route.query.thema, "Werwölfe · Das Spiel");
});

test("encoded path segments are decoded once", () => {
  const route = parse("#/abos/neu%20test");
  assert.deepEqual(route.params, ["neu test"]);
  assert.equal(route.path, "/abos/neu%20test");
});

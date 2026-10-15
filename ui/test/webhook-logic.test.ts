import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError } from "../src/api";
import {
  FIELDS,
  headerPayload,
  headerProblem,
  headerRow,
  insertAt,
  keepsValue,
  PRESETS,
  problemOf,
  storedRows,
  withPresetHeaders,
} from "../src/webhook-logic";

test("stored headers keep their value unless one is typed in", () => {
  const [key, title] = storedRows(["X-Api-Key", "Title"]);
  assert.ok(keepsValue(key));
  const rows = [key, { ...title, value: "mtdl-web" }, headerRow("X-Source", "nas")];
  assert.deepEqual(headerPayload(rows), { "X-Api-Key": null, Title: "mtdl-web", "X-Source": "nas" });
  const renamed = { ...key, name: "X-Key" }; // the stored value belongs to the old name
  assert.equal(keepsValue(renamed), false);
  assert.equal(headerProblem(renamed, [renamed]), "missing");
  assert.deepEqual(headerPayload([headerRow(), key]), { "X-Api-Key": null }); // empty rows are left out
});

test("header rows the server would refuse", () => {
  const check = (name: string, value: string, ...others: [string, string][]) => {
    const row = headerRow(name, value);
    return headerProblem(row, [row, ...others.map(([n, v]) => headerRow(n, v))]);
  };
  assert.equal(check("X-Api-Key", "0123abcd"), null);
  assert.equal(check("", ""), null);
  assert.equal(check("X Api Key", "a"), "name");
  assert.equal(check("Content-Length", "3"), "reserved");
  assert.equal(check("x-mtdl-signature", "a"), "reserved");
  assert.equal(check("X-Key", "a", ["x-key", "b"]), "duplicate");
  assert.equal(check("X-Key", ""), "missing");
  assert.equal(check("X-Key", "a\r\nX-Evil: 1"), "value");
  assert.equal(check("X-Key", "Grüße"), "value");
  assert.equal(check("X-Key", " \t "), "missing");
  assert.equal(check("X-Key", "abc123 "), null); // trimmed like the server does
  assert.deepEqual(headerPayload([headerRow("X-Key", "\tabc123 ")]), { "X-Key": "abc123" });
});

test("examples add their headers once and set fixed values", () => {
  const sonarr = PRESETS.find((p) => p.id === "sonarr");
  const ntfy = PRESETS.find((p) => p.id === "ntfy");
  assert.ok(sonarr && ntfy);
  const added = withPresetHeaders([headerRow()], sonarr);
  assert.deepEqual(
    added.map((r) => [r.name, r.value]),
    [["X-Api-Key", ""]],
  );
  const stored = storedRows(["x-api-key"]);
  assert.deepEqual(withPresetHeaders(stored, sonarr), stored); // the stored key stays, no second row
  const plain = withPresetHeaders([headerRow("content-type", "application/json")], ntfy);
  assert.deepEqual(
    plain.map((r) => [r.name, r.value]),
    [
      ["content-type", "text/plain; charset=utf-8"],
      ["Title", "mtdl-web"],
    ],
  );
});

test("JSON examples place placeholders inside strings only, so they stay valid JSON", () => {
  for (const preset of PRESETS) {
    const text = preset.headers.some(([name, value]) => name.toLowerCase() === "content-type" && !value.includes("json"));
    if (text) continue;
    assert.doesNotThrow(() => JSON.parse(preset.template.replace(/\{\{[\w.]+\}\}/g, "x")), preset.id);
  }
  for (const field of FIELDS) assert.match(field, /^(event|time|data\.\w+)$/);
});

test("placeholders go where the caret is", () => {
  assert.deepEqual(insertAt('{"a": ""}', 7, 7, "{{data.title}}"), { text: '{"a": "{{data.title}}"}', caret: 21 });
  assert.deepEqual(insertAt("x NAME y", 2, 6, "{{event}}"), { text: "x {{event}} y", caret: 11 });
});

test("what the server refused, at the field it concerns", () => {
  const json = problemOf(new ApiError(422, { code: "invalid_json", line: 3, column: 3, message: "Expecting ',' delimiter" }));
  assert.equal(json.field, "template");
  assert.match(json.text, /Zeile 3, Spalte 3/);
  const unknown = problemOf(new ApiError(422, { code: "unknown_placeholders", placeholders: ["data.nope", "secret"] }));
  assert.deepEqual([unknown.field, unknown.text], ["template", "Unbekannte Platzhalter: data.nope, secret"]);
  const headers = problemOf(new ApiError(422, { code: "invalid_headers", headers: ["X-Other"] }));
  assert.deepEqual([headers.field, headers.text], ["headers", "Ungültige Kopfzeilen: X-Other"]);
  assert.equal(problemOf(new ApiError(500, null)).field, null);
});

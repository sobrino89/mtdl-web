import assert from "node:assert/strict";
import { test } from "node:test";

import type { Condition } from "../src/api";
import { setLocale } from "../src/format";
import { defaultCondition, operatorsFor, reason, regexProblem, shortText, summary, TEMPLATES, templateOf } from "../src/rule-texts";

setLocale("de-DE");

// The reference case and the Werwölfe subscription, as stored by the backend.
const topic: Condition = { field: "topic", operator: "is", value: "Das Gipfeltreffen", regex: false, enabled: true };
const numbered: Condition = { field: "title", operator: "is", value: String.raw`\(\d+\)$`, regex: true, enabled: true };
const br: Condition = { field: "channel", operator: "any_of", value: ["BR"], regex: false, enabled: true };

test("short forms for the list", () => {
  assert.equal(summary([topic, numbered]), String.raw`Thema = Das Gipfeltreffen · Titel ~ \(\d+\)$`);
  assert.equal(shortText(br), "Sender BR");
  assert.equal(shortText({ ...br, operator: "none_of", value: ["KiKA", "funk"] }), "ohne KiKA, funk");
  assert.equal(shortText({ field: "duration", operator: "at_least", value: 20, regex: false }), "≥ 20 min");
  assert.equal(shortText({ field: "date", operator: "after", value: "2026-10-13", regex: false }), "ab 13.10.2026");
  assert.equal(shortText({ field: "title", operator: "not_contains", value: "Best of", regex: false }), "Titel ohne „Best of“");
  assert.equal(summary([{ ...topic, enabled: false }]), "keine Regeln");
});

test("templates are ordinary conditions and are recognised", () => {
  const today = "2026-10-13";
  const made = TEMPLATES.map((tpl) => tpl.condition(today));
  assert.deepEqual(made.map((c) => templateOf(c)), ["folgennummer", "staffelfolge", null, "fassungen", "clips"]);
  assert.equal(shortText(made[0] as Condition), "hat Folgennummer");
  assert.equal(shortText(made[2] as Condition), "ab 13.10.2026");
  assert.equal(reason(made[0] as Condition), "keine Folgennummer");
  assert.equal(templateOf(numbered), null); // "\(\d+\)$" is not the template's pattern
});

test("reasons for excluded hits", () => {
  assert.equal(reason(topic), "anderes Thema");
  assert.equal(reason(numbered), "Titel passt nicht");
  assert.equal(reason(br), "anderer Sender");
  assert.equal(reason({ field: "duration", operator: "at_least", value: 20, regex: false }), "zu kurz");
  assert.equal(reason({ field: "date", operator: "after", value: "2026-10-13", regex: false }), "vor 13.10.2026");
  assert.equal(reason(topic, { passed: false, code: "value_missing", actual: null, span: null }), "Thema fehlt");
});

test("operators and defaults per field", () => {
  assert.deepEqual(operatorsFor("title", true), ["is", "is_not"]);
  assert.deepEqual(operatorsFor("topic", false), ["is", "is_not", "contains", "not_contains"]);
  assert.deepEqual(operatorsFor("channel", false), ["any_of", "none_of"]);
  assert.deepEqual(defaultCondition("duration", "2026-10-13"), { field: "duration", operator: "at_least", value: 20, regex: false, enabled: true });
  assert.deepEqual(defaultCondition("date", "2026-10-13").value, "2026-10-13");
});

test("regex errors in words, marking the culprit", () => {
  // Messages and positions as the backend's regex library reports them (probed 13.10.2026).
  assert.deepEqual(regexProblem(String.raw`(\d+$`, "missing )", 5), { position: 0, text: "Klammer „(“ wird nicht geschlossen." });
  assert.deepEqual(regexProblem(String.raw`\(\d+)$`, "unbalanced parenthesis", 5), { position: 5, text: "Klammer „)“ ohne öffnende Klammer." });
  assert.equal(regexProblem("a|*b", "nothing to repeat", 2).text, "„*“ steht ohne etwas davor, das wiederholt werden kann.");
  assert.equal(regexProblem("[abc", "unterminated character set", 4).position, 0);
  assert.deepEqual(regexProblem("abc\\", "bad escape (end of pattern)", 4), { position: 3, text: "„\\“ am Ende braucht ein Zeichen dahinter." });
  assert.equal(regexProblem("a{2,1}", "min repeat greater than max repeat", 2).text, "Bei {m,n} muss m höchstens n sein.");
  assert.equal(regexProblem("x", "something new", 9).position, null);
});

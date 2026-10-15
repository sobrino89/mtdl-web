import assert from "node:assert/strict";
import { test } from "node:test";

import { unknownTokens } from "../src/naming";

test("template placeholders in both languages, unknown ones reported", () => {
  assert.deepEqual(unknownTokens("{thema}/Staffel {staffel}/{thema} - S{staffel}E{folge} - {titel}.{ext}"), []);
  assert.deepEqual(unknownTokens("{topic}/{season}/{episode} {title} {date} {year} {channel} {datum} {jahr} {sender}"), []);
  assert.deepEqual(unknownTokens("{genre}/{thema}/{Folge}"), ["genre", "Folge"]);
});

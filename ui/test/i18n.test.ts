import assert from "node:assert/strict";
import { test } from "node:test";

import { resolveLanguage, type Vars } from "../src/i18n";
import { de } from "../src/i18n/de";
import { en } from "../src/i18n/en";

// Literal braces meant for display (naming placeholders differ by language on purpose).
const LITERAL = new Set(["ed.scheme.template_ex", "ed.template_placeholders"]);
const placeholders = (text: string) => new Set([...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]));

test("both catalogs have the same keys and placeholders", () => {
  assert.deepEqual(Object.keys(en).sort(), Object.keys(de).sort());
  for (const [key, german] of Object.entries(de)) {
    const english = en[key as keyof typeof de];
    if (typeof german === "string" && typeof english === "string") {
      if (!LITERAL.has(key)) assert.deepEqual(placeholders(english), placeholders(german), key);
    } else {
      assert.equal(typeof english, typeof german, key);
    }
  }
});

test("plural messages work in both languages", () => {
  const vars: Vars = { n: 1, size: "1 GB", checked: 2, queued: 0, name: "Werwölfe", shortest: 15, minutes: 8 };
  for (const catalog of [de, en]) {
    for (const [key, message] of Object.entries(catalog)) {
      if (typeof message !== "function") continue;
      for (const n of [0, 1, 3]) {
        const text = message({ ...vars, n });
        assert.ok(text.length > 0 && !text.includes("undefined"), `${key}: ${text}`);
      }
    }
  }
  assert.equal(en["subs.checked_all"] instanceof Function && en["subs.checked_all"](vars), "2 subscriptions checked · no new episodes");
  assert.equal(de["hist.summary"] instanceof Function && de["hist.summary"]({ n: 1, size: "5 MB" }), "1 Eintrag · 5 MB");
});

test("German unless the browser prefers English", () => {
  assert.equal(resolveLanguage("auto", ["en-US", "de"]), "en");
  assert.equal(resolveLanguage("auto", ["fr-FR", "de-AT", "en"]), "de");
  assert.equal(resolveLanguage("auto", ["fr-FR"]), "de");
  assert.equal(resolveLanguage("en", ["de-DE"]), "en");
  assert.equal(resolveLanguage("de", ["en-GB"]), "de");
});

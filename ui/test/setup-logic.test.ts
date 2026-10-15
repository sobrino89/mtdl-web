// The guide of the first start: its steps per access mode and the film list's state from the polled status.
import assert from "node:assert/strict";
import { test } from "node:test";

import type { FilmlistStatus } from "../src/api";
import { guideSteps, listStage, stepOf } from "../src/setup-logic";

const IDLE: FilmlistStatus = {
  films: 0,
  last_import: null,
  running: false,
  last_run: null,
  last_error: null,
  next_run: null,
  backoff_until: null,
  progress: null,
};

test("the password comes first only where the guide asks for it", () => {
  assert.deepEqual(guideSteps({ mode: "password", password_from_env: false }), ["passwort", "ziel", "filmliste", "benennung"]);
  assert.deepEqual(guideSteps({ mode: "password", password_from_env: true }), ["ziel", "filmliste", "benennung"]);
  for (const mode of ["local", "proxy", "none"] as const) {
    assert.deepEqual(guideSteps({ mode, password_from_env: false }), ["ziel", "filmliste", "benennung"]);
  }
  assert.equal(stepOf("filmliste"), "filmliste");
  assert.equal(stepOf("passwort"), "ziel"); // the password is not a place in the app
  assert.equal(stepOf(undefined), "ziel");
});

test("the film list as the guide shows it", () => {
  assert.deepEqual(listStage(undefined), { phase: "wait", fraction: null });
  assert.deepEqual(listStage(IDLE), { phase: "wait", fraction: null });
  assert.deepEqual(listStage({ ...IDLE, running: true }), { phase: "download", fraction: null }); // starting
  const download = { ...IDLE, running: true, progress: { phase: "download" as const, done: 19_000_000, total: 38_000_000 } };
  assert.deepEqual(listStage(download), { phase: "download", fraction: 0.5 });
  const first = { ...IDLE, running: true, progress: { phase: "import" as const, done: 300_000, total: null } };
  assert.deepEqual(listStage(first), { phase: "import", fraction: null }); // the very first list: no end known
  const again = { ...IDLE, running: true, progress: { phase: "import" as const, done: 730_000, total: 720_000 } };
  assert.deepEqual(listStage(again), { phase: "import", fraction: 1 }); // more than last time: full, not beyond
  assert.deepEqual(listStage({ ...IDLE, films: 719_925 }), { phase: "ready", fraction: 1 });
  assert.deepEqual(listStage({ ...IDLE, last_error: "FetchError: HTTP 503" }), { phase: "error", fraction: null });
});

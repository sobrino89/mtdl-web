import assert from "node:assert/strict";
import { test } from "node:test";

import type { HookRun, HookScript } from "../src/api";
import { EXAMPLE_SCRIPT, hookStatus } from "../src/hook-texts";
import { setLocale } from "../src/format";

setLocale("de-DE");
const TZ = "Europe/Berlin";

function script(executable: boolean, run: Partial<HookRun> | null): HookScript {
  const last_run = run && { started_at: "2026-10-13T02:30:00Z", seconds: 1.234, exit_code: 0, output: "", error: null, ...run };
  return { event: "download.completed", name: "10-move.sh", executable, last_run };
}

test("status of a hook script in the settings", () => {
  assert.deepEqual(hookStatus(script(false, { exit_code: 0 }), TZ), { tone: "muted", text: "aus (nicht ausführbar)" });
  assert.deepEqual(hookStatus(script(true, null), TZ), { tone: "muted", text: "noch nicht gelaufen" });
  const done = hookStatus(script(true, {}), TZ);
  assert.equal(done.tone, "ok");
  assert.match(done.text, /^✓ 13\.10\. 04:30 · 1,23 s$/);
  assert.deepEqual(hookStatus(script(true, { exit_code: 3 }), TZ).tone, "err");
  assert.match(hookStatus(script(true, { exit_code: 3 }), TZ).text, /^Exit-Code 3 · /);
  assert.match(hookStatus(script(true, { exit_code: null, error: "timeout after 600 s" }), TZ).text, /^nach Zeitüberschreitung beendet/);
  const broken = hookStatus(script(true, { exit_code: null, error: "OSError: [Errno 8] Exec format error" }), TZ);
  assert.match(broken.text, /^nicht gestartet: OSError: \[Errno 8\] Exec format error · /);
});

test("the example script uses the shell and the documented variables", () => {
  assert.ok(EXAMPLE_SCRIPT.startsWith("#!/bin/sh\n"));
  for (const name of EXAMPLE_SCRIPT.match(/\$MTDL_\w+/g) ?? []) assert.ok(["$MTDL_EVENT", "$MTDL_TITLE", "$MTDL_FILE"].includes(name));
});

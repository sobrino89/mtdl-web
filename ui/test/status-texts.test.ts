import assert from "node:assert/strict";
import { test } from "node:test";

import type { Settings, Status } from "../src/api";
import { setLocale, weekdayShort } from "../src/format";
import { dayRanges, downloadTexts, filmlistTexts, limitAction, limitTexts, limitToggle, scheduleSegments, scheduleSummary } from "../src/status-texts";

setLocale("de-DE");

/** The limit as the server reports it: the rate in effect follows the normal limit unless a test sets it. */
function limit(patch: Partial<Status["limit"]> = {}): Status["limit"] {
  const base = { bandwidth_limit: 0, bandwidth_enabled: true, speed_unit: "Mbit/s" as const, source: "normal" as const, until: null, ...patch };
  return { rate: base.bandwidth_enabled ? base.bandwidth_limit : 0, ...base };
}

function status(patch: { filmlist?: Partial<Status["filmlist"]>; downloads?: Partial<Status["downloads"]>; limit?: Partial<Status["limit"]> } = {}): Status {
  return {
    version: "0.1.0",
    filmlist: {
      films: 722672,
      last_import: { kind: "diff", list_id: "x", created: "2026-10-12T21:33:00Z", added: 1, changed: 0, removed: 0, total: 722672 },
      running: false,
      last_run: null,
      last_error: null,
      next_run: null,
      backoff_until: null,
      ...patch.filmlist,
    },
    downloads: { counts: {}, running: [], speed: 0, progress: null, ...patch.downloads },
    limit: limit(patch.limit),
    subscriptions: 1,
    writable: { config: null, downloads: null },
    problems: [],
  };
}

test("film list line in the server time zone", () => {
  assert.deepEqual(filmlistTexts(status(), "Europe/Berlin"), {
    dot: "ok",
    text: "MediathekView-Filmliste · Stand 12.10. 23:33 · 722.672 Einträge",
    short: "Stand 12.10. 23:33 · 722.672 Einträge",
    stale: false,
  });
  const failed = filmlistTexts(status({ filmlist: { last_error: "HTTP 503" } }), "Europe/Berlin");
  assert.equal(failed.dot, "warn");
  assert.equal(failed.stale, true);
  assert.equal(failed.text, "MediathekView-Filmliste · Stand 12.10. 23:33 · Abruf fehlgeschlagen: HTTP 503");
  assert.equal(filmlistTexts(status({ filmlist: { films: 0, last_import: null, running: true } })).text, "Erste Filmliste wird geladen …");
  assert.equal(filmlistTexts(null).text, "Lädt …");
});

test("download summary", () => {
  assert.equal(downloadTexts(status()).text, "Keine Downloads");
  assert.equal(downloadTexts(status({ downloads: { counts: { queued: 3 } } })).text, "3 warten");
  const busy = downloadTexts(status({ downloads: { counts: { running: 2, queued: 1 }, progress: 0.427 } }));
  assert.deepEqual(busy, { text: "2 laden · 1 warten", short: "2 laden · 43 %", active: true });
});

test("no value means no limit, whatever the switch says", () => {
  assert.deepEqual(limitTexts(status()), { on: false, text: "Kein Limit", short: "unbegrenzt", title: "Bandbreitenlimit ändern" });
  const on = limitTexts(status({ limit: { bandwidth_limit: 2_500_000, speed_unit: "MB/s" } }));
  assert.deepEqual(on, { on: true, text: "Limit 2,5 MB/s", short: "2,5 MB/s", title: "Bandbreitenlimit ändern" });
  assert.equal(limitTexts(status({ limit: { bandwidth_limit: 2_500_000, bandwidth_enabled: false } })).on, false);
});

test("the switch turns the limit on with 50 Mbit/s when none is set", () => {
  assert.deepEqual(limitToggle(status().limit), { bandwidth_enabled: true, bandwidth_limit: 6_250_000 });
  assert.deepEqual(limitToggle(status({ limit: { bandwidth_limit: 1_000_000 } }).limit), { bandwidth_enabled: false });
  assert.deepEqual(limitToggle(status({ limit: { bandwidth_limit: 1_000_000, bandwidth_enabled: false } }).limit), {
    bandwidth_enabled: true,
  });
});

test("the limit by schedule in the status bar", () => {
  const now = new Date("2026-10-13T09:00:00Z"); // Tuesday, 11:00 in Berlin
  const tz = "Europe/Berlin";
  const office = limit({ rate: 2_500_000, source: "schedule", until: "2026-10-13T16:00:00Z" });
  assert.deepEqual(limitTexts(status({ limit: office }), tz, now), {
    on: true,
    text: "20 Mbit/s · Zeitplan bis 18:00",
    short: "20 Mbit/s",
    title: "Nach Zeitplan. Der Schalter öffnet das Limit bis zum Ende des Zeitfensters.",
  });
  const paused = limit({ rate: 0, source: "paused", until: "2026-10-13T16:00:00Z" });
  assert.equal(limitTexts(status({ limit: paused }), tz, now).text, "Offen bis 18:00 · Zeitplan pausiert");
  const evening = limit({ until: "2026-10-14T06:00:00Z" }); // open, the next window tomorrow at 8:00
  assert.equal(limitTexts(status({ limit: evening }), tz, now).title, `Zeitplan ab ${weekdayShort(2)} 08:00`);
  assert.deepEqual(limitAction(office), { pause: true });
  assert.deepEqual(limitAction(paused), { pause: false });
  assert.deepEqual(limitAction(evening), { settings: { bandwidth_enabled: true, bandwidth_limit: 6_250_000 } });
});

test("a schedule day as bar and as sentence", () => {
  assert.deepEqual(scheduleSegments("08:00", "18:00"), [
    { from: 0, to: 480, limited: false },
    { from: 480, to: 1080, limited: true },
    { from: 1080, to: 1440, limited: false },
  ]);
  assert.deepEqual(scheduleSegments("22:00", "06:00"), [
    { from: 0, to: 360, limited: true },
    { from: 360, to: 1320, limited: false },
    { from: 1320, to: 1440, limited: true },
  ]);
  assert.deepEqual(scheduleSegments("00:00", "00:00"), [{ from: 0, to: 1440, limited: true }]);
  const [mo, , mi, , fr, sa, so] = [0, 1, 2, 3, 4, 5, 6].map(weekdayShort); // "Mo" or "Mo.", as the ICU data has it
  assert.equal(dayRanges([0, 1, 2, 3, 4]), `${mo}–${fr}`);
  assert.equal(dayRanges([5, 6]), `${sa}, ${so}`);
  assert.equal(dayRanges([4, 0, 2]), `${mo}, ${mi}, ${fr}`);
  const settings = {
    bandwidth_limit: 0,
    bandwidth_enabled: true,
    speed_unit: "Mbit/s",
    schedule_days: [0, 1, 2, 3, 4],
    schedule_start: "08:00",
    schedule_end: "18:00",
    schedule_limit: 2_500_000,
  } as Settings;
  assert.equal(scheduleSummary(settings), `${mo}–${fr} von 08:00 bis 18:00: 20 Mbit/s, sonst unbegrenzt`);
  assert.equal(scheduleSummary({ ...settings, schedule_days: [5, 6], schedule_end: "08:00" }), `${sa}, ${so} ganztägig: 20 Mbit/s, sonst unbegrenzt`);
});

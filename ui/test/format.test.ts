import assert from "node:assert/strict";
import { test } from "node:test";

import {
  clock,
  date,
  dateTime,
  duration,
  eta,
  fromBytesPerSecond,
  parseDecimal,
  percent,
  setLocale,
  size,
  speed,
  time,
  toBytesPerSecond,
} from "../src/format";

setLocale("de-DE");

test("duration in whole minutes", () => {
  assert.equal(duration(null), "–");
  assert.equal(duration(20), "1 min");
  assert.equal(duration(29 * 60 + 57), "30 min");
  assert.equal(duration(61 * 60), "61 min");
  assert.equal(duration(2 * 3600 + 15 * 60), "135 min");
});

test("clock and eta", () => {
  assert.equal(clock(1797), "29:57");
  assert.equal(clock(3725), "1:02:05");
  assert.equal(eta(95), "1:35 min");
  assert.equal(eta(3 * 3600 + 120), "3 h 02 min");
  assert.equal(eta(Number.POSITIVE_INFINITY), "–");
});

test("sizes are decimal like the speeds", () => {
  assert.equal(size(null), "–");
  assert.equal(size(400_000), "1 MB");
  assert.equal(size(812_400_000), "812 MB");
  assert.equal(size(1_540_000_000), "1,5 GB");
  assert.equal(size(3_600_000_000_000), "3,6 TB");
});

test("speed units: 1 MB/s is 8 Mbit/s", () => {
  assert.equal(speed(2_500_000, "MB/s"), "2,5 MB/s");
  assert.equal(speed(2_500_000, "Mbit/s"), "20,0 Mbit/s");
  assert.equal(toBytesPerSecond(20, "Mbit/s"), 2_500_000);
  assert.equal(toBytesPerSecond(2.5, "MB/s"), 2_500_000);
  assert.equal(fromBytesPerSecond(2_500_000, "Mbit/s"), 20);
  assert.equal(fromBytesPerSecond(2_500_000, "MB/s"), 2.5);
});

test("decimal input accepts comma and point", () => {
  assert.equal(parseDecimal("12,5"), 12.5);
  assert.equal(parseDecimal(" 7.25 "), 7.25);
  assert.equal(parseDecimal(""), null);
  assert.ok(Number.isNaN(parseDecimal("zwölf")));
});

test("dates use the server time zone", () => {
  // 19:30 UTC is 21:30 in Berlin (summer time); a late film belongs to the Berlin day.
  assert.equal(date("2026-10-12T19:30:00+00:00", "Europe/Berlin"), "12.10.2026");
  assert.equal(time("2026-10-12T19:30:00+00:00", "Europe/Berlin"), "21:30");
  assert.equal(date("2026-10-12T22:30:00+00:00", "Europe/Berlin"), "13.10.2026");
  assert.equal(dateTime("2026-10-12T19:30:00+00:00", "Europe/Berlin"), "12.10. 21:30");
  assert.equal(date(null), "–");
  assert.equal(percent(0.427), "43 %");
});

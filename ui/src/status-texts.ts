// What the status bar and the phone header say about film list, downloads and the bandwidth limit (pure, tested).
import type { api, Limit, Settings, Status } from "./api";
import * as F from "./format";
import { t } from "./i18n";

export function filmlistTexts(
  status: Status | null,
  timeZone?: string,
): { dot: string; text: string; short: string; stale: boolean } {
  const fl = status?.filmlist;
  if (!fl) return { dot: "muted", text: t("app.loading"), short: t("app.loading"), stale: false };
  if (fl.running && !fl.films) return { dot: "accent", text: t("fl.first"), short: t("fl.first"), stale: false };
  const created = fl.last_import?.created;
  const when = created ? `${F.dateShort(created, timeZone)} ${F.time(created, timeZone)}` : "–";
  if (fl.last_error) {
    return {
      dot: "warn",
      text: t("fl.stale", { date: when, error: fl.last_error }),
      short: t("fl.stale_short", { date: when }),
      stale: true,
    };
  }
  if (fl.running) {
    return { dot: "accent", text: t("fl.updating", { date: when }), short: t("fl.updating", { date: when }), stale: false };
  }
  if (!fl.films) return { dot: "muted", text: t("fl.none"), short: t("fl.none"), stale: false };
  const count = F.num(fl.films);
  return { dot: "ok", text: t("fl.ok", { date: when, count }), short: t("fl.short", { date: when, count }), stale: false };
}

export function downloadTexts(status: Status | null): { text: string; short: string; active: boolean } {
  const counts = status?.downloads.counts ?? {};
  const running = counts.running ?? 0;
  const queued = counts.queued ?? 0;
  if (running) {
    const progress = status?.downloads.progress;
    const text = t("dl.summary", { run: running, wait: queued });
    return {
      text,
      short: progress != null ? t("dl.short_progress", { run: running, pct: Math.round(progress * 100) }) : text,
      active: true,
    };
  }
  if (queued) return { text: t("dl.waiting", { n: queued }), short: t("dl.waiting", { n: queued }), active: false };
  return { text: t("dl.none"), short: "", active: false };
}

export function limitTexts(status: Status | null, timeZone?: string, now?: Date): { on: boolean; text: string; short: string; title: string } {
  const limit = status?.limit;
  if (!limit) return { on: false, text: "", short: "", title: "" };
  const on = limit.rate > 0;
  const value = F.dec(F.fromBytesPerSecond(limit.rate, limit.speed_unit));
  const short = on ? `${value} ${limit.speed_unit}` : t("limit.unlimited");
  const when = limit.until ? F.moment(limit.until, timeZone, now) : "";
  if (limit.source === "schedule") return { on, text: t("limit.schedule", { value: short, time: when }), short, title: t("limit.schedule_title") };
  if (limit.source === "paused") return { on, text: t("limit.paused", { time: when }), short, title: t("limit.paused_title") };
  const text = on ? t("limit.text", { value, unit: limit.speed_unit }) : t("limit.off");
  return { on, text, short, title: when ? t("limit.next", { time: when }) : t("limit.button") };
}

/** The switch shows whether a limit is in effect; switching on without a value starts at 50 Mbit/s. */
/** What the limit switch does: inside a schedule window it pauses or resumes the schedule, else it switches the
 * normal limit. */
export function limitAction(limit: Limit): { pause: boolean } | { settings: Parameters<typeof api.saveSettings>[0] } {
  if (limit.source === "schedule") return { pause: true };
  if (limit.source === "paused") return { pause: false };
  return { settings: limitToggle(limit) };
}

export interface Segment {
  from: number; // minutes after midnight
  to: number;
  limited: boolean;
}

/** One schedule day as segments of the 24-hour bar; a window past midnight wraps, equal times fill the day. */
export function scheduleSegments(start: string, end: string): Segment[] {
  const minutes = (text: string) => Number(text.slice(0, 2)) * 60 + Number(text.slice(3, 5));
  const s = minutes(start);
  const e = minutes(end);
  const parts: Segment[] =
    s === e
      ? [{ from: 0, to: 1440, limited: true }]
      : s < e
        ? [
            { from: 0, to: s, limited: false },
            { from: s, to: e, limited: true },
            { from: e, to: 1440, limited: false },
          ]
        : [
            { from: 0, to: e, limited: true },
            { from: e, to: s, limited: false },
            { from: s, to: 1440, limited: true },
          ];
  return parts.filter((p) => p.to > p.from);
}

/** "Mo.–Fr.", "Sa.–So.", "Mo., Mi., Fr.": consecutive days as one range. */
export function dayRanges(days: number[]): string {
  const sorted = [...new Set(days)].sort((a, b) => a - b);
  const runs: number[][] = [];
  for (const day of sorted) {
    const last = runs.at(-1);
    if (last && day === (last.at(-1) ?? -2) + 1) last.push(day);
    else runs.push([day]);
  }
  return runs
    .map((run) => (run.length > 2 ? `${F.weekdayShort(run[0] ?? 0)}–${F.weekdayShort(run.at(-1) ?? 0)}` : run.map(F.weekdayShort).join(", ")))
    .join(", ");
}

/** The schedule in one sentence for the settings. */
export function scheduleSummary(settings: Settings): string {
  const rate = (value: number) => (value > 0 ? `${F.dec(F.fromBytesPerSecond(value, settings.speed_unit))} ${settings.speed_unit}` : t("limit.unlimited"));
  const normal = rate(settings.bandwidth_enabled ? settings.bandwidth_limit : 0);
  const vars = { days: dayRanges(settings.schedule_days), start: settings.schedule_start, end: settings.schedule_end, limit: rate(settings.schedule_limit), normal };
  return settings.schedule_start === settings.schedule_end ? t("set.schedule_summary_day", vars) : t("set.schedule_summary", vars);
}

export function limitToggle(limit: Status["limit"]): Parameters<typeof api.saveSettings>[0] {
  if (limit.bandwidth_enabled && limit.bandwidth_limit > 0) return { bandwidth_enabled: false };
  return limit.bandwidth_limit > 0
    ? { bandwidth_enabled: true }
    : { bandwidth_enabled: true, bandwidth_limit: F.toBytesPerSecond(50, "Mbit/s") };
}

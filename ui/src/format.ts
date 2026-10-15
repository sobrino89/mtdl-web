// Number, date and size formatting for the UI (German first; the locale comes from the language setting).

let locale = "de-DE";
let integer = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 });
let oneDecimal = new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
let upToTwo = new Intl.NumberFormat(locale, { maximumFractionDigits: 2 });

export function setLocale(tag: string): void {
  locale = tag;
  integer = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 });
  oneDecimal = new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  upToTwo = new Intl.NumberFormat(locale, { maximumFractionDigits: 2 });
}

export const num = (n: number): string => integer.format(n);
export const dec = (n: number): string => upToTwo.format(n);

const pad = (n: number): string => String(n).padStart(2, "0");

/** "12.10.2026" in the given time zone (the server's TZ, so dates match the film list). */
export function date(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return "–";
  return new Date(iso).toLocaleDateString(locale, { day: "2-digit", month: "2-digit", year: "numeric", timeZone });
}

/** "Mo., 12.10.2026" */
export function weekdayDate(iso: string, timeZone?: string): string {
  return new Date(iso).toLocaleDateString(locale, { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric", timeZone });
}

/** "Mo." for day 0 (Monday) up to "So." for 6, in the interface language. */
export function weekdayShort(day: number): string {
  return new Date(Date.UTC(2026, 9, 12 + day)).toLocaleDateString(locale, { weekday: "short", timeZone: "UTC" });
}

/** "18:00" when ``iso`` falls on the same day as ``now``, else "Mo. 08:00". */
export function moment(iso: string, timeZone?: string, now: Date = new Date()): string {
  const clock = time(iso, timeZone);
  if (date(iso, timeZone) === date(now.toISOString(), timeZone)) return clock;
  return `${new Date(iso).toLocaleDateString(locale, { weekday: "short", timeZone })} ${clock}`;
}

export function dateShort(iso: string, timeZone?: string): string {
  return new Date(iso).toLocaleDateString(locale, { day: "2-digit", month: "2-digit", timeZone });
}

export function time(iso: string, timeZone?: string): string {
  return new Date(iso).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", timeZone });
}

export function dateTime(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return "–";
  return `${dateShort(iso, timeZone)} ${time(iso, timeZone)}`;
}

/** Seconds as whole minutes like the design ("30 min", "135 min"); unknown as "–". */
export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "–";
  return `${integer.format(Math.max(1, Math.round(seconds / 60)))} min`;
}

/** "29:57" for a player position. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
}

/** Decimal units like the speeds (1 MB = 10^6 byte). */
export function size(bytes: number | null | undefined): string {
  if (bytes == null) return "–";
  const mb = bytes / 1e6;
  if (mb < 1000) return `${integer.format(Math.max(1, Math.round(mb)))} MB`;
  if (mb < 1e6) return `${oneDecimal.format(mb / 1000)} GB`;
  return `${oneDecimal.format(mb / 1e6)} TB`;
}

export type SpeedUnit = "Mbit/s" | "MB/s";

/** Byte/s in the chosen unit (1 MB/s = 8 Mbit/s). */
export function speed(bytesPerSecond: number, unit: SpeedUnit): string {
  const value = unit === "MB/s" ? bytesPerSecond / 1e6 : (bytesPerSecond * 8) / 1e6;
  return `${oneDecimal.format(value)} ${unit}`;
}

export function toBytesPerSecond(value: number, unit: SpeedUnit): number {
  return Math.round(unit === "MB/s" ? value * 1e6 : (value * 1e6) / 8);
}

export function fromBytesPerSecond(bytesPerSecond: number, unit: SpeedUnit): number {
  return unit === "MB/s" ? bytesPerSecond / 1e6 : (bytesPerSecond * 8) / 1e6;
}

export function percent(fraction: number): string {
  return `${Math.round(fraction * 100)} %`;
}

export function eta(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "–";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h} h ${pad(m)} min` : `${m}:${pad(s % 60)} min`;
}

/** Parse "12,5" or "12.5"; empty is null. */
export function parseDecimal(text: string): number | null {
  const trimmed = text.trim().replace(",", ".");
  if (!trimmed) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : NaN;
}

// Webhook templates and own headers: examples for common receivers, the rows of the header editor and what the
// server says about a configuration. Pure functions, tested. Header values are write-only: rows of stored headers
// come with an empty value, which keeps the stored one.
import { ApiError, errorMessage, type WebhookEvent } from "./api";
import { t, type Key } from "./i18n";

export interface HeaderRow {
  key: number;
  name: string;
  value: string;
  storedName: string | null; // stored on the server under this name
}

export type HeaderProblem = "name" | "reserved" | "duplicate" | "missing" | "value";

const HEADER_NAME = /^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,100}$/;
const HEADER_VALUE = /^[\t -~]{0,2000}$/; // printable ASCII: no line breaks, no umlauts
const RESERVED = new Set(["host", "content-length", "transfer-encoding", "connection", "x-mtdl-event", "x-mtdl-signature"]);

let rowKey = 0;
export const headerRow = (name = "", value = "", storedName: string | null = null): HeaderRow => ({ key: ++rowKey, name, value, storedName });
export const storedRows = (names: string[]): HeaderRow[] => names.map((name) => headerRow(name, "", name));

const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();
/** A pasted key often ends in a space or tab; the server trims the same way. */
const trimmed = (value: string) => value.replace(/^[ \t]+|[ \t]+$/g, "");

/** The row stands for a stored header whose value stays as it is. */
export const keepsValue = (row: HeaderRow): boolean => row.storedName !== null && row.value === "" && same(row.name, row.storedName);

const blank = (row: HeaderRow) => !row.name.trim() && !row.value;

export function headerProblem(row: HeaderRow, rows: HeaderRow[]): HeaderProblem | null {
  if (blank(row)) return null; // an empty row is left out
  const name = row.name.trim();
  if (!HEADER_NAME.test(name)) return "name";
  if (RESERVED.has(name.toLowerCase())) return "reserved";
  if (rows.filter((r) => same(r.name, name)).length > 1) return "duplicate";
  if (!trimmed(row.value) && !keepsValue(row)) return "missing";
  if (!HEADER_VALUE.test(trimmed(row.value))) return "value";
  return null;
}

/** For the API: every row by name, null where the stored value stays. */
export function headerPayload(rows: HeaderRow[]): Record<string, string | null> {
  const out: Record<string, string | null> = {};
  for (const row of rows) if (!blank(row)) out[row.name.trim()] = keepsValue(row) ? null : trimmed(row.value);
  return out;
}

export interface Preset {
  id: "sonarr" | "gotify" | "ntfy" | "discord";
  template: string;
  headers: [string, string][]; // an empty value is for the user to fill in
  events?: WebhookEvent[];
}

export const PRESETS: Preset[] = [
  {
    id: "sonarr",
    template: '{\n  "name": "DownloadedEpisodesScan",\n  "path": "/downloads/{{data.path}}",\n  "importMode": "Move"\n}',
    headers: [["X-Api-Key", ""]],
    events: ["download.completed"],
  },
  {
    id: "gotify",
    template: '{\n  "title": "{{data.topic}}",\n  "message": "{{data.title}} ({{data.channel}})",\n  "priority": 5\n}',
    headers: [["X-Gotify-Key", ""]],
  },
  {
    id: "ntfy",
    template: "{{data.topic}}: {{data.title}} ({{data.channel}})",
    headers: [
      ["Content-Type", "text/plain; charset=utf-8"],
      ["Title", "mtdl-web"],
    ],
  },
  {
    id: "discord",
    template: '{\n  "content": "**{{data.topic}}**: {{data.title}} ({{data.channel}})"\n}',
    headers: [],
  },
];

/** The rows after choosing an example: its headers added, existing rows of the same name kept (or set, if the
 * example brings a value). */
export function withPresetHeaders(rows: HeaderRow[], preset: Preset): HeaderRow[] {
  const out = rows.filter((r) => !blank(r));
  for (const [name, value] of preset.headers) {
    const found = out.findIndex((r) => same(r.name, name));
    const row = out[found];
    if (!row) out.push(headerRow(name, value));
    else if (value) out[found] = { ...row, value };
  }
  return out;
}

/** Placeholders offered for insertion; every field is listed in the API documentation. */
export const FIELDS = ["data.topic", "data.title", "data.channel", "data.path", "data.size", "data.error", "event", "time"];

/** Insert ``piece`` in place of the selection; returns the text and where the caret goes. */
export function insertAt(text: string, start: number, end: number, piece: string): { text: string; caret: number } {
  return { text: text.slice(0, start) + piece + text.slice(end), caret: start + piece.length };
}

export interface Problem {
  field: "template" | "headers" | null;
  text: string;
}

/** What a failed save means, for the field it concerns. */
export function problemOf(error: unknown): Problem {
  const detail = error instanceof ApiError && error.detail && typeof error.detail === "object" ? (error.detail as Record<string, unknown>) : {};
  const list = (key: string) => (Array.isArray(detail[key]) ? (detail[key] as unknown[]).join(", ") : "");
  switch (error instanceof ApiError ? error.code : undefined) {
    case "unknown_placeholders":
      return { field: "template", text: t("wh.tpl.unknown", { names: list("placeholders") }) };
    case "invalid_json":
      return { field: "template", text: t("wh.tpl.invalid_json", { line: String(detail.line), column: String(detail.column) }) };
    case "invalid_headers":
      return { field: "headers", text: t("wh.hdr.invalid", { names: list("headers") }) };
    default:
      return { field: null, text: `${t("settings.save_failed")}: ${errorMessage(error)}` };
  }
}

export const presetKey = (preset: Preset, part: "label" | "hint"): Key => `wh.preset.${preset.id}.${part}` as Key;

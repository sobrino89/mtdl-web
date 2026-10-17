// Search view logic, kept pure for tests: the hash query as filters, filtering, facet
// counts, sorting and grouping of the hits the API returned. The API searches (word prefixes in topic and title);
// the filters narrow the loaded hits in the browser, so counts and chips react without a round trip.
import type { Film, Quality, Scope } from "./api";

export type ScopeKey = "titelthema" | "titel" | "thema" | "alles";
export type SortKey = "sender" | "thema" | "titel" | "datum" | "dauer" | "groesse";
export type Period = "" | "7" | "30" | "365";

export interface Filters {
  q: string;
  scope: ScopeKey;
  sender: string[];
  thema: string[];
  titel: string;
  dmin: number | null; // minutes
  dmax: number | null;
  zeit: Period;
  hd: boolean;
  ut: boolean;
  /** Only broadcasts that seem to be in German (the server's other_language). */
  deutsch: boolean;
  /** Without audio description, sign language and the other special versions. */
  ohnesonder: boolean;
  kuenftig: boolean;
  gruppe: boolean;
  sort: SortKey;
  desc: boolean;
  id: string | null;
}

const SCOPES: Record<ScopeKey, Scope> = { titelthema: "topic_title", titel: "title", thema: "topic", alles: "all" };
const SORT_KEYS: SortKey[] = ["sender", "thema", "titel", "datum", "dauer", "groesse"];
/** Columns that start descending on the first click (newest, longest, largest first). */
export const DESCENDING_FIRST: SortKey[] = ["datum", "dauer", "groesse"];

function minutes(text: string | undefined): number | null {
  if (!text) return null;
  const value = Number(text);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

export function parseFilters(query: Record<string, string>): Filters {
  const scope = (query.in ?? "") in SCOPES ? (query.in as ScopeKey) : "titelthema";
  const [sortKey, sortDir] = (query.sort ?? "datum-desc").split("-");
  const sort = SORT_KEYS.includes(sortKey as SortKey) ? (sortKey as SortKey) : "datum";
  const zeit = ["7", "30", "365"].includes(query.zeit ?? "") ? (query.zeit as Period) : "";
  return {
    q: query.q ?? "",
    scope,
    sender: query.sender ? query.sender.split(",").filter(Boolean) : [],
    thema: query.thema ? query.thema.split("|").filter(Boolean) : [],
    titel: query.titel ?? "",
    dmin: minutes(query.dmin),
    dmax: minutes(query.dmax),
    zeit,
    hd: query.hd === "1",
    ut: query.ut === "1",
    deutsch: query.deutsch === "1",
    ohnesonder: query.ohnesonder === "1",
    kuenftig: query.kuenftig === "1",
    gruppe: query.gruppe === "1",
    sort,
    desc: sortKey === sort ? sortDir !== "asc" : true,
    id: query.id || null,
  };
}

export function apiScope(scope: ScopeKey, descriptionSearch: boolean): Scope {
  return scope === "alles" && !descriptionSearch ? "topic_title" : SCOPES[scope];
}

/** The hash value of a sort column after a click on its header. */
export function nextSort(filters: Filters, key: SortKey): string {
  if (filters.sort === key) return `${key}-${filters.desc ? "asc" : "desc"}`;
  return `${key}-${DESCENDING_FIRST.includes(key) ? "desc" : "asc"}`;
}

// Same comparison text as the backend (mtdl_web.text): NFKC, dashes as "-", whitespace collapsed, no case.
const DASH_CODES = [0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xfe58, 0xfe63];
const DASHES = new RegExp(`[${String.fromCharCode(...DASH_CODES)}]`, "g");

export function normalize(text: string): string {
  return text.normalize("NFKC").replace(DASHES, "-").split(/\s+/).filter(Boolean).join(" ");
}

export function fold(text: string): string {
  return normalize(text).toLowerCase().replace(/ß/g, "ss");
}

const DAY = 86_400_000;

type Skip = "sender" | "thema" | null;

function passes(film: Film, f: Filters, now: number, skip: Skip): boolean {
  const start = film.start ? Date.parse(film.start) : null;
  if (!f.kuenftig && start !== null && start > now) return false;
  if (skip !== "sender" && f.sender.length && !f.sender.includes(film.channel)) return false;
  if (skip !== "thema" && f.thema.length && !f.thema.includes(film.topic)) return false;
  if (f.titel && !fold(film.title).includes(fold(f.titel))) return false;
  const length = film.duration == null ? null : film.duration / 60;
  if (f.dmin !== null && (length === null || length < f.dmin)) return false;
  if (f.dmax !== null && (length === null || length > f.dmax)) return false;
  if (f.zeit && (start === null || start < now - Number(f.zeit) * DAY)) return false;
  if (f.hd && !film.url_hd) return false;
  if (f.ut && !film.url_subtitle) return false;
  if (f.deutsch && film.other_language) return false;
  if (f.ohnesonder && film.special_version) return false;
  return true;
}

export function applyFilters(films: Film[], f: Filters, now: number, skip: Skip = null): Film[] {
  return films.filter((film) => passes(film, f, now, skip));
}

function counted(films: Film[], key: (f: Film) => string): Map<string, number> {
  const counts = new Map<string, number>();
  for (const film of films) counts.set(key(film), (counts.get(key(film)) ?? 0) + 1);
  return counts;
}

/** Options for the channel and topic chips: each counted with all other filters applied. Chosen values stay
 * listed with 0 so they can be unticked. Channels alphabetical, topics by count. */
export function facets(
  films: Film[],
  f: Filters,
  now: number,
): { senders: [string, number][]; topics: [string, number][] } {
  const channels = counted(applyFilters(films, f, now, "sender"), (film) => film.channel);
  for (const chosen of f.sender) if (!channels.has(chosen)) channels.set(chosen, 0);
  const topics = counted(applyFilters(films, f, now, "thema"), (film) => film.topic);
  for (const chosen of f.thema) if (!topics.has(chosen)) topics.set(chosen, 0);
  return {
    senders: [...channels].sort((a, b) => a[0].localeCompare(b[0], "de")),
    topics: [...topics].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "de")),
  };
}

function sortValue(film: Film, key: SortKey): string | number {
  switch (key) {
    case "sender":
      return film.channel.toLowerCase();
    case "thema":
      return fold(film.topic);
    case "titel":
      return fold(film.title);
    case "dauer":
      return film.duration ?? -1;
    case "groesse":
      return film.size ?? -1;
    default:
      return film.start ? Date.parse(film.start) : 0;
  }
}

/** Sorted copy; ties fall back to newest first. */
export function sortFilms(films: Film[], key: SortKey, desc: boolean): Film[] {
  const direction = desc ? -1 : 1;
  const newest = (a: Film, b: Film) => (b.start ? Date.parse(b.start) : 0) - (a.start ? Date.parse(a.start) : 0);
  return [...films].sort((a, b) => {
    const x = sortValue(a, key);
    const y = sortValue(b, key);
    const order = typeof x === "string" && typeof y === "string" ? x.localeCompare(y, "de") : Number(x) - Number(y);
    return order * direction || newest(a, b);
  });
}

export interface Group {
  topic: string;
  films: Film[];
  channels: [string, number][];
}

/** Topics with the most hits first; inside a group the given order stays. */
export function groupByTopic(films: Film[]): Group[] {
  const groups = new Map<string, Film[]>();
  for (const film of films) {
    const list = groups.get(film.topic);
    if (list) list.push(film);
    else groups.set(film.topic, [film]);
  }
  return [...groups]
    .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0], "de"))
    .map(([topic, list]) => ({
      topic,
      films: list,
      channels: [...counted(list, (film) => film.channel)].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "de")),
    }));
}

export function topicCount(films: Film[]): number {
  return new Set(films.map((film) => film.topic)).size;
}

// -- one file on several channels ---------------------------------------------------------------------------------------
// 3sat lists ZDF's "Die Anstalt" under a title of its own, ARD and WDR share the audio-described "Maus": one row per
// file, the other channels as "+1". Same rules as the backend (mtdl_web/episodes.py same_file,
// subscriptions.py _representatives), so the row names the film a subscription would load.

/** The film's video files in every quality. */
export function videos(film: Film): string[] {
  return [film.url, film.url_hd, film.url_low].filter((url): url is string => !!url);
}

const dayFormats = new Map<string, Intl.DateTimeFormat>();

/** The calendar day of the broadcast in the server's time zone ("2026-10-06"); empty without a start. */
export function broadcastDay(film: Film, timeZone?: string): string {
  if (!film.start) return "";
  let format = dayFormats.get(timeZone ?? "");
  if (!format) {
    format = new Intl.DateTimeFormat("en", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" });
    dayFormats.set(timeZone ?? "", format);
  }
  const parts = format.formatToParts(Date.parse(film.start));
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

/** Within one minute or ten percent; unknown durations do not tell the films apart. */
export function sameDuration(a: number | null, b: number | null): boolean {
  if (a === null || b === null) return true;
  return Math.abs(a - b) <= Math.max(60, 0.1 * Math.max(a, b));
}

/** One video file on one day and of one length: one broadcast, whatever channel and title say. */
export function sameFile(a: Film, b: Film, timeZone?: string): boolean {
  const theirs = new Set(videos(b));
  return (
    videos(a).some((url) => theirs.has(url)) &&
    broadcastDay(a, timeZone) === broadcastDay(b, timeZone) &&
    sameDuration(a.duration, b.duration)
  );
}

const SPECIAL_VERSION = /Audiodeskription|Gebärdensprache|Hörfassung/i;

export interface Twins {
  /** One film per file: the one that stands for it. */
  shown: Film[];
  /** Films that share their file with others → all films of that file, the shown one first. */
  of: Map<string, Film[]>;
}

/** Merges the ``films`` that share a file. The one shown is on the show's home channel (most hits on the topic in
 * ``all``, the whole result), then a plain version over audio description or sign language, then the longer title. */
export function mergeTwins(films: Film[], all: Film[], timeZone?: string): Twins {
  const home = counted(all, (film) => `${fold(film.topic)}|${film.channel}`);
  const rank = (film: Film) => [
    home.get(`${fold(film.topic)}|${film.channel}`) ?? 0,
    SPECIAL_VERSION.test(film.title) ? 0 : 1,
    [...film.title].length,
  ];
  const better = (a: Film, b: Film): Film => {
    const x = rank(a);
    const y = rank(b);
    const i = x.findIndex((value, n) => value !== y[n]);
    if (i >= 0) return (x[i] ?? 0) > (y[i] ?? 0) ? a : b;
    return b.id.localeCompare(a.id, "en", { numeric: true }) > 0 ? b : a;
  };
  const groups: Film[][] = [];
  const byFile = new Map<string, number[]>(); // day + video → groups with such a film
  for (const film of films) {
    const day = broadcastDay(film, timeZone);
    const keys = videos(film).map((url) => `${day} ${url}`);
    const candidates = [...new Set(keys.flatMap((key) => byFile.get(key) ?? []))].sort((a, b) => a - b);
    let index = candidates.find((n) => groups[n]?.some((member) => sameFile(member, film, timeZone)));
    if (index === undefined) {
      index = groups.length;
      groups.push([film]);
    } else groups[index]?.push(film);
    for (const key of keys) {
      const list = byFile.get(key) ?? [];
      if (!list.includes(index)) byFile.set(key, [...list, index]);
    }
  }
  const shown: Film[] = [];
  const of = new Map<string, Film[]>();
  for (const group of groups) {
    const best = group.reduce(better);
    shown.push(best);
    if (group.length < 2) continue;
    const members = [best, ...group.filter((film) => film !== best)];
    for (const film of group) of.set(film.id, members);
  }
  return { shown, of };
}

/** Hash patches that remove one active filter each (for the "no hits" view). */
export function activeFilters(f: Filters): { kind: string; value: string; patch: Record<string, string> }[] {
  const chips: { kind: string; value: string; patch: Record<string, string> }[] = [];
  for (const s of f.sender) chips.push({ kind: "sender", value: s, patch: { sender: f.sender.filter((x) => x !== s).join(",") } });
  for (const t of f.thema) chips.push({ kind: "thema", value: t, patch: { thema: f.thema.filter((x) => x !== t).join("|") } });
  if (f.titel) chips.push({ kind: "titel", value: f.titel, patch: { titel: "" } });
  if (f.dmin !== null || f.dmax !== null) chips.push({ kind: "dauer", value: "", patch: { dmin: "", dmax: "" } });
  if (f.zeit) chips.push({ kind: "zeit", value: f.zeit, patch: { zeit: "" } });
  if (f.hd) chips.push({ kind: "hd", value: "", patch: { hd: "" } });
  if (f.ut) chips.push({ kind: "ut", value: "", patch: { ut: "" } });
  if (f.deutsch) chips.push({ kind: "deutsch", value: "", patch: { deutsch: "" } });
  if (f.ohnesonder) chips.push({ kind: "ohnesonder", value: "", patch: { ohnesonder: "" } });
  return chips;
}

export function hasFilters(f: Filters): boolean {
  return activeFilters(f).length > 0 || f.kuenftig;
}

export const NO_FILTERS = {
  sender: "",
  thema: "",
  titel: "",
  dmin: "",
  dmax: "",
  zeit: "",
  hd: "",
  ut: "",
  deutsch: "",
  ohnesonder: "",
  kuenftig: "",
};

export function toggled(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((x) => x !== value) : [...list, value];
}

/** Geo restriction as shown ("DE", "DE-AT-CH"); empty when the film is available everywhere. */
export function geoLabel(film: Film): string {
  return film.geo.join("-");
}

export function marks(film: Film): string[] {
  const list: string[] = [];
  if (film.url_hd) list.push("HD");
  if (film.url_subtitle) list.push("UT");
  if (film.geo.length) list.push(geoLabel(film));
  return list;
}

export interface QualityOption {
  quality: Quality;
  available: boolean;
  /** Only the normal quality has a size in the film list. */
  size: number | null;
}

export function qualities(film: Film): QualityOption[] {
  return [
    { quality: "hd", available: !!film.url_hd, size: null },
    { quality: "normal", available: !!film.url, size: film.size },
    { quality: "low", available: !!film.url_low, size: null },
  ];
}

export function isFuture(film: Film, now: number): boolean {
  return !!film.start && Date.parse(film.start) > now;
}

/** The URL of one quality, if the film list has it. */
export function urlOf(film: Film, quality: Quality): string | null {
  return (quality === "hd" ? film.url_hd : quality === "normal" ? film.url : film.url_low) || null;
}

const FALLBACK: Record<Quality, readonly Quality[]> = {
  hd: ["hd", "normal", "low"],
  normal: ["normal", "low", "hd"],
  low: ["low", "normal", "hd"],
};

/** The quality a download gets when ``wanted`` is asked for, the same way the server picks it: that one, else the next
 * lower, else the next higher one. The browser preview starts with it too. */
export function pickQuality(film: Film, wanted: Quality): Quality | null {
  return FALLBACK[wanted].find((q) => urlOf(film, q) !== null) ?? null;
}

export function isHls(url: string): boolean {
  return /\.m3u8(\?|$)/i.test(url);
}

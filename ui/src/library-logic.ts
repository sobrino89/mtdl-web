// Libraries, kept pure for tests: template presets and placeholders per kind for the settings page, the
// automatic choice of the TMDB series for a subscription, and the texts of a TMDB episode in the preview.
import type { Episode, Library, LibraryKind, SeriesCandidate } from "./api";
import { fold } from "./search-logic";

/** Templates for the series library; placeholders as in mtdl_web/library.py. The first one is the backend default
 * and FileBot's layout ("Die Anstalt {tmdb-73860}/Season 13/Die Anstalt - S13E08 - <name>.mp4"). */
export const PRESETS = [
  { key: "tmdb", template: "{serie} {tmdb}/{staffelordner}/{serie} - S{staffel}E{folge} - {folgentitel}" },
  { key: "tvdb", template: "{serie} {tvdb}/{staffelordner}/{serie} - S{staffel}E{folge} - {folgentitel}" },
  { key: "imdb", template: "{serie} ({serienjahr}) {imdb}/{staffelordner}/{serie} - S{staffel}E{folge} - {folgentitel}" },
  { key: "plain", template: "{serie}/{staffelordner}/{serie} - S{staffel}E{folge} - {folgentitel}" },
] as const;

/** Templates for a movie library (mtdl_web/library.py): FileBot's "Crime 101 (2026) {tmdb-1171145}/Crime 101 (2026).mkv"
 * first, the backend default. */
export const MOVIE_PRESETS = [
  { key: "movie_tmdb", template: "{titel} ({jahr}) {tmdb}/{titel} ({jahr})" },
  { key: "movie_imdb", template: "{titel} ({jahr}) {imdb}/{titel} ({jahr})" },
  { key: "movie_plain", template: "{titel} ({jahr})/{titel} ({jahr})" },
] as const;

export type PresetKey = (typeof PRESETS)[number]["key"] | (typeof MOVIE_PRESETS)[number]["key"];

export function presetsOf(kind: LibraryKind): readonly { key: PresetKey; template: string }[] {
  return kind === "movies" ? MOVIE_PRESETS : PRESETS;
}

export function presetOf(template: string, kind: LibraryKind = "series"): PresetKey | null {
  return presetsOf(kind).find((p) => p.template === template.trim())?.key ?? null;
}

/** The placeholders a template of the kind knows, German and English (as in mtdl_web/library.py). */
export const TOKENS: Record<LibraryKind, { de: string[]; en: string[] }> = {
  series: {
    de: ["serie", "serienjahr", "tmdb", "tvdb", "imdb", "staffelordner", "staffel", "folge", "folgentitel", "titel", "datum", "sender"],
    en: ["show", "show_year", "tmdb", "tvdb", "imdb", "season_folder", "season", "episode", "episode_title", "title", "date", "channel"],
  },
  movies: {
    de: ["titel", "originaltitel", "jahr", "tmdb", "imdb", "sender", "datum"],
    en: ["title", "original_title", "year", "tmdb", "imdb", "channel", "date"],
  },
};

/** The library a subscription or download goes to: the one named, else the only one of the kind (subscriptions of
 * servers before 0.1.0.dev20 name none). */
export function libraryFor(libraries: Library[] | null, id: number | null | undefined, kind: LibraryKind): Library | null {
  if (!libraries) return null;
  if (id !== null && id !== undefined) return libraries.find((l) => l.id === id) ?? null;
  const found = libraries.filter((l) => l.kind === kind);
  return found.length === 1 ? (found[0] ?? null) : null;
}

/** A folder's path without the slash at its end, for joining. */
export function folder(path: string): string {
  return path.replace(/\/+$/, "") || "/";
}

/** Names without case and punctuation, like the backend's episode match ("Werwölfe · Das Spiel" of the Mediathek
 * is TMDB's "Werwölfe – Das Spiel"). */
function words(text: string): string {
  return fold(text)
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

/** The candidate to take without asking: the only one whose German or original name is the query. A name that
 * several series share needs the person to choose. */
export function autoSeries(candidates: SeriesCandidate[], query: string): SeriesCandidate | null {
  const wanted = words(query);
  if (!wanted) return null;
  const same = candidates.filter((c) => words(c.name) === wanted || words(c.original_name) === wanted);
  return same.length === 1 ? (same[0] ?? null) : null;
}

/** "S13E08" */
export function episodeCode(e: Pick<Episode, "season" | "number">): string {
  return `S${String(e.season).padStart(2, "0")}E${String(e.number).padStart(2, "0")}`;
}

const ERROR_CODES = [
  "library_unavailable",
  "library_required",
  "library_kind",
  "library_not_writable",
  "library_path_invalid",
  "library_path_overlap",
  "library_name_taken",
  "library_in_use",
  "library_single_download",
  "movie_missing",
  "name_missing",
  "valid_until_past",
  "tmdb_missing",
  "series_missing",
  "tmdb_no_token",
  "tmdb_unauthorized",
  "tmdb_not_found",
  "tmdb_unavailable",
  "plex_no_url",
  "plex_unauthorized",
  "plex_unavailable",
  "unknown_placeholders",
  "template_empty",
];

/** The catalogue key for an API error code of the library features, null for other errors. */
export function errorKey(code: string | undefined): string | null {
  return code && ERROR_CODES.includes(code) ? `lib.err.${code}` : null;
}

/** Why a Mediathek entry is that TMDB episode, or why none: the keys of the language catalogue. */
export function reasonKey(reason: string | null | undefined): string {
  const known = ["number", "number_title", "number_date", "title", "title_date", "date"];
  const missing = ["no_series", "no_match", "ambiguous", "conflict", "too_short"];
  if (reason && known.includes(reason)) return `lib.reason.${reason}`;
  if (reason && missing.includes(reason)) return `lib.none.${reason}`;
  return "lib.none.no_match";
}

// Genre words after which a Mediathek title carries production details: "Nikita - Spielfilm, Frankreich/Italien 1990".
const DETAILS = /^(.+?)\s+[-–]\s+(?:Spielfilm|Fernsehfilm|Kinofilm|Film|Komödie|Drama|Thriller|Krimi|Western|Dokumentarfilm|Animationsfilm)\b(.*)$/i;
const VERSION = /\s*\([^()]*\)\s*$/; // "(Audiodeskription)", "(Englisch)", "(mit Untertitel)"
const YEAR = /\b(?:19|20)\d{2}\b/g;

/** What to ask TMDB for a film of the Mediathek: the title without version notes and production details, and the
 * year those details name (not one in the title itself: "1917 - Spielfilm, Großbritannien 2019" is 2019). */
export function movieQuery(title: string): { query: string; year: number | null } {
  let rest = title.trim();
  while (VERSION.test(rest)) rest = rest.replace(VERSION, "");
  const details = DETAILS.exec(rest);
  const years = details ? [...(details[2] ?? "").matchAll(YEAR)].map((m) => Number(m[0])) : [];
  return { query: (details?.[1] ?? rest).trim(), year: years.length ? (years[years.length - 1] ?? null) : null };
}

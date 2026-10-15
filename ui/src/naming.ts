import type { Film } from "./api";
import type { Key } from "./i18n";

// Placeholders of an own naming template, as the backend knows them (mtdl_web.naming): German or English.
export const TOKENS = ["thema", "titel", "staffel", "folge", "datum", "jahr", "sender", "ext"] as const;
/** The same placeholders in English, in the order of TOKENS. */
export const TOKENS_EN = ["topic", "title", "season", "episode", "date", "year", "channel", "ext"] as const;
const KNOWN = new Set<string>([...TOKENS, ...TOKENS_EN]);

export const DEFAULT_TEMPLATE = "{thema}/Staffel {staffel}/{thema} - S{staffel}E{folge} - {titel}.{ext}";
export const DEFAULT_TEMPLATE_EN = "{topic}/Season {season}/{topic} - S{season}E{episode} - {title}.{ext}";

export function unknownTokens(template: string): string[] {
  return [...template.matchAll(/\{(\w+)\}/g)].map((m) => m[1] ?? "").filter((name) => !KNOWN.has(name));
}

/** Real broadcasts from the film list (13.10.2026) for the live example. */
export const EXAMPLES: { label: Key; film: Omit<Film, "id" | "status"> }[] = [
  {
    label: "set.ex_numbered",
    film: example("MDR", "Das Gipfeltreffen", "Das Beste kommt noch! (58)", "2026-08-20T21:50:00Z", 1797),
  },
  {
    label: "set.ex_season",
    film: example("BR", "Werwölfe · Das Spiel von List und Täuschung", "Folge 3: Was für ein Theater (S02/E03)", "2026-10-08T22:05:00Z", 2247),
  },
  {
    label: "set.ex_plain",
    film: example("MDR", "Das Gipfeltreffen", "Das Gipfeltreffen – Best of 2025", "2025-12-05T11:00:00Z", 3642),
  },
];

function example(channel: string, topic: string, title: string, start: string, duration: number): Omit<Film, "id" | "status"> {
  return {
    channel,
    topic,
    title,
    description: "",
    start,
    duration,
    size: null,
    url: "https://example.invalid/film.mp4",
    url_low: null,
    url_hd: null,
    url_subtitle: null,
    url_website: null,
    geo: [],
  };
}

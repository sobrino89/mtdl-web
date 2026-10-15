// Rule suggestions. They only propose ordinary, visible conditions; the
// user adds or drops them. Pure functions, tested against real search results.
import type { Condition, Film, Scheme } from "./api";
import { fold } from "./search-logic";
import { EPISODE_NUMBER, SEASON_EPISODE, TEMPLATES } from "./rule-texts";

/** "(39)" at the end of the title: the reference case's numbering (Gipfeltreffen). */
export const NUMBERED = String.raw`\(\d+\)$`;
const NUMBERED_RE = /\(\d+\)$/;
const SEASON_RE = /\(S\d+\/E\d+\)$/;

export interface Start {
  conditions: Condition[];
  name: string;
  scheme: Scheme;
}

/** First rules for a new subscription from the search hits: the topic of the chosen hit (else the most frequent),
 * then the numbering most of that topic's titles carry. */
export function suggestFromSearch(films: Film[], query: string, from?: Film | null): Start {
  if (!films.length) return { conditions: [], name: query, scheme: "date" };
  const counts = new Map<string, number>();
  for (const f of films) counts.set(f.topic, (counts.get(f.topic) ?? 0) + 1);
  const topic = from?.topic ?? [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "de"))[0]?.[0] ?? "";
  const pool = films.filter((f) => f.topic === topic);
  const conditions: Condition[] = [];
  if (pool.length < films.length) conditions.push({ field: "topic", operator: "is", value: topic, regex: false, enabled: true });
  const numbered = pool.filter((f) => NUMBERED_RE.test(f.title)).length;
  const seasons = pool.filter((f) => SEASON_RE.test(f.title)).length;
  let scheme: Scheme = "date";
  if (numbered >= pool.length * 0.5 && numbered < pool.length) {
    conditions.push({ field: "title", operator: "is", value: NUMBERED, regex: true, enabled: true });
    scheme = "episode_number";
  } else if (seasons >= pool.length * 0.5 && seasons < pool.length) {
    conditions.push({ field: "title", operator: "is", value: SEASON_EPISODE, regex: true, enabled: true });
    scheme = "season_episode";
  } else if (numbered === pool.length && pool.length) {
    scheme = "episode_number";
  } else if (seasons === pool.length && pool.length) {
    scheme = "season_episode";
  }
  const name = conditions.some((c) => c.field === "topic") ? topic : query.trim() || topic;
  return { conditions, name, scheme };
}

// -- learning from thumbs ------------------------------------------------------------------------------------------

const STOP = new Set(
  "der die das und mit von vom im in am an auf für zu zum zur ein eine den dem des ist nicht".split(" "),
);
const WORD = /^[a-zäöüß][a-zäöüß'’.]*$/;

export type Lesson =
  | { kind: "text"; condition: Condition; value: string; many: boolean }
  | { kind: "numbered"; condition: Condition }
  | { kind: "topic"; condition: Condition; topic: string }
  | { kind: "duration"; condition: Condition; minutes: number; shortest: number }
  | { kind: "none" };

/** What the thumbs-down hits have in common that no thumbs-up hit has (``kept`` are the other included hits;
 * unrated hits are no counterexample, the suggestion shows what else it would exclude). */
export function learn(down: Film[], kept: Film[], topics: string[], up: Film[]): Lesson | null {
  if (!down.length) return null;
  const topicWords = new Set(topics.flatMap((t) => fold(t).split(/\s+/)));
  const usable = (w: string) => WORD.test(w) && !STOP.has(w) && !topicWords.has(w);
  const gramSets = down.map((film) => {
    const words = fold(film.title).split(/[\s:-]+/).filter(Boolean);
    const grams = new Set<string>();
    for (let n = 1; n <= 3; n++) {
      for (let i = 0; i + n <= words.length; i++) {
        const gram = words.slice(i, i + n);
        if (gram.every(usable) && gram.join(" ").length >= 4) grams.add(gram.join(" "));
      }
    }
    return grams;
  });
  // n-grams every thumbs-down title has
  const candidates = gramSets
    .slice(1)
    .reduce((common, grams) => new Set([...common].filter((g) => grams.has(g))), gramSets[0] ?? new Set<string>());
  const positive = up.map((f) => fold(f.title));
  const keptTitles = kept.map((f) => fold(f.title));
  const collisions = (g: string) => keptTitles.filter((t) => t.includes(g)).length;
  const good = [...candidates]
    .filter((g) => !positive.some((t) => t.includes(g)))
    .sort((a, b) => b.split(" ").length - a.split(" ").length || collisions(a) - collisions(b) || b.length - a.length);
  const first = down[0] as Film;
  if (good.length) {
    const gram = good[0] as string;
    const at = fold(first.title).indexOf(gram);
    const original = at >= 0 && fold(first.title).length === first.title.length ? first.title.slice(at, at + gram.length) : gram;
    return {
      kind: "text",
      condition: { field: "title", operator: "not_contains", value: original, regex: false, enabled: true },
      value: original,
      many: down.length > 1,
    };
  }
  if (kept.length && down.every((f) => !NUMBERED_RE.test(f.title)) && kept.every((f) => NUMBERED_RE.test(f.title))) {
    const template = TEMPLATES.find((t) => t.key === "folgennummer");
    return { kind: "numbered", condition: template ? template.condition("") : { field: "title", operator: "is", value: EPISODE_NUMBER, regex: true, enabled: true } };
  }
  const keptTopics = new Set(kept.map((f) => f.topic));
  const [onlyTopic] = keptTopics;
  if (keptTopics.size === 1 && onlyTopic !== undefined && down.every((f) => !keptTopics.has(f.topic))) {
    return { kind: "topic", topic: onlyTopic, condition: { field: "topic", operator: "is", value: onlyTopic, regex: false, enabled: true } };
  }
  const minutes = (f: Film) => (f.duration ?? 0) / 60;
  if (kept.length && down.every((f) => f.duration != null) && kept.every((f) => f.duration != null)) {
    const shortest = Math.min(...kept.map(minutes));
    const longest = Math.max(...down.map(minutes));
    if (longest < shortest) {
      const value = Math.floor((longest + shortest) / 2);
      return { kind: "duration", minutes: value, shortest: Math.round(shortest), condition: { field: "duration", operator: "at_least", value, regex: false, enabled: true } };
    }
  }
  return { kind: "none" };
}

/** Whether a film passes a condition, for the effect of a suggestion (the backend decides for real). */
export function passes(c: Condition, film: Film): boolean {
  const text = c.field === "topic" ? film.topic : c.field === "title" ? film.title : c.field === "description" ? film.description : null;
  if (text !== null) {
    if (c.regex) {
      let hit = false;
      try {
        hit = new RegExp(String(c.value), "iu").test(text);
      } catch {
        return true;
      }
      return c.operator === "is_not" ? !hit : hit;
    }
    const a = fold(text);
    const b = fold(String(c.value));
    if (c.operator === "is") return a === b;
    if (c.operator === "is_not") return a !== b;
    if (c.operator === "contains") return a.includes(b);
    return !a.includes(b);
  }
  if (c.field === "channel") {
    const listed = Array.isArray(c.value) && c.value.includes(film.channel);
    return c.operator === "none_of" ? !listed : listed;
  }
  if (c.field === "duration") {
    if (film.duration == null) return false;
    const m = film.duration / 60;
    return c.operator === "at_most" ? m <= Number(c.value) : m >= Number(c.value);
  }
  return true;
}

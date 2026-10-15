// Order of the subscription preview: a column header sorts all three groups (load, exists, excluded); the choice
// sits in the URL hash like the search's ("sort=dauer-desc"). Pure functions, tested.
import type { PreviewItem } from "./api";

export type PreviewSortKey = "sender" | "titel" | "datum" | "dauer" | "ziel";

export interface PreviewSort {
  key: PreviewSortKey;
  desc: boolean;
}

const KEYS: PreviewSortKey[] = ["sender", "titel", "datum", "dauer", "ziel"];
const DESCENDING_FIRST: PreviewSortKey[] = ["datum", "dauer"]; // newest and longest first on the first click

export function parsePreviewSort(value: string | undefined): PreviewSort {
  const [key, dir] = (value ?? "").split("-");
  if (!KEYS.includes(key as PreviewSortKey)) return { key: "datum", desc: true };
  return { key: key as PreviewSortKey, desc: dir === "desc" };
}

/** The hash value after a click on a column header: same column flips, another starts in its natural order. */
export function nextPreviewSort(current: PreviewSort, key: PreviewSortKey): string {
  if (current.key === key) return `${key}-${current.desc ? "asc" : "desc"}`;
  return `${key}-${DESCENDING_FIRST.includes(key) ? "desc" : "asc"}`;
}

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" }); // "S01E9" before "S01E10"

function value(item: PreviewItem, key: PreviewSortKey): string | number {
  const film = item.film;
  switch (key) {
    case "sender":
      return film.channel;
    case "titel":
      return film.title;
    case "datum":
      return film.start ? Date.parse(film.start) : 0;
    case "dauer":
      return film.duration ?? 0;
    case "ziel": // the right column: the file name to load, else the rule that excludes
      return item.path ?? (item.excluded_by != null ? String(item.excluded_by).padStart(3, "0") : "");
  }
}

/** Comparator for `sort`; ties go newest first, so the order never jumps between refreshes. */
export function comparePreview(sort: PreviewSort): (a: PreviewItem, b: PreviewItem) => number {
  return (a, b) => {
    const x = value(a, sort.key);
    const y = value(b, sort.key);
    const order = typeof x === "number" && typeof y === "number" ? x - y : collator.compare(String(x), String(y));
    return (sort.desc ? -order : order) || (b.film.start ?? "").localeCompare(a.film.start ?? "");
  };
}

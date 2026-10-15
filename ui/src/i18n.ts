// Every text of the UI comes from here: German complete, English prepared (missing keys fall back to German).
// Values are strings with {placeholders} or functions for plurals and grammar.
import { de } from "./i18n/de";
import { en } from "./i18n/en";

export type Vars = Record<string, string | number>;
export type Message = string | ((v: Vars) => string);
export type Key = keyof typeof de;
export type Catalog = Record<Key, Message>;

let active: Partial<Catalog> = de;
let lang = "de";

export function setLanguage(code: string): void {
  lang = code.startsWith("en") ? "en" : "de";
  active = lang === "en" ? en : de;
  document.documentElement.lang = lang;
}

export function language(): string {
  return lang;
}

export type LanguageChoice = "de" | "en" | "auto";
const LANGUAGE_KEY = "mtdl.lang";

/** The viewer's choice (a per-browser convenience in localStorage), "auto" follows the browser. */
export function languageChoice(): LanguageChoice {
  try {
    const saved = localStorage.getItem(LANGUAGE_KEY);
    if (saved === "de" || saved === "en") return saved;
  } catch {
    // private window or blocked storage
  }
  return "auto";
}

export function chooseLanguage(choice: LanguageChoice): void {
  try {
    if (choice === "auto") localStorage.removeItem(LANGUAGE_KEY);
    else localStorage.setItem(LANGUAGE_KEY, choice);
  } catch {
    // nothing to remember
  }
}

/** German unless the browser prefers English (the content is German television after all). */
export function resolveLanguage(choice: LanguageChoice, preferred: readonly string[]): "de" | "en" {
  if (choice !== "auto") return choice;
  const first = preferred.find((l) => /^(de|en)\b/i.test(l));
  return first?.toLowerCase().startsWith("en") ? "en" : "de";
}

export function t(key: Key, vars: Vars = {}): string {
  const message = active[key] ?? de[key];
  if (typeof message === "function") return message(vars);
  return message.replace(/\{(\w+)\}/g, (all, name: string) => (name in vars ? String(vars[name]) : all));
}

/** Plural helper for catalogs: one(n) or other(n). */
export function plural(n: number, one: string, other: string): string {
  return (n === 1 ? one : other).replace("{n}", String(n));
}

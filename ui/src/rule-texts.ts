// Conditions of a subscription in words: short form for lists, the reason a
// hit was excluded, templates that create ordinary visible conditions.
import type { Condition, ConditionResult, Field, Operator } from "./api";
import * as F from "./format";
import { t, type Key } from "./i18n";

export type FieldType = "text" | "channel" | "date" | "duration";

export const FIELDS: Field[] = ["topic", "title", "description", "channel", "date", "duration"];

export function fieldType(field: Field): FieldType {
  if (field === "channel") return "channel";
  if (field === "date") return "date";
  if (field === "duration") return "duration";
  return "text";
}

export function fieldLabel(field: Field): string {
  return t(`field.${field}` as Key);
}

export function operatorsFor(field: Field, regex: boolean): Operator[] {
  switch (fieldType(field)) {
    case "channel":
      return ["any_of", "none_of"];
    case "date":
      return ["after", "before"];
    case "duration":
      return ["at_least", "at_most"];
    default:
      return regex ? ["is", "is_not"] : ["is", "is_not", "contains", "not_contains"];
  }
}

export function operatorLabel(operator: Operator): string {
  return t(`op.${operator}` as Key);
}

/** A fresh condition for a field, as the design proposes it. ``today`` is YYYY-MM-DD in the server's zone. */
export function defaultCondition(field: Field, today: string): Condition {
  switch (fieldType(field)) {
    case "channel":
      return { field, operator: "any_of", value: [], regex: false, enabled: true };
    case "date":
      return { field, operator: "after", value: today, regex: false, enabled: true };
    case "duration":
      return { field, operator: "at_least", value: 20, regex: false, enabled: true };
    default:
      return { field, operator: "contains", value: "", regex: false, enabled: true };
  }
}

export interface Template {
  key: "folgennummer" | "staffelfolge" | "neu" | "fassungen" | "clips";
  condition: (today: string) => Condition;
}

export const EPISODE_NUMBER = String.raw`\((S\d+/E)?\d+\)$`;
export const SEASON_EPISODE = String.raw`\(S\d+/E\d+\)$`;
export const SPECIAL_VERSIONS = "Audiodeskription|Gebärdensprache|Hörfassung";

export const TEMPLATES: Template[] = [
  { key: "folgennummer", condition: () => ({ field: "title", operator: "is", value: EPISODE_NUMBER, regex: true, enabled: true }) },
  { key: "staffelfolge", condition: () => ({ field: "title", operator: "is", value: SEASON_EPISODE, regex: true, enabled: true }) },
  { key: "neu", condition: (today) => ({ field: "date", operator: "after", value: today, regex: false, enabled: true }) },
  { key: "fassungen", condition: () => ({ field: "title", operator: "is_not", value: SPECIAL_VERSIONS, regex: true, enabled: true }) },
  { key: "clips", condition: () => ({ field: "duration", operator: "at_least", value: 10, regex: false, enabled: true }) },
];

function sameCondition(a: Condition, b: Condition): boolean {
  return a.field === b.field && a.operator === b.operator && !!a.regex === !!b.regex && JSON.stringify(a.value) === JSON.stringify(b.value);
}

/** The template a condition came from (value unchanged), if any; "neu" matches any date after. */
export function templateOf(condition: Condition): Template["key"] | null {
  for (const template of TEMPLATES) {
    if (template.key === "neu") continue;
    if (sameCondition(condition, template.condition(""))) return template.key;
  }
  return null;
}

const list = (value: Condition["value"]) => (Array.isArray(value) ? value.join(", ") : String(value));
const day = (value: Condition["value"]) => (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? F.date(`${value}T12:00:00Z`, "UTC") : String(value));

/** Short form for lists: "Thema = Das Gipfeltreffen", "hat Folgennummer", "≥ 10 min". */
export function shortText(c: Condition): string {
  const template = templateOf(c);
  if (template) return t(`tpl.${template}.short` as Key);
  const field = fieldLabel(c.field);
  const value = list(c.value);
  switch (fieldType(c.field)) {
    case "channel":
      return c.operator === "none_of" ? t("rule.short.none_of", { list: value }) : t("rule.short.any_of", { list: value });
    case "date":
      return c.operator === "before" ? t("rule.short.before", { date: day(c.value) }) : t("rule.short.after", { date: day(c.value) });
    case "duration":
      return c.operator === "at_most" ? `≤ ${value} min` : `≥ ${value} min`;
    default:
      if (c.regex) return c.operator === "is_not" ? t("rule.short.regex_not", { field, value }) : `${field} ~ ${value}`;
      if (c.operator === "is") return `${field} = ${value}`;
      if (c.operator === "is_not") return `${field} ≠ ${value}`;
      return c.operator === "contains" ? t("rule.short.contains", { field, value }) : t("rule.short.not_contains", { field, value });
  }
}

/** Active conditions of a subscription in one line. */
export function summary(conditions: Condition[]): string {
  const active = conditions.filter((c) => c.enabled !== false);
  return active.length ? active.map(shortText).join(" · ") : t("rule.none");
}

/** Why a hit was excluded by this condition ("anderes Thema", "keine Folgennummer", "zu kurz"). */
export function reason(c: Condition, result?: ConditionResult): string {
  if (result?.code === "value_missing") return t("rule.reason.missing", { field: fieldLabel(c.field) });
  if (result?.code === "regex_timeout") return t("rule.reason.timeout");
  const template = templateOf(c);
  if (template && template !== "neu") return t(`tpl.${template}.reason` as Key);
  const field = fieldLabel(c.field);
  const value = list(c.value);
  switch (fieldType(c.field)) {
    case "channel":
      return c.operator === "none_of" ? t("rule.reason.channel_excluded") : t("rule.reason.other_channel");
    case "date":
      return c.operator === "before" ? t("rule.reason.from", { date: day(c.value) }) : t("rule.reason.before", { date: day(c.value) });
    case "duration":
      return c.operator === "at_most" ? t("rule.reason.too_long") : t("rule.reason.too_short");
    default:
      if (c.regex) return c.operator === "is_not" ? t("rule.reason.regex_matches", { field }) : t("rule.reason.regex_no_match", { field });
      if (c.operator === "is") return t(`rule.reason.other_${c.field}` as Key);
      if (c.operator === "is_not") return `${field} „${value}“`;
      return c.operator === "contains" ? t("rule.reason.without", { value }) : t("rule.reason.with", { value });
  }
}

/** Where an opening bracket stays open (escapes and character classes skipped), for "missing )" and an
 * unterminated "[": the regex library reports the end of the pattern, the design marks the bracket. */
function unclosed(value: string, open: "(" | "["): number | null {
  const stack: number[] = [];
  let inClass = -1;
  for (let i = 0; i < value.length; i++) {
    const c = value[i];
    if (c === "\\") {
      i++;
      continue;
    }
    if (inClass >= 0) {
      if (c === "]") inClass = -1;
      continue;
    }
    if (c === "[") inClass = i;
    else if (c === "(") stack.push(i);
    else if (c === ")") stack.pop();
  }
  if (open === "[") return inClass >= 0 ? inClass : null;
  return stack.length ? (stack[stack.length - 1] ?? null) : null;
}

/** The regex library's message (English) in words, with the position to mark (0-based, null = none). */
export function regexProblem(value: string, message: string, position: number | null): { position: number | null; text: string } {
  const at = (p: number | null) => (p !== null && p >= 0 && p < value.length ? p : null);
  const char = (p: number | null) => (p !== null ? (value[p] ?? "") : "");
  switch (message) {
    case "missing )":
      return { position: at(unclosed(value, "(") ?? position), text: t("rx.missing_close") };
    case "unbalanced parenthesis":
      return { position: at(position), text: t("rx.unbalanced") };
    case "nothing to repeat":
      return { position: at(position), text: t("rx.nothing_to_repeat", { c: char(position) }) };
    case "multiple repeat":
      return { position: at(position), text: t("rx.multiple_repeat", { c: char(position) }) };
    case "unterminated character set":
      return { position: at(unclosed(value, "[") ?? position), text: t("rx.unterminated_set") };
    case "bad escape (end of pattern)":
      return { position: at(value.length - 1), text: t("rx.trailing_backslash") };
    case "min repeat greater than max repeat":
      return { position: at(position), text: t("rx.min_max") };
    case "unknown property":
      return { position: at(position), text: t("rx.unknown_property") };
    case "unknown extension":
      return { position: at(position), text: t("rx.unknown_extension") };
    default:
      return { position: at(position), text: t("rx.other", { message }) };
  }
}

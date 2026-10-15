// The guide of the first start: which steps an installation goes
// through, and the film list's state as the guide shows it.
import type { AuthStatus, FilmlistStatus } from "./api";

export type GuideStep = "passwort" | "ziel" | "filmliste" | "benennung";
export type PageStep = Exclude<GuideStep, "passwort">; // the steps after the sign-in, each a place #/start/<step>

const AFTER_SIGN_IN: PageStep[] = ["ziel", "filmliste", "benennung"];

/** The password comes first where the guide asks for one (mode password without MTDL_PASSWORD). */
export function guideSteps(auth: Pick<AuthStatus, "mode" | "password_from_env"> | null): GuideStep[] {
  return auth?.mode === "password" && !auth.password_from_env ? ["passwort", ...AFTER_SIGN_IN] : AFTER_SIGN_IN;
}

/** The step a route names (#/start/<step>); anything else is the first step after the sign-in. */
export function stepOf(param: string | undefined): PageStep {
  return param === "filmliste" || param === "benennung" ? param : "ziel";
}

export interface ListStage {
  phase: "wait" | "download" | "import" | "ready" | "error";
  fraction: number | null; // of the current phase, 0..1; null when nobody knows the end (the very first import)
}

export function listStage(list: FilmlistStatus | undefined): ListStage {
  if (!list) return { phase: "wait", fraction: null };
  if (list.progress) {
    const { phase, done, total } = list.progress;
    return { phase, fraction: total ? Math.min(1, done / total) : null };
  }
  if (list.running) return { phase: "download", fraction: null };
  if (list.films > 0) return { phase: "ready", fraction: 1 };
  return { phase: list.last_error ? "error" : "wait", fraction: null };
}

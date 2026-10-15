// What the settings say about a hook script: switched off, not run yet, done, failed. Pure functions, tested.
import type { HookScript } from "./api";
import * as F from "./format";
import { t } from "./i18n";

export interface HookStatus {
  tone: "ok" | "err" | "muted";
  text: string;
}

export function hookStatus(script: HookScript, timeZone?: string): HookStatus {
  const run = script.last_run;
  if (!script.executable) return { tone: "muted", text: t("hk.off") };
  if (!run) return { tone: "muted", text: t("hk.never") };
  const time = F.dateTime(run.started_at, timeZone);
  if (run.exit_code === 0) return { tone: "ok", text: t("hk.ok", { time, seconds: `${F.dec(run.seconds)} s` }) };
  if (run.exit_code !== null) return { tone: "err", text: t("hk.exit", { code: run.exit_code, time }) };
  if (run.error?.startsWith("timeout")) return { tone: "err", text: t("hk.timeout", { time }) };
  return { tone: "err", text: t("hk.error", { error: run.error ?? "", time }) };
}

/** A first script to copy: language-neutral, works with the shell of the image. */
export const EXAMPLE_SCRIPT = '#!/bin/sh\necho "$MTDL_EVENT: $MTDL_TITLE -> $MTDL_FILE"\n';

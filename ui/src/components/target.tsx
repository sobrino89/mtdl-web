// The result of the download folder's write test: free space and the user mtdl-web runs as, or why it fails and
// how to fix it. Shown in the settings ("Ziel und Benennung") and in the guide of the first start.
import type { JSX } from "preact";

import type { Info, SystemInfo } from "../api";
import * as F from "../format";
import { t } from "../i18n";
import { I, Icon } from "../icons";
import { copyText } from "./controls";

export function TargetResult(props: {
  problem: string | null;
  system: SystemInfo | null;
  info: Info | null;
  onCheck: () => void;
  checkedAt?: string | null;
  boxed?: boolean; // the guide shows success as a box, the settings as a line
}): JSX.Element | null {
  const { problem, system, info } = props;
  const dir = info?.download_dir ?? "/downloads";
  const command = `docker exec -u 0 mtdl-web chown -R ${info?.uid ?? 99}:${info?.gid ?? 100} ${dir}`;
  if (problem) {
    return (
      <div role="alert" class="alert-box">
        <strong class="err-text">{t("set.target_bad_title")}</strong>
        <span>{t("set.target_bad_text", { path: dir, reason: problem, uid: info?.uid ?? "?", gid: info?.gid ?? "?" })}</span>
        <div class="row wrap">
          <code class="command grow">{command}</code>
          <button class="button" onClick={() => copyText(command)}>
            {t("target.copy")}
          </button>
          <button class="button primary" onClick={props.onCheck}>
            {t("target.recheck")}
          </button>
        </div>
        <span class="small muted-text">{t("set.target_alt")}</span>
      </div>
    );
  }
  if (!system) return null;
  return (
    <div class={props.boxed ? "ok-box" : "row small ok-text"}>
      <Icon d={I.check} size={props.boxed ? 16 : 14} stroke={2.2} />
      <span>
        {t("set.target_ok", { free: F.size(system.download_free_bytes), uid: system.uid, gid: system.gid })}
        {props.checkedAt ? t("set.checked_at", { time: props.checkedAt }) : ""}
      </span>
    </div>
  );
}

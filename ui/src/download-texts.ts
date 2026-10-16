// What the downloads view says about a download: origin, progress, and for failures what happened and what helps.
// Pure functions, tested; the {path} marker stays in texts so the view can set the path as code.
import type { Download, Info, Running, SpeedUnit } from "./api";
import * as F from "./format";
import { t, type Key } from "./i18n";

const CODES = [
  "geo",
  "forbidden",
  "gone",
  "http",
  "network",
  "not_public",
  "permission",
  "disk_full",
  "filesystem",
  "not_mounted",
  "mux",
  "format",
  "livestream",
  "no_url",
  "unknown",
] as const;
type Code = (typeof CODES)[number];

export interface Failure {
  code: Code;
  title: string;
  text: string; // may contain "{path}"
  solution: string;
  command: string | null;
  retryable: boolean;
  path: string; // folder of the target in the container
}

/** The file as the container sees it: the server says (a library keeps paths relative to itself); servers before
 * 0.1.0.dev20 sent relative paths for the downloads folder and absolute ones for the library. */
export function targetFile(download: Download, info: Info | null): string {
  if (download.file) return download.file;
  if (download.path.startsWith("/")) return download.path;
  return `${(info?.download_dir ?? "/downloads").replace(/\/+$/, "")}/${download.path}`;
}

/** The folder the file goes to, as the container sees it. */
export function targetFolder(download: Download, info: Info | null): string {
  const file = targetFile(download, info);
  return file.slice(0, file.lastIndexOf("/")) || "/";
}

export function failure(download: Download, info: Info | null): Failure {
  const code: Code = CODES.includes(download.error_code as Code) ? (download.error_code as Code) : "unknown";
  const path = targetFolder(download, info);
  const uid = info?.uid ?? "?";
  const gid = info?.gid ?? "?";
  const geo = download.film.geo.join(", ");
  const vars = { geo, uid, gid };
  const key = (part: string) => `err.${code}.${part}` as Key;
  return {
    code,
    title: t(key("title"), vars),
    text: t(key("text"), vars),
    solution: t(key("solution"), vars),
    command: code === "permission" ? `docker exec -u 0 mtdl-web chown -R ${uid}:${gid} "${path}"` : null,
    retryable: code !== "gone" && code !== "livestream",
    path,
  };
}

export function origin(download: Download, subscriptionName: (id: number) => string | undefined): string {
  const name = download.subscription_id != null ? subscriptionName(download.subscription_id) : undefined;
  if (download.subscription_id != null) return t("dl.sub", { name: name ?? `#${download.subscription_id}` });
  return t("dl.single");
}

export interface Progress {
  fraction: number | null;
  text: string; // "43 % · 115 MB von 267 MB"
  speed: string;
  eta: string;
}

/** Progress of a running download from the polled status (live) or the stored numbers. */
export function progress(download: Download, live: Running | undefined, unit: SpeedUnit): Progress {
  const done = live?.done ?? download.bytes_done;
  const total = live?.total ?? download.bytes_total;
  const speed = live?.speed ?? download.speed ?? 0;
  const phase = live?.phase ?? download.phase;
  const fraction = total ? Math.min(1, done / total) : null;
  const parts = [fraction != null ? F.percent(fraction) : null, total ? t("dl.of", { done: F.size(done), total: F.size(total) }) : F.size(done || null)];
  if (phase === "mux") parts.push(t("dl.phase_mux"));
  if (phase === "subtitles") parts.push(t("dl.phase_subtitles"));
  const remaining = total && speed > 0 ? (total - done) / speed : Number.NaN;
  return {
    fraction,
    text: parts.filter(Boolean).join(" · "),
    speed: speed > 0 ? F.speed(speed, unit) : "–",
    eta: Number.isFinite(remaining) ? t("dl.eta", { eta: F.eta(remaining) }) : "–",
  };
}

export const QUALITY_KEYS: Record<string, Key> = { hd: "q.hd", normal: "q.normal", low: "q.low" };

// Downloads: speed, limit and parallel downloads on top; then running, the queue (order
// by dragging or arrows) and failures with what happened and what helps.
import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";

import { api, type Download, errorMessage, type Running, type Subscription } from "../api";
import { ChannelBadge } from "../components/channel";
import { copyText, LimitFields, LimitSwitch, limitConversion } from "../components/controls";
import { failure, origin, progress, QUALITY_KEYS, targetFile } from "../download-texts";
import { limitTexts } from "../status-texts";
import * as F from "../format";
import { t } from "../i18n";
import { I, Icon } from "../icons";
import { go, type Route } from "../router";
import { type Layout, refreshStatus, saveSettings, toast, useInfo, useSettings, useStatus } from "../store";

interface Lists {
  running: Download[];
  queued: Download[];
  failed: Download[];
}

const MAX_PARALLEL = 10;
// A queued download that ran into its folder (download folder or library) waits for it.
const FOLDER_CODES = new Set(["filesystem", "permission", "not_mounted"]);

export function Downloads(props: { route: Route; layout: Layout }): JSX.Element {
  const mobile = props.layout === "mobile";
  const status = useStatus();
  const info = useInfo();
  const settings = useSettings();
  const [lists, setLists] = useState<Lists | null>(null);
  const [subs, setSubs] = useState<Subscription[]>([]);
  const [drag, setDrag] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const [tech, setTech] = useState<ReadonlySet<number>>(new Set());
  const [confirmAll, setConfirmAll] = useState(false);
  const failRef = useRef<HTMLHeadingElement>(null);
  const scrolled = useRef(false);
  const seq = useRef(0);

  const load = async () => {
    const mine = ++seq.current;
    try {
      const [running, queued, failed] = await Promise.all([
        api.downloads({ state: ["running"], limit: 100 }),
        api.downloads({ state: ["queued"], limit: 1000, order: "queue" }),
        api.downloads({ state: ["failed"], limit: 200 }),
      ]);
      if (mine === seq.current) setLists({ running: running.items, queued: queued.items, failed: failed.items });
    } catch (e) {
      if (mine === seq.current) toast(errorMessage(e));
    }
  };

  useEffect(() => {
    void api.subscriptions().then(setSubs, () => undefined);
  }, []);
  const countsKey = status ? JSON.stringify(status.downloads.counts) : "";
  useEffect(() => {
    void load();
  }, [countsKey]);

  // #/downloads?bereich=fehler (from the status bar) jumps to the failures once.
  useEffect(() => {
    if (props.route.query.bereich !== "fehler") {
      scrolled.current = false;
      return;
    }
    if (scrolled.current || !lists || !failRef.current) return;
    failRef.current.scrollIntoView({ block: "start" });
    scrolled.current = true;
  }, [lists, props.route.query.bereich]);

  const subscriptionName = (id: number) => subs.find((s) => s.id === id)?.name;
  const live = useMemo(() => new Map<number, Running>((status?.downloads.running ?? []).map((r) => [r.id, r])), [status]);
  const unit = status?.limit.speed_unit ?? "Mbit/s";
  const targetBroken = !!status?.writable.downloads;

  const act = async (run: () => Promise<unknown>, message?: string, undo?: () => Promise<unknown>) => {
    try {
      await run();
      if (message) toast(message, undo ? { label: t("dl.undo"), run: () => void act(undo) } : undefined);
    } catch (e) {
      toast(errorMessage(e));
    }
    await refreshStatus();
    await load();
  };

  // Everything waiting or loading at once, after a second click; the undo queues the same downloads again.
  const cancelAll = async () => {
    if (!confirmAll) {
      setConfirmAll(true);
      window.setTimeout(() => setConfirmAll(false), 4000);
      return;
    }
    setConfirmAll(false);
    try {
      const { cancelled } = await api.cancelDownloads();
      const undo = async () => {
        for (const id of cancelled) await api.retryDownload(id); // one by one: the queue keeps their order
      };
      toast(t("dl.cancelled_all", { n: cancelled.length }), cancelled.length ? { label: t("dl.undo"), run: () => void act(undo) } : undefined);
    } catch (e) {
      toast(errorMessage(e));
    }
    await refreshStatus();
    await load();
  };

  const move = (download: Download, index: number) => {
    if (!lists) return;
    // Show the new order at once; the server's answer follows with the reload.
    const queued = lists.queued.filter((d) => d.id !== download.id);
    queued.splice(Math.max(0, Math.min(index, queued.length)), 0, download);
    setLists({ ...lists, queued });
    void act(() => api.moveDownload(download.id, index));
  };

  const parallel = settings?.parallel_downloads ?? 2;
  const setParallel = (n: number) => {
    const value = Math.max(1, Math.min(MAX_PARALLEL, n));
    if (value !== parallel) void saveSettings({ parallel_downloads: value }).catch(() => toast(t("settings.save_failed")));
  };

  const counts = status?.downloads.counts ?? {};
  const nRun = lists?.running.length ?? counts.running ?? 0;
  const nQueued = lists?.queued.length ?? counts.queued ?? 0;
  const nFailed = lists?.failed.length ?? counts.failed ?? 0;
  const empty = !!lists && !nRun && !nQueued && !nFailed;
  const totalSpeed = status?.downloads.speed ?? 0;

  const header = (
    <div class="page-head">
      <h1>{t("dl.title")}</h1>
      <div class="dl-stats">
        <div class="stat">
          <span class="stat-label">{t("dl.total_now")}</span>
          <span class="row">
            {nRun > 0 && !targetBroken && <span class="dot rec pulse big-dot" />}
            <span class={`stat-value${targetBroken ? " err-text" : ""}`}>
              {targetBroken ? t("dl.paused_short") : nRun ? F.speed(totalSpeed, unit) : "–"}
            </span>
          </span>
          <span class="muted-text">{t("dl.counts", { run: nRun, wait: nQueued, failed: nFailed })}</span>
          {nRun + nQueued > 0 && (
            <div>
              <button class="button ghost small danger" onClick={() => void cancelAll()}>
                {confirmAll ? t("dl.cancel_all_confirm") : t("dl.cancel_all")}
              </button>
            </div>
          )}
        </div>
        {status && (
          <div class="stat divided">
            <span class="stat-label">{t("limit.title")}</span>
            <LimitFields limit={status.limit}>
              <LimitSwitch limit={status.limit} normal />
            </LimitFields>
            {status.limit.source !== "normal" && <span class="small hook-note">{limitTexts(status, info?.timezone).text}</span>}
            <span class="small muted-text">
              {limitConversion(status.limit)} · {t("dl.limit_hint")}
            </span>
          </div>
        )}
        <div class="stat divided">
          <span class="stat-label">{t("dl.parallel")}</span>
          <div class="stepper">
            <button aria-label={t("dl.less")} disabled={parallel <= 1} onClick={() => setParallel(parallel - 1)}>
              −
            </button>
            <span>{parallel}</span>
            <button aria-label={t("dl.more")} disabled={parallel >= MAX_PARALLEL} onClick={() => setParallel(parallel + 1)}>
              +
            </button>
          </div>
          <span class="small muted-text">{t("dl.parallel_hint")}</span>
        </div>
      </div>
    </div>
  );

  if (!lists) {
    return (
      <div class={`page${mobile ? " mobile" : ""}`}>
        {header}
        <div class="skeleton-list" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <div key={i} class="skel-row tall">
              <span class="skel" style={{ width: "46px", height: "16px" }} />
              <span class="skel grow" style={{ maxWidth: `${200 + i * 60}px` }} />
              <span class="skel push" style={{ width: "120px" }} />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (empty) {
    return (
      <div class={`page${mobile ? " mobile" : ""}`}>
        {header}
        <div class="empty">
          <h2>{t("dl.empty_title")}</h2>
          <p>{t("dl.empty_text")}</p>
          <div>
            <button class="button primary" onClick={() => go("/suche")}>
              {t("dl.to_search")}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div class={`page${mobile ? " mobile" : ""}`}>
      {header}

      <div class="section-head">
        <h2>{t("dl.running")}</h2>
        <span class="count">{nRun}</span>
        {targetBroken && nRun > 0 && <span class="small err-text">{t("dl.paused_note")}</span>}
      </div>
      {nRun === 0 ? (
        <div class="dashed">
          {t("dl.running_empty")}
          {nQueued > 0 && !targetBroken ? t("dl.next_soon") : ""}
        </div>
      ) : (
        <div class="rows">
          {lists.running.map((d) => {
            const p = progress(d, live.get(d.id), unit);
            const quality = t(QUALITY_KEYS[d.quality] ?? "q.hd");
            const sub = `${origin(d, subscriptionName)} · ${d.film.topic} · ${quality}`;
            const bar = (
              <span class="bar wide">
                <span style={{ width: `${((p.fraction ?? 0) * 100).toFixed(1)}%` }} />
              </span>
            );
            return (
              <div key={d.id} class="dl-row running">
                {!mobile && (
                  <span>
                    <ChannelBadge channel={d.film.channel} />
                  </span>
                )}
                <span class="dl-main">
                  <span class="row">
                    <span class={`dot ${targetBroken ? "muted" : "rec pulse"}`} />
                    <span class="ellipsis strong" title={d.film.title}>
                      {d.film.title}
                    </span>
                  </span>
                  <span class="ellipsis small faint">{sub}</span>
                  {mobile && (
                    <span class="dl-mobile-progress">
                      {bar}
                      <span class="small muted-text">
                        {p.text} · {p.speed} · {p.eta}
                      </span>
                    </span>
                  )}
                </span>
                {!mobile && (
                  <>
                    <span class="dl-progress">
                      {bar}
                      <span class="small muted-text">{p.text}</span>
                    </span>
                    <span class="num strong">{targetBroken ? t("dl.paused_short") : p.speed}</span>
                    <span class="num muted-text">{targetBroken ? "–" : p.eta}</span>
                  </>
                )}
                <button
                  class="icon-button"
                  title={t("dl.cancel")}
                  aria-label={t("dl.cancel")}
                  onClick={() => void act(() => api.cancelDownload(d.id), t("dl.cancelled"), () => api.retryDownload(d.id))}
                >
                  <Icon d={I.close} size={15} stroke={1.9} />
                </button>
              </div>
            );
          })}
        </div>
      )}

      <div class="section-head">
        <h2>{t("dl.queue")}</h2>
        <span class="count">{nQueued}</span>
        {nQueued > 1 && !mobile && <span class="small faint">{t("dl.queue_hint")}</span>}
      </div>
      {nQueued === 0 ? (
        <div class="dashed">{t("dl.queue_empty")}</div>
      ) : (
        <div class="rows">
          {lists.queued.map((d, i) => {
            const first = i === 0;
            const last = i === lists.queued.length - 1;
            const quality = t(QUALITY_KEYS[d.quality] ?? "q.hd");
            const later = d.retry_at
              ? t("dl.retry_at", { time: F.time(d.retry_at, info?.timezone) })
              : (d.error && FOLDER_CODES.has(d.error_code ?? "")) || (targetBroken && d.library_id === null)
                ? t("dl.waits_folder")
                : "";
            const sub = mobile
              ? [d.film.channel, origin(d, subscriptionName), d.film.topic, later].filter(Boolean).join(" · ")
              : [origin(d, subscriptionName), d.film.topic, later].filter(Boolean).join(" · ");
            const dropLine = over === i && drag !== null && drag !== i ? (drag < i ? " drop-below" : " drop-above") : "";
            return (
              <div
                key={d.id}
                class={`dl-row queued${drag === i ? " dragging" : ""}${dropLine}`}
                draggable={!mobile}
                onDragStart={(e) => {
                  e.dataTransfer?.setData("text/plain", String(d.id));
                  if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
                  setDrag(i);
                }}
                onDragOver={(e) => {
                  e.preventDefault();
                  if (over !== i) setOver(i);
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  const from = drag;
                  setDrag(null);
                  setOver(null);
                  const item = from !== null ? lists.queued[from] : undefined;
                  if (item && from !== i) move(item, i);
                }}
                onDragEnd={() => {
                  setDrag(null);
                  setOver(null);
                }}
              >
                <span class="grip" title={t("dl.drag")}>
                  <Icon d="M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01" size={14} stroke={3} />
                </span>
                <span class="pos">{i + 1}</span>
                {!mobile && (
                  <span>
                    <ChannelBadge channel={d.film.channel} />
                  </span>
                )}
                <span class="dl-main">
                  <span class="ellipsis strong" title={d.film.title}>
                    {d.film.title}
                  </span>
                  <span class="ellipsis small faint">{sub}</span>
                </span>
                {!mobile && (
                  <>
                    <span class="muted-text">{quality}</span>
                    <span class="num muted-text">{d.film.size ? F.size(d.film.size) : "–"}</span>
                  </>
                )}
                <span class="dl-buttons">
                  <button class="icon-button small-icon" title={t("dl.top")} aria-label={t("dl.top")} disabled={first} onClick={() => move(d, 0)}>
                    <Icon d="M5 4.5h14M12 20V9M7 13l5-5 5 5" size={14} stroke={1.9} />
                  </button>
                  <button class="icon-button small-icon" title={t("dl.up")} aria-label={t("dl.up")} disabled={first} onClick={() => move(d, i - 1)}>
                    <Icon d="M6 15l6-6 6 6" size={14} stroke={1.9} />
                  </button>
                  <button class="icon-button small-icon" title={t("dl.down")} aria-label={t("dl.down")} disabled={last} onClick={() => move(d, i + 1)}>
                    <Icon d={I.chevron} size={14} stroke={1.9} />
                  </button>
                  <button
                    class="icon-button small-icon danger-hover"
                    title={t("dl.remove")}
                    aria-label={t("dl.remove")}
                    onClick={() => void act(() => api.cancelDownload(d.id), t("dl.removed"), () => api.retryDownload(d.id))}
                  >
                    <Icon d={I.close} size={14} stroke={1.9} />
                  </button>
                </span>
              </div>
            );
          })}
        </div>
      )}

      <div class="section-head">
        <h2 ref={failRef}>{t("dl.failed_title")}</h2>
        <span class={`count${nFailed ? " err-text" : ""}`}>{nFailed}</span>
      </div>
      {nFailed === 0 ? (
        <div class="dashed">{t("dl.failed_empty")}</div>
      ) : (
        <div class="cards">
          {lists.failed.map((d) => {
            const f = failure(d, info);
            const open = tech.has(d.id);
            const [before, after] = f.text.split("{path}");
            const finished = d.finished_at ?? d.created_at;
            const today = F.date(finished, info?.timezone) === F.date(new Date().toISOString(), info?.timezone);
            return (
              <div key={d.id} class="fail-card">
                <Icon d={I.warn} size={18} stroke={1.8} class="err-icon" />
                <div class="fail-body">
                  <div class="row wrap baseline">
                    <strong class="fail-title">{f.title}</strong>
                    <span class="grow" />
                    <span class="small faint">
                      {today ? t("dl.today", { time: F.time(finished, info?.timezone) }) : F.dateTime(finished, info?.timezone)}
                    </span>
                  </div>
                  <div class="ellipsis small muted-text">
                    {d.film.channel} · {d.film.topic} · {d.film.title}
                  </div>
                  <div class="fail-text">
                    {before}
                    {after !== undefined && <code class="inline-code">{f.path}</code>}
                    {after}
                  </div>
                  <div class="solution">
                    <span class="stat-label">{t("dl.solution")}</span>
                    <span>{f.solution}</span>
                    {f.command && (
                      <div class="row wrap">
                        <code class="command grow">{f.command}</code>
                        <button class="button" onClick={() => copyText(f.command ?? "")}>
                          <Icon d={I.copy} size={14} />
                          {t("dl.copy_command")}
                        </button>
                      </div>
                    )}
                  </div>
                  <div class="row wrap">
                    {/* Always possible: for a lasting error (gone, live stream) it is offered, but not pushed. */}
                    <button class={`button${f.retryable ? " primary" : ""}`} onClick={() => void act(() => api.retryDownload(d.id), t("dl.retrying"))}>
                      <Icon d={I.retry} size={14} stroke={1.9} />
                      {f.retryable ? t("dl.retry") : t("dl.retry_anyway")}
                    </button>
                    <button class="button" onClick={() => copyText(targetFile(d, info))}>
                      {t("dl.copy_path")}
                    </button>
                    <button class="button ghost" onClick={() => void act(() => api.deleteDownload(d.id), t("dl.failed_removed"))}>
                      {t("dl.remove")}
                    </button>
                    <span class="grow" />
                    <button
                      class="link-button small faint"
                      aria-expanded={open}
                      onClick={() =>
                        setTech((s) => {
                          const next = new Set(s);
                          if (next.has(d.id)) next.delete(d.id);
                          else next.add(d.id);
                          return next;
                        })
                      }
                    >
                      {t("dl.tech")} {open ? "▴" : "▾"}
                    </button>
                  </div>
                  {open && <code class="tech">{d.error ?? ""}</code>}
                </div>
              </div>
            );
          })}
        </div>
      )}
      <div style={{ height: "32px" }} />
    </div>
  );
}

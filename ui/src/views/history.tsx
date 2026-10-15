// History: what was loaded, by day, with target path and size. Cancelled entries are listed
// too: they keep subscriptions from loading the episode again, and removing the entry frees it.
import type { JSX } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";

import { api, type Download, errorMessage, type Subscription } from "../api";
import { ChannelBadge } from "../components/channel";
import { copyText } from "../components/controls";
import { origin, targetFile } from "../download-texts";
import * as F from "../format";
import { t } from "../i18n";
import { I, Icon } from "../icons";
import { go, type Route, setQuery } from "../router";
import { fold } from "../search-logic";
import { type Layout, toast, useInfo, useStatus } from "../store";

const PAGE = 300;
const LRM = String.fromCharCode(0x200e); // keeps the path start visible in a right-to-left ellipsis

function dayKey(iso: string, timeZone: string | undefined): string {
  // en-CA formats as YYYY-MM-DD: a sortable day in the server's time zone
  return new Date(iso).toLocaleDateString("en-CA", { timeZone });
}

export function History(props: { route: Route; layout: Layout }): JSX.Element {
  const mobile = props.layout === "mobile";
  const info = useInfo();
  const status = useStatus();
  const timeZone = info?.timezone;
  const q = props.route.query.q ?? "";
  const abo = props.route.query.abo ?? "";
  const [items, setItems] = useState<Download[] | null>(null);
  const [complete, setComplete] = useState(false);
  const [subs, setSubs] = useState<Subscription[]>([]);
  const [draft, setDraft] = useState<string | null>(null);

  const load = async (more: boolean) => {
    const offset = more ? (items?.length ?? 0) : 0;
    const limit = more ? PAGE : Math.min(1000, Math.max(PAGE, items?.length ?? 0));
    try {
      const page = await api.downloads({ state: ["done", "cancelled"], limit, offset });
      setItems((list) => (more ? [...(list ?? []), ...page.items] : page.items));
      if (!more || page.items.length < limit) setComplete(page.items.length < limit);
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  useEffect(() => {
    void api.subscriptions().then(setSubs, () => undefined);
  }, []);
  const doneKey = status ? `${status.downloads.counts.done ?? 0}|${status.downloads.counts.cancelled ?? 0}` : "";
  useEffect(() => {
    void load(false);
  }, [doneKey]);

  const subscriptionName = (id: number) => subs.find((s) => s.id === id)?.name;
  const needle = fold(q);
  const shown = useMemo(() => {
    const list = (items ?? []).filter((d) => {
      if (abo === "-" && d.subscription_id != null) return false;
      if (abo && abo !== "-" && String(d.subscription_id) !== abo) return false;
      return !needle || fold(`${d.film.title} ${d.film.topic} ${d.path}`).includes(needle);
    });
    const when = (d: Download) => d.finished_at ?? d.created_at;
    return list.sort((a, b) => when(b).localeCompare(when(a)));
  }, [items, needle, abo]);

  const days = useMemo(() => {
    const map = new Map<string, Download[]>();
    for (const d of shown) {
      const key = dayKey(d.finished_at ?? d.created_at, timeZone);
      const list = map.get(key);
      if (list) list.push(d);
      else map.set(key, [d]);
    }
    return [...map];
  }, [shown, timeZone]);

  const size = (list: Download[]) => F.size(list.reduce((sum, d) => sum + (d.state === "done" ? d.bytes_done : 0), 0) || null);
  const today = dayKey(new Date().toISOString(), timeZone);
  const yesterday = dayKey(new Date(Date.now() - 86_400_000).toISOString(), timeZone);
  const dayTitle = (key: string) => {
    const noon = `${key}T12:00:00Z`;
    if (key === today) return `${t("hist.today")} · ${F.date(noon, "UTC")}`;
    if (key === yesterday) return `${t("hist.yesterday")} · ${F.date(noon, "UTC")}`;
    return F.weekdayDate(noon, "UTC");
  };

  const filtered = !!(q || abo);
  const total = items?.length ?? 0;
  const summary = total
    ? t("hist.summary", { n: F.num(total), size: size(items ?? []) }) + (filtered ? t("hist.shown", { n: F.num(shown.length) }) : "")
    : t("hist.nothing");

  // A cancelled download is not loaded again by any subscription; from here it goes back into the queue.
  const retry = async (d: Download) => {
    try {
      await api.retryDownload(d.id);
      setItems((list) => list?.filter((x) => x.id !== d.id) ?? null);
      toast(t("dl.retrying"));
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  const remove = async (d: Download) => {
    try {
      await api.deleteDownload(d.id);
      setItems((list) => list?.filter((x) => x.id !== d.id) ?? null);
      toast(t("hist.removed"));
    } catch (e) {
      toast(errorMessage(e));
    }
  };

  return (
    <div class={`page${mobile ? " mobile" : ""}`}>
      <div class="page-head">
        <div class="head-line">
          <div class="head-title">
            <h1>{t("hist.title")}</h1>
            <span class="muted-text">{items ? summary : ""}</span>
          </div>
          <label class="field-search">
            <Icon d={I.search} size={15} class="faint" />
            <input
              value={draft ?? q}
              placeholder={t("hist.search")}
              aria-label={t("hist.search")}
              onInput={(e) => {
                const value = (e.target as HTMLInputElement).value;
                setDraft(value);
                setQuery({ q: value });
              }}
              onBlur={() => setDraft(null)}
            />
          </label>
          <select class="input field-select" value={abo} aria-label={t("hist.filter")} onChange={(e) => setQuery({ abo: (e.target as HTMLSelectElement).value })}>
            <option value="">{t("hist.all")}</option>
            <option value="-">{t("hist.single")}</option>
            {subs.map((s) => (
              <option key={s.id} value={String(s.id)}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      {!items ? (
        <div class="skeleton-list" aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} class="skel-row tall">
              <span class="skel" style={{ width: "40px" }} />
              <span class="skel" style={{ width: "46px", height: "16px" }} />
              <span class="skel grow" style={{ maxWidth: `${220 + i * 40}px` }} />
              <span class="skel push" style={{ width: "160px" }} />
            </div>
          ))}
        </div>
      ) : !shown.length ? (
        <div class="empty">
          <h2>{filtered ? t("hist.empty_filtered") : t("hist.nothing")}</h2>
          <p>{filtered ? t("hist.empty_filtered_text") : t("hist.empty_text")}</p>
          <div>
            <button class="button" onClick={() => (filtered ? setQuery({ q: "", abo: "" }) : go("/suche"))}>
              {filtered ? t("hist.reset") : t("dl.to_search")}
            </button>
          </div>
        </div>
      ) : (
        <>
          {days.map(([key, list]) => (
            <div key={key}>
              <div class="day-head">
                <strong>{dayTitle(key)}</strong>
                <span class="small faint">{t("hist.day_meta", { n: list.length, size: size(list) })}</span>
              </div>
              {list.map((d) => {
                const imported = !d.path; // taken over from MediathekView: no file of ours, only the day is known
                const when = imported ? "–" : F.time(d.finished_at ?? d.created_at, timeZone);
                const cancelled = d.state === "cancelled";
                const sub = `${origin(d, subscriptionName)} · ${t("hist.sent", { date: F.date(d.film.start, timeZone) })}`;
                const path = imported ? t("hist.imported") : targetFile(d, info);
                const sizeText = cancelled ? t("hist.cancelled") : imported ? "–" : F.size(d.bytes_done || null);
                if (mobile) {
                  return (
                    <div key={d.id} class={`hist-item${cancelled ? " cancelled" : ""}`}>
                      <span class="hist-time">{when}</span>
                      <div class="dl-main">
                        <span class="strong wrap-text">{d.film.title}</span>
                        <span class="small faint">
                          {d.film.channel} · {sub} · {sizeText}
                        </span>
                        <span class="mono small muted-text break">{path}</span>
                      </div>
                      {cancelled && (
                        <button class="hit-round" aria-label={t("hist.retry")} onClick={() => void retry(d)}>
                          <Icon d={I.retry} size={16} stroke={1.8} />
                        </button>
                      )}
                      <button class="hit-round" aria-label={t("hist.remove_title")} onClick={() => void remove(d)}>
                        <Icon d={I.trash} size={16} stroke={1.8} />
                      </button>
                    </div>
                  );
                }
                return (
                  <div key={d.id} role="row" class={`hist-row${cancelled ? " cancelled" : ""}`}>
                    <span class="hist-time">{when}</span>
                    <span>
                      <ChannelBadge channel={d.film.channel} />
                    </span>
                    <span class="dl-main">
                      <span class="ellipsis strong" title={d.film.title}>
                        {d.film.title}
                      </span>
                      <span class="ellipsis small faint">{sub}</span>
                    </span>
                    <span class={imported ? "small faint" : "path-rtl"} title={path}>
                      {imported ? path : LRM + path + LRM}
                    </span>
                    <span class={`num ${cancelled ? "faint" : "muted-text"}`} title={cancelled ? t("hist.cancelled_title") : undefined}>
                      {sizeText}
                    </span>
                    <span class="row end">
                      {!imported && (
                        <button class="icon-button small-icon" title={t("hist.copy")} aria-label={t("hist.copy")} onClick={() => copyText(path)}>
                          <Icon d={I.copy} size={14} />
                        </button>
                      )}
                      {cancelled && (
                        <button class="button small" title={t("hist.retry_title")} onClick={() => void retry(d)}>
                          {t("hist.retry")}
                        </button>
                      )}
                      <button class="button ghost small" title={t("hist.remove_title")} onClick={() => void remove(d)}>
                        {t("hist.remove")}
                      </button>
                    </span>
                  </div>
                );
              })}
            </div>
          ))}
          {!complete && (
            <div class="more">
              <button class="button ghost" onClick={() => void load(true)}>
                {t("hist.more")}
              </button>
            </div>
          )}
          <p class="page-note">{t("hist.note")}</p>
        </>
      )}
    </div>
  );
}

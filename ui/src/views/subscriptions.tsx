// Subscriptions: every subscription with its rules in one line, state, last episode, count,
// next check; pause, resume and check from the list.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";

import { api, ApiError, errorMessage, type Subscription, type SubscriptionDraft } from "../api";
import * as F from "../format";
import { t, type Key } from "../i18n";
import { I, Icon } from "../icons";
import { errorKey } from "../library-logic";
import { go } from "../router";
import { summary } from "../rule-texts";
import { type Layout, refreshStatus, toast, useInfo, useStatus } from "../store";

export function draftOf(sub: Subscription): SubscriptionDraft {
  return {
    name: sub.name,
    query: sub.query,
    include_description: sub.include_description,
    source: sub.source,
    conditions: sub.conditions,
    naming: sub.naming,
    quality: sub.quality,
    subtitles: sub.subtitles,
    state: sub.state,
    valid_until: sub.valid_until ?? null,
    on_expiry: sub.on_expiry ?? "pause",
    special_versions: sub.special_versions ?? "setting",
  };
}

type Tone = "active" | "paused" | "error";

function toneOf(sub: Subscription): Tone {
  if (sub.last_error || (sub.stats.failed ?? 0) > 0) return "error";
  return sub.state === "paused" ? "paused" : "active";
}

export function Subscriptions(props: { layout?: Layout }): JSX.Element {
  const mobile = props.layout === "mobile";
  const info = useInfo();
  const status = useStatus();
  const [subs, setSubs] = useState<Subscription[] | null>(null);
  const [busy, setBusy] = useState<number | "all" | null>(null);

  const load = async () => {
    try {
      setSubs(await api.subscriptions());
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  const countsKey = status ? JSON.stringify(status.downloads.counts) : "";
  useEffect(() => {
    void load();
  }, [countsKey]);

  const checkAll = async () => {
    setBusy("all");
    try {
      const result = await api.checkSubscriptions();
      toast(t("subs.checked_all", { checked: result.checked, queued: result.queued }));
    } catch (e) {
      toast(errorMessage(e));
    }
    setBusy(null);
    await refreshStatus();
    await load();
  };

  const check = async (sub: Subscription) => {
    setBusy(sub.id);
    try {
      const result = await api.checkSubscription(sub.id);
      toast(t("subs.checked_one", { name: sub.name, queued: result.queued }));
    } catch (e) {
      toast(errorMessage(e));
    }
    setBusy(null);
    await refreshStatus();
    await load();
  };

  const toggle = async (sub: Subscription) => {
    const state = sub.state === "paused" ? "active" : "paused";
    try {
      await api.saveSubscription(sub.id, { ...draftOf(sub), state });
      toast(t(state === "paused" ? "subs.paused_toast" : "subs.resumed_toast", { name: sub.name }));
    } catch (e) {
      const key = errorKey(e instanceof ApiError ? e.code : undefined);
      toast(key ? t(key as Key) : errorMessage(e));
    }
    await load();
  };

  // Valid until a day: shown under the state, so a paused subscription's reason stays visible.
  const until = (sub: Subscription) =>
    sub.valid_until
      ? t(sub.on_expiry === "delete" ? "subs.until_delete" : "subs.until_pause", { date: F.date(`${sub.valid_until}T12:00:00Z`, info?.timezone) })
      : null;

  const list = subs ?? [];
  const count = (tone: Tone) => list.filter((s) => toneOf(s) === tone).length;
  const parts = [t("subs.count", { n: list.length })];
  if (count("active")) parts.push(t("subs.active_n", { n: count("active") }));
  if (count("paused")) parts.push(t("subs.paused_n", { n: count("paused") }));
  if (count("error")) parts.push(t("subs.error_n", { n: count("error") }));

  const next = status?.filmlist.next_run;
  const nextText = (sub: Subscription) => {
    if (sub.state === "paused") return t("subs.paused");
    if (status?.filmlist.running) return t("subs.next_running");
    if (!next) return "–";
    const today = F.date(new Date().toISOString(), info?.timezone) === F.date(next, info?.timezone);
    return today ? t("subs.next_today", { time: F.time(next, info?.timezone) }) : F.dateTime(next, info?.timezone);
  };
  const lastText = (sub: Subscription) => sub.stats.last_title ?? t("subs.no_episode");
  const lastDate = (sub: Subscription) =>
    sub.stats.last_finished ? t("subs.loaded_at", { date: F.date(new Date(sub.stats.last_finished * 1000).toISOString(), info?.timezone) }) : "–";

  const pill = (sub: Subscription) => {
    const tone = toneOf(sub);
    const label = tone === "error" ? t("subs.error") : tone === "paused" ? t("subs.paused") : t("subs.active");
    return (
      <span class={`state-pill ${tone}`}>
        <span class="state-dot" />
        {label}
      </span>
    );
  };
  const problem = (sub: Subscription) => {
    if (sub.last_error) return { text: t("subs.check_failed"), title: sub.last_error, go: null };
    if ((sub.stats.failed ?? 0) > 0) return { text: t("subs.failed_downloads", { n: sub.stats.failed ?? 0 }), title: "", go: "/downloads?bereich=fehler" };
    return null;
  };

  return (
    <div class={`page${mobile ? " mobile" : ""}`}>
      <div class="page-head">
        <div class="head-line">
          <div class="head-title">
            <h1>{t("subs.title")}</h1>
            <span class="muted-text">{subs ? (list.length ? parts.join(" · ") : t("subs.nothing")) : ""}</span>
          </div>
          {list.length > 0 && (
            <button class="button" onClick={() => void checkAll()} disabled={busy !== null}>
              {t("subs.check_all")}
            </button>
          )}
          <button class="button primary" onClick={() => go("/suche")}>
            <Icon d={I.plus} size={14} stroke={2.2} />
            {t("subs.new")}
          </button>
        </div>
      </div>

      {!subs ? (
        <div aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} class="skel-row tall">
              <span class="skel-stack grow">
                <span class="skel" style={{ width: `${120 + ((i * 31) % 90)}px`, height: "11px" }} />
                <span class="skel" style={{ width: `${220 + ((i * 47) % 120)}px`, height: "9px" }} />
              </span>
              <span class="skel" style={{ width: "70px", height: "18px", borderRadius: "9px" }} />
              <span class="skel" style={{ width: "160px" }} />
            </div>
          ))}
        </div>
      ) : !list.length ? (
        <div class="empty">
          <div class="stripes" aria-hidden="true">
            {[0, 1, 2, 3, 4, 5, 6].map((i) => (
              <span key={i} />
            ))}
          </div>
          <h2>{t("subs.empty_title")}</h2>
          <p>{t("subs.empty_text")}</p>
          <div>
            <button class="button primary" onClick={() => go("/suche")}>
              {t("dl.to_search")}
            </button>
          </div>
        </div>
      ) : (
        <>
          {!mobile && (
            <div role="row" class="sub-row head">
              <span>{t("subs.col_name")}</span>
              <span>{t("subs.col_state")}</span>
              <span>{t("subs.col_last")}</span>
              <span class="num">{t("subs.col_done")}</span>
              <span>{t("subs.col_next")}</span>
              <span />
            </div>
          )}
          {list.map((sub) => {
            const rules = summary(sub.conditions);
            const issue = problem(sub);
            const paused = sub.state === "paused";
            const open = () => go(`/abos/${sub.id}`);
            if (mobile) {
              return (
                <div key={sub.id} class="sub-card" onClick={open}>
                  <div class="row">
                    <span class="grow ellipsis sub-name">{sub.name}</span>
                    {pill(sub)}
                  </div>
                  <div class="mono small muted-text">{rules}</div>
                  <div class="small">
                    {lastText(sub)} <span class="faint">· {lastDate(sub)}</span>
                  </div>
                  {issue && (
                    <div class="small err-text" title={issue.title}>
                      {issue.text}
                    </div>
                  )}
                  <div class="small faint">
                    {t("subs.loaded_n", { n: F.num(sub.stats.done ?? 0) })} · {nextText(sub)}
                  </div>
                </div>
              );
            }
            return (
              <div key={sub.id} role="row" class="sub-row" onClick={open}>
                <span class="dl-main">
                  <span class="ellipsis sub-name">{sub.name}</span>
                  <span class="ellipsis mono small muted-text" title={rules}>
                    {rules}
                  </span>
                </span>
                <span class="sub-state">
                  {pill(sub)}
                  {until(sub) && <span class="small faint">{until(sub)}</span>}
                  {issue && (
                    <button
                      class="link-button small err-text underline"
                      title={issue.title}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (issue.go) go(issue.go);
                        else open();
                      }}
                    >
                      {issue.text}
                    </button>
                  )}
                </span>
                <span class="dl-main">
                  <span class="ellipsis">{lastText(sub)}</span>
                  <span class="small faint">{lastDate(sub)}</span>
                </span>
                <span class="num strong">{F.num(sub.stats.done ?? 0)}</span>
                <span class={paused ? "faint" : "muted-text"}>{nextText(sub)}</span>
                <span class="row end" onClick={(e) => e.stopPropagation()}>
                  <button
                    class="square-button"
                    title={paused ? t("subs.resume") : t("subs.pause")}
                    aria-label={paused ? t("subs.resume") : t("subs.pause")}
                    onClick={() => void toggle(sub)}
                  >
                    <Icon d={paused ? I.play : I.pause} size={14} stroke={2} fill={paused} />
                  </button>
                  <button
                    class="square-button"
                    title={t("subs.check")}
                    aria-label={t("subs.check")}
                    disabled={busy !== null}
                    onClick={() => void check(sub)}
                  >
                    <Icon d={I.retry} size={14} stroke={1.9} />
                  </button>
                </span>
              </div>
            );
          })}
          <p class="page-note">{t("subs.note")}</p>
        </>
      )}
    </div>
  );
}

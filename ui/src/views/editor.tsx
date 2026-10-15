// Subscription editor: rules on the left as a waterfall (each shows how many hits it
// excludes), the live preview from the backend on the right, suggestions from the search and from thumbs.
import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";

import {
  api,
  ApiError,
  type Condition,
  errorMessage,
  type Field,
  type Film,
  type Naming,
  type Operator,
  type SpecialVersions,
  type Preview,
  type PreviewItem,
  type Quality,
  type Scheme,
  type Series,
  type SeriesCandidate,
  type Source,
  type Subscription,
  type SubscriptionDraft,
} from "../api";
import { ChannelBadge } from "../components/channel";
import { Switch } from "../components/controls";
import * as F from "../format";
import { t, type Key } from "../i18n";
import { I, Icon } from "../icons";
import { autoSeries, episodeCode, errorKey, folder, libraryFor, reasonKey } from "../library-logic";
import { comparePreview, nextPreviewSort, parsePreviewSort, type PreviewSortKey } from "../preview-sort";
import { build, go, type Route, setQuery } from "../router";
import {
  defaultCondition,
  FIELDS,
  fieldLabel,
  fieldType,
  operatorLabel,
  operatorsFor,
  reason,
  regexProblem,
  shortText,
  TEMPLATES,
} from "../rule-texts";
import { fold } from "../search-logic";
import { type Layout, refreshStatus, toast, useInfo, useLibraries, useSettings, useStatus } from "../store";
import { learn, type Lesson, passes, suggestFromSearch } from "../suggest";
import { draftOf } from "./subscriptions";

// -- model -----------------------------------------------------------------------------------------------------------

interface Rule extends Condition {
  key: number;
  suggested?: boolean;
  regex: boolean;
  enabled: boolean;
}

interface Draft {
  name: string;
  query: string;
  include_description: boolean;
  source: Source | null;
  rules: Rule[];
  naming: Naming;
  quality: Quality | null;
  subtitles: boolean | null;
  state: "active" | "paused";
  valid_until: string | null; // ISO date, the last day it searches
  on_expiry: "pause" | "delete";
  special_versions: SpecialVersions; // audio description, sign language
}

type Section = "ziel" | "optionen";

/** "2026-11-05" plus some days, as ISO date (noon UTC: no daylight saving step moves the day). */
function plusDays(iso: string, days: number): string {
  const day = new Date(`${iso}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() + days);
  return day.toISOString().slice(0, 10);
}

interface RuleError {
  value: string; // the value the server rejected
  position: number | null;
  message: string;
}

let lastKey = 0;
const keyed = (c: Condition, suggested = false): Rule => ({
  ...c,
  key: ++lastKey,
  suggested,
  regex: !!c.regex,
  enabled: c.enabled !== false,
});

const valueText = (r: Condition) => (Array.isArray(r.value) ? r.value.join("|") : String(r.value));

/** Complete enough to send: a value of the right kind (the server judges regexes and the rest). */
function complete(r: Rule): boolean {
  switch (fieldType(r.field)) {
    case "channel":
      return Array.isArray(r.value) && r.value.length > 0;
    case "date":
      return typeof r.value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(r.value);
    case "duration":
      return typeof r.value === "number" && Number.isInteger(r.value) && r.value >= 0;
    default:
      return typeof r.value === "string" && r.value.trim() !== "";
  }
}

function toCondition(r: Rule): Condition {
  return { field: r.field, operator: r.operator, value: r.value, regex: r.regex, enabled: r.enabled };
}

function draftFrom(sub: Subscription): Draft {
  return {
    name: sub.name,
    query: sub.query,
    include_description: sub.include_description ?? false,
    source: sub.source ?? null,
    rules: sub.conditions.map((c) => keyed(c)),
    naming: {
      scheme: sub.naming.scheme,
      show: sub.naming.show ?? null,
      folder: sub.naming.folder ?? null,
      season: sub.naming.season ?? null,
      template: sub.naming.template ?? null,
      target: sub.naming.target ?? "downloads",
      tmdb_id: sub.naming.tmdb_id ?? null,
      library_id: sub.naming.library_id ?? null,
    },
    quality: sub.quality ?? null,
    subtitles: sub.subtitles ?? null,
    state: sub.state ?? "active",
    valid_until: sub.valid_until ?? null,
    on_expiry: sub.on_expiry ?? "pause",
    special_versions: sub.special_versions ?? "setting",
  };
}

function todayIn(timeZone: string | undefined): string {
  return new Date().toLocaleDateString("en-CA", { timeZone });
}

/** An API error as text: the library features' codes from the catalogue, else what the server said. */
function apiText(e: unknown): string {
  const key = errorKey(e instanceof ApiError ? e.code : undefined);
  return key ? t(key as Key) : errorMessage(e);
}

/** "S01E39" or the date of a target path, for the preview. */
function codeOf(path: string | null): string {
  if (!path) return "";
  const name = path.slice(path.lastIndexOf("/") + 1);
  const episode = /S(\d+)E(\d+)/.exec(name);
  if (episode) return episode[0];
  return /\d{4}-\d{2}-\d{2}/.exec(name)?.[0] ?? "";
}

// -- the view ---------------------------------------------------------------------------------------------------------

export function SubscriptionEditor(props: { route: Route; layout: Layout }): JSX.Element {
  const { route, layout } = props;
  const mobile = layout === "mobile";
  const info = useInfo();
  const status = useStatus();
  const settings = useSettings();
  const libraries = useLibraries();
  const idParam = route.params[0] ?? "neu";
  const isNew = idParam === "neu";
  const subId = isNew ? null : Number(idParam);
  // Rules always show; target and options open on demand, kept in the hash ("offen=ziel,optionen", "keins" for
  // none). A new subscription starts with the target open: download folder or library is a choice to make once.
  // Old links with "reiter=" still open that section.
  const legacyTab = route.query.reiter === "ziel" || route.query.reiter === "optionen" ? route.query.reiter : undefined;
  const openParam = route.query.offen ?? legacyTab ?? (isNew ? "ziel" : "");
  const openSections = new Set(openParam.split(",").filter((s): s is Section => s === "ziel" || s === "optionen"));
  const show = route.query.zeige ?? "";
  const previewSort = parsePreviewSort(route.query.sort);
  const loadKey = isNew ? `neu|${route.query.q ?? ""}|${route.query.von ?? ""}` : idParam;

  const [draft, setDraft] = useState<Draft | null>(null);
  const [saved, setSaved] = useState<Subscription | null>(null);
  const [missing, setMissing] = useState(false);
  const [banner, setBanner] = useState(false);
  const [preview, setPreview] = useState<{ data: Preview; sent: number[] } | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [errors, setErrors] = useState<ReadonlyMap<number, RuleError>>(new Map());
  const [votes, setVotes] = useState<ReadonlyMap<string, "up" | "down">>(new Map());
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(new Set());
  const [flash, setFlash] = useState<number | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [channelFor, setChannelFor] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0); // a new preview after changes the draft does not show (released episodes)
  const [confirmCancel, setConfirmCancel] = useState(false);
  // Library target: the TMDB series of the subscription, and the search to choose it.
  const [series, setSeries] = useState<Series | null>(null);
  const [seriesError, setSeriesError] = useState<string | null>(null);
  const [seriesQuery, setSeriesQuery] = useState("");
  const [candidates, setCandidates] = useState<SeriesCandidate[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [picking, setPicking] = useState(false);
  const [autoPicked, setAutoPicked] = useState(false);
  const flashTimer = useRef<number | undefined>(undefined);
  const seq = useRef(0);
  const ruleRefs = useRef(new Map<number, HTMLDivElement>());
  const sectionRefs = useRef(new Map<Section, HTMLDivElement>());

  // -- loading ------------------------------------------------------------------------------------------------------

  useEffect(() => {
    let cancelled = false;
    setDraft(null);
    setPreview(null);
    setMissing(false);
    setErrors(new Map());
    setVotes(new Map());
    setDismissed(new Set());
    (async () => {
      if (!isNew) {
        try {
          const sub = await api.subscription(Number(idParam));
          if (cancelled) return;
          setSaved(sub);
          setDraft(draftFrom(sub));
          setBanner(false);
        } catch {
          if (!cancelled) setMissing(true);
        }
        return;
      }
      const q = route.query.q ?? "";
      const films = q.trim() ? (await api.search({ q, limit: 2000 }).catch(() => null))?.films ?? [] : [];
      const vonId = route.query.von;
      const from = vonId ? (films.find((f) => f.id === vonId) ?? (await api.film(vonId).catch(() => null))) : null;
      if (cancelled) return;
      const start = suggestFromSearch(films, q, from);
      setSaved(null);
      setDraft({
        name: start.name,
        query: q,
        include_description: false,
        source: null,
        rules: start.conditions.map((c) => keyed(c, true)),
        naming: {
          scheme: start.scheme,
          show: null,
          folder: null,
          season: null,
          template: null,
          target: "downloads",
          tmdb_id: null,
          library_id: null,
        },
        quality: null,
        subtitles: null,
        state: "active",
        valid_until: null,
        on_expiry: "pause",
        special_versions: "setting",
      });
      setBanner(start.conditions.length > 0);
    })();
    return () => {
      cancelled = true;
    };
  }, [loadKey]);

  useEffect(() => () => window.clearTimeout(flashTimer.current), []);

  // -- preview --------------------------------------------------------------------------------------------------------

  // What goes to the server: complete rules without a known error, in order; ``sent`` maps back to rule keys.
  const sendable = useMemo(() => {
    if (!draft) return null;
    const rules = draft.rules.filter((r) => complete(r) && !(errors.get(r.key)?.value === valueText(r)));
    return { rules, keys: rules.map((r) => r.key) };
  }, [draft, errors]);

  const previewBody = draft && sendable
    ? {
        query: draft.query,
        include_description: draft.include_description,
        source: draft.source,
        conditions: sendable.rules.map(toCondition),
        naming: draft.naming,
      }
    : null;
  const previewKey = previewBody ? `${refresh}|${JSON.stringify(previewBody)}` : "";

  useEffect(() => {
    if (!previewBody || !sendable) return;
    const mine = ++seq.current;
    setPreviewBusy(true);
    const timer = window.setTimeout(() => {
      api
        .preview(previewBody, subId)
        .then((data) => {
          if (mine !== seq.current) return;
          setPreview({ data, sent: sendable.keys });
          setPreviewError(null);
        })
        .catch((e: unknown) => {
          if (mine !== seq.current) return;
          const detail = e instanceof ApiError && e.detail && typeof e.detail === "object" ? (e.detail as Record<string, unknown>) : null;
          const index = typeof detail?.condition === "number" ? detail.condition : null;
          const key = index !== null ? sendable.keys[index] : undefined;
          const rule = key !== undefined ? draft?.rules.find((r) => r.key === key) : undefined;
          if (rule && key !== undefined) {
            const params = (detail?.params ?? {}) as { position?: number | null; message?: string };
            setErrors((m) => new Map(m).set(key, { value: valueText(rule), position: params.position ?? null, message: params.message ?? String(detail?.code ?? "") }));
          } else setPreviewError(apiText(e));
        })
        .finally(() => {
          if (mine === seq.current) setPreviewBusy(false);
        });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [previewKey]);

  // The assigned TMDB series (library target): name, seasons and the folders its library has of it already.
  const tmdbId = draft?.naming.target === "library" ? (draft.naming.tmdb_id ?? null) : null;
  const seriesLibrary = draft?.naming.target === "library" ? libraryFor(libraries, draft.naming.library_id, "series") : null;
  const seriesLibraryId = seriesLibrary?.id ?? null;
  useEffect(() => {
    setSeries(null);
    setSeriesError(null);
    if (tmdbId === null) return;
    let cancelled = false;
    api.series(tmdbId, seriesLibraryId).then(
      (s) => !cancelled && setSeries(s),
      (e: unknown) => !cancelled && setSeriesError(apiText(e)),
    );
    return () => {
      cancelled = true;
    };
  }, [tmdbId, seriesLibraryId]);

  // -- editing helpers ----------------------------------------------------------------------------------------------

  const update = (patch: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...patch } : d));
  const updateRule = (key: number, change: (r: Rule) => Rule) =>
    setDraft((d) => (d ? { ...d, rules: d.rules.map((r) => (r.key === key ? change(r) : r)) } : d));
  const setNaming = (patch: Partial<Naming>) => setDraft((d) => (d ? { ...d, naming: { ...d.naming, ...patch } } : d));

  const flashRule = (key: number) => {
    window.clearTimeout(flashTimer.current);
    setFlash(key);
    window.setTimeout(() => ruleRefs.current.get(key)?.scrollIntoView({ block: "nearest", behavior: "smooth" }), 50);
    flashTimer.current = window.setTimeout(() => setFlash(null), 1800);
  };
  const addRule = (c: Condition, suggested = false) => {
    const rule = keyed(c, suggested);
    setDraft((d) => (d ? { ...d, rules: [...d.rules, rule] } : d));
    setAddOpen(false);
    flashRule(rule.key);
  };

  if (missing) {
    return (
      <div class="page">
        <div class="empty">
          <h2>{t("ed.missing_title")}</h2>
          <p>{t("ed.missing_text")}</p>
          <div>
            <button class="button primary" onClick={() => go("/abos")}>
              {t("ed.to_list")}
            </button>
          </div>
        </div>
      </div>
    );
  }
  if (!draft) {
    return (
      <div class="editor loading" aria-busy="true">
        <div class="editor-skel">
          <span class="skel" style={{ width: "180px", height: "16px" }} />
          <span class="skel block" style={{ height: "84px" }} />
          <span class="skel block" style={{ height: "84px" }} />
        </div>
        {!mobile && (
          <div class="editor-skel">
            <span class="skel" style={{ width: "240px", height: "28px" }} />
            {[0, 1, 2].map((i) => (
              <span key={i} class="skel block" style={{ height: "40px" }} />
            ))}
          </div>
        )}
      </div>
    );
  }

  // -- derived from the preview -------------------------------------------------------------------------------------

  const data = preview?.data ?? null;
  const sentKeys = preview?.sent ?? [];
  const indexOf = (key: number) => sentKeys.indexOf(key);
  const items = data?.items ?? [];
  const byStatus = (...states: PreviewItem["status"][]) => items.filter((i) => states.includes(i.status));
  const loadItems = byStatus("load");
  const existItems = byStatus("exists", "failed", "cancelled", "skipped");
  const excludedItems = byStatus("excluded", "unavailable");
  const duplicates = byStatus("duplicate");
  const unmatchedItems = byStatus("unmatched"); // library target: no TMDB episode found
  const alsoOf = (film: Film) => duplicates.filter((d) => d.duplicate_of === film.id).map((d) => d.film.channel);
  const total = data?.total ?? 0;
  const steps = data?.steps ?? [];
  const included = total - excludedItems.length;
  // The fixed step after the conditions: audio description, sign language and the like.
  const specialStep = steps.find((s) => s.kind === "special_versions");
  const bySpecial = (item: PreviewItem) => item.status === "excluded" && specialStep !== undefined && item.excluded_by === specialStep.index;

  const activeTitleKey = draft.rules.find(
    (r) => r.enabled && r.field === "title" && (r.regex || r.operator === "contains" || r.operator === "not_contains") && indexOf(r.key) >= 0,
  )?.key;

  const ruleNr = (key: number) => draft.rules.findIndex((r) => r.key === key) + 1;
  const ruleByIndex = (index: number | null) => (index === null ? undefined : draft.rules.find((r) => r.key === sentKeys[index]));
  const filterRule = show.startsWith("regel:") ? Number(show.slice(6)) : null;

  // -- suggestions from thumbs ---------------------------------------------------------------------------------------

  const includedFilms = [...loadItems, ...existItems, ...duplicates].map((i) => i.film);
  const down = includedFilms.filter((f) => votes.get(f.id) === "down");
  const up = includedFilms.filter((f) => votes.get(f.id) === "up");
  const kept = includedFilms.filter((f) => votes.get(f.id) !== "down");
  const downKey = down.map((f) => f.id).sort().join(",");
  let suggestion: JSX.Element | null = null;
  if (down.length && !dismissed.has(downKey)) {
    const topics = [...draft.rules.filter((r) => r.enabled && r.field === "topic" && !r.regex).map((r) => String(r.value)), draft.name];
    const lesson: Lesson | null = learn(down, kept, topics, up);
    if (lesson) {
      const text =
        lesson.kind === "text"
          ? [t(lesson.many ? "learn.text_many" : "learn.text_one", { value: lesson.value }), t("learn.ask_text", { value: lesson.value })]
          : lesson.kind === "numbered"
            ? [t("learn.numbered"), t("learn.ask_numbered")]
            : lesson.kind === "topic"
              ? [t("learn.topic"), t("learn.ask_topic", { topic: lesson.topic })]
              : lesson.kind === "duration"
                ? [t("learn.duration", { shortest: lesson.shortest }), t("learn.ask_duration", { minutes: lesson.minutes })]
                : [t("learn.none"), t("learn.ask_none")];
      const condition = lesson.kind === "none" ? null : lesson.condition;
      const effect = condition ? includedFilms.filter((f) => !passes(condition, f)).length : 0;
      suggestion = (
        <div role="status" class="suggestion">
          <Icon d="M12 3l1.9 5.6L19.5 10.5l-5.6 1.9L12 18l-1.9-5.6L4.5 10.5l5.6-1.9z" size={16} stroke={1.7} class="accent-text" />
          <div class="grow suggestion-body">
            <div>
              <strong>{text[0]}</strong> {text[1]}
            </div>
            {condition && (
              <div class="small muted-text">
                {t("learn.effect", { n: effect, total: includedFilms.length, left: includedFilms.length - effect })}
              </div>
            )}
            <div class="row wrap">
              {condition && (
                <button class="button primary small" onClick={() => addRule(condition, true)}>
                  {t("learn.add")}
                </button>
              )}
              <button class="button ghost small" onClick={() => setDismissed((s) => new Set(s).add(downKey))}>
                {t("learn.dismiss")}
              </button>
            </div>
          </div>
        </div>
      );
    }
  } else {
    const wanted = excludedItems.find((i) => votes.get(i.film.id) === "up" && !dismissed.has(`u:${i.film.id}`));
    const rule = wanted ? ruleByIndex(wanted.excluded_by) : undefined;
    if (wanted && rule) {
      const nr = ruleNr(rule.key);
      const why = reason(rule, wanted.results[wanted.excluded_by ?? 0]);
      suggestion = (
        <div role="status" class="suggestion">
          <Icon d="M12 3l1.9 5.6L19.5 10.5l-5.6 1.9L12 18l-1.9-5.6L4.5 10.5l5.6-1.9z" size={16} stroke={1.7} class="accent-text" />
          <div class="grow suggestion-body">
            <div>
              <strong>{t("learn.excluded", { title: wanted.film.title, nr, reason: why })}</strong> {t("learn.ask_excluded", { nr })}
            </div>
            <div class="row wrap">
              <button class="button primary small" onClick={() => flashRule(rule.key)}>
                {t("learn.show", { nr })}
              </button>
              <button class="button ghost small" onClick={() => setDismissed((s) => new Set(s).add(`u:${wanted.film.id}`))}>
                {t("learn.dismiss")}
              </button>
            </div>
          </div>
        </div>
      );
    }
  }

  // -- saving --------------------------------------------------------------------------------------------------------

  const activeComplete = draft.rules.filter((r) => r.enabled && complete(r));
  const brokenRegex = draft.rules.find((r) => r.enabled && r.regex && errors.get(r.key)?.value === valueText(r));
  const validActive = activeComplete.filter((r) => !(errors.get(r.key)?.value === valueText(r)));
  const blocker = brokenRegex
    ? t("ed.err_regex", { nr: ruleNr(brokenRegex.key) })
    : !validActive.length
      ? t("ed.err_rules")
      : !draft.name.trim()
        ? t("ed.err_name")
        : null;

  const toDraft = (): SubscriptionDraft => ({
    name: draft.name.trim(),
    query: draft.query,
    include_description: draft.include_description,
    source: draft.source,
    conditions: draft.rules.filter((r) => complete(r) && !(errors.get(r.key)?.value === valueText(r))).map(toCondition),
    naming: draft.naming,
    quality: draft.quality,
    subtitles: draft.subtitles,
    state: draft.state,
    valid_until: draft.valid_until,
    on_expiry: draft.on_expiry,
    special_versions: draft.special_versions,
  });

  // Saving an active subscription loads what the preview shows at once; a paused one loads nothing until it runs.
  // "Only future episodes" is a rule now (date after today), no longer a way of saving.
  const save = async () => {
    if (blocker || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      const body = toDraft();
      const sub = subId === null ? await api.createSubscription(body) : await api.saveSubscription(subId, body);
      if (sub.state === "paused") toast(t("ed.saved_paused", { name: sub.name }));
      else if (loadItems.length > 0) {
        const result = await api.checkSubscription(sub.id);
        toast(t("ed.saved_load", { name: sub.name, n: result.queued }), { label: t("ed.to_downloads"), run: () => go("/downloads") });
      } else toast(t("ed.saved", { name: sub.name }));
      await refreshStatus();
      go("/abos");
    } catch (e) {
      setSaveError(apiText(e));
    } finally {
      setSaving(false);
    }
  };

  // -- library target: choosing the TMDB series --------------------------------------------------------------------

  const topicValue = String(draft.rules.find((r) => r.field === "topic" && r.operator === "is" && !r.regex)?.value ?? "");
  const findSeries = async (query: string, auto: boolean) => {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setSeriesError(null);
    try {
      const found = await api.searchSeries(q);
      setCandidates(found);
      const only = auto ? autoSeries(found, q) : null;
      if (only) {
        setNaming({ tmdb_id: only.tmdb_id });
        setAutoPicked(true);
        setPicking(false);
      }
    } catch (e) {
      setSeriesError(apiText(e));
    } finally {
      setSearching(false);
    }
  };
  const chooseTarget = (target: "downloads" | "library", libraryId: number | null = null) => {
    setNaming({ target, library_id: libraryId });
    if (target === "library" && !draft.naming.tmdb_id && candidates === null) {
      const query = draft.naming.show || topicValue || draft.name;
      setSeriesQuery(query);
      void findSeries(query, true);
    }
  };
  const pickSeries = (c: SeriesCandidate) => {
    setNaming({ tmdb_id: c.tmdb_id });
    setAutoPicked(false);
    setPicking(false);
  };

  const togglePause = async () => {
    if (subId === null) return;
    try {
      const fresh = await api.subscription(subId);
      const state = fresh.state === "paused" ? "active" : "paused";
      const result = await api.saveSubscription(subId, { ...draftOf(fresh), state });
      setSaved(result);
      update({ state: result.state });
      toast(t(state === "paused" ? "subs.paused_toast" : "subs.resumed_toast", { name: result.name }));
    } catch (e) {
      toast(errorMessage(e));
    }
  };

  const remove = async () => {
    if (subId === null) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      window.setTimeout(() => setConfirmDelete(false), 4000);
      return;
    }
    try {
      await api.deleteSubscription(subId);
      toast(t("ed.deleted", { name: draft.name }));
      go("/abos");
    } catch (e) {
      toast(errorMessage(e));
    }
  };

  // Catching the subscription's downloads again: cancelled and failed episodes load anew, waiting ones stop.
  const givenUp = (sub: Subscription) => (sub.stats.cancelled ?? 0) + (sub.stats.failed ?? 0);
  const pending = (sub: Subscription) => (sub.stats.queued ?? 0) + (sub.stats.running ?? 0);
  const afterwards = async (id: number) => {
    setSaved(await api.subscription(id));
    setRefresh((n) => n + 1);
    await refreshStatus();
  };
  const requeue = async (id: number) => {
    try {
      const result = await api.requeueSubscription(id);
      toast(t("ed.requeued", { queued: result.queued }));
      await afterwards(id);
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  const cancelPending = async (id: number) => {
    if (!confirmCancel) {
      setConfirmCancel(true);
      window.setTimeout(() => setConfirmCancel(false), 4000);
      return;
    }
    setConfirmCancel(false);
    try {
      const { cancelled } = await api.cancelDownloads(id);
      toast(t("dl.cancelled_all", { n: cancelled.length }));
      await afterwards(id);
    } catch (e) {
      toast(errorMessage(e));
    }
  };

  // -- pieces ---------------------------------------------------------------------------------------------------------

  const timeZone = info?.timezone;
  const loadSize = loadItems.reduce((sum, i) => sum + (i.film.size ?? 0), 0);
  const nLoad = loadItems.length;
  const folderName = draft.naming.folder || draft.naming.show || draft.name;
  // In the library: the series folder of the first target (an existing one, or the template's new one).
  const libraryFolder = (() => {
    if (draft.naming.target !== "library") return null;
    const root = seriesLibrary ? folder(seriesLibrary.path) : "";
    const example = loadItems[0]?.path ?? existItems[0]?.path;
    if (example && root && example.startsWith(`${root}/`)) return `${root}/${example.slice(root.length + 1).split("/")[0]}`;
    return root || null;
  })();
  const targetPath = libraryFolder ?? `${(info?.download_dir ?? "/downloads").replace(/\/+$/, "")}/${folderName}`;
  const fullPath = (path: string) => (path.startsWith("/") ? path : `${(info?.download_dir ?? "/downloads").replace(/\/+$/, "")}/${path}`);
  const stateTone = isNew ? "new" : saved?.last_error || (saved?.stats.failed ?? 0) > 0 ? "error" : draft.state === "paused" ? "paused" : "active";
  const stateLabel = { new: t("ed.state_new"), error: t("subs.error"), paused: t("subs.paused"), active: t("subs.active") }[stateTone];

  const header = (
    <div class={`editor-head${mobile ? " mobile" : ""}`}>
      <button class="button ghost small back" onClick={() => go("/abos")}>
        <Icon d="M15 6l-6 6 6 6" size={15} stroke={1.9} />
        {t("ed.back")}
      </button>
      <input
        class="name-input"
        value={draft.name}
        aria-label={t("ed.name")}
        onInput={(e) => update({ name: (e.target as HTMLInputElement).value })}
      />
      <span class={`state-pill ${stateTone === "new" ? "new" : stateTone}`}>
        <span class="state-dot" />
        {stateLabel}
      </span>
      <span class="small faint">{t("ed.from_search", { q: draft.query, n: F.num(total) })}</span>
      <span class="grow" />
      {!isNew && (
        <>
          <button class="button small" onClick={() => void togglePause()}>
            {draft.state === "paused" ? t("subs.resume") : t("subs.pause")}
          </button>
          <button class="button ghost small danger" onClick={() => void remove()}>
            {confirmDelete ? t("ed.delete_confirm") : t("ed.delete")}
          </button>
        </>
      )}
    </div>
  );

  const setOpen = (k: Section, open: boolean) => {
    const next = new Set(openSections);
    if (open) next.add(k);
    else next.delete(k);
    setQuery({ offen: [...next].join(",") || "keins", reiter: "" });
  };
  const showSection = (k: Section) => {
    setOpen(k, true);
    window.setTimeout(() => sectionRefs.current.get(k)?.scrollIntoView({ block: "start", behavior: "smooth" }), 50);
  };
  const sectionHead = (nr: number, title: string, meta: string, k?: Section) => (
    <div
      class="section-head"
      ref={(el) => {
        if (k && el) sectionRefs.current.set(k, el);
      }}
    >
      <span class="section-num">{nr}</span>
      <h3>{title}</h3>
      {meta && <span class="small faint">{meta}</span>}
      <span class="grow" />
      {k && openSections.has(k) && (
        <button class="button ghost small" aria-expanded onClick={() => setOpen(k, false)}>
          {t("ed.collapse")}
        </button>
      )}
    </div>
  );

  const channelCounts = new Map<string, number>();
  for (const i of items) channelCounts.set(i.film.channel, (channelCounts.get(i.film.channel) ?? 0) + 1);
  const channels = [...channelCounts].sort((a, b) => a[0].localeCompare(b[0], "de"));

  const ruleCard = (r: Rule, i: number) => {
    const idx = indexOf(r.key);
    const step = idx >= 0 ? steps[idx] : undefined;
    const err = errors.get(r.key);
    const broken = !!err && err.value === valueText(r);
    const isComplete = complete(r);
    const counted = r.enabled && isComplete && !broken && step !== undefined;
    const excluded = counted ? (step?.excluded ?? 0) : 0;
    const type = fieldType(r.field);
    const ops = operatorsFor(r.field, r.regex);
    const firstExcluded = excludedItems.find((x) => x.excluded_by === idx);
    let status: string;
    if (!r.enabled) status = t("ed.inactive");
    else if (!isComplete) status = t("ed.value_missing");
    else if (broken) status = t("ed.invalid");
    else status = excluded ? t("ed.excludes", { n: excluded }) : t("ed.excludes_none");
    const text = type === "text" ? String(r.value ?? "") : "";
    return (
      <div key={r.key} class="rule-line">
        <span class="rule-rail">
          <span class="rail-line" />
          <span class="rule-nr">{i + 1}</span>
          <span class="rail-line" />
        </span>
        <div
          ref={(el) => {
            if (el) ruleRefs.current.set(r.key, el);
            else ruleRefs.current.delete(r.key);
          }}
          class={`rule-card${r.enabled ? "" : " off"}${flash === r.key ? " flash" : ""}${broken ? " broken" : ""}`}
        >
          <div class="rule-fields">
            <select
              class="input rule-field"
              value={r.field}
              aria-label={t("ed.field")}
              onChange={(e) => {
                const field = (e.target as HTMLSelectElement).value as Field;
                updateRule(r.key, (x) => ({ ...keyed(defaultCondition(field, todayIn(timeZone))), key: x.key, enabled: x.enabled }));
              }}
            >
              {FIELDS.map((f) => (
                <option key={f} value={f}>
                  {fieldLabel(f)}
                </option>
              ))}
            </select>
            <select
              class="input"
              value={r.operator}
              aria-label={t("ed.operator")}
              onChange={(e) => updateRule(r.key, (x) => ({ ...x, operator: (e.target as HTMLSelectElement).value as Operator, suggested: false }))}
            >
              {ops.map((o) => (
                <option key={o} value={o}>
                  {operatorLabel(o)}
                </option>
              ))}
            </select>
            {type === "text" && (
              <div class={`value-box${broken ? " bad" : ""}`}>
                <input
                  value={text}
                  aria-label={t("ed.value")}
                  spellcheck={false}
                  autoComplete="off"
                  class={r.regex ? "mono" : ""}
                  placeholder={r.regex ? t("ed.placeholder_regex") : r.field === "topic" ? t("ed.placeholder_topic") : t("ed.placeholder_text")}
                  onInput={(e) => updateRule(r.key, (x) => ({ ...x, value: (e.target as HTMLInputElement).value, suggested: false }))}
                />
                <button
                  class={`regex-toggle${r.regex ? " on" : ""}`}
                  aria-pressed={r.regex}
                  title={r.regex ? t("ed.regex_on") : t("ed.regex_off")}
                  onClick={() =>
                    updateRule(r.key, (x) => {
                      const on = !x.regex;
                      const operator: Operator = on ? (x.operator === "is_not" || x.operator === "not_contains" ? "is_not" : "is") : x.operator;
                      return { ...x, regex: on, operator, suggested: false };
                    })
                  }
                >
                  .*
                </button>
              </div>
            )}
            {type === "channel" && (
              <div class="value-box chips-box">
                {(Array.isArray(r.value) ? r.value : []).map((c) => (
                  <span key={c} class="value-chip">
                    {c}
                    <button
                      aria-label={t("ed.remove")}
                      onClick={() => updateRule(r.key, (x) => ({ ...x, value: (Array.isArray(x.value) ? x.value : []).filter((y) => y !== c) }))}
                    >
                      <Icon d={I.close} size={10} stroke={2.6} />
                    </button>
                  </span>
                ))}
                <button class="link-button small" onClick={() => setChannelFor((k) => (k === r.key ? null : r.key))}>
                  {t("ed.add_channel")}
                </button>
                {channelFor === r.key && (
                  <>
                    <div class="pop-backdrop" onClick={() => setChannelFor(null)} />
                    <div class="menu channel-menu">
                      {channels.map(([c, n]) => {
                        const list = Array.isArray(r.value) ? r.value : [];
                        return (
                          <label key={c} class="menu-item">
                            <input
                              type="checkbox"
                              checked={list.includes(c)}
                              onChange={() =>
                                updateRule(r.key, (x) => {
                                  const current = Array.isArray(x.value) ? x.value : [];
                                  return { ...x, value: current.includes(c) ? current.filter((y) => y !== c) : [...current, c] };
                                })
                              }
                            />
                            <span class="grow channel-name">{c}</span>
                            <span class="faint small">{n}</span>
                          </label>
                        );
                      })}
                    </div>
                  </>
                )}
              </div>
            )}
            {type === "date" && (
              <input
                type="date"
                class="input"
                value={String(r.value ?? "")}
                aria-label={t("field.date")}
                onInput={(e) => updateRule(r.key, (x) => ({ ...x, value: (e.target as HTMLInputElement).value }))}
              />
            )}
            {type === "duration" && (
              <span class="row">
                <input
                  type="number"
                  min="0"
                  class="input minutes"
                  value={typeof r.value === "number" ? String(r.value) : ""}
                  aria-label={t("ed.minutes")}
                  onInput={(e) => {
                    const v = (e.target as HTMLInputElement).value;
                    updateRule(r.key, (x) => ({ ...x, value: v === "" ? "" : Math.max(0, Math.round(Number(v))) }));
                  }}
                />
                <span class="muted-text">min</span>
              </span>
            )}
          </div>
          {broken && err && <RegexError error={err} />}
          {r.regex && !broken && <div class="small faint">{t("ed.regex_hint")}</div>}
          <div class="rule-foot">
            {r.suggested && <span class="tag">{t("ed.suggested")}</span>}
            <button
              class={`link-button small strong${counted && excluded ? " underline-strong" : " muted"}`}
              disabled={!(counted && excluded)}
              onClick={() => setQuery({ zeige: `regel:${i + 1}` })}
            >
              {status}
            </button>
            {counted && excluded > 0 && <span class="small faint ellipsis">{t("ed.reason", { reason: reason(r, firstExcluded?.results[idx]) })}</span>}
            <span class="grow" />
            <label class="rule-switch" title={t("ed.toggle_title")}>
              <Switch on={r.enabled} small label={t("ed.toggle_title")} onToggle={() => updateRule(r.key, (x) => ({ ...x, enabled: !x.enabled }))} />
              <span class="small muted-text">{r.enabled ? t("ed.active") : t("ed.off")}</span>
            </label>
            <button
              class="icon-button small-icon danger-hover"
              aria-label={t("ed.remove_rule")}
              title={t("ed.remove")}
              onClick={() => setDraft((d) => (d ? { ...d, rules: d.rules.filter((x) => x.key !== r.key) } : d))}
            >
              <Icon d={I.trash} size={14} stroke={1.8} />
            </button>
          </div>
        </div>
      </div>
    );
  };

  const today = todayIn(timeZone);
  const rulesTab = (
    <div class="editor-pane-body">
      {banner && isNew && (
        <div class="hint-banner">
          <Icon d="M12 3l1.9 5.6L19.5 10.5l-5.6 1.9L12 18l-1.9-5.6L4.5 10.5l5.6-1.9z" size={16} stroke={1.7} class="accent-text" />
          <div class="grow">
            <strong>{t("ed.banner_title")}</strong>{" "}
            {data && draft.rules.every((r) => r.enabled) ? t("ed.banner_text", { n: included, total }) : t("ed.banner_text_plain")}
          </div>
          <button class="button link small" onClick={() => (update({ rules: draft.rules.filter((r) => !r.suggested) }), setBanner(false))}>
            {t("ed.banner_drop")}
          </button>
          <button class="icon-button small-icon" aria-label={t("ed.banner_close")} onClick={() => setBanner(false)}>
            <Icon d={I.close} size={13} stroke={2} />
          </button>
        </div>
      )}
      <p class="muted-text intro">{t("ed.rules_intro")}</p>
      <div class="flow-line">
        <span class="flow-count">{data ? F.num(total) : "…"}</span>
        <span class="muted-text">{t("ed.hits_of", { q: draft.query })}</span>
        <button class="link-button small" onClick={() => go(build("/suche", { q: draft.query }))}>
          {t("ed.open_search")}
        </button>
      </div>
      {!draft.rules.length && (
        <div class="flow-line empty-flow">
          <span class="rule-rail">
            <span class="rail-line tall" />
          </span>
          <span class="small faint">{t("ed.no_rules", { n: F.num(total) })}</span>
        </div>
      )}
      {draft.rules.map((r, i) => {
        const idx = indexOf(r.key);
        const remaining = idx >= 0 ? steps[idx]?.remaining : undefined;
        return (
          <div key={r.key}>
            {ruleCard(r, i)}
            {i < draft.rules.length - 1 && remaining !== undefined && (
              <div class="flow-line small-gap">
                <span class="flow-count">{F.num(remaining)}</span>
                <span class="faint">{t("ed.remain")}</span>
              </div>
            )}
          </div>
        );
      })}
      {specialStep && (
        <>
          {draft.rules.length > 0 && (
            <div class="flow-line small-gap">
              <span class="flow-count">{F.num(specialStep.before)}</span>
              <span class="faint">{t("ed.remain")}</span>
            </div>
          )}
          <div class="rule-line">
            <span class="rule-rail">
              <span class="rail-line" />
              <span class="rule-nr" aria-hidden="true">
                <Icon d={I.check} size={11} stroke={2.2} />
              </span>
              <span class="rail-line" />
            </span>
            <div class="rule-card implicit">
              <div class="row wrap">
                <strong>{t("ed.special_title")}</strong>
                <span class="grow" />
                <span class="small muted-text">
                  {specialStep.excluded ? t("ed.special_excludes", { n: specialStep.excluded }) : t("ed.special_none")}
                </span>
              </div>
              <span class="small faint">{t(draft.special_versions === "exclude" ? "ed.special_by_sub" : "ed.special_by_setting")}</span>
              {specialStep.before > 0 && specialStep.remaining === 0 && (
                <span role="note" class="small warn-text">
                  {t("ed.special_all")}
                </span>
              )}
              <div>
                <button class="link-button small" onClick={() => update({ special_versions: "include" })}>
                  {t("ed.special_include")}
                </button>
              </div>
            </div>
          </div>
        </>
      )}
      {!specialStep && draft.special_versions === "include" && (
        <div class="flow-line small-gap">
          <span class="small faint">{t("ed.special_included")}</span>
          <button class="link-button small" onClick={() => update({ special_versions: "setting" })}>
            {t("ed.special_back")}
          </button>
        </div>
      )}
      <div class="flow-line">
        <span class="flow-count final">{data ? F.num(included) : "…"}</span>
        <span>
          <strong>{included === 1 ? t("ed.final_one") : t("ed.final_many")}</strong>
          <span class="faint">{t("ed.final_sub", { load: nLoad, exists: existItems.length })}</span>
        </span>
      </div>
      <div class="add-line">
        <div class="chip-wrap">
          <button class="button small" aria-expanded={addOpen} onClick={() => setAddOpen((o) => !o)}>
            <Icon d={I.plus} size={13} stroke={2.2} />
            {t("ed.add")}
          </button>
          {addOpen && (
            <>
              <div class="pop-backdrop" onClick={() => setAddOpen(false)} />
              <div role="menu" class="menu add-menu">
                {FIELDS.map((f) => {
                  const hint: Key = fieldType(f) === "channel" ? "ed.hint_choice" : fieldType(f) === "date" ? "ed.hint_date" : fieldType(f) === "duration" ? "ed.hint_minutes" : "ed.hint_text";
                  return (
                    <button key={f} role="menuitem" class="menu-item between" onClick={() => addRule(defaultCondition(f, today))}>
                      <span>{fieldLabel(f)}</span>
                      <span class="faint small">{t(hint)}</span>
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>
        <span class="small faint">{t("ed.templates")}</span>
        {TEMPLATES.filter((tpl) => !(tpl.key === "fassungen" && specialStep)).map((tpl) => {
          const c = tpl.condition(today);
          return (
            <button key={tpl.key} class="template" title={t("ed.template_title", { rule: shortText(c) })} onClick={() => addRule(c)}>
              {t(`tpl.${tpl.key}.label` as Key)}
            </button>
          );
        })}
      </div>
    </div>
  );

  // The example: the newest episode that gets a number under the chosen scheme, else the newest hit.
  const pool = [...(loadItems.length ? loadItems : existItems)].sort((a, b) => (b.film.start ?? "").localeCompare(a.film.start ?? ""));
  const newest = pool.find((i) => /S\d+E\d+/.test(i.path ?? "")) ?? pool[0];
  const examplePath = newest?.path ?? null;
  const noNumber = draft.naming.scheme === "episode_number" || draft.naming.scheme === "season_episode" ? loadItems.filter((i) => !/S\d+E\d+/.test(i.path ?? "")).length : 0;
  const schemes: Scheme[] = ["episode_number", "season_episode", "date", "template"];
  const writable = !status?.writable.downloads;

  // Targets: the download folder, or one of the series libraries (settings, libraries); subscriptions are series.
  const library = draft.naming.target === "library";
  const seriesLibraries = (libraries ?? []).filter((l) => l.kind === "series");
  const tmdbReady = !!info?.tmdb;
  const libraryChoice = (l: (typeof seriesLibraries)[number]) => {
    const on = library && seriesLibrary?.id === l.id;
    return (
      <button
        key={l.id}
        role="radio"
        aria-checked={on}
        disabled={!tmdbReady && !on}
        class={`scheme${on ? " on" : ""}`}
        onClick={() => chooseTarget("library", l.id)}
      >
        <span class="radio-dot" />
        <span class="scheme-text">
          <strong>{t("ed.target_library_named", { name: l.name })}</strong>
          <span class="small muted-text">{tmdbReady ? t("ed.target_library_desc") : t("ed.target_tmdb_missing")}</span>
          <code class="small faint">{l.path}</code>
          {l.problem && <span class="small err-text">{t("ed.target_library_bad", { reason: l.problem })}</span>}
        </span>
      </button>
    );
  };
  const targetChoice = (
    <div class="field">
      <div class="field-label">{t("ed.target")}</div>
      <div role="radiogroup" aria-label={t("ed.target")} class="schemes">
        <button role="radio" aria-checked={!library} class={`scheme${!library ? " on" : ""}`} onClick={() => chooseTarget("downloads")}>
          <span class="radio-dot" />
          <span class="scheme-text">
            <strong>{t("ed.target_downloads")}</strong>
            <span class="small muted-text">{t("ed.target_downloads_desc")}</span>
            <code class="small faint">{info?.download_dir ?? "/downloads"}</code>
          </span>
        </button>
        {seriesLibraries.map(libraryChoice)}
        {libraries !== null && !seriesLibraries.length && (
          <button role="radio" aria-checked={library} disabled={!library} class={`scheme${library ? " on" : ""}`}>
            <span class="radio-dot" />
            <span class="scheme-text">
              <strong>{t("ed.target_library")}</strong>
              <span class="small muted-text">{t("ed.target_library_missing")}</span>
            </span>
          </button>
        )}
      </div>
      {library && !seriesLibrary && seriesLibraries.length > 1 && <span class="small err-text">{t("lib.err.library_required")}</span>}
    </div>
  );

  const regular = series ? series.seasons.filter((s) => s.number > 0) : [];
  const seriesField = (
    <div class="field">
      <div class="field-label">{t("ed.series")}</div>
      {draft.naming.tmdb_id && !picking ? (
        <div class="series-card">
          <div class="row wrap baseline">
            <strong>{series?.name ?? `TMDB ${draft.naming.tmdb_id}`}</strong>
            {series?.year && <span class="faint">{series.year}</span>}
            <span class="grow" />
            <button
              class="button small"
              onClick={() => {
                setPicking(true);
                if (candidates === null) {
                  const q = draft.naming.show || topicValue || draft.name;
                  setSeriesQuery(q);
                  void findSeries(q, false);
                }
              }}
            >
              {t("ed.series_change")}
            </button>
          </div>
          {series && (
            <span class="small muted-text">
              {t("ed.series_meta", { seasons: regular.length, episodes: regular.reduce((n, s) => n + s.episodes, 0), id: series.tmdb_id })}
            </span>
          )}
          {series &&
            (series.folders.length ? (
              <span class="small ok-text row">
                <Icon d={I.check} size={13} stroke={2.2} />
                {t("ed.series_folder", { folder: series.folders.join(", ") })}
              </span>
            ) : (
              <span class="small faint">{t("ed.series_new_folder")}</span>
            ))}
          {autoPicked && <span class="small faint">{t("ed.series_auto")}</span>}
        </div>
      ) : (
        <div class="series-picker">
          <div class="row">
            <input
              class="input grow"
              value={seriesQuery}
              placeholder={t("ed.series_placeholder")}
              aria-label={t("ed.series_search")}
              onInput={(e) => setSeriesQuery((e.target as HTMLInputElement).value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void findSeries(seriesQuery, false);
              }}
            />
            <button class="button" disabled={searching || !seriesQuery.trim()} onClick={() => void findSeries(seriesQuery, false)}>
              {t("ed.series_search")}
            </button>
            {!!draft.naming.tmdb_id && (
              <button class="button ghost" onClick={() => setPicking(false)}>
                {t("ed.cancel")}
              </button>
            )}
          </div>
          {searching && <span class="small faint">{t("ed.series_searching")}</span>}
          {candidates !== null &&
            !searching &&
            (candidates.length ? (
              <div class="series-options">
                {candidates.map((c) => (
                  <button key={c.tmdb_id} class={`series-option${c.tmdb_id === draft.naming.tmdb_id ? " on" : ""}`} onClick={() => pickSeries(c)}>
                    <span class="row baseline">
                      <strong>{c.name}</strong>
                      <span class="faint">{c.year ?? ""}</span>
                      {c.original_name !== c.name && <span class="small faint ellipsis">{c.original_name}</span>}
                    </span>
                    {c.overview && <span class="small muted-text clamp2">{c.overview}</span>}
                  </button>
                ))}
              </div>
            ) : (
              <span class="small faint">{t("ed.series_none")}</span>
            ))}
        </div>
      )}
      {seriesError && <span class="small err-text">{seriesError}</span>}
      <span class="small faint">{t("ed.series_hint")}</span>
    </div>
  );

  const libraryExample = (
    <div class="example">
      <span class="stat-label">{t("ed.example", { who: loadItems.length ? t("ed.example_newest") : t("ed.example_hit") })}</span>
      <code class="break">{examplePath ?? "–"}</code>
      {unmatchedItems.length > 0 && (
        <div class="row small warn-text">
          <Icon d={I.warn} size={13} stroke={2} />
          <span>{t("ed.unmatched_warn", { n: unmatchedItems.length })}</span>
        </div>
      )}
    </div>
  );

  const downloadFields = (
    <>
      <div class="field">
        <label class="field-label" for="ed-show">
          {t("ed.show")}
        </label>
        <input
          id="ed-show"
          class="input"
          value={draft.naming.show ?? ""}
          placeholder={String(draft.rules.find((r) => r.field === "topic" && r.operator === "is" && !r.regex)?.value ?? "")}
          onInput={(e) => setNaming({ show: (e.target as HTMLInputElement).value || null })}
        />
        <span class="small faint">{t("ed.show_hint")}</span>
      </div>
      <div class="field">
        <label class="field-label" for="ed-folder">
          {t("ed.folder")}
        </label>
        <div class="prefixed">
          <span class="mono faint">{(info?.download_dir ?? "/downloads").replace(/\/+$/, "")}/</span>
          <input
            id="ed-folder"
            class="mono"
            value={draft.naming.folder ?? ""}
            placeholder={draft.naming.show || t("ed.show")}
            spellcheck={false}
            onInput={(e) => setNaming({ folder: (e.target as HTMLInputElement).value || null })}
          />
        </div>
        <span class={`small row ${writable ? "ok-text" : "err-text"}`}>
          <Icon d={writable ? I.check : I.warn} size={13} stroke={2.2} />
          {writable ? t("ed.target_ok") : t("ed.target_bad", { path: info?.download_dir ?? "/downloads" })}
        </span>
      </div>
      <div class="field">
        <div class="field-label">{t("ed.naming")}</div>
        <div role="radiogroup" aria-label={t("ed.naming")} class="schemes">
          {schemes.map((s) => {
            const on = draft.naming.scheme === s;
            return (
              <button key={s} role="radio" aria-checked={on} class={`scheme${on ? " on" : ""}`} onClick={() => setNaming({ scheme: s })}>
                <span class="radio-dot" />
                <span class="scheme-text">
                  <strong>{t(`ed.scheme.${s}` as Key)}</strong>
                  <span class="small muted-text">{t(`ed.scheme.${s}_desc` as Key)}</span>
                  <code class="small faint">{t(`ed.scheme.${s}_ex` as Key)}</code>
                </span>
              </button>
            );
          })}
        </div>
        {draft.naming.scheme === "episode_number" && (
          <label class="row muted-text">
            {t("ed.season")}
            <input
              type="number"
              min="0"
              class="input minutes"
              value={String(draft.naming.season ?? 1)}
              onInput={(e) => setNaming({ season: Math.max(0, Math.round(Number((e.target as HTMLInputElement).value) || 0)) })}
            />
          </label>
        )}
        {draft.naming.scheme === "template" && (
          <div class="field">
            <input
              class="input mono"
              value={draft.naming.template ?? ""}
              placeholder={t("ed.scheme.template_ex")}
              spellcheck={false}
              onInput={(e) => setNaming({ template: (e.target as HTMLInputElement).value })}
            />
            <span class="small faint mono">{t("ed.template_placeholders")}</span>
          </div>
        )}
      </div>
      <div class="example">
        <span class="stat-label">{t("ed.example", { who: loadItems.length ? t("ed.example_newest") : t("ed.example_hit") })}</span>
        <code class="break">
          <span class="faint">{(info?.download_dir ?? "/downloads").replace(/\/+$/, "")}/</span>
          {examplePath ?? "–"}
        </code>
        {noNumber > 0 && (
          <div class="row small warn-text">
            <Icon d={I.warn} size={13} stroke={2} />
            <span>{t("ed.no_number_warn", { n: noNumber })}</span>
          </div>
        )}
      </div>
    </>
  );

  const targetTab = (
    <div class="editor-pane-body form">
      {targetChoice}
      {library ? (
        <>
          {seriesField}
          {libraryExample}
        </>
      ) : (
        downloadFields
      )}
    </div>
  );

  const qualityName = settings ? t(`q.${settings.quality}` as Key) : "";
  const sourceName = settings ? (settings.source === "mediathekviewweb" ? t("ed.source_mvw") : t("ed.source_filmlist")) : "";
  const next = status?.filmlist.next_run;
  const optionsTab = (
    <div class="editor-pane-body form">
      <div class="field">
        <label class="field-label" for="ed-quality">
          {t("ed.quality")}
        </label>
        <select
          id="ed-quality"
          class="input narrow-input"
          value={draft.quality ?? ""}
          onChange={(e) => update({ quality: ((e.target as HTMLSelectElement).value || null) as Quality | null })}
        >
          <option value="">{t("ed.quality_default", { quality: qualityName })}</option>
          <option value="hd">{t("ed.quality_hd")}</option>
          <option value="normal">{t("ed.quality_normal")}</option>
          <option value="low">{t("ed.quality_low")}</option>
        </select>
      </div>
      <div class="field">
        <div class="field-label">{t("ed.subtitles")}</div>
        <label class="check-label plain">
          <input
            type="checkbox"
            checked={draft.subtitles ?? settings?.subtitles ?? true}
            onChange={(e) => update({ subtitles: (e.target as HTMLInputElement).checked })}
          />
          {t("ed.subtitles_load")}
        </label>
      </div>
      <div class="field">
        <label class="field-label" for="ed-special">
          {t("ed.special")}
        </label>
        <select
          id="ed-special"
          class="input narrow-input"
          value={draft.special_versions}
          onChange={(e) => update({ special_versions: (e.target as HTMLSelectElement).value as SpecialVersions })}
        >
          <option value="setting">{settings?.exclude_special_versions === false ? t("ed.special_opt.setting_off") : t("ed.special_opt.setting_on")}</option>
          <option value="exclude">{t("ed.special_opt.exclude")}</option>
          <option value="include">{t("ed.special_opt.include")}</option>
        </select>
      </div>
      <div class="field">
        <label class="field-label" for="ed-source">
          {t("ed.source")}
        </label>
        <select
          id="ed-source"
          class="input narrow-input"
          value={draft.source ?? ""}
          onChange={(e) => update({ source: ((e.target as HTMLSelectElement).value || null) as Source | null })}
        >
          <option value="">{t("ed.source_default", { source: sourceName })}</option>
          <option value="filmlist">{t("ed.source_filmlist")}</option>
          <option value="mediathekviewweb">{t("ed.source_mvw")}</option>
        </select>
        {info?.description_search && (
          <label class="check-label plain">
            <input
              type="checkbox"
              checked={draft.include_description}
              onChange={(e) => update({ include_description: (e.target as HTMLInputElement).checked })}
            />
            {t("ed.description")}
          </label>
        )}
      </div>
      <div class="field">
        <label class="field-label" for="ed-valid">
          {t("ed.valid_until")}
        </label>
        <div class="row wrap">
          <input
            id="ed-valid"
            type="date"
            class="input narrow-input"
            value={draft.valid_until ?? ""}
            min={todayIn(timeZone)}
            onInput={(e) => update({ valid_until: (e.target as HTMLInputElement).value || null })}
          />
          {[2, 4, 8].map((weeks) => (
            <button key={weeks} class="button small" onClick={() => update({ valid_until: plusDays(todayIn(timeZone), weeks * 7) })}>
              {t("ed.valid_weeks", { n: weeks })}
            </button>
          ))}
          {draft.valid_until && (
            <button class="button ghost small" onClick={() => update({ valid_until: null })}>
              {t("ed.valid_clear")}
            </button>
          )}
        </div>
        {draft.valid_until && (
          <select
            class="input narrow-input"
            value={draft.on_expiry}
            aria-label={t("ed.on_expiry")}
            onChange={(e) => update({ on_expiry: (e.target as HTMLSelectElement).value as "pause" | "delete" })}
          >
            <option value="pause">{t("ed.on_expiry_pause")}</option>
            <option value="delete">{t("ed.on_expiry_delete")}</option>
          </select>
        )}
        <span class="small faint">{t("ed.valid_hint")}</span>
      </div>
      <div class="field">
        <div class="field-label">{t("ed.check")}</div>
        <div class="muted-text">{t("ed.check_text", { time: next ? F.time(next, timeZone) : "–" })}</div>
      </div>
      {!!saved?.stats.skipped && subId !== null && (
        <div class="field">
          <div class="muted-text">{t("ed.skipped", { n: saved.stats.skipped })}</div>
          <div>
            <button
              class="button small"
              onClick={() =>
                void api.releaseSkipped(subId).then(
                  async () => {
                    toast(t("ed.released"));
                    setSaved(await api.subscription(subId));
                    setRefresh((n) => n + 1);
                  },
                  (e: unknown) => toast(errorMessage(e)),
                )
              }
            >
              {t("ed.release")}
            </button>
          </div>
        </div>
      )}
      {!!saved && subId !== null && givenUp(saved) > 0 && (
        <div class="field">
          <div class="muted-text">{t("ed.given_up", { n: givenUp(saved) })}</div>
          <div>
            <button class="button small" onClick={() => void requeue(subId)}>
              {t("ed.requeue")}
            </button>
          </div>
        </div>
      )}
      {!!saved && subId !== null && pending(saved) > 0 && (
        <div class="field">
          <div class="muted-text">{t("ed.pending", { n: pending(saved) })}</div>
          <div>
            <button class="button small ghost danger" onClick={() => void cancelPending(subId)}>
              {confirmCancel ? t("ed.cancel_pending_confirm") : t("ed.cancel_pending")}
            </button>
          </div>
        </div>
      )}
    </div>
  );

  // Closed sections show the choice made, readable without opening them.
  const summaryCard = (k: Section, icon: string, line: string, detail?: string, warn = false) => (
    <button class={`section-summary${warn ? " warn" : ""}`} aria-expanded={false} onClick={() => setOpen(k, true)}>
      <Icon d={icon} size={18} stroke={1.8} class="faint" />
      <span class="summary-text">
        <span class="ellipsis">{line}</span>
        {detail && <code class="small faint ellipsis">{detail}</code>}
      </span>
      <span class="button small">{t("ed.change")}</span>
    </button>
  );
  const seriesLabel = series ? `${series.name} (TMDB ${series.tmdb_id})` : draft.naming.tmdb_id ? `TMDB ${draft.naming.tmdb_id}` : t("ed.series_missing_short");
  const targetSummary = summaryCard(
    "ziel",
    I.folder,
    library
      ? `${seriesLibrary?.name ?? t("ed.target_library_short")} · ${seriesLabel}`
      : `${t("ed.target_downloads")} · ${t(`ed.scheme.${draft.naming.scheme}` as Key)}`,
    examplePath ? fullPath(examplePath) : targetPath,
    library && (!draft.naming.tmdb_id || !seriesLibrary),
  );
  const optionsSummary = summaryCard(
    "optionen",
    I.settings,
    [
      draft.quality ? t(`q.${draft.quality}` as Key) : t("ed.as_default", { value: qualityName }),
      (draft.subtitles ?? settings?.subtitles ?? true) ? t("ed.subtitles_on") : t("ed.subtitles_off"),
      draft.source ? (draft.source === "mediathekviewweb" ? t("ed.source_mvw") : t("ed.source_filmlist")) : t("ed.as_default", { value: sourceName }),
      ...(draft.include_description ? [t("ed.description_on")] : []),
      ...(draft.valid_until ? [t("ed.valid_summary", { date: F.date(`${draft.valid_until}T12:00:00Z`, timeZone) })] : []),
    ].join(" · "),
  );

  // -- preview pane --------------------------------------------------------------------------------------------------

  const fullRows = !mobile;
  const previewRow = (item: PreviewItem, kind: "load" | "exists" | "excluded" | "unmatched", also: string[] = []) => {
    const film = item.film;
    const vote = votes.get(film.id);
    const titleIdx = activeTitleKey !== undefined ? indexOf(activeTitleKey) : -1;
    const result = titleIdx >= 0 ? item.results[titleIdx] : undefined;
    const excludedByTitle = kind === "excluded" && item.excluded_by === titleIdx && titleIdx >= 0;
    let title: JSX.Element | string = film.title;
    if (result?.span && typeof result.actual === "string") {
      const [a, b] = result.span;
      title = (
        <>
          {result.actual.slice(0, a)}
          <mark class={excludedByTitle ? "bad" : ""}>{result.actual.slice(a, b)}</mark>
          {result.actual.slice(b)}
        </>
      );
    }
    const rule = kind === "excluded" ? ruleByIndex(item.excluded_by) : undefined;
    const excludedByTopic = rule?.field === "topic";
    const nr = rule ? ruleNr(rule.key) : 0;
    const why =
      item.status === "unavailable"
        ? t("ed.livestream")
        : bySpecial(item)
          ? t("ed.special_reason", { marker: item.special ?? "" })
          : rule
            ? reason(rule, item.results[item.excluded_by ?? 0])
            : "";
    const label =
      item.status === "failed"
        ? t("ed.failed")
        : item.status === "cancelled"
          ? t("ed.cancelled")
          : item.status === "skipped"
            ? t("ed.skipped_row")
            : item.in_library
              ? t("ed.in_library")
              : t("ed.exists");
    const episode = item.episode;
    const target = item.path
      ? [fullPath(item.path), episode ? `${episodeCode(episode)} · ${episode.name} · ${t(reasonKey(item.reason) as Key)}` : ""].filter(Boolean).join("\n")
      : "";
    const missing = kind === "unmatched" ? t(reasonKey(item.reason) as Key) : "";
    const thumb = (dir: "up" | "down") => (
      <button
        class={`thumb ${dir}${vote === dir ? " on" : ""}`}
        aria-pressed={vote === dir}
        aria-label={dir === "up" ? t("ed.thumb_up") : t("ed.thumb_down")}
        title={dir === "up" ? t("ed.thumb_up") : t("ed.thumb_down")}
        onClick={() =>
          setVotes((v) => {
            const next = new Map(v);
            if (next.get(film.id) === dir) next.delete(film.id);
            else next.set(film.id, dir);
            return next;
          })
        }
      >
        <Icon d={dir === "up" ? THUMB_UP : THUMB_DOWN} size={14} stroke={1.8} />
      </button>
    );
    const sub = `${mobile ? ` · ${F.date(film.start, timeZone)}` : ""}${also.length ? t("ed.also_on", { list: also.join(", ") }) : ""}`;
    return (
      <div key={film.id} class={`prev-row${fullRows ? " full" : ""}${vote === "down" && kind !== "excluded" ? " voted-down" : ""}`}>
        <span class="row thumbs">
          {thumb("up")}
          {thumb("down")}
        </span>
        {!mobile && (
          <span>
            <ChannelBadge channel={film.channel} />
          </span>
        )}
        <span class="dl-main">
          <span class={`ellipsis prev-title${kind === "excluded" ? " muted-text" : ""}`} title={film.title}>
            {title}
          </span>
          <span class="ellipsis small faint">
            {mobile && <span class="mono channel-name muted-text">{film.channel} </span>}
            <span class={excludedByTopic ? "err-text" : ""}>{film.topic}</span>
            {sub}
          </span>
          {mobile && (
            <span class="row small">
              {kind === "excluded" && (
                <span class="why">
                  {nr > 0 && <span class="mono faint">R{nr}</span>}
                  {why}
                </span>
              )}
              {kind === "load" && <span class="mono ok-text">{codeOf(item.path)}</span>}
              {kind === "exists" && <span class={item.status === "failed" ? "err-text" : "muted-text"}>{label}</span>}
              {kind === "unmatched" && <span class="warn-text">{missing}</span>}
            </span>
          )}
        </span>
        {fullRows && (
          <>
            <span class="muted-text nowrap">{F.date(film.start, timeZone)}</span>
            <span class="num muted-text">{F.duration(film.duration)}</span>
          </>
        )}
        {!mobile && (
          <span class="row end">
            {kind === "excluded" && (
              <button class="why" title={rule ? t("ed.excluded_by", { nr, rule: shortText(rule) }) : ""} onClick={() => rule && flashRule(rule.key)}>
                {nr > 0 && <span class="mono faint">R{nr}</span>}
                <span class="ellipsis">{why}</span>
              </button>
            )}
            {kind === "load" && (
              <span class="mono ok-text strong" title={target}>
                {codeOf(item.path)}
              </span>
            )}
            {kind === "exists" && (
              <span class={`row small ${item.status === "failed" ? "err-text" : "muted-text"}`} title={target}>
                {item.status === "exists" && <Icon d={I.check} size={13} stroke={2.2} />}
                {label}
              </span>
            )}
            {kind === "unmatched" && <span class="small warn-text ellipsis">{missing}</span>}
          </span>
        )}
      </div>
    );
  };

  const newestFirst = (a: PreviewItem, b: PreviewItem) => (b.film.start ?? "").localeCompare(a.film.start ?? "");
  const order = comparePreview(previewSort);
  // Excluded hits of the same broadcast on several channels as one row (design: "Dubletten zusammengefasst").
  const excludedRows = (() => {
    const map = new Map<string, { item: PreviewItem; also: string[] }>();
    for (const item of [...excludedItems].sort(newestFirst)) {
      const key = `${fold(item.film.topic)}|${fold(item.film.title)}|${Math.round((item.film.duration ?? 0) / 60)}`;
      const seen = map.get(key);
      if (seen) seen.also.push(item.film.channel);
      else map.set(key, { item, also: [] });
    }
    return [...map.values()];
  })();
  const filteredExcluded = filterRule !== null ? excludedRows.filter((e) => ruleByIndex(e.item.excluded_by)?.key === draft.rules[filterRule - 1]?.key) : excludedRows;
  const merged = excludedItems.length - excludedRows.length;

  const groups: JSX.Element[] = [];
  const showGroup = (k: string) => !show || show === k || (k === "aus" && filterRule !== null);
  if (showGroup("laden")) {
    groups.push(
      <div key="load">
        <div class="prev-group">
          <span class="dot ok" />
          <strong>{t("ed.show_load")}</strong>
          <span class="count">{nLoad}</span>
          <span class="grow" />
          <span class="small faint">{nLoad ? t("ed.size_meta", { size: F.size(loadSize || null) }) : ""}</span>
        </div>
        {!loadItems.length && data && <div class="dashed">{draft.rules.length ? t("ed.empty_load") : t("ed.empty_load_norules")}</div>}
        <div class="prev-rows">{[...loadItems].sort(order).map((i) => previewRow(i, "load", alsoOf(i.film)))}</div>
      </div>,
    );
  }
  if (unmatchedItems.length > 0 && showGroup("ohne")) {
    groups.push(
      <div key="unmatched">
        <div class="prev-group">
          <span class="dot warn" />
          <strong>{t("ed.show_unmatched")}</strong>
          <span class="count">{unmatchedItems.length}</span>
          <span class="grow" />
          <span class="small faint">{t("ed.unmatched_hint")}</span>
        </div>
        <div class="prev-rows">{[...unmatchedItems].sort(order).map((i) => previewRow(i, "unmatched", alsoOf(i.film)))}</div>
      </div>,
    );
  }
  if (showGroup("vorhanden")) {
    groups.push(
      <div key="exists">
        <div class="prev-group">
          <span class="dot muted" />
          <strong>{t("ed.show_exists")}</strong>
          <span class="count">{existItems.length}</span>
        </div>
        {!existItems.length && data && <div class="dashed">{t("ed.empty_exists")}</div>}
        <div class="prev-rows">{[...existItems].sort(order).map((i) => previewRow(i, "exists", alsoOf(i.film)))}</div>
      </div>,
    );
  }
  if (showGroup("aus")) {
    groups.push(
      <div key="excluded">
        <div class="prev-group">
          <span class="dot err" />
          <strong>{t("ed.show_excluded")}</strong>
          <span class="count">{filterRule !== null ? filteredExcluded.reduce((n, e) => n + 1 + e.also.length, 0) : excludedItems.length}</span>
          <span class="grow" />
          <span class="small faint">{merged > 0 && filterRule === null ? t("ed.dups", { n: merged }) : ""}</span>
        </div>
        {!filteredExcluded.length && data && <div class="dashed">{t("ed.empty_excluded")}</div>}
        <div class="prev-rows">{[...filteredExcluded].sort((a, b) => order(a.item, b.item)).map((e) => previewRow(e.item, "excluded", e.also))}</div>
      </div>,
    );
  }

  // One order for all groups: column headers on wide screens, a choice on the phone; kept in the hash.
  const sortHead = (k: PreviewSortKey, label: Key, right = false) => (
    <button class={`sort${previewSort.key === k ? " on" : ""}${right ? " right" : ""}`} onClick={() => setQuery({ sort: nextPreviewSort(previewSort, k) })}>
      {t(label)}
      <span>{previewSort.key === k ? (previewSort.desc ? "↓" : "↑") : ""}</span>
    </button>
  );
  const sortChoices: [string, string][] = [
    ["datum-desc", `${t("col.datum")} ↓`],
    ["datum-asc", `${t("col.datum")} ↑`],
    ["titel-asc", t("col.titel")],
    ["sender-asc", t("col.sender")],
    ["dauer-desc", `${t("col.dauer")} ↓`],
    ["ziel-asc", t("ed.col_target")],
  ];
  const previewHead = fullRows ? (
    <div class="prev-row full prev-head small">
      <span />
      {sortHead("sender", "col.sender")}
      {sortHead("titel", "col.titel")}
      {sortHead("datum", "col.datum")}
      {sortHead("dauer", "col.dauer", true)}
      {sortHead("ziel", "ed.col_target", true)}
    </div>
  ) : (
    <label class="row prev-sort small">
      <span class="faint">{t("ed.sort")}</span>
      <select
        class="input"
        value={`${previewSort.key}-${previewSort.desc ? "desc" : "asc"}`}
        onChange={(e) => setQuery({ sort: (e.target as HTMLSelectElement).value })}
      >
        {sortChoices.map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
    </label>
  );

  const segment = (k: string, label: string, count: number, dot: string) => {
    const on = show === k || (k === "aus" && filterRule !== null) || (!show && k === "");
    return (
      <button key={k} role="tab" aria-selected={on} class={on ? "on" : ""} onClick={() => setQuery({ zeige: k })}>
        {dot && <span class={`dot ${dot}`} />}
        {label}
        <span class="faint strong">{F.num(count)}</span>
      </button>
    );
  };

  const previewPane = (
    <section aria-label={t("ed.preview")} class="editor-pane preview-pane">
      <div class="pane-head">
        <div class="row wrap baseline">
          <h3>{t("ed.preview")}</h3>
          <span class="small faint">{t("ed.preview_hint")}</span>
          {previewBusy && <span class="small faint">{t("ed.loading_preview")}</span>}
        </div>
        <div role="tablist" class="segmented wide-seg">
          {segment("", t("ed.show_all"), total, "")}
          {segment("laden", t("ed.show_load"), nLoad, "ok")}
          {unmatchedItems.length > 0 && segment("ohne", t("ed.show_unmatched"), unmatchedItems.length, "warn")}
          {segment("vorhanden", t("ed.show_exists"), existItems.length, "muted")}
          {segment("aus", t("ed.show_excluded"), excludedItems.length, "err")}
        </div>
        {filterRule !== null && (
          <div>
            <span class="chip on">
              {t("ed.only_rule", { nr: filterRule })}
              <button class="chip-x" aria-label={t("ed.clear_filter")} onClick={() => setQuery({ zeige: "aus" })}>
                <Icon d={I.close} size={11} stroke={2.4} />
              </button>
            </span>
          </div>
        )}
      </div>
      {previewError && <div class="notice warn">{t("ed.preview_failed", { message: previewError })}</div>}
      {suggestion}
      {!data && !previewError ? (
        <div class="skeleton-list" aria-busy="true">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} class="skel-row">
              <span class="skel" style={{ width: "54px" }} />
              <span class="skel grow" style={{ maxWidth: `${200 + i * 40}px` }} />
            </div>
          ))}
        </div>
      ) : (
        <>
          {previewHead}
          {groups}
        </>
      )}
      <div style={{ height: "28px", flex: "none" }} />
    </section>
  );

  // A new subscription starts active or paused by this switch; a saved one pauses with the button in the header.
  const paused = draft.state === "paused";
  const size = F.size(loadSize || null);
  const footer = (
    <div class={`editor-foot${mobile ? " mobile" : ""}`}>
      <div class="grow foot-text">
        <strong>{paused ? t("ed.sum_paused", { n: nLoad, size }) : nLoad ? t("ed.sum_load", { n: nLoad, size }) : t("ed.sum_nothing")}</strong>
        <button class="foot-path break" title={t("ed.change_target")} onClick={() => showSection("ziel")}>
          {t(paused ? "ed.sum_target_paused" : "ed.sum_target", { path: targetPath })}
        </button>
      </div>
      {(blocker || saveError) && (
        <span role="alert" class="small err-text">
          {saveError ?? blocker}
        </span>
      )}
      <div class="row wrap end">
        {isNew && (
          <label class="rule-switch state-switch">
            <Switch on={!paused} label={t("ed.state_switch")} onToggle={() => update({ state: paused ? "active" : "paused" })} />
            <span class={paused ? "muted-text" : "strong"}>{paused ? t("ed.state_paused") : t("ed.state_active")}</span>
          </label>
        )}
        <button class="button primary" disabled={!!blocker || saving} onClick={() => void save()}>
          {t("ed.save")}
        </button>
        <button class="button cancel" onClick={() => go("/abos")}>
          {t("ed.cancel")}
        </button>
      </div>
    </div>
  );

  return (
    <div class={`editor${mobile ? " mobile" : ""}`}>
      {header}
      <div class="editor-body">
        <section aria-label={t("ed.sections")} class="editor-pane settings-pane">
          {sectionHead(1, t("ed.sec_rules"), t("ed.sec_rules_meta", { n: draft.rules.length }))}
          {rulesTab}
          {sectionHead(2, t("ed.sec_target"), "", "ziel")}
          {openSections.has("ziel") ? targetTab : targetSummary}
          {sectionHead(3, t("ed.sec_options"), "", "optionen")}
          {openSections.has("optionen") ? optionsTab : optionsSummary}
          <div class="section-end" />
        </section>
        {previewPane}
      </div>
      {footer}
    </div>
  );
}

const THUMB_UP =
  "M7 10v12M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z";
const THUMB_DOWN =
  "M17 14V2M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z";

function RegexError(props: { error: RuleError }): JSX.Element {
  const { value } = props.error;
  const problem = regexProblem(value, props.error.message, props.error.position);
  const at = problem.position;
  return (
    <div role="alert" class="regex-error">
      <code>
        {at !== null ? (
          <>
            {value.slice(0, at)}
            <span class="at">{value[at] ?? " "}</span>
            {value.slice(at + 1)}
          </>
        ) : (
          value
        )}
      </code>
      <span class="small err-text">
        {at !== null ? t("ed.regex_at", { pos: at + 1 }) : ""}
        {problem.text}
      </span>
    </div>
  );
}

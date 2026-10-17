// Search: one query against the film list, filters, sorting and grouping in the browser,
// details beside the list (wide screens) or above it, downloads and subscriptions from every hit.
import { Component, type JSX } from "preact";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "preact/hooks";

import { api, ApiError, type DownloadBody, errorMessage, type Film, type Library, type Naming, type Quality, type SearchResult } from "../api";
import { ChannelBadge } from "../components/channel";
import * as F from "../format";
import { t, type Key } from "../i18n";
import { I, Icon } from "../icons";
import { build, go, type Route, setQuery } from "../router";
import * as S from "../search-logic";
import { type Layout, refreshStatus, toast, useInfo, useLibraries, useSettings, useStatus } from "../store";
import { MovieTarget } from "./movie-target";

const LIMIT = 2000; // the API's maximum; newest first
const PAGE = 150; // rows rendered per step while scrolling
const RECENT_KEY = "mtdl.recent";
const QUALITY_KEYS: Record<Quality, Key> = { hd: "q.hd", normal: "q.normal", low: "q.low" };

type Pop = "sender" | "thema" | "titel" | "dauer" | "zeit" | "syntax" | null;

/** MediathekViewWeb's search syntax, understood by the backend (mtdl_web/query.py); a click adds the example. */
const SYNTAX: { example: string; label: Key; description?: true }[] = [
  { example: "!ard", label: "search.syntax.channel" },
  { example: "#tatort", label: "search.syntax.topic" },
  { example: "+mord", label: "search.syntax.title" },
  { example: "*berlin", label: "search.syntax.description", description: true },
  { example: ">80", label: "search.syntax.longer" },
  { example: "<10", label: "search.syntax.shorter" },
  { example: "#sturm,der,liebe", label: "search.syntax.all" },
  { example: "!ard !zdf", label: "search.syntax.either" },
];

// -- recent searches: a per-viewer convenience in localStorage ---------------------------------------------------------

function readRecent(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((x): x is string => typeof x === "string").slice(0, 6) : [];
  } catch {
    return [];
  }
}

function remember(q: string): void {
  const query = q.trim();
  if (!query) return;
  try {
    const list = [query, ...readRecent().filter((x) => x.toLowerCase() !== query.toLowerCase())].slice(0, 6);
    localStorage.setItem(RECENT_KEY, JSON.stringify(list));
  } catch {
    // storage blocked: nothing to remember
  }
}

// -- geometry: columns by the width the list really has ----------------------------------------------------------------

interface Geometry {
  list: boolean;
  wide: boolean;
  medium: boolean;
  compact: boolean;
  side: boolean;
  cols: string;
}

function geometry(width: number, detail: boolean, twins: boolean): Geometry {
  const side = detail && width >= 1340;
  const table = side ? width - 420 : width;
  const list = table < 780;
  const wide = table >= 1100;
  const medium = !wide && table >= 960;
  // Room for "+1" beside the badge, taken from the topic's minimum: the table needs no more width than without.
  const extra = twins ? 22 : 0;
  const cols = wide
    ? `24px ${62 + extra}px minmax(${110 - extra}px,1fr) minmax(180px,2.3fr) 84px 58px 66px 28px 28px 56px 132px 108px`
    : medium
      ? `24px ${56 + extra}px minmax(${96 - extra}px,1fr) minmax(150px,2fr) 82px 56px 64px 84px 112px 108px`
      : `22px ${52 + extra}px minmax(${80 - extra}px,1fr) minmax(140px,2fr) 78px 52px 60px 96px 104px`;
  return { list, wide, medium, compact: !list && !wide && !medium, side, cols };
}

function useWidth(ref: { current: HTMLElement | null }): number {
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const observer = new ResizeObserver(() => setWidth(el.clientWidth));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return width;
}

// -- status of a hit ---------------------------------------------------------------------------------------------------

interface HitStatus {
  kind: "running" | "queued" | "done" | "dup" | "failed" | "cancelled" | null;
  progress: number | null;
  sub: string | null;
  subId: number | null;
}

function hitStatus(film: Film, live: number | undefined): HitStatus {
  const s = film.status;
  if (!s) return { kind: null, progress: null, sub: null, subId: null };
  let kind: HitStatus["kind"] = null;
  if (s.state === "running") kind = "running";
  else if (s.state === "queued") kind = "queued";
  else if (s.state === "done") kind = s.duplicate ? "dup" : "done";
  else if (s.state === "failed") kind = "failed";
  else if (s.state === "cancelled") kind = "cancelled";
  return { kind, progress: live ?? s.progress, sub: s.subscription_name, subId: s.subscription_id };
}

function statusCell(st: HitStatus): { text: string; line: string; icon: string | null; rec: boolean; tone: string } {
  const pct = Math.round((st.progress ?? 0) * 100);
  switch (st.kind) {
    case "running":
      return { text: t("st.running", { pct }), line: t("st.running", { pct }), icon: null, rec: true, tone: "strong" };
    case "queued":
      return { text: t("st.queued"), line: t("st.queued_line"), icon: I.clock, rec: false, tone: "muted-text" };
    case "done":
      return { text: t("st.done"), line: t("st.done_line"), icon: I.check, rec: false, tone: "ok-text" };
    case "dup":
      return { text: t("st.done_dup"), line: t("st.dup_line"), icon: I.check, rec: false, tone: "faint" };
    case "failed":
      return { text: t("st.failed"), line: t("st.failed"), icon: I.warn, rec: false, tone: "err-text" };
    case "cancelled":
      return { text: t("st.cancelled"), line: t("st.cancelled"), icon: null, rec: false, tone: "faint" };
    default:
      return { text: "", line: "", icon: null, rec: false, tone: "muted-text" };
  }
}

// -- one hit (memoized: polling re-renders the view every few seconds) -----------------------------------------------

interface RowActions {
  open(film: Film): void;
  check(film: Film): void;
  play(film: Film): void;
  download(film: Film, quality: Quality): void;
  menu(film: Film): void;
  subscribe(film: Film): void;
}

interface RowProps {
  film: Film;
  /** All films of the row's file when other channels have it too (search-logic mergeTwins), the shown one first. */
  twins: Film[] | undefined;
  geo: Geometry;
  checked: boolean;
  active: boolean;
  menu: boolean;
  live: number | undefined;
  timeZone: string | undefined;
  actions: RowActions;
}

const stop = (e: Event) => e.stopPropagation();

/** Marks come language-neutral from search-logic ("UT" for subtitles); shown in the interface language. */
const markLabel = (mark: string) => (mark === "UT" ? t("col.ut") : mark);

class Hit extends Component<RowProps> {
  override shouldComponentUpdate(next: RowProps): boolean {
    const p = this.props;
    return (
      p.film !== next.film ||
      p.twins !== next.twins ||
      p.geo !== next.geo ||
      p.checked !== next.checked ||
      p.active !== next.active ||
      p.menu !== next.menu ||
      p.live !== next.live ||
      p.timeZone !== next.timeZone
    );
  }

  override render(): JSX.Element {
    const { film, twins, geo, checked, active, menu, live, timeZone, actions } = this.props;
    const st = hitStatus(film, live);
    const cell = statusCell(st);
    const marks = S.marks(film).map(markLabel);
    const others = twins?.filter((x) => x !== film) ?? [];
    const cls = `hit${active || checked ? " sel" : ""}${active ? " active" : ""}${menu ? " menu-open" : ""}`;
    if (geo.list) {
      const meta = [F.date(film.start, timeZone), F.duration(film.duration), film.size ? F.size(film.size) : null, ...marks];
      const line = [cell.line, st.sub ? t("st.sub_line", { name: st.sub }) : ""].filter(Boolean).join(" · ");
      return (
        <div role="row" class={`${cls} hit-list`} onClick={() => actions.open(film)}>
          <div class="hit-body">
            <div class="row">
              <ChannelBadge channel={film.channel} small />
              {others.length > 0 && <TwinCount others={others} />}
              <span class="ellipsis small muted-text">{film.topic}</span>
            </div>
            <div class="hit-title">{film.title}</div>
            <div class="small muted-text">{meta.filter(Boolean).join(" · ")}</div>
            {line && (
              <div class={`row small ${cell.tone}`}>
                {cell.rec && <span class="dot rec" />}
                <span>{line}</span>
              </div>
            )}
          </div>
          <button
            class="hit-round"
            aria-label={t("search.download")}
            onClick={(e) => (stop(e), actions.download(film, "hd"))}
          >
            <Icon d={I.download} size={18} />
          </button>
        </div>
      );
    }
    const geoText = S.geoLabel(film);
    return (
      <div role="row" class={cls} style={{ gridTemplateColumns: geo.cols }} onClick={() => actions.open(film)}>
        <span class="cell-check" onClick={stop}>
          <input type="checkbox" checked={checked} onChange={() => actions.check(film)} aria-label={t("search.select")} />
        </span>
        <span class="channel-cell">
          <ChannelBadge channel={film.channel} />
          {others.length > 0 && <TwinCount others={others} />}
        </span>
        <span class="ellipsis muted-text" title={film.topic}>
          {film.topic}
        </span>
        <span class="ellipsis hit-name" title={film.title}>
          {film.title}
        </span>
        <span class="nowrap muted-text">{F.date(film.start, timeZone)}</span>
        <span class="num muted-text">{F.duration(film.duration)}</span>
        <span class="num muted-text">{film.size ? F.size(film.size) : "–"}</span>
        {geo.wide && (
          <>
            <span class={`center ${film.url_hd ? "muted-text" : "faint"}`}>{film.url_hd ? "✓" : "–"}</span>
            <span class={`center ${film.url_subtitle ? "muted-text" : "faint"}`}>{film.url_subtitle ? "✓" : "–"}</span>
            <span class={`center mono small-mono ${geoText ? "muted-text" : "faint"}`} title={geoText}>
              {geoText || "–"}
            </span>
          </>
        )}
        {geo.medium && (
          <span class="marks">
            {marks.map((m) => (
              <span key={m} class="mark">
                {m}
              </span>
            ))}
          </span>
        )}
        <span class={`hit-status ${cell.tone}`}>
          {cell.rec && <span class="dot rec pulse" />}
          {cell.icon && <Icon d={cell.icon} size={13} stroke={2} />}
          <span class="ellipsis">{cell.text}</span>
          {st.sub && (
            <span class="tag" title={t("st.sub_title", { name: st.sub })}>
              {t("st.sub")}
            </span>
          )}
        </span>
        <span class="hit-actions" onClick={stop}>
          <button class="act" title={t("row.play")} aria-label={t("row.play")} onClick={() => actions.play(film)}>
            <Icon d={I.play} size={13} stroke={1.6} fill />
          </button>
          <button
            class="act"
            title={t("row.download", { quality: t("q.hd") })}
            aria-label={t("row.download", { quality: t("q.hd") })}
            onClick={() => actions.download(film, "hd")}
          >
            <Icon d={I.download} size={15} stroke={1.9} />
          </button>
          <button
            class="act narrow"
            title={t("q.choose")}
            aria-label={t("q.choose")}
            aria-expanded={menu}
            onClick={() => actions.menu(film)}
          >
            <Icon d={I.chevron} size={11} stroke={2.4} />
          </button>
          <button class="act-abo" title={t("row.subscribe")} onClick={() => actions.subscribe(film)}>
            {t("st.sub")}
          </button>
          {menu && <QualityMenu film={film} onPick={(q) => actions.download(film, q)} />}
        </span>
      </div>
    );
  }
}

/** "+1" beside the channel: the same file on other channels, named in the tooltip and for screen readers. */
function TwinCount(props: { others: Film[] }): JSX.Element {
  const { others } = props;
  const title = [`${t("row.twins")}:`, ...others.map((x) => `${x.channel} · ${x.title}`)].join("\n");
  return (
    <span class="twin-count" title={title}>
      <span aria-hidden="true">+{others.length}</span>
      <span class="sr-only">{`${t("row.twins")}: ${others.map((x) => x.channel).join(", ")}`}</span>
    </span>
  );
}

function QualityMenu(props: { film: Film; onPick: (q: Quality) => void }): JSX.Element {
  return (
    <div role="menu" class="menu quality-menu">
      {S.qualities(props.film).map((q) => (
        <button
          key={q.quality}
          role="menuitem"
          class="menu-item quality-item"
          disabled={!q.available}
          onClick={() => props.onPick(q.quality)}
        >
          <strong>{t(QUALITY_KEYS[q.quality])}</strong>
          <span class="faint small">{q.available ? "" : t("q.missing")}</span>
          <span class="muted-text">{q.size ? F.size(q.size) : ""}</span>
        </button>
      ))}
    </div>
  );
}

// -- the view ----------------------------------------------------------------------------------------------------------

export function Search(props: { route: Route; layout: Layout }): JSX.Element {
  const { route, layout } = props;
  const f = useMemo(() => S.parseFilters(route.query), [route.query]);
  const info = useInfo();
  const status = useStatus();
  const settings = useSettings();
  const libraries = useLibraries();
  const movieLibraries = (libraries ?? []).filter((l) => l.kind === "movies");
  const movieOpen = route.query.ziel === "film"; // the details show "into the movie library" (kept over F5)
  const timeZone = info?.timezone;
  const descriptionSearch = info?.description_search ?? false;
  const scope = S.apiScope(f.scope, descriptionSearch);
  const mobile = layout === "mobile";

  const [draft, setDraft] = useState<string | null>(null);
  const [result, setResult] = useState<(SearchResult & { key: string }) | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [pop, setPop] = useState<Pop>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [selQuality, setSelQuality] = useState<Quality | null>(null); // null: the preferred one from the settings
  const bulkQuality = selQuality ?? settings?.quality ?? "hd";
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [shown, setShown] = useState(PAGE);
  const [extra, setExtra] = useState<Film | null>(null); // detail of a hit that is not in the result
  const [retrying, setRetrying] = useState(false);

  const root = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const debounce = useRef<number | undefined>(undefined);
  const seq = useRef(0);
  const width = useWidth(root);

  const key = `${f.q.trim()}|${scope}`;
  const load = (silent: boolean) => {
    const q = f.q.trim();
    const mine = ++seq.current;
    if (!q) {
      setResult(null);
      setLoading(false);
      setFailure(null);
      return;
    }
    if (!silent) {
      setLoading(true);
      setFailure(null);
    }
    api
      .search({ q, scope, limit: LIMIT, status: true })
      .then((r) => {
        if (mine === seq.current) setResult({ ...r, key });
      })
      .catch((e: unknown) => {
        if (mine !== seq.current || silent) return;
        const noIndex = e instanceof ApiError && e.code === "description_search_unavailable";
        setFailure(noIndex ? t("search.no_description_index") : errorMessage(e));
      })
      .finally(() => {
        if (mine === seq.current) setLoading(false);
      });
  };

  // A new query: fresh result, no selection, first page.
  useEffect(() => {
    load(false);
    setSelected(new Set());
    setShown(PAGE);
    setCollapsed(new Set());
  }, [key]);

  // Download states change: reload quietly so every row shows what happened (cheap, LAN, < 0.2 s).
  const downloadsKey = status ? JSON.stringify(status.downloads.counts) : "";
  const listKey = status?.filmlist.last_import?.created ?? "";
  const seen = useRef("");
  useEffect(() => {
    const now = `${downloadsKey}|${listKey}`;
    const before = seen.current;
    seen.current = now;
    if (before && before !== now && result) load(true);
  }, [downloadsKey, listKey]);

  useEffect(() => () => window.clearTimeout(debounce.current), []);

  const now = useMemo(() => Date.now(), [result]);
  const films = result?.key === key ? result.films : [];
  const filtered = useMemo(() => S.applyFilters(films, f, now), [films, f, now]);
  // One row per file: filters count every channel's entry, the list shows each file once ("ZDF +1").
  const twins = useMemo(() => S.mergeTwins(filtered, films, timeZone), [filtered, films, timeZone]);
  const sorted = useMemo(() => S.sortFilms(twins.shown, f.sort, f.desc), [twins, f.sort, f.desc]);
  const groups = useMemo(() => (f.gruppe ? S.groupByTopic(sorted) : null), [sorted, f.gruppe]);
  const facets = useMemo(() => S.facets(films, f, now), [films, f, now]);

  // Reset the rendered page when the filters change.
  useEffect(() => setShown(PAGE), [filtered]);

  // Render more rows when the end of the list comes into view.
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !scroller.current) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setShown((n) => n + PAGE);
      },
      { root: scroller.current, rootMargin: "600px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [shown, sorted, collapsed]);

  const detailId = f.id;
  const detail = detailId ? (films.find((x) => x.id === detailId) ?? (extra?.id === detailId ? extra : null)) : null;
  const playNext = useRef<string | null>(null);
  useEffect(() => {
    setPlaying(!!detailId && playNext.current === detailId);
    playNext.current = null;
  }, [detailId]);
  useEffect(() => {
    if (!detailId || films.some((x) => x.id === detailId) || extra?.id === detailId) return;
    if (loading || (f.q.trim() && !result)) return;
    api.film(detailId).then(setExtra, () => setQuery({ id: "" }));
  }, [detailId, films, loading]);

  const shownId = detailId ? (twins.of.get(detailId)?.[0]?.id ?? detailId) : null; // a twin's detail marks its row
  const geo = useMemo(() => geometry(width, !!detail, twins.of.size > 0), [width, !!detail, twins.of.size > 0]);

  // Live progress of running downloads from the polled status.
  const live = new Map<number, number>();
  for (const r of status?.downloads.running ?? []) if (r.total) live.set(r.id, Math.min(1, r.done / r.total));

  // -- actions ---------------------------------------------------------------------------------------------------------

  const runQuery = (value: string) => {
    window.clearTimeout(debounce.current);
    setDraft(null);
    setPop(null);
    setQuery({ q: value.trim(), id: "" });
  };

  const setFilter = (patch: Record<string, string | number | boolean | null>) => setQuery(patch);

  /** How a film travels to the API: by its id in the film list. */
  const bodyOf = (film: Film): DownloadBody => ({ film_id: film.id });

  const enqueue = async (list: Film[], quality: Quality | undefined, force = false, naming?: Naming): Promise<number> => {
    if (!list.length || !result) return 0;
    remember(f.q);
    let queued = 0;
    let duplicates = 0;
    let failed = "";
    for (const film of list) {
      try {
        await api.download({ ...bodyOf(film), quality, force, naming });
        queued++;
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) duplicates++;
        else failed = errorMessage(e);
      }
    }
    const only = list[0];
    if (failed) toast(t("dl.error", { message: failed }));
    else if (list.length === 1 && duplicates && only) {
      toast(t("dl.duplicate_one"), { label: t("dl.force"), run: () => void enqueue([only], quality, true, naming) });
    } else if (queued) {
      const text = queued === 1 && only ? t("dl.queued_one", { title: only.title }) : t("dl.queued_many", { n: queued });
      toast(duplicates ? `${text} · ${t("dl.duplicate_many", { n: duplicates })}` : text, {
        label: t("dl.show"),
        run: () => go("/downloads"),
      });
    } else if (duplicates) toast(t("dl.duplicate_many", { n: duplicates }));
    setMenuFor(null);
    await refreshStatus();
    load(true);
    return queued;
  };

  const subscribeFrom = (film: Film | null) => go(build("/abos/neu", { q: f.q.trim(), von: film?.id ?? null }));

  const handlers = useRef<RowActions | null>(null);
  handlers.current = {
    open: (film) => {
      remember(f.q);
      setMenuFor(null);
      setQuery({ id: detailId === film.id ? "" : film.id, ziel: "" });
    },
    check: (film) =>
      setSelected((sel) => {
        const next = new Set(sel);
        if (next.has(film.id)) next.delete(film.id);
        else next.add(film.id);
        return next;
      }),
    play: (film) => {
      remember(f.q);
      if (detailId === film.id) setPlaying(true);
      else {
        playNext.current = film.id;
        setQuery({ id: film.id });
      }
    },
    download: (film, quality) => void enqueue([film], quality),
    menu: (film) => {
      setPop(null);
      setMenuFor((id) => (id === film.id ? null : film.id));
    },
    subscribe: (film) => subscribeFrom(film),
  };
  const actions = useMemo<RowActions>(
    () => ({
      open: (film) => handlers.current?.open(film),
      check: (film) => handlers.current?.check(film),
      play: (film) => handlers.current?.play(film),
      download: (film, q) => handlers.current?.download(film, q),
      menu: (film) => handlers.current?.menu(film),
      subscribe: (film) => handlers.current?.subscribe(film),
    }),
    [],
  );

  // Keyboard: "/" focuses the search, Escape closes menus, then the details.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement;
      if (e.key === "/" && !typing) {
        e.preventDefault();
        input.current?.focus();
      } else if (e.key === "Escape" && !typing) {
        if (pop || menuFor) {
          setPop(null);
          setMenuFor(null);
        } else if (detailId) setQuery({ id: "" });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pop, menuFor, detailId]);

  const retryFilmlist = async () => {
    setRetrying(true);
    await api.updateFilmlist().catch(() => undefined);
    window.setTimeout(() => {
      setRetrying(false);
      void refreshStatus();
    }, 1500);
  };

  // -- rendering ------------------------------------------------------------------------------------------------------

  const fl = status?.filmlist;
  const importing = !!fl && fl.running && !fl.films;
  const stale = !!fl?.last_error && fl.films > 0;
  const q = f.q.trim();
  const showSkeleton = (loading && !result) || (loading && result?.key !== key) || importing;
  const selectedFilms = sorted.filter((x) => selected.has(x.id));
  const allChecked = sorted.length > 0 && selectedFilms.length === sorted.length;
  const created = fl?.last_import?.created;

  const chip = (k: Exclude<Pop, null>, label: string, active: boolean, wide = false) => (
    <div class="chip-wrap">
      <button
        class={`chip${active ? " on" : ""}`}
        aria-expanded={pop === k}
        onClick={() => {
          setMenuFor(null);
          setPop((p) => (p === k ? null : k));
        }}
      >
        <span>{label}</span>
        <Icon d={I.chevron} size={12} stroke={2} />
      </button>
      {pop === k && <div class={`menu chip-pop${wide ? " wide" : ""}${mobile ? " sheet" : ""}`}>{popContent(k)}</div>}
    </div>
  );

  const popContent = (k: Exclude<Pop, null>): JSX.Element => {
    if (k === "sender") {
      return (
        <div role="dialog" aria-label={t("search.sender")}>
          <div class="menu-title">{t("search.sender_title")}</div>
          {facets.senders.map(([name, n]) => (
            <label key={name} class="menu-item">
              <input
                type="checkbox"
                checked={f.sender.includes(name)}
                onChange={() => setFilter({ sender: S.toggled(f.sender, name).join(",") })}
              />
              <span class="grow mono channel-name">{name}</span>
              <span class="faint small">{F.num(n)}</span>
            </label>
          ))}
          <div class="menu-foot">
            <button class="button link small" onClick={() => setFilter({ sender: "" })}>
              {t("search.sender_all")}
            </button>
          </div>
        </div>
      );
    }
    if (k === "thema") {
      return (
        <div role="dialog" aria-label={t("search.thema")}>
          <div class="menu-title">{t("search.thema_title")}</div>
          {facets.topics.map(([name, n]) => (
            <label key={name} class="menu-item">
              <input
                type="checkbox"
                checked={f.thema.includes(name)}
                onChange={() => setFilter({ thema: S.toggled(f.thema, name).join("|") })}
              />
              <span class="grow">{name}</span>
              <span class="faint small">{F.num(n)}</span>
            </label>
          ))}
          <div class="menu-foot">
            <button class="button link small" onClick={() => setFilter({ thema: "" })}>
              {t("search.thema_all")}
            </button>
          </div>
        </div>
      );
    }
    if (k === "titel") return <TitlePop value={f.titel} onInput={(titel) => setFilter({ titel })} />;
    if (k === "dauer") {
      return (
        <div role="dialog" aria-label={t("search.dauer")} class="pop-form">
          <div class="label">{t("search.dauer_label")}</div>
          <div class="row">
            <input
              class="input minutes"
              type="number"
              min="0"
              value={f.dmin ?? ""}
              placeholder={t("search.dauer_from")}
              aria-label={t("search.dauer_from")}
              onInput={(e) => setFilter({ dmin: (e.target as HTMLInputElement).value })}
            />
            <span class="faint">–</span>
            <input
              class="input minutes"
              type="number"
              min="0"
              value={f.dmax ?? ""}
              placeholder={t("search.dauer_to")}
              aria-label={t("search.dauer_to")}
              onInput={(e) => setFilter({ dmax: (e.target as HTMLInputElement).value })}
            />
            <span class="faint">min</span>
          </div>
        </div>
      );
    }
    const periods: [S.Period, Key][] = [
      ["", "search.zeit_all"],
      ["7", "search.zeit_7"],
      ["30", "search.zeit_30"],
      ["365", "search.zeit_365"],
    ];
    return (
      <div role="dialog" aria-label={t("search.zeit")}>
        {periods.map(([value, label]) => (
          <label key={value} class="menu-item">
            <input
              type="radio"
              name="zeitraum"
              checked={f.zeit === value}
              onChange={() => {
                setFilter({ zeit: value });
                setPop(null);
              }}
            />
            <span>{t(label)}</span>
          </label>
        ))}
      </div>
    );
  };

  const toggle = (k: "hd" | "ut" | "kuenftig", on: boolean, label: string) => (
    <button class={`chip toggle${on ? " on" : ""}`} aria-pressed={on} onClick={() => setFilter({ [k]: on ? "" : "1" })}>
      {on && <Icon d={I.check} size={13} stroke={2.2} />}
      {label}
    </button>
  );

  const zeitChip: Record<S.Period, string> = {
    "": t("search.zeit"),
    "7": t("search.zeit_chip_7"),
    "30": t("search.zeit_chip_30"),
    "365": t("search.zeit_chip_365"),
  };

  const header = (
    <div class={`search-head${mobile ? " mobile" : ""}`}>
      <div class="search-line">
        <label class="search-box">
          <Icon d={I.search} size={18} class="faint" />
          <input
            ref={input}
            type="text"
            value={draft ?? f.q}
            placeholder={t("search.placeholder")}
            aria-label={t("search.label")}
            enterKeyHint="search"
            autoComplete="off"
            spellcheck={false}
            onInput={(e) => {
              const value = (e.target as HTMLInputElement).value;
              setDraft(value);
              window.clearTimeout(debounce.current);
              debounce.current = window.setTimeout(() => runQuery(value), 320);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                const value = (e.target as HTMLInputElement).value;
                remember(value);
                runQuery(value);
              }
              if (e.key === "Escape") runQuery("");
            }}
          />
          {(draft ?? f.q) && (
            <button class="icon-button" aria-label={t("search.clear")} onClick={() => runQuery("")}>
              <Icon d={I.close} size={16} />
            </button>
          )}
        </label>
        <div class="chip-wrap">
          <button
            class={`icon-button syntax-button${pop === "syntax" ? " active" : ""}`}
            title={t("search.syntax")}
            aria-label={t("search.syntax")}
            aria-expanded={pop === "syntax"}
            onClick={() => setPop(pop === "syntax" ? null : "syntax")}
          >
            <Icon d={I.help} size={19} />
          </button>
          {pop === "syntax" && (
            <div class={`menu chip-pop syntax-pop${mobile ? " sheet" : ""}`}>
              <div class="menu-title">{t("search.syntax")}</div>
              <p class="small faint">{t("search.syntax_intro")}</p>
              {SYNTAX.filter((s) => descriptionSearch || !s.description).map((s) => (
                <button key={s.example} class="menu-item" onClick={() => runQuery(`${(draft ?? f.q).trim()} ${s.example}`.trim())}>
                  <code>{s.example}</code>
                  <span>{t(s.label)}</span>
                </button>
              ))}
              <p class="small faint">{t("search.syntax_rules")}</p>
            </div>
          )}
        </div>
        {!geo.list && (
          <select
            class="scope-select"
            value={f.scope === "alles" && !descriptionSearch ? "titelthema" : f.scope}
            aria-label={t("search.scope")}
            onChange={(e) => {
              const value = (e.target as HTMLSelectElement).value;
              setFilter({ in: value === "titelthema" ? "" : value });
            }}
          >
            <option value="titelthema">{t("search.scope.titelthema")}</option>
            <option value="titel">{t("search.scope.titel")}</option>
            <option value="thema">{t("search.scope.thema")}</option>
            {descriptionSearch && <option value="alles">{t("search.scope.alles")}</option>}
          </select>
        )}
      </div>
      <div class={`chips${mobile ? " scroll" : ""}`}>
        {pop && <div class="pop-backdrop" onClick={() => setPop(null)} />}
        {chip("sender", f.sender.length ? t("search.sender_on", { list: f.sender.join(", ") }) : t("search.sender"), f.sender.length > 0)}
        {chip(
          "thema",
          f.thema.length > 1
            ? t("search.thema_many", { n: f.thema.length })
            : f.thema[0]
              ? t("search.thema_one", { name: f.thema[0] })
              : t("search.thema"),
          f.thema.length > 0,
          true,
        )}
        {chip("titel", f.titel ? t("search.titel_on", { text: f.titel }) : t("search.titel"), !!f.titel, true)}
        {chip(
          "dauer",
          f.dmin !== null || f.dmax !== null
            ? t("search.dauer_on", { min: f.dmin ?? 0, max: f.dmax ?? "∞" })
            : t("search.dauer"),
          f.dmin !== null || f.dmax !== null,
        )}
        {chip("zeit", zeitChip[f.zeit], !!f.zeit)}
        <span class="chip-sep" />
        {toggle("hd", f.hd, t("search.hd"))}
        {toggle("ut", f.ut, t("search.ut"))}
        {toggle("kuenftig", f.kuenftig, t("search.kuenftig"))}
        {S.hasFilters(f) && (
          <button class="chip-reset" onClick={() => setFilter(S.NO_FILTERS)}>
            {t("search.reset")}
          </button>
        )}
      </div>
    </div>
  );

  let body: JSX.Element;
  if (showSkeleton) {
    body = (
      <div aria-busy="true" aria-label={t("search.loading")} class="skeleton-list">
        <div class="skel-head">
          <span class="skel" style={{ width: "110px", height: "12px" }} />
        </div>
        {Array.from({ length: 10 }, (_, i) => (
          <div key={i} class="skel-row">
            <span class="skel" style={{ width: "44px", height: "16px", borderRadius: "3px" }} />
            <span class="skel" style={{ width: `${90 + ((i * 37) % 70)}px` }} />
            <span class="skel grow" style={{ maxWidth: `${180 + ((i * 53) % 220)}px` }} />
            <span class="skel push" style={{ width: "70px" }} />
            <span class="skel" style={{ width: "40px" }} />
          </div>
        ))}
      </div>
    );
  } else if (failure) {
    body = (
      <div class="empty">
        <h2>{t("search.failed", { message: failure })}</h2>
        <div>
          <button class="button" onClick={() => load(false)}>
            {t("error.retry")}
          </button>
        </div>
      </div>
    );
  } else if (!q) {
    const recent = readRecent();
    body = (
      <div class="empty">
        <div class="stripes" aria-hidden="true">
          {[0, 1, 2, 3, 4, 5, 6].map((i) => (
            <span key={i} />
          ))}
        </div>
        <h2>{t("search.empty_title")}</h2>
        <p>{t("search.empty_text")}</p>
        {recent.length > 0 && (
          <div class="row wrap">
            <span class="small faint">{t("search.recent")}</span>
            {recent.map((r) => (
              <button key={r} class="recent" onClick={() => runQuery(r)}>
                {r}
              </button>
            ))}
          </div>
        )}
      </div>
    );
  } else if (!sorted.length) {
    const chips = S.activeFilters(f);
    const label = (kind: string, value: string) => {
      if (kind === "sender") return `${t("search.sender")}: ${value}`;
      if (kind === "thema") return t("search.thema_one", { name: value });
      if (kind === "titel") return t("search.titel_on", { text: value });
      if (kind === "dauer") return t("search.dauer");
      if (kind === "zeit") return zeitChip[value as S.Period];
      return kind === "hd" ? t("search.hd") : t("search.ut");
    };
    const unfiltered = S.applyFilters(films, S.parseFilters({ q: f.q, kuenftig: f.kuenftig ? "1" : "" }), now).length;
    body = (
      <div class="empty">
        <h2>{t("search.nohits_title", { q })}</h2>
        <p>{chips.length ? t("search.nohits_filtered", { n: F.num(unfiltered) }) : t("search.nohits_plain")}</p>
        {chips.length > 0 && (
          <div class="row wrap">
            {chips.map((c) => (
              <button key={c.kind + c.value} class="chip on" onClick={() => setFilter(c.patch)}>
                <span>{label(c.kind, c.value)}</span>
                <Icon d={I.close} size={12} stroke={2.2} />
              </button>
            ))}
          </div>
        )}
        <div class="row wrap">
          {f.scope !== "alles" && descriptionSearch && (
            <button class="button" onClick={() => setFilter({ in: "alles" })}>
              {t("search.widen")}
            </button>
          )}
          {!f.kuenftig && (
            <button class="button" onClick={() => setFilter({ kuenftig: "1" })}>
              {t("search.include_future")}
            </button>
          )}
        </div>
      </div>
    );
  } else {
    let budget = shown;
    const rows = (list: Film[]) => {
      const part = list.slice(0, Math.max(0, budget));
      budget -= part.length;
      return part.map((film) => (
        <Hit
          key={film.id}
          film={film}
          twins={twins.of.get(film.id)}
          geo={geo}
          checked={selected.has(film.id)}
          active={shownId === film.id}
          menu={menuFor === film.id}
          live={film.status?.download_id != null ? live.get(film.status.download_id) : undefined}
          timeZone={timeZone}
          actions={actions}
        />
      ));
    };
    const sortHeader = (k: S.SortKey, label: Key, right = false) => (
      <button class={`sort${f.sort === k ? " on" : ""}${right ? " right" : ""}`} onClick={() => setFilter({ sort: S.nextSort(f, k) })}>
        {t(label)}
        <span>{f.sort === k ? (f.desc ? "↓" : "↑") : ""}</span>
      </button>
    );
    const truncated = result && result.total > films.length;
    body = (
      <>
        <div class="results-head">
          <div class="results-bar">
            {selectedFilms.length === 0 ? (
              <>
                <div class="row baseline">
                  <strong class="hits">{t("search.hits", { n: F.num(sorted.length) })}</strong>
                  <span class="faint">{t("search.topics", { n: S.topicCount(sorted) })}</span>
                </div>
                <span class="grow" />
                <label class="check-label">
                  <input type="checkbox" checked={f.gruppe} onChange={() => setFilter({ gruppe: f.gruppe ? "" : "1" })} />
                  {t("search.group")}
                </label>
                <button class="button" onClick={() => subscribeFrom(null)}>
                  <Icon d={I.aboAdd} size={15} stroke={1.7} />
                  {t("search.subscribe")}
                </button>
              </>
            ) : (
              <>
                <div class="row baseline">
                  <strong class="hits">{t("search.selected", { n: selectedFilms.length })}</strong>
                  <span class="muted-text">
                    {F.size(selectedFilms.reduce((sum, x) => sum + (x.size ?? 0), 0) || null)}
                  </span>
                </div>
                <span class="grow" />
                <select
                  class="input"
                  value={bulkQuality}
                  aria-label={t("q.label")}
                  onChange={(e) => setSelQuality((e.target as HTMLSelectElement).value as Quality)}
                >
                  <option value="hd">{t("q.prefer_hd")}</option>
                  <option value="normal">{t("q.normal")}</option>
                  <option value="low">{t("q.low")}</option>
                </select>
                <button
                  class="button primary"
                  onClick={() => {
                    void enqueue(selectedFilms, bulkQuality);
                    setSelected(new Set());
                  }}
                >
                  <Icon d={I.download} size={15} stroke={1.9} />
                  {t("search.download")}
                </button>
                <button class="button ghost" onClick={() => setSelected(new Set())}>
                  {t("search.clear_selection")}
                </button>
              </>
            )}
          </div>
          {truncated && (
            <div class="truncated small">
              {t("search.truncated", { shown: F.num(films.length), total: F.num(result.total) })}
            </div>
          )}
          {!geo.list && (
            <div role="row" class="hit-head" style={{ gridTemplateColumns: geo.cols }}>
              <span class="cell-check">
                <input
                  type="checkbox"
                  checked={allChecked}
                  aria-label={t("search.select_all")}
                  onChange={() => setSelected(allChecked ? new Set() : new Set(sorted.map((x) => x.id)))}
                />
              </span>
              {sortHeader("sender", "col.sender")}
              {sortHeader("thema", "col.thema")}
              {sortHeader("titel", "col.titel")}
              {sortHeader("datum", "col.datum")}
              {sortHeader("dauer", "col.dauer", true)}
              {sortHeader("groesse", "col.groesse", true)}
              {geo.wide && (
                <>
                  <span class="center">{t("col.hd")}</span>
                  <span class="center">{t("col.ut")}</span>
                  <span class="center">{t("col.geo")}</span>
                </>
              )}
              {geo.medium && <span>{t("col.marks")}</span>}
              <span>{t("col.status")}</span>
              <span />
            </div>
          )}
        </div>
        {groups
          ? groups.map((g) => {
              const open = !collapsed.has(g.topic);
              return (
                <div key={g.topic}>
                  <div class="group-head">
                    <button
                      class="group-toggle"
                      aria-expanded={open}
                      onClick={() =>
                        setCollapsed((c) => {
                          const next = new Set(c);
                          if (next.has(g.topic)) next.delete(g.topic);
                          else next.add(g.topic);
                          return next;
                        })
                      }
                    >
                      <Icon d={I.chevron} size={14} stroke={2} class={open ? "" : "turned"} />
                      {g.topic}
                    </button>
                    <span class="small faint">
                      {t("group.meta", {
                        n: F.num(g.films.length),
                        channels: g.channels.map(([c, n]) => `${c} ${n}`).join(" · "),
                      })}
                    </span>
                    <span class="grow" />
                    <button class="button small" onClick={() => subscribeFrom(g.films[0] ?? null)}>
                      {t("group.subscribe")}
                    </button>
                  </div>
                  {open && rows(g.films)}
                </div>
              );
            })
          : rows(sorted)}
        {shown < sorted.length && (
          <div ref={sentinel} class="more">
            <button class="button ghost" onClick={() => setShown((n) => n + PAGE)}>
              {t("search.more")}
            </button>
          </div>
        )}
        <div style={{ height: "24px" }} />
      </>
    );
  }

  return (
    <div class={`search${geo.compact ? " compact" : ""}${mobile ? " mobile" : ""}`} ref={root}>
      {header}
      {stale && (
        <div role="status" class="notice warn">
          <Icon d={I.warn} size={16} stroke={1.9} class="warn-text" />
          <div class="grow notice-text">
            <strong>{t("search.stale_title", { date: created ? F.dateTime(created, timeZone) : "–" })}</strong>{" "}
            {t("search.stale_text", { error: fl?.last_error ?? "" })}
          </div>
          <div class="row">
            <button class="button primary small" onClick={retryFilmlist} disabled={retrying}>
              {retrying ? t("fl.retrying") : t("fl.retry")}
            </button>
            <button class="button small" onClick={() => go("/einstellungen/datenquelle")}>
              {t("search.stale_source")}
            </button>
          </div>
        </div>
      )}
      {importing && (
        <div role="status" class="notice">
          <div class="grow">
            <strong>{t("search.import_title")}</strong>
            <div class="small muted-text">{t("search.import_text")}</div>
          </div>
        </div>
      )}
      <div class="search-main">
        <div class="search-scroll" ref={scroller} onScroll={() => menuFor && setMenuFor(null)}>
          {body}
        </div>
        {detail && !geo.side && <div class={`detail-backdrop${mobile ? " clear" : ""}`} onClick={() => setQuery({ id: "" })} />}
        {detail && (
          <Detail
            film={detail}
            others={twins.of.get(detail.id)?.filter((x) => x !== detail) ?? []}
            onOther={(other) => setQuery({ id: other.id, ziel: "" })}
            side={geo.side}
            mobile={mobile}
            playing={playing}
            setPlaying={setPlaying}
            timeZone={timeZone}
            subtitles={settings?.subtitles ?? true}
            quality={settings?.quality ?? "hd"}
            live={detail.status?.download_id != null ? live.get(detail.status.download_id) : undefined}
            onClose={() => setQuery({ id: "", ziel: "" })}
            onDownload={(q) => void enqueue([detail], q)}
            movieLibraries={movieLibraries}
            movieOpen={movieOpen}
            onMovie={(open) => setQuery({ ziel: open ? "film" : "" })}
            movieBody={bodyOf(detail)}
            onMovieLoad={(naming) =>
              void enqueue([detail], undefined, false, naming).then((n) => n > 0 && setQuery({ ziel: "" }))
            }
            onSubscribe={() => subscribeFrom(detail)}
            onTopic={() => setFilter({ thema: detail.topic, id: "" })}
            future={S.isFuture(detail, now)}
          />
        )}
      </div>
    </div>
  );
}

// -- details -----------------------------------------------------------------------------------------------------------

function Detail(props: {
  film: Film;
  /** The same file on other channels. */
  others: Film[];
  onOther: (film: Film) => void;
  side: boolean;
  mobile: boolean;
  playing: boolean;
  setPlaying: (on: boolean) => void;
  timeZone: string | undefined;
  subtitles: boolean;
  /** The preferred quality from the settings: the download button and the preview start with it. */
  quality: Quality;
  live: number | undefined;
  future: boolean;
  onClose: () => void;
  onDownload: (q: Quality) => void;
  onSubscribe: () => void;
  onTopic: () => void;
  /** Movie libraries: the film goes there under its TMDB name; empty: no such library, no button. */
  movieLibraries: Library[];
  movieOpen: boolean;
  onMovie: (open: boolean) => void;
  movieBody: DownloadBody;
  onMovieLoad: (naming: Naming) => void;
}): JSX.Element {
  const { film, timeZone } = props;
  const st = hitStatus(film, props.live);
  const pct = Math.round((st.progress ?? 0) * 100);
  let statusText = "";
  if (st.kind === "running") statusText = t("det.st_running", { pct });
  else if (st.kind === "queued") statusText = t("det.st_queued");
  else if (st.kind === "done") statusText = t("det.st_done");
  else if (st.kind === "dup") statusText = t("det.st_dup");
  else if (st.kind === "failed") statusText = t("det.st_failed");
  if (st.sub) statusText = statusText ? statusText + t("det.st_sub_suffix", { name: st.sub }) : t("det.st_sub", { name: st.sub });
  const icon = st.kind === "done" || st.kind === "dup" ? I.check : st.kind === "queued" ? I.clock : st.kind === "failed" ? I.warn : st.sub ? I.abos : I.download;
  const geoText = S.geoLabel(film);
  const marks = [film.url_hd ? "HD" : null, film.url_subtitle ? t("col.ut") : null, geoText ? `Geo ${geoText}` : null].filter(
    (x): x is string => !!x,
  );
  const preferred = S.pickQuality(film, props.quality) ?? props.quality;
  // The preview plays the preferred quality unless another one was picked for this film.
  const [picked, setPicked] = useState<{ film: string; quality: Quality } | null>(null);
  const previewQuality = picked?.film === film.id ? picked.quality : preferred;
  const available = S.qualities(film).filter((q) => q.available);
  const website = film.url_website;
  return (
    <aside aria-label={t("det.label")} class={`detail${props.side ? " side" : ""}${props.mobile ? " mobile" : ""}`}>
      <div class="detail-top">
        <ChannelBadge channel={film.channel} />
        <span class="grow ellipsis small muted-text">{film.topic}</span>
        <button class="icon-button" aria-label={t("det.close")} onClick={props.onClose}>
          <Icon d={I.close} size={16} />
        </button>
      </div>
      <h2 class="detail-title">{film.title}</h2>
      <div class="detail-meta">
        <span>{F.date(film.start, timeZone)}</span>
        {film.start && <span>{F.time(film.start, timeZone)}</span>}
        <span>{F.duration(film.duration)}</span>
        {marks.map((m) => (
          <span key={m} class="mark">
            {m}
          </span>
        ))}
      </div>
      <Preview
        key={film.id}
        url={S.urlOf(film, previewQuality)}
        playing={props.playing}
        setPlaying={props.setPlaying}
        duration={film.duration}
        future={props.future ? t("det.future", { date: F.dateTime(film.start, timeZone) }) : null}
      />
      {available.length > 1 && !props.future && (
        <label class="preview-quality">
          <span>{t("det.preview_quality")}</span>
          <select
            class="input"
            value={previewQuality}
            onChange={(e) => setPicked({ film: film.id, quality: (e.target as HTMLSelectElement).value as Quality })}
          >
            {available.map((q) => (
              <option key={q.quality} value={q.quality}>
                {t(QUALITY_KEYS[q.quality])}
              </option>
            ))}
          </select>
        </label>
      )}
      {statusText && (
        <div class="detail-status">
          <Icon d={icon} size={14} stroke={2} class={st.kind === "done" ? "ok-text" : st.kind === "failed" ? "err-text" : ""} />
          <span class="grow">{statusText}</span>
          {st.subId != null && (
            <button class="button link small" onClick={() => go(`/abos/${st.subId}`)}>
              {t("det.open_subscription")}
            </button>
          )}
        </div>
      )}
      {film.description && <p class="detail-text">{film.description}</p>}
      <div class="detail-label">{t("det.qualities")}</div>
      <div class="detail-qualities">
        {S.qualities(film).map((q) => (
          <div key={q.quality} class={`quality-row${q.available ? "" : " missing"}`}>
            <strong>{t(QUALITY_KEYS[q.quality])}</strong>
            <span class="faint small">{q.available ? "" : t("q.missing")}</span>
            <span class="muted-text">{q.size ? F.size(q.size) : ""}</span>
            <button
              class="icon-button small-icon"
              aria-label={t("det.quality_download")}
              title={t("det.quality_download")}
              disabled={!q.available}
              onClick={() => props.onDownload(q.quality)}
            >
              <Icon d={I.download} size={15} stroke={1.9} />
            </button>
          </div>
        ))}
      </div>
      <div class="detail-facts">
        <span class="faint">{t("det.subtitles")}</span>
        <span>{film.url_subtitle ? (props.subtitles ? t("det.subtitles_yes") : t("det.subtitles_off")) : t("det.none")}</span>
        <span class="faint">{t("det.geo")}</span>
        <span>{geoText ? t("det.geo_yes", { list: film.geo.join(", ") }) : t("det.none")}</span>
        <span class="faint">{t("det.show")}</span>
        <button class="link-button" onClick={props.onTopic}>
          {t("det.show_only", { topic: film.topic })}
        </button>
        {website && (
          <>
            <span class="faint">{t("det.website")}</span>
            <a href={website} target="_blank" rel="noopener noreferrer" class="website">
              <span class="ellipsis">{website.replace(/^https?:\/\/(www\.)?/, "")}</span>
              <Icon d={I.external} size={12} stroke={2} />
            </a>
          </>
        )}
      </div>
      {props.others.length > 0 && (
        <>
          <div class="detail-label">{t("row.twins")}</div>
          <div class="detail-twins">
            {props.others.map((other) => (
              <button key={other.id} class="twin-row" title={other.title} onClick={() => props.onOther(other)}>
                <ChannelBadge channel={other.channel} small />
                <span class="ellipsis">{other.title}</span>
              </button>
            ))}
          </div>
        </>
      )}
      {props.movieLibraries.length > 0 &&
        (props.movieOpen ? (
          <MovieTarget
            title={film.title}
            filmKey={film.id}
            body={props.movieBody}
            libraries={props.movieLibraries}
            onLoad={props.onMovieLoad}
            onClose={() => props.onMovie(false)}
          />
        ) : (
          <div class="detail-movie-open">
            <button class="button" onClick={() => props.onMovie(true)}>
              <Icon d={I.folder} size={15} stroke={1.8} />
              {t("mv.open")}
            </button>
          </div>
        ))}
      <div class="grow detail-spacer" />
      <div class="detail-actions">
        <button class="button primary grow" onClick={() => props.onDownload(preferred)}>
          <Icon d={I.download} size={15} stroke={1.9} />
          {t("det.download_quality", { quality: t(QUALITY_KEYS[preferred]) })}
        </button>
        <button class="button" onClick={props.onSubscribe}>
          {t("search.subscribe")}
        </button>
      </div>
    </aside>
  );
}

function Preview(props: {
  url: string | null;
  playing: boolean;
  setPlaying: (on: boolean) => void;
  duration: number | null;
  future: string | null;
}): JSX.Element {
  const { url } = props;
  const nativeHls = useMemo(() => document.createElement("video").canPlayType("application/vnd.apple.mpegurl") !== "", []);
  const [hlsFailed, setHlsFailed] = useState(false);
  const position = useRef(0); // seconds played: another quality goes on from there
  useEffect(() => setHlsFailed(false), [url]);
  if (props.future) return <div class="preview stripes-bg"><div class="preview-note">{props.future}</div></div>;
  if (!url) return <div class="preview stripes-bg"><div class="preview-note">{t("det.preview_none")}</div></div>;
  if (props.playing) {
    if (S.isHls(url) && !nativeHls) {
      return (
        <div class="preview dark">
          {hlsFailed ? (
            <div class="preview-note">{t("det.preview_hls")}</div>
          ) : (
            <HlsVideo url={url} position={position} onFail={() => setHlsFailed(true)} />
          )}
        </div>
      );
    }
    return (
      <div class="preview dark">
        <video
          src={url}
          controls
          autoPlay
          playsInline
          preload="metadata"
          onTimeUpdate={(e) => notePosition(position, e.currentTarget)}
          onLoadedMetadata={(e) => {
            if (position.current > 0) e.currentTarget.currentTime = position.current;
          }}
        />
      </div>
    );
  }
  return (
    <div class="preview stripes-bg">
      <button class="preview-start" aria-label={t("det.preview", { duration: F.duration(props.duration) })} onClick={() => props.setPlaying(true)}>
        <span class="preview-play">
          <Icon d={I.play} size={20} stroke={1.5} fill />
        </span>
        <span class="mono small">{t("det.preview", { duration: F.duration(props.duration) })}</span>
      </button>
    </div>
  );
}

/** HLS where the browser cannot play it itself (Chrome, Firefox): hls.js, loaded only for this. It fetches playlist
 * and segments straight from the broadcaster, which has to allow that (CORS); ORF and SRF do. */
function HlsVideo(props: { url: string; position: { current: number }; onFail: () => void }): JSX.Element {
  const video = useRef<HTMLVideoElement>(null);
  const onFail = useRef(props.onFail);
  onFail.current = props.onFail;
  const { position } = props;
  useEffect(() => {
    let player: { destroy(): void } | null = null;
    let gone = false;
    import("hls.js/light").then(
      ({ default: Hls }) => {
        const element = video.current;
        if (gone || !element) return;
        if (!Hls.isSupported()) return onFail.current();
        // A worker would need a blob: script in the CSP. -1 is hls.js's own start: the beginning, or live.
        const hls = new Hls({ enableWorker: false, startPosition: position.current > 0 ? position.current : -1 });
        player = hls;
        hls.on(Hls.Events.ERROR, (_event, data) => data.fatal && onFail.current());
        hls.loadSource(props.url);
        hls.attachMedia(element);
      },
      () => onFail.current(),
    );
    return () => {
      gone = true;
      player?.destroy();
    };
  }, [props.url, position]);
  return <video ref={video} controls autoPlay playsInline onTimeUpdate={(e) => notePosition(position, e.currentTarget)} />;
}

/** Notes where playback is. A new source resets the element to 0 before it loads (readyState 0): that does not count,
 * so another quality goes on where the last one was. */
function notePosition(position: { current: number }, video: HTMLVideoElement): void {
  if (video.readyState > 0) position.current = video.currentTime;
}

function TitlePop(props: { value: string; onInput: (value: string) => void }): JSX.Element {
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => field.current?.focus(), []);
  return (
    <div role="dialog" aria-label={t("search.titel")} class="pop-form">
      <label class="label" for="search-titel">
        {t("search.titel_label")}
      </label>
      <input
        ref={field}
        id="search-titel"
        class="input"
        type="text"
        value={props.value}
        placeholder={t("search.titel_placeholder")}
        onInput={(e) => props.onInput((e.target as HTMLInputElement).value)}
      />
      <div class="small faint">{t("search.titel_hint")}</div>
    </div>
  );
}

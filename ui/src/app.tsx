// The shell: navigation for desktop (side bar), tablet (rail) and phone
// (tab bar), the status bar with film list, downloads and bandwidth limit, the target folder banner, toasts.
import { Component, type ComponentChildren, type JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { api, type Status } from "./api";
import * as F from "./format";
import { t, type Key } from "./i18n";
import { copyText, LimitFields, LimitSwitch, limitConversion } from "./components/controls";
import { I, Icon, Logo } from "./icons";
import { downloadTexts, filmlistTexts, limitTexts } from "./status-texts";
import { build, go, type Route, useRoute } from "./router";
import {
  closeToast,
  logout,
  refreshStatus,
  setTheme,
  type ThemeName,
  toast,
  useInfo,
  useLayout,
  useAuth,
  useOffline,
  useStatus,
  useTheme,
  useToast,
} from "./store";
import { Downloads } from "./views/downloads";
import { History } from "./views/history";
import { Settings } from "./views/settings";
import { Search } from "./views/search";
import { SubscriptionEditor } from "./views/editor";
import { Subscriptions } from "./views/subscriptions";

type View = "suche" | "abos" | "downloads" | "verlauf" | "einstellungen";

const last: Partial<Record<View, string>> = {};

function viewOf(route: Route): View {
  const known: View[] = ["suche", "abos", "downloads", "verlauf", "einstellungen"];
  return known.includes(route.view as View) ? (route.view as View) : "suche";
}

export function App(): JSX.Element {
  const route = useRoute();
  const view = viewOf(route);
  const { layout } = useLayout();
  const status = useStatus();
  const offline = useOffline();
  const auth = useAuth();
  const [limitOpen, setLimitOpen] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const previousPath = useRef(route.path);

  useEffect(() => {
    if (!location.hash || location.hash === "#" || location.hash === "#/") history.replaceState(null, "", "#/suche");
  }, []);
  useEffect(() => {
    if (view === "suche" || view === "einstellungen") last[view] = location.hash;
    if (previousPath.current !== route.path && scroller.current) scroller.current.scrollTop = 0;
    previousPath.current = route.path;
    setLimitOpen(false);
  }, [route]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLimitOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const editor = view === "abos" && route.params.length > 0;
  const failed = status?.downloads.counts.failed ?? 0;
  const targetBroken = !!status?.writable.downloads;
  const navItems = [
    { view: "suche" as const, label: t("nav.search"), icon: I.search, href: last.suche ?? "#/suche", count: "" },
    { view: "abos" as const, label: t("nav.subscriptions"), icon: I.abos, href: "#/abos", count: status?.subscriptions || "" },
    {
      view: "downloads" as const,
      label: t("nav.downloads"),
      icon: I.download,
      href: "#/downloads",
      count: (status?.downloads.counts.running ?? 0) + (status?.downloads.counts.queued ?? 0) || "",
      alert: failed > 0 || targetBroken,
    },
    { view: "verlauf" as const, label: t("nav.history"), icon: I.history, href: "#/verlauf", count: "" },
    { view: "einstellungen" as const, label: t("nav.settings"), icon: I.settings, href: last.einstellungen ?? "#/einstellungen", count: "" },
  ];
  const navigate = (href: string) => (e: Event) => {
    e.preventDefault();
    go(href);
  };

  let content: JSX.Element;
  if (view === "suche") content = <Search route={route} layout={layout} />;
  else if (editor) content = <SubscriptionEditor route={route} layout={layout} />;
  else if (view === "abos") content = <Subscriptions layout={layout} />;
  else if (view === "downloads") content = <Downloads route={route} layout={layout} />;
  else if (view === "verlauf") content = <History route={route} layout={layout} />;
  else content = <Settings route={route} layout={layout} />;

  return (
    <div class={`app layout-${layout}`}>
      {layout === "mobile" && <MobileHeader status={status} onLimit={() => setLimitOpen((o) => !o)} view={view} />}
      <div class="app-body">
        {layout === "desktop" && (
          <nav class="sidenav" aria-label={t("nav.main")}>
            <a href={last.suche ?? "#/suche"} onClick={navigate(last.suche ?? "#/suche")} class="brand">
              <Logo size={26} />
              <span class="brand-name">
                mtdl<span class="brand-web">web</span>
              </span>
            </a>
            {navItems.map((n) => (
              <a
                key={n.view}
                href={n.href}
                onClick={navigate(n.href)}
                class={`nav-item${n.view === view ? " current" : ""}`}
                aria-current={n.view === view ? "page" : undefined}
              >
                <Icon d={n.icon} size={17} stroke={1.7} />
                <span class="nav-label">{n.label}</span>
                {n.alert && <span class="dot err" title={t("nav.error")} />}
                <span class="nav-count">{n.count}</span>
              </a>
            ))}
            <div class="sidenav-foot">
              <ThemeSwitch />
              <div class="row between">
                <div class="version">mtdl-web {status?.version ?? ""}</div>
                {auth?.via === "session" && <LogoutButton />}
              </div>
            </div>
          </nav>
        )}
        {layout === "rail" && (
          <nav class="rail" aria-label={t("nav.main")}>
            <a href={last.suche ?? "#/suche"} onClick={navigate(last.suche ?? "#/suche")} title="mtdl-web" class="rail-brand">
              <Logo size={28} />
            </a>
            {navItems.map((n) => (
              <a
                key={n.view}
                href={n.href}
                onClick={navigate(n.href)}
                title={n.label}
                aria-label={n.label}
                class={`rail-item${n.view === view ? " current" : ""}`}
                aria-current={n.view === view ? "page" : undefined}
              >
                <Icon d={n.icon} size={18} stroke={1.7} />
                {n.alert && <span class="dot err rail-dot" />}
              </a>
            ))}
            <ThemeCycle class="rail-theme" />
          </nav>
        )}
        <main class="main">
          {auth?.outside && <OutsideBanner reason={auth.outside} />}
          {targetBroken && <TargetBanner reason={status?.writable.downloads ?? ""} />}
          {offline && (
            <div class="banner warn" role="alert">
              <Icon d={I.warn} size={18} class="warn-text" />
              <div class="banner-body">
                <div class="banner-title warn-text">{t("app.offline_title")}</div>
                <div>{t("app.offline_text")}</div>
              </div>
            </div>
          )}
          <div class={`view${view === "suche" || (editor && layout !== "mobile") ? " fixed" : ""}`} ref={scroller}>
            <ViewBoundary key={route.path}>{content}</ViewBoundary>
          </div>
        </main>
      </div>
      {layout === "mobile" && (
        <nav class="tabbar" aria-label={t("nav.main")}>
          {navItems.slice(0, 4).map((n) => (
            <a
              key={n.view}
              href={n.href}
              onClick={navigate(n.href)}
              class={`tab${n.view === view ? " current" : ""}`}
              aria-current={n.view === view ? "page" : undefined}
            >
              <Icon d={n.icon} size={20} stroke={1.7} />
              <span>{n.label}</span>
              {n.alert && <span class="dot err tab-dot" />}
            </a>
          ))}
        </nav>
      )}
      {layout !== "mobile" && <StatusBar status={status} limitOpen={limitOpen} onLimit={() => setLimitOpen((o) => !o)} />}
      {limitOpen && status && <LimitPopover status={status} mobile={layout === "mobile"} onClose={() => setLimitOpen(false)} />}
      <ToastView mobile={layout === "mobile"} />
    </div>
  );
}

function MobileHeader(props: { status: Status | null; onLimit: () => void; view: View }): JSX.Element {
  const { status } = props;
  const limit = limitTexts(status, useInfo()?.timezone);
  const fl = filmlistTexts(status, useInfo()?.timezone);
  return (
    <>
      <header class="mobile-header">
        <a href="#/suche" class="brand" onClick={(e) => (e.preventDefault(), go(last.suche ?? "#/suche"))}>
          <Logo size={22} />
          <span class="brand-name">mtdl</span>
        </a>
        <span class="grow" />
        <button class={`pill${limit.on ? "" : " muted"}`} onClick={props.onLimit} aria-label={t("limit.title")}>
          <Icon d={I.gauge} size={14} />
          <span>{limit.short}</span>
        </button>
        <ThemeCycle class="icon-button round" />
        <a
          href="#/einstellungen"
          onClick={(e) => (e.preventDefault(), go(last.einstellungen ?? "#/einstellungen"))}
          aria-label={t("nav.settings")}
          class={`icon-button round${props.view === "einstellungen" ? " active" : ""}`}
        >
          <Icon d={I.settings} size={18} stroke={1.7} />
        </a>
      </header>
      <button class="mobile-strip" onClick={() => go("/downloads")}>
        <span class={`dot ${fl.dot}`} />
        <span class="ellipsis">{fl.short}</span>
        <span class="grow" />
        {(status?.downloads.running.length ?? 0) > 0 && <span class="dot rec pulse" />}
        <span class="strong">{downloadTexts(status).short}</span>
      </button>
    </>
  );
}

function ThemeSwitch(): JSX.Element {
  const theme = useTheme();
  const options: [ThemeName, Key, string][] = [
    ["light", "theme.light", I.sun],
    ["dark", "theme.dark", I.moon],
    ["system", "theme.system", I.monitor],
  ];
  return (
    <div class="segmented" role="radiogroup" aria-label={t("theme.label")}>
      {options.map(([name, label, icon]) => (
        <button
          key={name}
          role="radio"
          aria-checked={theme === name}
          title={t(label)}
          aria-label={t(label)}
          class={theme === name ? "on" : ""}
          onClick={() => setTheme(name)}
        >
          <Icon d={icon} size={15} stroke={1.7} />
        </button>
      ))}
    </div>
  );
}

function ThemeCycle(props: { class?: string }): JSX.Element {
  const theme = useTheme();
  const order: ThemeName[] = ["light", "dark", "system"];
  const icon = theme === "light" ? I.sun : theme === "dark" ? I.moon : I.monitor;
  const name = t(theme === "light" ? "theme.light" : theme === "dark" ? "theme.dark" : "theme.system");
  const label = t("theme.title", { name });
  return (
    <button
      class={props.class ?? "icon-button"}
      title={label}
      aria-label={label}
      onClick={() => setTheme(order[(order.indexOf(theme) + 1) % 3] ?? "system")}
    >
      <Icon d={icon} size={17} stroke={1.7} />
    </button>
  );
}

function StatusBar(props: { status: Status | null; limitOpen: boolean; onLimit: () => void }): JSX.Element {
  const { status } = props;
  const fl = filmlistTexts(status, useInfo()?.timezone);
  const dl = downloadTexts(status);
  const limit = limitTexts(status, useInfo()?.timezone);
  const failed = status?.downloads.counts.failed ?? 0;
  const targetBroken = !!status?.writable.downloads;
  const unit = status?.limit.speed_unit ?? "Mbit/s";
  const [retrying, setRetrying] = useState(false);
  const retry = async () => {
    setRetrying(true);
    await api.updateFilmlist().catch(() => undefined);
    window.setTimeout(() => {
      setRetrying(false);
      void refreshStatus();
    }, 1500);
  };
  return (
    <footer class="statusbar">
      <button class="sb-item" title={t("fl.source")} onClick={() => go("/einstellungen/datenquelle")}>
        <span class={`dot ${fl.dot}`} />
        <span class="ellipsis">{fl.text}</span>
      </button>
      {fl.stale && (
        <button class="sb-retry" onClick={retry} disabled={retrying}>
          {retrying ? t("fl.retrying") : t("fl.retry")}
        </button>
      )}
      <span class="sb-sep" />
      <button class="sb-item" onClick={() => go("/downloads")}>
        {dl.active && !targetBroken && <span class="dot rec pulse" />}
        {targetBroken && (status?.downloads.counts.queued ?? 0) > 0 && <Icon d={I.pause} size={13} stroke={2.2} class="err-text" />}
        <span>{targetBroken ? t("dl.paused", { n: (status?.downloads.counts.queued ?? 0) + (status?.downloads.counts.running ?? 0) }) : dl.text}</span>
        {dl.active && status?.downloads.progress != null && (
          <>
            <span class="bar">
              <span style={{ width: `${(status.downloads.progress * 100).toFixed(1)}%` }} />
            </span>
            <span>{F.percent(status.downloads.progress)}</span>
          </>
        )}
        {dl.active && status && <span class="strong">{F.speed(status.downloads.speed, unit)}</span>}
      </button>
      <span class="grow" />
      {(failed > 0 || targetBroken) && (
        <button class="sb-item err-chip" onClick={() => go(targetBroken ? "/einstellungen/ziel" : build("/downloads", { bereich: "fehler" }))}>
          <Icon d={I.warn} size={13} stroke={2} />
          {targetBroken ? t("target.chip") : t("dl.failed", { n: failed })}
        </button>
      )}
      <span class="sb-sep" />
      <div class="sb-limit">
        <button
          class={`sb-limit-button${limit.on ? "" : " muted"}`}
          onClick={props.onLimit}
          aria-haspopup="dialog"
          aria-expanded={props.limitOpen}
          title={limit.title}
        >
          <Icon d={I.gauge} size={14} />
          <span>{limit.text}</span>
        </button>
        {status && <LimitSwitch limit={status.limit} small />}
      </div>
    </footer>
  );
}

function LimitPopover(props: { status: Status; mobile: boolean; onClose: () => void }): JSX.Element {
  const { limit } = props.status;
  const schedule = limitTexts(props.status, useInfo()?.timezone);
  return (
    <>
      <div class="backdrop" onClick={props.onClose} />
      <div class={`popover limit-popover${props.mobile ? " mobile" : ""}`} role="dialog" aria-label={t("limit.title")}>
        <div class="row between">
          <strong>{t("limit.title")}</strong>
          <LimitSwitch limit={limit} />
        </div>
        <LimitFields limit={limit} presets />
        <div class="small muted-text">{limitConversion(limit)}</div>
        {limit.source !== "normal" && <div class="small hook-note">{schedule.text} · {schedule.title}</div>}
        <div class="small faint">{t("limit.hint")}</div>
        <button class="link-button small" onClick={() => (props.onClose(), go("/einstellungen/downloads"))}>
          {t("limit.schedule_edit")}
        </button>
      </div>
    </>
  );
}

function LogoutButton(): JSX.Element {
  return (
    <button class="link-button small faint" onClick={() => void logout()}>
      {t("auth.logout")}
    </button>
  );
}

/** Mode none, and a request came from outside the own network: anyone with the address may use mtdl-web. */
function OutsideBanner(props: { reason: string }): JSX.Element {
  return (
    <div class="banner err" role="alert">
      <Icon d={I.warn} size={18} class="err-icon" />
      <div class="banner-body">
        <div class="banner-title">{t("auth.outside_title")}</div>
        <div>{t("auth.outside_text", { reason: t(`auth.reason.${props.reason}` as Key) })}</div>
        <div>
          <button class="button" onClick={() => go("/einstellungen/zugang")}>
            {t("auth.outside_settings")}
          </button>
        </div>
      </div>
    </div>
  );
}

function TargetBanner(props: { reason: string }): JSX.Element {
  const info = useInfo();
  const command = `docker exec -u 0 mtdl-web chown -R ${info?.uid ?? 99}:${info?.gid ?? 100} ${info?.download_dir ?? "/downloads"}`;
  const recheck = async () => {
    const result = await api.checkDirectories();
    await refreshStatus();
    toast(result.downloads ? t("target.still") : t("target.ok"));
  };
  const copy = () => copyText(command);
  return (
    <div class="banner err" role="alert">
      <Icon d={I.warn} size={18} class="err-icon" />
      <div class="banner-body">
        <div class="banner-title">{t("target.title")}</div>
        <div>{t("target.text", { path: info?.download_dir ?? "/downloads", reason: props.reason, uid: info?.uid ?? "?", gid: info?.gid ?? "?" })}</div>
        <div class="row wrap">
          <code class="command">{command}</code>
          <button class="button" onClick={copy}>
            <Icon d={I.copy} size={14} />
            {t("target.copy")}
          </button>
          <button class="button primary" onClick={recheck}>
            {t("target.recheck")}
          </button>
        </div>
      </div>
    </div>
  );
}

function ToastView(props: { mobile: boolean }): JSX.Element | null {
  const current = useToast();
  if (!current) return null;
  return (
    <div class={`toast${props.mobile ? " mobile" : ""}`} role="status">
      <span>{current.text}</span>
      {current.action && (
        <button
          class="toast-action"
          onClick={() => {
            closeToast();
            current.action?.run();
          }}
        >
          {current.action.label}
        </button>
      )}
      <button class="toast-close" onClick={closeToast} aria-label={t("toast.close")}>
        <Icon d={I.close} size={14} stroke={2} />
      </button>
    </div>
  );
}

/** A view that fails to render shows what happened instead of an empty page; the next navigation starts fresh. */
class ViewBoundary extends Component<{ children: ComponentChildren }, { error: string | null }> {
  override state = { error: null as string | null };

  static override getDerivedStateFromError(error: unknown): { error: string } {
    return { error: error instanceof Error ? `${error.name}: ${error.message}` : String(error) };
  }

  override componentDidCatch(error: unknown): void {
    console.error("view failed", error);
  }

  override render(): ComponentChildren {
    if (!this.state.error) return this.props.children;
    return (
      <div class="empty">
        <h2>{t("app.view_failed")}</h2>
        <p>{t("app.view_failed_text")}</p>
        <code class="tech">{this.state.error}</code>
        <div class="row wrap">
          <button class="button primary" onClick={() => location.reload()}>
            {t("app.reload")}
          </button>
          <button class="button" onClick={() => copyText(`mtdl-web ${location.hash}\n${this.state.error ?? ""}`)}>
            {t("app.copy_error")}
          </button>
        </div>
      </div>
    );
  }
}

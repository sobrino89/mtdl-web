// Settings: sections in the hash (#/einstellungen/<section>); every change is saved
// at once. What the backend does not offer yet is marked "kommt später" instead of pretending.
import type { ComponentChildren, JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

import {
  api,
  ApiError,
  type ApiKey,
  type AuthStatus,
  errorMessage,
  type Film,
  type Hooks,
  type Integrations,
  type KeyScope,
  type Library,
  type LibraryDraft,
  type LibraryKind,
  type Naming,
  type Quality,
  type Scheme,
  type SecretState,
  type Settings as AppSettings,
  type SystemInfo,
  type TakeoverReport,
  type Webhook,
  type WebhookDraft,
  type WebhookEvent,
} from "../api";
import { copyText, LimitFields, LimitSwitch, limitConversion, Switch } from "../components/controls";
import { TargetResult } from "../components/target";
import * as F from "../format";
import { EXAMPLE_SCRIPT, hookStatus } from "../hook-texts";
import { chooseLanguage, language, languageChoice, type LanguageChoice, t, type Key } from "../i18n";
import { I, Icon } from "../icons";
import { errorKey, folder, presetOf, presetsOf, TOKENS as LIBRARY_TOKENS } from "../library-logic";
import { claudeCodeCommand, claudeDesktopConfig } from "../mcp-config";
import { DEFAULT_TEMPLATE, DEFAULT_TEMPLATE_EN, EXAMPLES, TOKENS, TOKENS_EN, unknownTokens } from "../naming";
import { summary } from "../rule-texts";
import { go, type Route } from "../router";
import { limitTexts, scheduleSegments, scheduleSummary } from "../status-texts";
import { accessError } from "../access-texts";
import {
  type Layout,
  logout,
  refreshInfo,
  refreshLibraries,
  refreshStatus,
  saveSettings,
  setAuth,
  toast,
  useAuth,
  useInfo,
  useLibraries,
  useSettings,
  useStatus,
  useSystem,
} from "../store";
import {
  FIELDS,
  type HeaderRow,
  headerPayload,
  headerProblem,
  headerRow,
  insertAt,
  keepsValue,
  type Preset,
  PRESETS,
  type Problem,
  presetKey,
  problemOf,
  storedRows,
  withPresetHeaders,
} from "../webhook-logic";

const SECTIONS = ["ziel", "qualitaet", "downloads", "datenquelle", "benachrichtigungen", "mediaserver", "zugang", "system"] as const;
type Section = (typeof SECTIONS)[number];


export function Settings(props: { route: Route; layout: Layout }): JSX.Element {
  const mobile = props.layout === "mobile";
  const requested = props.route.params[0] as Section | undefined;
  const section: Section | null = requested && SECTIONS.includes(requested) ? requested : mobile ? null : "ziel";
  const status = useStatus();
  const settings = useSettings();
  const targetBroken = !!status?.writable.downloads;
  const libraryBroken = Object.entries(status?.writable ?? {}).some(([name, problem]) => name.startsWith("library ") && !!problem);
  const stale = !!status?.filmlist.last_error;

  const nav = (
    <nav aria-label={t("set.title")} class={`settings-nav${mobile ? " mobile" : ""}`}>
      <h1 class="page-title">{t("set.title")}</h1>
      {SECTIONS.map((k) => {
        const on = k === section && !mobile;
        const warn = (k === "ziel" && targetBroken) || (k === "mediaserver" && libraryBroken) || (k === "datenquelle" && stale);
        return (
          <button key={k} class={`settings-link${on ? " current" : ""}`} aria-current={on ? "page" : undefined} onClick={() => go(`/einstellungen/${k}`)}>
            <span class="grow">{t(`set.sec.${k}` as Key)}</span>
            {warn && <span class={`dot ${k === "datenquelle" ? "warn" : "err"}`} />}
            {mobile && <Icon d="M9 6l6 6-6 6" size={14} stroke={2} class="faint" />}
          </button>
        );
      })}
    </nav>
  );

  if (mobile && !section) return <div class="settings mobile">{nav}</div>;

  return (
    <div class={`settings${mobile ? " mobile" : ""}`}>
      {!mobile && nav}
      <div class="settings-content">
        <div class="settings-inner">
          {mobile && (
            <button class="button ghost small back" onClick={() => go("/einstellungen")}>
              <Icon d="M15 6l-6 6 6 6" size={15} stroke={1.9} />
              {t("set.title")}
            </button>
          )}
          <h2 class="section-title">{t(`set.sec.${section ?? "ziel"}` as Key)}</h2>
          <p class="section-lead">{t(`set.lead.${section ?? "ziel"}` as Key)}</p>
          {!settings ? (
            <div class="skel block" style={{ height: "120px" }} />
          ) : section === "ziel" ? (
            <TargetSection settings={settings} />
          ) : section === "qualitaet" ? (
            <QualitySection settings={settings} />
          ) : section === "downloads" ? (
            <DownloadsSection settings={settings} />
          ) : section === "datenquelle" ? (
            <SourceSection settings={settings} />
          ) : section === "benachrichtigungen" ? (
            <NotificationsSection />
          ) : section === "mediaserver" ? (
            <LibrarySection settings={settings} editing={props.route.params[1]} />
          ) : section === "zugang" ? (
            <AccessSection settings={settings} />
          ) : (
            <SystemSection />
          )}
        </div>
      </div>
    </div>
  );
}

function save(changes: Partial<AppSettings>): Promise<boolean> {
  return saveSettings(changes).then(
    () => true,
    (e: unknown) => {
      toast(`${t("settings.save_failed")}: ${errorMessage(e)}`);
      return false;
    },
  );
}

function Row(props: { label: string; sub?: string; children: ComponentChildren; badge?: string }): JSX.Element {
  return (
    <div class="form-row">
      <div>
        <div class="form-label">
          {props.label}
          {props.badge && <span class="soon">{props.badge}</span>}
        </div>
        {props.sub && <div class="small faint">{props.sub}</div>}
      </div>
      <div class="form-control">{props.children}</div>
    </div>
  );
}

function Later(props: { text: string }): JSX.Element {
  return (
    <div class="later">
      <span class="soon">{t("set.later")}</span>
      <p>{props.text}</p>
    </div>
  );
}

// -- target and naming ---------------------------------------------------------------------------------------------

function TargetSection(props: { settings: AppSettings }): JSX.Element {
  const { settings } = props;
  const info = useInfo();
  const [system, reload] = useSystem();
  const [checking, setChecking] = useState(false);
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const problem = system?.writable.downloads ?? null;
  const dir = info?.download_dir ?? "/downloads";

  const check = async () => {
    setChecking(true);
    try {
      await api.checkDirectories();
      await refreshStatus();
      await reload();
      setCheckedAt(F.time(new Date().toISOString(), info?.timezone));
    } catch (e) {
      toast(errorMessage(e));
    }
    setChecking(false);
  };

  // naming: presets save at once; an own template once it is complete and known
  const scheme = settings.naming_scheme;
  const [templateMode, setTemplateMode] = useState(scheme === "template");
  const english = language() === "en";
  const [draft, setDraft] = useState(settings.naming_template ?? (english ? DEFAULT_TEMPLATE_EN : DEFAULT_TEMPLATE));
  const unknown = unknownTokens(draft);
  const naming: Naming = templateMode ? { scheme: "template", template: draft } : { scheme };
  const [paths, setPaths] = useState<string[] | null>(null);
  const seq = useRef(0);
  const namingKey = JSON.stringify([naming, settings.normalize_filenames]);
  useEffect(() => {
    if (templateMode && (unknown.length || !draft.trim())) {
      setPaths(null);
      return;
    }
    const mine = ++seq.current;
    const timer = window.setTimeout(() => {
      void api.namingPreview({ naming, films: EXAMPLES.map((e) => e.film) }).then(
        (r) => mine === seq.current && setPaths(r.paths),
        () => mine === seq.current && setPaths(null),
      );
    }, 250);
    return () => window.clearTimeout(timer);
  }, [namingKey]);
  useEffect(() => {
    if (!templateMode || unknown.length || !draft.trim() || (scheme === "template" && draft === settings.naming_template)) return;
    const timer = window.setTimeout(() => void save({ naming_scheme: "template", naming_template: draft }), 600);
    return () => window.clearTimeout(timer);
  }, [draft, templateMode]);

  const pick = (k: Scheme) => {
    if (k === "template") {
      setTemplateMode(true);
      return;
    }
    setTemplateMode(false);
    void save({ naming_scheme: k });
  };
  const insert = (token: string) => setDraft((d) => d + `{${token}}`);
  const current: Scheme = templateMode ? "template" : scheme;

  return (
    <>
      <Row label={t("set.target")} sub={t("set.target_sub")}>
        <div class="row">
          <input class="input mono grow" value={dir} readOnly aria-label={t("set.target")} />
          <button class="button" disabled={checking} onClick={() => void check()}>
            {checking ? t("set.checking") : t("set.check")}
          </button>
        </div>
        <TargetResult problem={problem} system={system} info={info} checkedAt={checkedAt} onCheck={() => void check()} />
        <span class="small faint">{t("set.target_mount", { path: dir })}</span>
      </Row>
      <Row label={t("set.naming")} sub={t("set.naming_sub")}>
        <div role="radiogroup" class="segmented fit">
          {(["season_episode", "date", "template"] as const).map((k) => (
            <button key={k} role="radio" aria-checked={current === k} class={current === k ? "on" : ""} onClick={() => pick(k)}>
              {t(`set.preset.${k}` as Key)}
            </button>
          ))}
        </div>
        <span class="small muted-text">{t(`set.preset_desc.${current}` as Key)}</span>
        {templateMode && (
          <>
            <input
              class={`input mono${unknown.length || !draft.trim() ? " invalid" : ""}`}
              value={draft}
              spellcheck={false}
              aria-label={t("set.preset.template")}
              onInput={(e) => setDraft((e.target as HTMLInputElement).value)}
            />
            <div class="row wrap">
              <span class="small faint">{t("set.insert")}</span>
              {TOKENS.map((token, i) => {
                const shown = english ? TOKENS_EN[i] : token;
                return (
                  <button key={token} class="token" title={t(`set.token.${token}` as Key)} onClick={() => insert(shown ?? token)}>
                    {`{${shown}}`}
                  </button>
                );
              })}
            </div>
          </>
        )}
        <div class="example">
          <span class="stat-label">{t("set.example")}</span>
          {EXAMPLES.map((e, i) => (
            <div key={e.label} class="example-item">
              <span class="small muted-text">{t(e.label)}</span>
              <code class="break">
                <span class="faint">{dir.replace(/\/+$/, "")}/</span>
                {paths?.[i] ?? "…"}
              </code>
            </div>
          ))}
          {templateMode && unknown.length > 0 && <span class="small err-text">{t("set.unknown_tokens", { list: unknown.map((u) => `{${u}}`).join(", ") })}</span>}
          {templateMode && !draft.trim() && <span class="small err-text">{t("set.template_missing")}</span>}
        </div>
        <label class="check-label plain top">
          <input type="checkbox" checked={settings.normalize_filenames} onChange={() => void save({ normalize_filenames: !settings.normalize_filenames })} />
          <span>
            <strong>{t("set.normalize")}</strong>
            <span class="small muted-text block-text">{t("set.normalize_desc")}</span>
          </span>
        </label>
      </Row>
    </>
  );
}

// -- libraries, TMDB and Plex --------------------------------------------------------------------------------

/** An API error of the library features as text: unknown placeholders with their names, conflicts with details. */
function libraryError(e: unknown): string {
  const detail = e instanceof ApiError && e.detail && typeof e.detail === "object" ? (e.detail as Record<string, unknown>) : {};
  const code = e instanceof ApiError ? e.code : undefined;
  if (code === "unknown_placeholders") {
    const list = (detail.placeholders as string[] | undefined) ?? [];
    return t("set.unknown_tokens", { list: list.map((p) => `{${p}}`).join(", ") });
  }
  if (code === "library_not_writable") return t("lib.err.library_not_writable", { message: String(detail.message ?? "") });
  if (code === "library_path_overlap") return t("lib.err.library_path_overlap", { library: String(detail.library ?? "") });
  if (code === "library_in_use") {
    const subs = Array.isArray(detail.subscriptions) ? detail.subscriptions.length : 0;
    return t("lib.err.library_in_use", { subs, downloads: Number(detail.downloads ?? 0) });
  }
  const key = errorKey(code);
  return key ? t(key as Key) : errorMessage(e);
}

function SecretField(props: {
  state: SecretState | undefined;
  env: string;
  onSave: (value: string | null) => Promise<void>;
}): JSX.Element {
  const { state } = props;
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const store = async (next: string | null) => {
    setBusy(true);
    await props.onSave(next);
    setValue("");
    setBusy(false);
  };
  return (
    <>
      <span class={`small row ${state?.configured ? "ok-text" : "faint"}`}>
        {state?.configured && <Icon d={I.check} size={13} stroke={2.2} />}
        {state?.source === "env" ? t("set.secret_env", { name: props.env }) : state?.source === "settings" ? t("set.secret_saved") : t("set.secret_none")}
      </span>
      {state?.source !== "env" && (
        <div class="row">
          <input
            type="password"
            class="input mono grow"
            value={value}
            autoComplete="off"
            spellcheck={false}
            placeholder={t("set.secret_placeholder")}
            aria-label={t("set.secret_placeholder")}
            onInput={(e) => setValue((e.target as HTMLInputElement).value)}
          />
          <button class="button" disabled={busy || !value.trim()} onClick={() => void store(value.trim())}>
            {t("set.secret_save")}
          </button>
          {state?.source === "settings" && (
            <button class="button ghost" disabled={busy} onClick={() => void store(null)}>
              {t("set.secret_remove")}
            </button>
          )}
        </div>
      )}
    </>
  );
}

/** The library being edited, from the hash: "#/einstellungen/mediaserver/<id>" or ".../neu". */
function editedLibrary(param: string | undefined): number | "neu" | null {
  if (param === "neu") return "neu";
  return param && /^\d+$/.test(param) ? Number(param) : null;
}

function LibrarySection(props: { settings: AppSettings; editing: string | undefined }): JSX.Element {
  const { settings } = props;
  const libraries = useLibraries();
  const editing = editedLibrary(props.editing);
  const close = () => go("/einstellungen/mediaserver");
  const [state, setState] = useState<Integrations | null>(null);
  useEffect(() => {
    api.integrations().then(setState, () => undefined);
  }, []);
  const saveSecret = async (name: "tmdb_token" | "plex_token", value: string | null) => {
    try {
      setState(await api.saveSecrets({ [name]: value }));
      await refreshInfo();
    } catch (e) {
      toast(libraryError(e));
    }
  };

  const [tmdbResult, setTmdbResult] = useState<{ ok: boolean; text: string } | null>(null);
  const testTmdb = async () => {
    setTmdbResult(null);
    try {
      await api.testTmdb();
      setTmdbResult({ ok: true, text: t("set.tmdb_ok") });
    } catch (e) {
      setTmdbResult({ ok: false, text: libraryError(e) });
    }
  };

  const [plexUrl, setPlexUrl] = useState(settings.plex_url ?? "");
  const [plexResult, setPlexResult] = useState<{ ok: boolean; text: string } | null>(null);
  const testPlex = async () => {
    setPlexResult(null);
    try {
      const r = await api.testPlex();
      const sections = r.sections.filter((s) => s.type === "show" || s.type === "movie").map((s) => `${s.title} (${s.paths.join(", ")})`);
      setPlexResult({ ok: true, text: t("set.plex_ok", { version: r.version, sections: sections.join(" · ") || "–" }) });
    } catch (e) {
      setPlexResult({ ok: false, text: libraryError(e) });
    }
  };

  return (
    <>
      <Row label={t("set.libraries")} sub={t("set.libraries_sub")}>
        {!libraries ? (
          <span class="skel" style={{ width: "260px" }} />
        ) : (
          <>
            {!libraries.length && editing !== "neu" && <span class="muted-text">{t("set.libraries_none")}</span>}
            {libraries.map((library) =>
              library.id === editing ? (
                <LibraryForm key={library.id} library={library} libraries={libraries} close={close} />
              ) : (
                <LibraryCard key={library.id} library={library} />
              ),
            )}
            {editing === "neu" ? (
              <LibraryForm key="neu" library={null} libraries={libraries} close={close} />
            ) : (
              <div>
                <button class="button small dashed-button" onClick={() => go("/einstellungen/mediaserver/neu")}>
                  {t("set.library_add")}
                </button>
              </div>
            )}
            <span class="small faint">{t("set.libraries_mount")}</span>
          </>
        )}
      </Row>
      <Row label={t("set.tmdb")} sub={t("set.tmdb_sub")}>
        <SecretField state={state?.tmdb} env="MTDL_TMDB_TOKEN" onSave={(v) => saveSecret("tmdb_token", v)} />
        <div class="row wrap">
          <button class="button" disabled={!state?.tmdb.configured} onClick={() => void testTmdb()}>
            {t("set.test_connection")}
          </button>
          {tmdbResult && <span class={`small ${tmdbResult.ok ? "ok-text" : "err-text"}`}>{tmdbResult.text}</span>}
        </div>
        <div class="attribution">
          <img src="/api/v1/attribution/tmdb" alt="TMDB" height={12} />
          <span class="small faint">{t("set.tmdb_notice")}</span>
        </div>
      </Row>
      <Row label={t("set.plex")} sub={t("set.plex_sub")}>
        <label class="field-label small" for="plex-url">
          {t("set.plex_url")}
        </label>
        <input
          id="plex-url"
          class="input mono"
          value={plexUrl}
          placeholder="http://plex:32400"
          spellcheck={false}
          onInput={(e) => setPlexUrl((e.target as HTMLInputElement).value)}
          onBlur={() => plexUrl.trim() !== (settings.plex_url ?? "") && void save({ plex_url: plexUrl.trim() || null })}
        />
        <span class="field-label small">{t("set.plex_token")}</span>
        <SecretField state={state?.plex_token} env="MTDL_PLEX_TOKEN" onSave={(v) => saveSecret("plex_token", v)} />
        <span class="small faint">{t("set.plex_token_sub")}</span>
        <div class="row wrap">
          <button class="button" disabled={!settings.plex_url} onClick={() => void testPlex()}>
            {t("set.test_connection")}
          </button>
          {plexResult && <span class={`small ${plexResult.ok ? "ok-text" : "err-text"}`}>{plexResult.text}</span>}
        </div>
      </Row>
    </>
  );
}

function LibraryCard(props: { library: Library }): JSX.Element {
  const { library } = props;
  const preset = presetOf(library.template, library.kind);
  const details = [
    preset ? t(`set.library_preset.${preset}` as Key) : t("set.library_own_template"),
    library.subscriptions.length ? t("set.library_subs", { n: library.subscriptions.length }) : null,
    library.plex_refresh ? t("set.library_rescan_on") : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <div class="hook">
      <div class="row wrap baseline">
        <strong>{library.name}</strong>
        <span class="tag muted">{t(`set.kind.${library.kind}` as Key)}</span>
        <span class="grow" />
        <button class="button small" onClick={() => go(`/einstellungen/mediaserver/${library.id}`)}>
          {t("set.library_edit")}
        </button>
      </div>
      <code class="small break">{library.path}</code>
      {library.problem ? (
        <span role="alert" class="small err-text">
          {t("set.library_bad", { reason: library.problem })}
        </span>
      ) : (
        <span class="small row ok-text">
          <Icon d={I.check} size={13} stroke={2.2} />
          {t("set.library_ok")}
        </span>
      )}
      <span class="small faint">{details}</span>
    </div>
  );
}

/** A new library: the kind still missing, named after it, with the kind's first preset. */
function newLibrary(libraries: Library[]): LibraryDraft {
  const kind: LibraryKind = libraries.some((l) => l.kind === "series") && !libraries.some((l) => l.kind === "movies") ? "movies" : "series";
  return { name: t(`set.kind.${kind}`), kind, path: "", template: presetsOf(kind)[0]?.template ?? "", normalize: false, plex_path: null, plex_refresh: false };
}

function LibraryForm(props: { library: Library | null; libraries: Library[]; close: () => void }): JSX.Element {
  const { library } = props;
  const english = language() === "en";
  const [draft, setDraft] = useState<LibraryDraft>(() =>
    library
      ? {
          name: library.name,
          kind: library.kind,
          path: library.path,
          template: library.template,
          normalize: library.normalize,
          plex_path: library.plex_path,
          plex_refresh: library.plex_refresh,
        }
      : newLibrary(props.libraries),
  );
  const [paths, setPaths] = useState<string[] | null>(null);
  const [templateError, setTemplateError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const seq = useRef(0);
  const change = (patch: Partial<LibraryDraft>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setError(null);
  };

  // The example: checked and shown by the server (one rule for preview and download).
  useEffect(() => {
    const mine = ++seq.current;
    const timer = window.setTimeout(() => {
      api.libraryPreview({ kind: draft.kind, template: draft.template, normalize: draft.normalize }).then(
        (r) => {
          if (mine !== seq.current) return;
          setPaths(r.paths);
          setTemplateError(null);
        },
        (e: unknown) => {
          if (mine !== seq.current) return;
          setPaths(null);
          setTemplateError(libraryError(e));
        },
      );
    }, 300);
    return () => window.clearTimeout(timer);
  }, [draft.kind, draft.template, draft.normalize]);

  const used = library?.subscriptions.length ?? 0;
  const chooseKind = (kind: LibraryKind) => {
    if (kind === draft.kind) return;
    // A preset of the other kind (or nothing) becomes this kind's first preset; an own template stays as it is.
    const template = !draft.template.trim() || presetOf(draft.template, draft.kind) ? (presetsOf(kind)[0]?.template ?? "") : draft.template;
    const name = !draft.name.trim() || draft.name === t(`set.kind.${draft.kind}`) ? t(`set.kind.${kind}`) : draft.name;
    change({ kind, template, name });
  };
  const ready = !!draft.name.trim() && draft.path.trim().startsWith("/") && !!draft.template.trim() && !templateError;

  const submit = async () => {
    setBusy(true);
    const body: LibraryDraft = { ...draft, name: draft.name.trim(), path: draft.path.trim(), plex_path: draft.plex_path?.trim() || null };
    try {
      const saved = library ? await api.saveLibrary(library.id, body) : await api.createLibrary(body);
      await refreshLibraries();
      await refreshStatus();
      toast(t("set.library_saved", { name: saved.name }));
      props.close();
    } catch (e) {
      setError(libraryError(e));
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!library) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      window.setTimeout(() => setConfirmDelete(false), 4000);
      return;
    }
    setBusy(true);
    try {
      await api.deleteLibrary(library.id);
      await refreshLibraries();
      await refreshStatus();
      toast(t("set.library_deleted", { name: library.name }));
      props.close();
    } catch (e) {
      setError(libraryError(e));
    } finally {
      setBusy(false);
    }
  };

  const preset = presetOf(draft.template, draft.kind);
  const tokens = LIBRARY_TOKENS[draft.kind][english ? "en" : "de"];
  const examples = draft.kind === "movies" ? [t("set.library_ex_movie")] : [t("set.library_ex_episode"), t("set.library_ex_special")];
  const root = folder(draft.path.trim() || (draft.kind === "movies" ? "/library/movies" : "/library/shows"));
  return (
    <div class="hook">
      <label class="field-label small" for="library-name">
        {t("set.library_name")}
      </label>
      <input id="library-name" class="input" value={draft.name} onInput={(e) => change({ name: (e.target as HTMLInputElement).value })} />
      <span class="field-label small">{t("set.library_kind")}</span>
      <div role="radiogroup" aria-label={t("set.library_kind")} class="segmented fit">
        {(["series", "movies"] as const).map((kind) => (
          <button
            key={kind}
            role="radio"
            aria-checked={draft.kind === kind}
            class={draft.kind === kind ? "on" : ""}
            disabled={used > 0 && kind !== draft.kind}
            onClick={() => chooseKind(kind)}
          >
            {t(`set.kind.${kind}` as Key)}
          </button>
        ))}
      </div>
      <span class="small faint">{used > 0 ? t("set.library_kind_used", { n: used }) : t(`set.library_kind_desc.${draft.kind}` as Key)}</span>
      <label class="field-label small" for="library-path">
        {t("set.library_path")}
      </label>
      <input
        id="library-path"
        class={`input mono${draft.path && !draft.path.trim().startsWith("/") ? " invalid" : ""}`}
        value={draft.path}
        placeholder={draft.kind === "movies" ? "/library/movies" : "/library/shows"}
        spellcheck={false}
        onInput={(e) => change({ path: (e.target as HTMLInputElement).value })}
      />
      <span class="small faint">{library ? t("set.library_path_move") : t("set.library_path_sub")}</span>
      <span class="field-label small">{t("set.library_naming")}</span>
      <div role="radiogroup" aria-label={t("set.library_naming")} class="segmented fit wrap">
        {presetsOf(draft.kind).map((p) => (
          <button key={p.key} role="radio" aria-checked={preset === p.key} class={preset === p.key ? "on" : ""} onClick={() => change({ template: p.template })}>
            {t(`set.library_preset.${p.key}` as Key)}
          </button>
        ))}
      </div>
      <input
        class={`input mono${templateError ? " invalid" : ""}`}
        value={draft.template}
        spellcheck={false}
        aria-label={t("set.library_naming")}
        onInput={(e) => change({ template: (e.target as HTMLInputElement).value })}
      />
      <div class="row wrap">
        <span class="small faint">{t("set.insert")}</span>
        {tokens.map((token) => (
          <button key={token} class="token" onClick={() => change({ template: `${draft.template}{${token}}` })}>
            {`{${token}}`}
          </button>
        ))}
      </div>
      <div class="example">
        <span class="stat-label">{t("set.example")}</span>
        {examples.map((label, i) => (
          <div key={label} class="example-item">
            <span class="small muted-text">{label}</span>
            <code class="break">
              <span class="faint">{root}/</span>
              {paths?.[i] ?? "…"}
            </code>
          </div>
        ))}
        {templateError && <span class="small err-text">{templateError}</span>}
      </div>
      <span class="small faint">{t("set.library_naming_sub")}</span>
      <label class="check-label plain top">
        <input type="checkbox" checked={draft.normalize} onChange={() => change({ normalize: !draft.normalize })} />
        <span>
          <strong>{t("set.library_normalize")}</strong>
          <span class="small muted-text block-text">{t("set.library_normalize_desc")}</span>
        </span>
      </label>
      <label class="field-label small" for="library-plex-path">
        {t("set.library_plex_path")}
      </label>
      <input
        id="library-plex-path"
        class="input mono"
        value={draft.plex_path ?? ""}
        placeholder={draft.path.trim() || root}
        spellcheck={false}
        onInput={(e) => change({ plex_path: (e.target as HTMLInputElement).value || null })}
      />
      <span class="small faint">{t("set.library_plex_path_sub")}</span>
      <label class="row">
        <Switch on={draft.plex_refresh} label={t("set.library_rescan")} onToggle={() => change({ plex_refresh: !draft.plex_refresh })} />
        <span>{t("set.library_rescan")}</span>
      </label>
      <span class="small faint">{t("set.library_rescan_sub")}</span>
      {error && (
        <span role="alert" class="small err-text">
          {error}
        </span>
      )}
      <div class="row wrap">
        <button class="button primary small" disabled={!ready || busy} onClick={() => void submit()}>
          {t("set.library_save")}
        </button>
        <button class="button cancel small" disabled={busy} onClick={props.close}>
          {t("set.library_cancel")}
        </button>
        <span class="grow" />
        {library && (
          <button class="button ghost small danger" disabled={busy} onClick={() => void remove()}>
            {confirmDelete ? t("set.library_delete_confirm") : t("set.library_delete")}
          </button>
        )}
      </div>
    </div>
  );
}

// -- access ------------------------------------------------------------------------------------------------

function AccessSection(props: { settings: AppSettings }): JSX.Element {
  const auth = useAuth();
  const info = useInfo();
  if (!auth) return <span class="skel" style={{ width: "260px" }} />;
  const passwordMode = auth.mode === "password" || auth.mode === "local";
  return (
    <>
      <Row label={t("set.auth_mode")} sub={t("set.auth_mode_sub")}>
        <strong>{t(`set.auth_mode.${auth.mode}` as Key)}</strong>
        <span class="small muted-text">{t(`set.auth_mode_desc.${auth.mode}` as Key, { user: auth.user ?? "–" })}</span>
        {(auth.mode === "local" || auth.mode === "none") && (
          <span role="note" class="small warn-text row">
            <Icon d={I.warn} size={13} stroke={2} />
            {t(`set.auth_mode_warn.${auth.mode}` as Key)}
          </span>
        )}
      </Row>
      {passwordMode && <PasswordRow auth={auth} />}
      {auth.via === "session" && (
        <Row label={t("set.sessions")} sub={t("set.sessions_sub")}>
          <div class="row wrap">
            <button class="button" onClick={() => void logout()}>
              {t("auth.logout")}
            </button>
            <button
              class="button ghost"
              onClick={() =>
                void api.endOtherSessions().then(
                  (r) => toast(t("set.ended_others", { n: r.ended })),
                  (e: unknown) => toast(accessError(e)),
                )
              }
            >
              {t("set.end_others")}
            </button>
          </div>
        </Row>
      )}
      <ApiKeysRow timeZone={info?.timezone} />
      <McpRow settings={props.settings} />
    </>
  );
}

// MCP server (mcp_server.py): the switch, the address and how a client gets there.
function McpRow(props: { settings: AppSettings }): JSX.Element {
  const on = props.settings.mcp_enabled;
  const url = `${location.origin}/mcp`;
  const key = t("set.mcp_key_placeholder");
  const code = claudeCodeCommand(url, key);
  const desktop = claudeDesktopConfig(url, key);
  return (
    <Row label={t("set.mcp")} sub={t("set.mcp_sub")}>
      <Switch on={on} label={t("set.mcp_on")} onToggle={() => void save({ mcp_enabled: !on })} />
      {on && (
        <>
          <span class="small">
            {t("set.mcp_url")} <code>{url}</code>
          </span>
          <span class="small faint">{t("set.mcp_example")}</span>
          <div class="row wrap">
            <code class="command grow">{code}</code>
            <button class="button small" onClick={() => copyText(code)}>
              {t("set.api_key_copy")}
            </button>
          </div>
          <span class="small faint">{t("set.mcp_desktop")}</span>
          <pre class="hook-output full">{desktop}</pre>
          <div>
            <button class="button small" onClick={() => copyText(desktop)}>
              {t("set.api_key_copy")}
            </button>
          </div>
          <span role="note" class="small warn-text">
            {t("set.mcp_connector")}
          </span>
        </>
      )}
    </Row>
  );
}

function PasswordRow(props: { auth: AuthStatus }): JSX.Element {
  const { auth } = props;
  const [code, setCode] = useState("");
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (auth.password_from_env) {
    return (
      <Row label={t("set.password")}>
        <span class="small row ok-text">
          <Icon d={I.check} size={13} stroke={2.2} />
          {t("set.password_env")}
        </span>
      </Row>
    );
  }
  const submit = async (e: Event) => {
    e.preventDefault();
    if (password !== repeat) {
      setError(t("auth.err.mismatch"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // The first password (mode local, from the own network) needs the setup code like the first start.
      setAuth(auth.password_set ? await api.changePassword(password, current) : await api.setup(code, password));
      setCode("");
      setCurrent("");
      setPassword("");
      setRepeat("");
      toast(t("set.password_saved"));
    } catch (err) {
      setError(accessError(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Row label={t("set.password")} sub={t("set.password_sub")}>
      {!auth.password_set && <span class="small warn-text">{t("set.password_none")}</span>}
      <form class="auth-form inline" onSubmit={(e) => void submit(e)}>
        <input type="text" name="username" autoComplete="username" value="mtdl-web" readOnly hidden />
        {!auth.password_set && (
          <>
            <input
              class="input mono"
              value={code}
              autoComplete="one-time-code"
              autoCapitalize="characters"
              spellcheck={false}
              placeholder={t("auth.setup_code")}
              aria-label={t("auth.setup_code")}
              onInput={(e) => (setCode((e.target as HTMLInputElement).value), setError(null))}
            />
            <span class="small faint">
              {t("auth.setup_code_where")} <code>docker logs mtdl-web</code>
            </span>
          </>
        )}
        {auth.password_set && (
          <input
            type="password"
            class="input"
            value={current}
            autoComplete="current-password"
            placeholder={t("set.password_current")}
            aria-label={t("set.password_current")}
            onInput={(e) => (setCurrent((e.target as HTMLInputElement).value), setError(null))}
          />
        )}
        <input
          type="password"
          class="input"
          value={password}
          autoComplete="new-password"
          placeholder={t("set.password_new")}
          aria-label={t("set.password_new")}
          onInput={(e) => (setPassword((e.target as HTMLInputElement).value), setError(null))}
        />
        <input
          type="password"
          class="input"
          value={repeat}
          autoComplete="new-password"
          placeholder={t("set.password_repeat")}
          aria-label={t("set.password_repeat")}
          onInput={(e) => (setRepeat((e.target as HTMLInputElement).value), setError(null))}
        />
        {error && (
          <span role="alert" class="small err-text">
            {error}
          </span>
        )}
        <div>
          <button
            class="button primary"
            type="submit"
            disabled={busy || !password || !repeat || (auth.password_set ? !current : !code.trim())}
          >
            {t("set.password_save")}
          </button>
        </div>
      </form>
    </Row>
  );
}

function ApiKeysRow(props: { timeZone: string | undefined }): JSX.Element {
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<KeyScope>("read");
  const [created, setCreated] = useState<{ id: number; name: string; key: string } | null>(null);
  const [confirm, setConfirm] = useState<number | null>(null);
  const load = () => api.apiKeys().then(setKeys, (e: unknown) => toast(errorMessage(e)));
  useEffect(() => {
    void load();
  }, []);
  const create = async (e: Event) => {
    e.preventDefault();
    try {
      const key = await api.createApiKey(name.trim(), scope);
      setCreated({ id: key.id, name: key.name, key: key.key });
      setName("");
      setScope("read");
      await load();
    } catch (err) {
      toast(errorMessage(err));
    }
  };
  const remove = async (key: ApiKey) => {
    if (confirm !== key.id) {
      setConfirm(key.id);
      window.setTimeout(() => setConfirm((c) => (c === key.id ? null : c)), 4000);
      return;
    }
    try {
      await api.deleteApiKey(key.id);
      setCreated((c) => (c?.id === key.id ? null : c)); // a deleted key is shown no more
      toast(t("set.api_key_deleted", { name: key.name }));
      await load();
    } catch (err) {
      toast(errorMessage(err));
    }
  };
  return (
    <Row label={t("set.api_keys")} sub={t("set.api_keys_sub")}>
      {!keys ? (
        <span class="skel" style={{ width: "200px" }} />
      ) : keys.length ? (
        <div class="hook-scripts">
          {keys.map((key) => (
            <div key={key.id} class="hook-script">
              <div class="row wrap">
                <strong>{key.name}</strong>
                <code class="small faint">{key.prefix}…</code>
                <span class={`small ${key.scope === "write" ? "warn-text" : "muted-text"}`}>{t(`set.api_key_scope.${key.scope}`)}</span>
                <span class="grow" />
                <span class="small faint">
                  {key.last_used_at ? t("set.api_key_used", { time: F.dateTime(key.last_used_at, props.timeZone) }) : t("set.api_key_unused")}
                </span>
                <button class="button ghost small danger" onClick={() => void remove(key)}>
                  {confirm === key.id ? t("set.api_key_delete_confirm") : t("set.api_key_delete")}
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <span class="muted-text">{t("set.api_key_none")}</span>
      )}
      {created && (
        <div class="hook-note row wrap">
          <span class="small">{t("set.api_key_created", { name: created.name })}</span>
          <code class="command grow">{created.key}</code>
          <button class="button small" onClick={() => copyText(created.key)}>
            {t("set.api_key_copy")}
          </button>
          <button class="icon-button" title={t("set.api_key_hide")} aria-label={t("set.api_key_hide")} onClick={() => setCreated(null)}>
            <Icon d={I.close} size={14} stroke={2} />
          </button>
        </div>
      )}
      <form class="row wrap" onSubmit={(e) => void create(e)}>
        <input
          class="input grow wide"
          value={name}
          maxLength={100}
          placeholder={t("set.api_key_name_placeholder")}
          aria-label={t("set.api_key_name")}
          onInput={(e) => setName((e.target as HTMLInputElement).value)}
        />
        <select
          class="input"
          value={scope}
          aria-label={t("set.api_key_scope")}
          onChange={(e) => setScope((e.target as HTMLSelectElement).value as KeyScope)}
        >
          <option value="read">{t("set.api_key_scope.read")}</option>
          <option value="write">{t("set.api_key_scope.write")}</option>
        </select>
        <button class="button" type="submit" disabled={!name.trim()}>
          {t("set.api_key_create")}
        </button>
      </form>
      <span class="small faint">{t("set.api_key_scope_sub")}</span>
    </Row>
  );
}

// -- quality ---------------------------------------------------------------------------------------------------------

function QualitySection(props: { settings: AppSettings }): JSX.Element {
  const { settings } = props;
  return (
    <>
      <Row label={t("set.quality_pref")}>
        <div role="radiogroup" class="cards">
          {(["hd", "normal", "low"] as Quality[]).map((q) => {
            const on = settings.quality === q;
            return (
              <button key={q} role="radio" aria-checked={on} class={`choice${on ? " on" : ""}`} onClick={() => void save({ quality: q })}>
                <strong>{t(`q.${q}` as Key)}</strong>
                <span class="small muted-text">{t(`set.q_desc.${q}` as Key)}</span>
              </button>
            );
          })}
        </div>
      </Row>
      <Row label={t("set.fallback")}>
        <span class="muted-text">{t("set.fallback_text")}</span>
      </Row>
      <Row label={t("set.subtitles")}>
        <label class="check-label plain">
          <input type="checkbox" checked={settings.subtitles} onChange={() => void save({ subtitles: !settings.subtitles })} />
          {t("set.subtitles_load")}
        </label>
      </Row>
      <Row label={t("set.special")} sub={t("set.special_sub")}>
        <label class="check-label plain top">
          <input
            type="checkbox"
            checked={settings.exclude_special_versions}
            onChange={() => void save({ exclude_special_versions: !settings.exclude_special_versions })}
          />
          <span>
            <strong>{t("set.special_off")}</strong>
            <span class="small muted-text block-text">{t("set.special_text")}</span>
          </span>
        </label>
      </Row>
      <Row label={t("set.container")} badge={t("set.later")}>
        <span class="muted-text">{t("set.container_text")}</span>
      </Row>
    </>
  );
}

// -- downloads -------------------------------------------------------------------------------------------------------

/** A second limit for chosen days and hours (schedule.py): edited in place, every change is saved at once. */
function ScheduleRow(props: { settings: AppSettings }): JSX.Element {
  const { settings } = props;
  const status = useStatus();
  const info = useInfo();
  const unit = settings.speed_unit;
  const days = settings.schedule_days;
  const shown = (rate: number) => (rate > 0 ? F.dec(F.fromBytesPerSecond(rate, unit)) : "");
  const [limitText, setLimitText] = useState(shown(settings.schedule_limit));
  useEffect(() => setLimitText(shown(settings.schedule_limit)), [settings.schedule_limit, unit]);
  const commitLimit = () => {
    const value = limitText.trim() ? F.parseDecimal(limitText) : 0;
    if (value === null || value < 0) {
      setLimitText(shown(settings.schedule_limit));
      return;
    }
    const rate = Math.round(F.toBytesPerSecond(value, unit));
    if (rate !== settings.schedule_limit) void save({ schedule_limit: rate });
  };
  const toggleDay = (day: number) =>
    void save({ schedule_days: days.includes(day) ? days.filter((d) => d !== day) : [...days, day].sort((a, b) => a - b) });
  const setTime = (key: "schedule_start" | "schedule_end", value: string) => {
    if (/^\d\d:\d\d$/.test(value) && value !== settings[key]) void save({ [key]: value });
  };
  const segments = scheduleSegments(settings.schedule_start, settings.schedule_end);
  const rateLabel = (rate: number) => (rate > 0 ? `${F.dec(F.fromBytesPerSecond(rate, unit))} ${unit}` : t("limit.unlimited"));
  const normal = rateLabel(settings.bandwidth_enabled ? settings.bandwidth_limit : 0);
  return (
    <Row label={t("set.schedule")} sub={t("set.schedule_sub")}>
      <div class="row wrap">
        <Switch on={settings.schedule_enabled} label={t("set.schedule_on")} onToggle={() => void save({ schedule_enabled: !settings.schedule_enabled })} />
        <span class={settings.schedule_enabled ? "strong" : "muted-text"}>{settings.schedule_enabled ? scheduleSummary(settings) : t("set.schedule_off")}</span>
      </div>
      <div class={`schedule-editor${settings.schedule_enabled ? "" : " dimmed"}`}>
        <div class="row wrap" role="group" aria-label={t("set.schedule_days")}>
          {[0, 1, 2, 3, 4, 5, 6].map((day) => (
            <button key={day} class={`chip toggle${days.includes(day) ? " on" : ""}`} aria-pressed={days.includes(day)} onClick={() => toggleDay(day)}>
              {F.weekdayShort(day)}
            </button>
          ))}
        </div>
        {!days.length && <span class="small err-text">{t("set.schedule_no_days")}</span>}
        <div class="row wrap schedule-fields">
          <label class="row">
            <span class="small">{t("set.schedule_from")}</span>
            <input type="time" class="input" value={settings.schedule_start} onChange={(e) => setTime("schedule_start", (e.target as HTMLInputElement).value)} />
          </label>
          <label class="row">
            <span class="small">{t("set.schedule_to")}</span>
            <input type="time" class="input" value={settings.schedule_end} onChange={(e) => setTime("schedule_end", (e.target as HTMLInputElement).value)} />
          </label>
          <label class="row">
            <span class="small">{t("set.schedule_limit")}</span>
            <input
              class="input num-input"
              inputMode="decimal"
              value={limitText}
              placeholder={t("limit.unlimited")}
              onInput={(e) => setLimitText((e.target as HTMLInputElement).value)}
              onBlur={commitLimit}
              onKeyDown={(e) => e.key === "Enter" && commitLimit()}
            />
            <span class="small">{unit}</span>
          </label>
        </div>
        <div class="schedule" style={{ gridTemplateColumns: segments.map((seg) => `${seg.to - seg.from}fr`).join(" ") }}>
          {segments.map((seg) => (
            <span key={seg.from} class={seg.limited ? "day" : "free"}>
              {seg.to - seg.from >= 180 ? (seg.limited ? rateLabel(settings.schedule_limit) : normal) : ""}
            </span>
          ))}
        </div>
        <div class="schedule-scale mono">
          <span>0</span>
          <span>6</span>
          <span>12</span>
          <span>18</span>
          <span>24</span>
        </div>
        {status && settings.schedule_enabled && <span class="small">{t("set.schedule_now", { text: limitTexts(status, info?.timezone).text })}</span>}
      </div>
      <span class="small faint">{t("set.schedule_text")}</span>
    </Row>
  );
}

function DownloadsSection(props: { settings: AppSettings }): JSX.Element {
  const { settings } = props;
  const status = useStatus();
  const parallel = settings.parallel_downloads;
  const setParallel = (n: number) => {
    const value = Math.max(1, Math.min(10, n));
    if (value !== parallel) void save({ parallel_downloads: value });
  };
  return (
    <>
      <Row label={t("set.parallel")}>
        <div class="row">
          <div class="stepper">
            <button aria-label={t("dl.less")} disabled={parallel <= 1} onClick={() => setParallel(parallel - 1)}>
              −
            </button>
            <span>{parallel}</span>
            <button aria-label={t("dl.more")} disabled={parallel >= 10} onClick={() => setParallel(parallel + 1)}>
              +
            </button>
          </div>
          <span class="muted-text">{t("set.parallel_text")}</span>
        </div>
      </Row>
      <Row label={t("set.limit")} sub={t("set.limit_sub")}>
        {status ? (
          <>
            <LimitFields limit={status.limit} presets>
              <LimitSwitch limit={status.limit} normal />
            </LimitFields>
            {status.limit.bandwidth_limit > 0 && <span class="strong">{limitConversion(status.limit)}</span>}
          </>
        ) : (
          <span class="skel" style={{ width: "220px" }} />
        )}
        <span class="small muted-text">{t("set.limit_text")}</span>
      </Row>
      <ScheduleRow settings={settings} />
    </>
  );
}

// -- data source -----------------------------------------------------------------------------------------------------

function SourceSection(props: { settings: AppSettings }): JSX.Element {
  const { settings } = props;
  const status = useStatus();
  const info = useInfo();
  const [system] = useSystem();
  const [updating, setUpdating] = useState(false);
  const fl = status?.filmlist;
  const created = fl?.last_import?.created;
  const tz = info?.timezone;
  const update = async () => {
    setUpdating(true);
    await api.updateFilmlist().catch(() => undefined);
    window.setTimeout(() => {
      setUpdating(false);
      void refreshStatus();
    }, 1500);
  };
  return (
    <>
      {fl?.last_error && (
        <div role="alert" class="notice warn rounded">
          <Icon d={I.warn} size={16} stroke={1.9} class="warn-text" />
          <div class="grow">
            <strong>{t("set.stale_title")}</strong> {t("set.stale_text", { error: fl.last_error, date: created ? F.dateTime(created, tz) : "–" })}
          </div>
          <button class="button primary small" disabled={updating} onClick={() => void update()}>
            {updating ? t("fl.retrying") : t("fl.retry")}
          </button>
        </div>
      )}
      <Row label={t("set.source")}>
        <span class="muted-text">{t("set.source_text")}</span>
      </Row>
      <Row label={t("set.update")}>
        <span class="muted-text">{t("set.update_text", { minute: fl?.next_run ? `:${F.time(fl.next_run, tz).slice(3)}` : "–" })}</span>
        <div class="row wrap">
          <span class="muted-text">
            {t("set.stand", {
              date: created ? F.dateTime(created, tz) : "–",
              count: F.num(fl?.films ?? 0),
              size: F.size(system?.film_db_bytes ?? null),
              next: fl?.next_run ? F.time(fl.next_run, tz) : "–",
            })}
          </span>
          <button class="button small" disabled={updating || !!fl?.running} onClick={() => void update()}>
            {updating || fl?.running ? t("set.updating") : t("set.update_now")}
          </button>
        </div>
      </Row>
    </>
  );
}

// -- system ----------------------------------------------------------------------------------------------------------

function SystemSection(): JSX.Element {
  const [system] = useSystem();
  const status = useStatus();
  const info = useInfo();
  const settings = useSettings();
  if (!system) return <div class="skel block" style={{ height: "200px" }} />;
  const created = status?.filmlist.last_import?.created;
  const tz = info?.timezone;
  const problem = system.writable.downloads;
  const rows: [string, string, string][] = [
    [t("set.sys.filmlist"), t("set.sys.filmlist_v", { date: created ? F.dateTime(created, tz) : "–", count: F.num(system.films) }), ""],
    [t("set.sys.db"), t("set.sys.db_v", { films: F.size(system.film_db_bytes), state: F.size(system.state_db_bytes) }), ""],
    [
      t("set.sys.target"),
      problem
        ? t("set.sys.target_bad", { path: system.download_dir, reason: problem })
        : t("set.sys.target_ok", { path: system.download_dir, free: F.size(system.download_free_bytes) }),
      problem ? "err-text mono" : "ok-text mono",
    ],
    [t("set.sys.config"), system.config_dir, "mono"],
    [t("set.sys.user"), t("set.sys.user_v", { uid: system.uid, gid: system.gid }), "mono"],
    [t("set.sys.workers"), t("set.sys.workers_v", { n: system.workers }), ""],
    [t("set.sys.version"), t("set.sys.version_v", { version: system.version, python: system.python }), "mono"],
    [t("set.sys.tz"), system.timezone, "mono"],
  ];
  const diagnosis = [
    `mtdl-web ${system.version} (Python ${system.python})`,
    `films ${system.films} · list ${created ?? "-"} · film db ${system.film_db_bytes} B · state db ${system.state_db_bytes} B`,
    `downloads ${system.download_dir} ${problem ? `NOT WRITABLE (${problem})` : "ok"} · free ${system.download_free_bytes ?? "?"} B`,
    `uid ${system.uid}:${system.gid} · workers ${system.workers} · tz ${system.timezone}`,
    `filmlist error: ${status?.filmlist.last_error ?? "-"} · problems: ${status?.problems.join("; ") || "-"}`,
  ].join("\n");
  return (
    <>
      <div class="sys-rows">
        {rows.map(([k, v, cls]) => (
          <div key={k} class="form-row compact">
            <span class="muted-text">{k}</span>
            <span class={`break ${cls}`}>{v}</span>
          </div>
        ))}
        <div class="form-row compact">
          <span class="muted-text">{t("set.sys.ui")}</span>
          <LanguageSwitch />
        </div>
        <div class="form-row compact">
          <span class="muted-text">{t("set.logos")}</span>
          <div class="row wrap">
            <Switch on={!!settings?.channel_logos} label={t("set.logos")} onToggle={() => settings && void save({ channel_logos: !settings.channel_logos })} />
            <span class="small faint">{t("set.logos_text")}</span>
          </div>
        </div>
      </div>
      <div class="row wrap top-gap">
        <button class="button" onClick={() => copyText(diagnosis)}>
          {t("set.copy_diag")}
        </button>
        <a class="button ghost" href="/api/docs" target="_blank" rel="noopener">
          {t("set.api_docs")}
        </a>
      </div>
      <Takeover />
    </>
  );
}

// -- takeover from MediathekView ----------------------------------------------------------------------------------

function Takeover(): JSX.Element {
  const [files, setFiles] = useState<{ history: string; abo_history: string; settings: string }>({ history: "", abo_history: "", settings: "" });
  const [names, setNames] = useState<Record<string, string>>({});
  const [report, setReport] = useState<TakeoverReport | null>(null);
  const [busy, setBusy] = useState<"check" | "apply" | null>(null);
  const [details, setDetails] = useState(false);

  const read = (key: keyof typeof files) => (e: Event) => {
    const file = (e.target as HTMLInputElement).files?.[0];
    if (!file) return;
    file.text().then(
      (text) => {
        setFiles((f) => ({ ...f, [key]: text }));
        setNames((n) => ({ ...n, [key]: file.name }));
        setReport(null);
      },
      () => toast(t("tk.read_failed")),
    );
  };
  const run = async (apply: boolean) => {
    setBusy(apply ? "apply" : "check");
    try {
      const result = await api.takeover({ ...files, apply });
      setReport(result);
      if (apply) {
        toast(t("tk.done", { downloads: result.imported_downloads, subs: result.imported_subscriptions }));
        void refreshStatus();
      }
    } catch (e) {
      toast(errorMessage(e));
    }
    setBusy(null);
  };

  const c = report?.counts ?? {};
  const found = (c.new ?? 0) + (c.known ?? 0);
  const repeats = report?.items.reduce((sum, i) => sum + i.repeats, 0) ?? 0;
  const pending = (c.new ?? 0) + (report?.subscriptions.filter((s) => !s.exists && !s.error).length ?? 0);
  const field = (key: keyof typeof files, label: Key, hint: Key) => (
    <label class="file-field">
      <span>
        <strong class="mono">{t(label)}</strong> <span class="small faint">{t(hint)}</span>
      </span>
      <input type="file" accept=".txt,.xml,text/plain,application/xml" onChange={read(key)} />
      {names[key] && <span class="small ok-text">{names[key]}</span>}
    </label>
  );
  return (
    <div class="takeover">
      <h3 class="section-title small-title">{t("tk.title")}</h3>
      <p class="section-lead">{t("tk.text")}</p>
      {field("history", "tk.history", "tk.history_hint")}
      {field("abo_history", "tk.abo_history", "tk.abo_history_hint")}
      {field("settings", "tk.settings", "tk.settings_hint")}
      <div class="row wrap">
        <button class="button" disabled={!files.history && !files.settings || busy !== null} onClick={() => void run(false)}>
          {busy === "check" ? t("tk.checking") : t("tk.check")}
        </button>
        <button class="button primary" disabled={!report || report.applied || !pending || busy !== null} onClick={() => void run(true)}>
          {busy === "apply" ? t("tk.applying") : t("tk.apply")}
        </button>
      </div>
      {report && (
        <div class="example">
          {report.applied ? (
            <strong>{t("tk.done", { downloads: report.imported_downloads, subs: report.imported_subscriptions })}</strong>
          ) : !pending ? (
            <strong>{t("tk.nothing")}</strong>
          ) : null}
          <span>
            {t("tk.summary", {
              lines: F.num(report.lines),
              found: F.num(found),
              known: F.num(c.known ?? 0),
              missing: F.num(c.missing ?? 0),
              ambiguous: F.num(c.ambiguous ?? 0),
            })}
          </span>
          <span class="small muted-text">
            {t("tk.how", { url: c.by_url ?? 0, folder: c.by_folder ?? 0, title: c.by_title ?? 0, repeats })}
          </span>
          {report.subscriptions.length > 0 && (
            <div class="tk-subs">
              <span class="stat-label">{t("tk.subs", { n: report.subscriptions.length })}</span>
              {report.subscriptions.map((sub) => (
                <div key={sub.name} class="tk-sub">
                  <strong>{sub.name}</strong>
                  <span class="mono small muted-text">{summary(sub.conditions)}</span>
                  <span class={`small ${sub.error ? "err-text" : "faint"}`}>
                    {sub.error ?? (sub.exists ? t("tk.sub_exists") : t("tk.sub_paused"))}
                    {sub.notes.map((note) => ` · ${t(`tk.note.${note}` as Key)}`).join("")}
                  </span>
                </div>
              ))}
            </div>
          )}
          <button class="link-button small" onClick={() => setDetails((d) => !d)}>
            {details ? t("tk.hide_items") : t("tk.show_items")}
          </button>
          {details && (
            <div class="tk-items">
              {report.items.map((item) => (
                <div key={item.lines.join(",")} class="tk-item">
                  <span class={`tag ${item.status === "new" ? "" : "muted"}`}>{t(`tk.status.${item.status}` as Key)}</span>
                  <span class="ellipsis">{item.film ? `${item.film.topic} · ${item.film.title}` : `${item.topic} · ${item.title}`}</span>
                  <span class="small faint nowrap">{item.repeats ? `+${item.repeats}` : ""}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// -- notifications: webhooks now, Apprise later -----------------------------------------------------------------------

const WEBHOOK_EVENTS: WebhookEvent[] = [
  "download.completed",
  "download.failed",
  "download.queued",
  "download.started",
  "filmlist.updated",
  "subscription.expired",
];

interface HookDraft {
  key: number;
  id: number | null; // null: not saved yet
  url: string;
  events: WebhookEvent[];
  enabled: boolean;
  hasSecret: boolean;
  secret: string | null; // null: keep (or none), "": remove, text: set
  template: string; // "": the default body
  headers: HeaderRow[];
  headersChanged: boolean; // false: the stored headers stay as they are
  advanced: boolean; // template and headers shown
  preset: Preset | null; // the example chosen last, for its hint
  problem: Problem | null; // what the server said about the last save
  result: { ok: boolean | null; text: string } | null; // ok null: under way
  saved: Webhook | null;
}

let hookKey = 0;
const fromHook = (h: Webhook, advanced?: boolean): HookDraft => ({
  key: ++hookKey,
  id: h.id,
  url: h.url,
  events: h.events,
  enabled: h.enabled,
  hasSecret: h.has_secret,
  secret: null,
  template: h.body_template ?? "",
  headers: storedRows(h.header_names),
  headersChanged: false,
  advanced: advanced ?? (h.body_template !== null || h.header_names.length > 0),
  preset: null,
  problem: null,
  result: null,
  saved: h,
});

const newHook = (): HookDraft => ({
  key: ++hookKey,
  id: null,
  url: "",
  events: ["download.completed", "download.failed"],
  enabled: true,
  hasSecret: false,
  secret: "",
  template: "",
  headers: [],
  headersChanged: false,
  advanced: false,
  preset: null,
  problem: null,
  result: null,
  saved: null,
});

function NotificationsSection(): JSX.Element {
  const [hooks, setHooks] = useState<HookDraft[] | null>(null);
  useEffect(() => {
    api.webhooks().then((list) => setHooks(list.map((h) => fromHook(h))), (e: unknown) => toast(errorMessage(e)));
  }, []);
  const change = (key: number, patch: Partial<HookDraft>) => setHooks((list) => list?.map((h) => (h.key === key ? { ...h, ...patch } : h)) ?? null);

  const save = async (h: HookDraft) => {
    const draft: WebhookDraft = {
      url: h.url.trim(),
      events: h.events,
      enabled: h.enabled,
      body_template: h.template,
      ...(h.secret !== null ? { secret: h.secret } : {}),
      ...(h.headersChanged ? { headers: headerPayload(h.headers) } : {}),
    };
    try {
      const saved = h.id === null ? await api.createWebhook(draft) : await api.saveWebhook(h.id, draft);
      setHooks((list) => list?.map((x) => (x.key === h.key ? { ...fromHook(saved, h.advanced), key: h.key } : x)) ?? null);
      toast(t("wh.saved"));
    } catch (e) {
      const problem = problemOf(e);
      if (problem.field) change(h.key, { problem, advanced: true });
      else toast(problem.text);
    }
  };
  const remove = async (h: HookDraft) => {
    if (h.id !== null) {
      try {
        await api.deleteWebhook(h.id);
      } catch (e) {
        toast(errorMessage(e));
        return;
      }
    }
    setHooks((list) => list?.filter((x) => x.key !== h.key) ?? null);
    if (h.id !== null) toast(t("wh.deleted"));
  };
  const test = async (h: HookDraft) => {
    if (h.id === null) return;
    change(h.key, { result: { ok: null, text: t("wh.testing") } });
    try {
      const r = await api.testWebhook(h.id);
      const text = r.ok ? t("wh.test_ok", { status: r.status ?? "" }) : t("wh.test_failed", { error: r.error ?? "" });
      change(h.key, { result: { ok: r.ok, text } });
    } catch (e) {
      change(h.key, { result: { ok: false, text: t("wh.test_failed", { error: errorMessage(e) }) } });
    }
  };

  return (
    <>
      <Row label={t("wh.title")} sub={t("wh.sub")}>
        {!hooks ? (
          <span class="skel" style={{ width: "260px" }} />
        ) : (
          <>
            {!hooks.length && <span class="muted-text">{t("wh.none")}</span>}
            {hooks.map((h) => (
              <HookCard
                key={h.key}
                hook={h}
                change={(patch) => change(h.key, patch)}
                save={() => void save(h)}
                test={() => void test(h)}
                remove={() => void remove(h)}
              />
            ))}
            <div>
              <button class="button small dashed-button" onClick={() => setHooks((list) => [...(list ?? []), newHook()])}>
                {t("wh.add")}
              </button>
            </div>
            <span class="small faint">{t("wh.format")}</span>
          </>
        )}
      </Row>
      <HooksRow />
      <Row label={t("set.apprise")} badge={t("set.later")}>
        <span class="muted-text">{t("set.notify_later")}</span>
      </Row>
    </>
  );
}

/** Hook scripts: read only, they live in the config folder. */
function HooksRow(): JSX.Element {
  const info = useInfo();
  const [hooks, setHooks] = useState<Hooks | null>(null);
  const load = () => api.hooks().then(setHooks, (e: unknown) => toast(errorMessage(e)));
  useEffect(() => {
    void load();
  }, []);
  return (
    <Row label={t("hk.title")} sub={t("hk.sub")}>
      {!hooks ? (
        <span class="skel" style={{ width: "260px" }} />
      ) : (
        <>
          {hooks.scripts.length ? (
            <div class="hook-scripts">
              {hooks.scripts.map((script) => {
                const status = hookStatus(script, info?.timezone);
                return (
                  <div key={`${script.event}/${script.name}`} class="hook-script">
                    <div class="row wrap">
                      <code>{script.name}</code>
                      <span class="small faint">{t(`wh.event.${script.event}` as Key)}</span>
                      <span class="grow" />
                      <span class={`small ${status.tone === "ok" ? "ok-text" : status.tone === "err" ? "err-text" : "faint"}`}>{status.text}</span>
                    </div>
                    {script.last_run?.output && (
                      <details>
                        <summary class="small">{t("hk.output")}</summary>
                        <pre class="hook-output">{script.last_run.output}</pre>
                      </details>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <span class="muted-text">{t("hk.none")}</span>
          )}
          <span class="small faint">{t("hk.how", { dir: hooks.directory, timeout: F.duration(hooks.timeout) })}</span>
          {!hooks.scripts.length && (
            <>
              <span class="small strong">{`${hooks.directory}/download.completed.d/50-example.sh`}</span>
              <pre class="hook-output">{EXAMPLE_SCRIPT}</pre>
            </>
          )}
          <div>
            <button class="button link small" onClick={() => void load()}>
              {t("hk.reload")}
            </button>
          </div>
        </>
      )}
    </Row>
  );
}

function HookCard(props: {
  hook: HookDraft;
  change: (patch: Partial<HookDraft>) => void;
  save: () => void;
  test: () => void;
  remove: () => void;
}): JSX.Element {
  const { hook: h, change } = props;
  const info = useInfo();
  const area = useRef<HTMLTextAreaElement>(null);
  const urlOk = /^https?:\/\/\S+$/.test(h.url.trim());
  const headerProblems = h.headers.map((row) => headerProblem(row, h.headers));
  const headersOk = headerProblems.every((p) => p === null);
  const dirty =
    h.id === null ||
    !h.saved ||
    h.url !== h.saved.url ||
    h.enabled !== h.saved.enabled ||
    h.secret !== null ||
    h.events.join() !== h.saved.events.join() ||
    h.template !== (h.saved.body_template ?? "") ||
    h.headersChanged;
  const last = h.saved?.last_sent_at
    ? t("wh.last", {
        event: h.saved.last_event ?? "",
        time: F.dateTime(h.saved.last_sent_at, info?.timezone),
        result: h.saved.last_error ?? t("wh.last_ok"),
      })
    : t("wh.never");
  const lastOk = !h.saved?.last_error;
  const setRows = (headers: HeaderRow[]) => change({ headers, headersChanged: true, problem: null });
  const setRow = (key: number, patch: Partial<HeaderRow>) => setRows(h.headers.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  const insert = (field: string) => {
    const el = area.current;
    const next = insertAt(h.template, el?.selectionStart ?? h.template.length, el?.selectionEnd ?? h.template.length, `{{${field}}}`);
    change({ template: next.text, problem: null });
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(next.caret, next.caret);
    });
  };
  const choosePreset = (preset: Preset) =>
    change({
      template: preset.template,
      headers: withPresetHeaders(h.headers, preset),
      headersChanged: h.headersChanged || preset.headers.length > 0,
      events: preset.events ?? h.events,
      preset,
      problem: null,
    });
  const summaryText = [
    h.template ? t("wh.adv_template") : t("wh.adv_default"),
    h.headers.length ? t("wh.adv_headers", { n: h.headers.length }) : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div class={`hook${h.enabled ? "" : " off"}`}>
      <div class="row">
        <input
          class={`input mono grow${h.url && !urlOk ? " invalid" : ""}`}
          value={h.url}
          placeholder="http://homeassistant:8123/api/webhook/mtdl"
          aria-label={t("wh.url")}
          spellcheck={false}
          onInput={(e) => change({ url: (e.target as HTMLInputElement).value })}
        />
        <Switch on={h.enabled} label={t("wh.enabled")} onToggle={() => change({ enabled: !h.enabled })} />
      </div>
      {h.url && !urlOk && <span class="small err-text">{t("wh.url_invalid")}</span>}
      <div class="hook-events">
        {WEBHOOK_EVENTS.map((ev) => (
          <label key={ev} class="check-label plain">
            <input
              type="checkbox"
              checked={h.events.includes(ev)}
              onChange={() => change({ events: h.events.includes(ev) ? h.events.filter((x) => x !== ev) : [...h.events, ev] })}
            />
            {t(`wh.event.${ev}` as Key)}
          </label>
        ))}
      </div>
      {!h.events.length && <span class="small err-text">{t("wh.events_none")}</span>}
      <div class="row wrap">
        <span class="small strong">{t("wh.secret")}</span>
        {h.hasSecret && h.secret === null ? (
          <>
            <span class="small ok-text">{t("wh.secret_set")}</span>
            <button class="button link small" onClick={() => change({ secret: "", hasSecret: false })}>
              {t("wh.secret_change")}
            </button>
          </>
        ) : (
          <input
            type="password"
            class="input grow"
            value={h.secret ?? ""}
            placeholder={t("wh.secret_placeholder")}
            autoComplete="new-password"
            aria-label={t("wh.secret")}
            onInput={(e) => change({ secret: (e.target as HTMLInputElement).value })}
          />
        )}
      </div>
      <span class="small faint">{t("wh.secret_hint")}</span>
      <button class="button link small hook-toggle" aria-expanded={h.advanced} onClick={() => change({ advanced: !h.advanced })}>
        <Icon d={I.chevron} size={14} stroke={2} class={h.advanced ? "" : "turned"} />
        {t("wh.adv")}
        <span class="faint">{summaryText}</span>
      </button>
      {h.advanced && (
        <div class="hook-advanced">
          <div class="row wrap">
            <span class="small faint">{t("wh.tpl.presets")}</span>
            {PRESETS.map((preset) => (
              <button key={preset.id} class="button link small" onClick={() => choosePreset(preset)}>
                {t(presetKey(preset, "label"))}
              </button>
            ))}
          </div>
          {h.preset && <span class="small hook-note">{t(presetKey(h.preset, "hint"))}</span>}
          <label class="small strong" for={`hook-template-${h.key}`}>
            {t("wh.tpl.label")}
          </label>
          <textarea
            id={`hook-template-${h.key}`}
            ref={area}
            class={`input mono hook-template${h.problem?.field === "template" ? " invalid" : ""}`}
            rows={Math.min(12, Math.max(3, h.template.split("\n").length + 1))}
            value={h.template}
            placeholder={t("wh.tpl.placeholder")}
            spellcheck={false}
            onInput={(e) => change({ template: (e.target as HTMLTextAreaElement).value, problem: null })}
          />
          {h.problem?.field === "template" && <span class="small err-text">{h.problem.text}</span>}
          <div class="row wrap hook-fields">
            <span class="small faint">{t("wh.tpl.insert")}</span>
            {FIELDS.map((field) => (
              <button key={field} class="field-chip mono" onClick={() => insert(field)}>
                {`{{${field}}}`}
              </button>
            ))}
          </div>
          <span class="small faint">{t("wh.tpl.help")}</span>
          <span class="small strong">{t("wh.hdr.label")}</span>
          {h.headers.map((row, i) => {
            const problem = headerProblems[i];
            const nameBad = problem === "name" || problem === "reserved" || problem === "duplicate";
            return (
              <div key={row.key} class="hook-header">
                <input
                  class={`input mono${nameBad ? " invalid" : ""}`}
                  value={row.name}
                  placeholder={t("wh.hdr.name")}
                  aria-label={t("wh.hdr.name")}
                  spellcheck={false}
                  onInput={(e) => setRow(row.key, { name: (e.target as HTMLInputElement).value })}
                />
                <input
                  class={`input mono${problem === "missing" || problem === "value" ? " invalid" : ""}`}
                  value={row.value}
                  placeholder={keepsValue(row) ? t("wh.hdr.stored") : t("wh.hdr.value")}
                  aria-label={t("wh.hdr.value")}
                  autoComplete="off"
                  spellcheck={false}
                  onInput={(e) => setRow(row.key, { value: (e.target as HTMLInputElement).value })}
                />
                <button class="icon-button" title={t("wh.hdr.remove")} aria-label={t("wh.hdr.remove")} onClick={() => setRows(h.headers.filter((r) => r.key !== row.key))}>
                  <Icon d={I.close} size={15} stroke={1.9} />
                </button>
                {problem && <span class="small err-text">{t(`wh.hdr.problem.${problem}` as Key)}</span>}
              </div>
            );
          })}
          {h.problem?.field === "headers" && <span class="small err-text">{h.problem.text}</span>}
          <div>
            <button class="button link small" onClick={() => setRows([...h.headers, headerRow()])}>
              {t("wh.hdr.add")}
            </button>
          </div>
          <span class="small faint">{t("wh.hdr.hint")}</span>
        </div>
      )}
      <div class="row wrap">
        <button class="button primary small" disabled={!dirty || !urlOk || !h.events.length || !headersOk} onClick={props.save}>
          {t("wh.save")}
        </button>
        <button class="button small" disabled={h.id === null || dirty} onClick={props.test}>
          {t("wh.test")}
        </button>
        <button class="button ghost small danger" onClick={props.remove}>
          {t("wh.delete")}
        </button>
        <span class="grow" />
        <span class={`small ${h.result ? (h.result.ok === null ? "faint" : h.result.ok ? "ok-text" : "err-text") : lastOk ? "faint" : "err-text"}`}>{h.result?.text ?? last}</span>
      </div>
    </div>
  );
}

/** Language of the interface for this browser; the page reloads to use it everywhere. */
function LanguageSwitch(): JSX.Element {
  const current = languageChoice();
  const options: [LanguageChoice, string][] = [
    ["de", "Deutsch"],
    ["en", "English"],
    ["auto", t("set.sys.lang_auto")],
  ];
  return (
    <div role="radiogroup" aria-label={t("set.sys.ui")} class="segmented fit">
      {options.map(([value, label]) => (
        <button
          key={value}
          role="radio"
          aria-checked={current === value}
          class={current === value ? "on" : ""}
          onClick={() => {
            if (value === current) return;
            chooseLanguage(value);
            location.reload();
          }}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

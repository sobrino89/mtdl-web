// The guide of the first start: target folder, film list, naming and media server.
// The password comes before it (access.tsx, same step bar). Its place is #/start/<step>; finishing or "later" ends
// it for good (setting setup_done). Everything it touches stays in the settings.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";

import { api, errorMessage, type Scheme } from "../api";
import { TargetResult } from "../components/target";
import * as F from "../format";
import { t, type Key } from "../i18n";
import { I, Icon, Logo } from "../icons";
import { EXAMPLES } from "../naming";
import { go, type Route } from "../router";
import { type GuideStep, guideSteps, type ListStage, listStage, stepOf } from "../setup-logic";
import { refreshAuth, refreshStatus, saveSettings, toast, useAuth, useInfo, useSettings, useStatus, useSystem } from "../store";

export function StepBar(props: { steps: GuideStep[]; current: GuideStep }): JSX.Element {
  const at = props.steps.indexOf(props.current);
  return (
    <ol class="steps" aria-label={t("guide.steps")} style={`--steps: ${props.steps.length}`}>
      {props.steps.map((step, i) => (
        <li key={step} class={i < at ? "done" : i === at ? "current" : ""} aria-current={i === at ? "step" : undefined}>
          <span class="ellipsis">
            <span class="nr">{i + 1}</span>
            {t(`guide.step.${step}` as Key)}
          </span>
        </li>
      ))}
    </ol>
  );
}

export function SetupGuide(props: { route: Route }): JSX.Element {
  const auth = useAuth();
  const status = useStatus();
  const steps = guideSteps(auth);
  const step = stepOf(props.route.params[0]);
  const own = steps.filter((s) => s !== "passwort"); // the steps of this page
  const index = own.indexOf(step);
  const last = index === own.length - 1;
  const stage = listStage(status?.filmlist);
  const ready = step === "ziel" ? !!status && !status.writable.downloads : step === "filmliste" ? stage.phase === "ready" : true;
  const [busy, setBusy] = useState(false);

  const finish = async (to: string) => {
    setBusy(true);
    try {
      await saveSettings({ setup_done: true });
      await refreshAuth();
      go(to);
      toast(t("guide.done"));
    } catch (e) {
      toast(errorMessage(e));
      setBusy(false);
    }
  };
  const next = () => (last ? void finish("/suche") : go(`/start/${own[index + 1]}`));

  return (
    <div class="auth-page">
      <main class="guide">
        <div class="auth-brand">
          <Logo size={30} />
          <div class="guide-brand">
            <span class="brand-name">
              mtdl<span class="brand-web">web</span>
            </span>
            <span class="small faint">{t("guide.sub", { n: steps.length })}</span>
          </div>
        </div>
        <StepBar steps={steps} current={step} />
        <div class="guide-card">
          {step === "ziel" ? (
            <TargetStep />
          ) : step === "filmliste" ? (
            <ListStep stage={stage} />
          ) : (
            <NamingStep onLibraries={() => void finish("/einstellungen/mediaserver")} busy={busy} />
          )}
        </div>
        <div class="row wrap guide-actions">
          {index > 0 && (
            <button class="button ghost" onClick={() => go(`/start/${own[index - 1]}`)}>
              {t("guide.back")}
            </button>
          )}
          <button class="button ghost" disabled={busy} onClick={() => void finish("/suche")}>
            {t("guide.later")}
          </button>
          <span class="grow" />
          {step === "filmliste" && !ready && (
            <button class="button" onClick={next}>
              {t("guide.background")}
            </button>
          )}
          <button class="button primary" disabled={!ready || busy} onClick={next}>
            {last ? t("guide.finish") : t("guide.next")}
          </button>
        </div>
      </main>
    </div>
  );
}

function Head(props: { title: string; lead: string }): JSX.Element {
  return (
    <div class="guide-head">
      <h1>{props.title}</h1>
      <p>{props.lead}</p>
    </div>
  );
}

function TargetStep(): JSX.Element {
  const info = useInfo();
  const status = useStatus();
  const [system, reload] = useSystem();
  const [checking, setChecking] = useState(true);
  const dir = info?.download_dir ?? "/downloads";
  const check = async () => {
    setChecking(true);
    try {
      await api.checkDirectories();
      await refreshStatus();
      await reload();
    } catch (e) {
      toast(errorMessage(e));
    }
    setChecking(false);
  };
  useEffect(() => {
    void check();
  }, []);
  return (
    <>
      <Head title={t("guide.target_title")} lead={t("guide.target_lead")} />
      <div class="row">
        <input class="input mono grow" value={dir} readOnly aria-label={t("set.target")} />
        <button class="button" disabled={checking} onClick={() => void check()}>
          {checking ? t("set.checking") : t("set.check")}
        </button>
      </div>
      {checking ? (
        <div aria-busy="true" class="guide-skel">
          <span class="skel" style={{ width: "60%" }} />
          <span class="skel" style={{ width: "40%" }} />
        </div>
      ) : (
        <TargetResult problem={status?.writable.downloads ?? null} system={system} info={info} onCheck={() => void check()} boxed />
      )}
      <span class="small faint">{t("set.target_mount", { path: dir })}</span>
    </>
  );
}

const PHASES = ["download", "import", "ready"] as const;

function ListStep(props: { stage: ListStage }): JSX.Element {
  const list = useStatus()?.filmlist;
  const { phase, fraction } = props.stage;
  const [retrying, setRetrying] = useState(false);
  const progress = list?.progress;
  // One bar over both phases: the download counts for 40 %, reading and indexing for the rest.
  const overall = phase === "ready" ? 1 : phase === "download" && fraction != null ? 0.4 * fraction : phase === "import" && fraction != null ? 0.4 + 0.6 * fraction : null;
  const moving = (phase === "download" || phase === "import") && overall == null;
  const at = PHASES.indexOf(phase as (typeof PHASES)[number]);
  const detail = (p: (typeof PHASES)[number]): string => {
    if (p === "download" && phase === "download" && progress) return progress.total ? t("guide.of", { done: F.size(progress.done), total: F.size(progress.total) }) : F.size(progress.done);
    if (p === "import" && phase === "import" && progress) return t("guide.read", { n: F.num(progress.done) });
    if (p === "ready" && phase === "ready" && list) return t("guide.ready", { n: F.num(list.films) });
    return "";
  };
  const retry = async () => {
    setRetrying(true);
    try {
      await api.updateFilmlist();
      await refreshStatus();
    } catch (e) {
      toast(errorMessage(e));
    }
    setRetrying(false);
  };
  return (
    <>
      <Head title={t("guide.list_title")} lead={t("guide.list_lead")} />
      <div class="guide-progress">
        <div class="row between">
          <strong>{t(`guide.phase.${phase}` as Key)}</strong>
          {overall != null && <span class="mono">{F.percent(overall)}</span>}
        </div>
        <div class="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={overall == null ? undefined : Math.round(overall * 100)}>
          <div class={`progress-fill${moving ? " unknown" : ""}`} style={moving ? undefined : { width: `${((overall ?? 0) * 100).toFixed(1)}%` }} />
        </div>
      </div>
      <ol class="phase-list">
        {PHASES.map((p, i) => (
          <li key={p} class={i < at || phase === "ready" ? "done" : i === at ? "now" : ""}>
            <span class="phase-mark" aria-hidden="true">
              {(i < at || phase === "ready") && <Icon d={I.check} size={14} stroke={2.2} />}
            </span>
            {t(`guide.phase.${p}` as Key)}
            <span class="small faint">{detail(p)}</span>
          </li>
        ))}
      </ol>
      {phase === "error" && list?.last_error && (
        <div role="alert" class="alert-box">
          <strong class="err-text">{t("guide.list_failed")}</strong>
          <span class="small">{list.last_error}</span>
          <div>
            <button class="button primary" disabled={retrying} onClick={() => void retry()}>
              {t("guide.retry")}
            </button>
          </div>
        </div>
      )}
    </>
  );
}

const SCHEMES: Scheme[] = ["season_episode", "date"];

function NamingStep(props: { onLibraries: () => void; busy: boolean }): JSX.Element {
  const settings = useSettings();
  const info = useInfo();
  const [paths, setPaths] = useState<Partial<Record<Scheme, string>>>({});
  const dir = (info?.download_dir ?? "/downloads").replace(/\/+$/, "");
  const example = EXAMPLES[0]?.film;
  useEffect(() => {
    if (!example) return;
    for (const scheme of SCHEMES) {
      void api.namingPreview({ naming: { scheme }, films: [example] }).then(
        (r) => setPaths((p) => ({ ...p, [scheme]: r.paths[0] })),
        () => undefined,
      );
    }
  }, []);
  const pick = (scheme: Scheme) => void saveSettings({ naming_scheme: scheme }).catch((e: unknown) => toast(errorMessage(e)));
  return (
    <>
      <Head title={t("guide.naming_title")} lead={t("guide.naming_lead")} />
      <div role="radiogroup" aria-label={t("set.naming")} class="cards stacked">
        {SCHEMES.map((scheme) => {
          const on = settings?.naming_scheme === scheme;
          return (
            <button key={scheme} role="radio" aria-checked={on} class={`choice${on ? " on" : ""}`} onClick={() => pick(scheme)}>
              <strong>{t(`guide.scheme.${scheme}` as Key)}</strong>
              <code class="small break muted-text">
                <span class="faint">{dir}/</span>
                {paths[scheme] ?? "…"}
              </code>
            </button>
          );
        })}
      </div>
      <span class="small faint">{t("guide.naming_more")}</span>
      <div class="guide-media">
        <strong>{t("guide.media_title")}</strong>
        <span class="small muted-text">{t("guide.media_text")}</span>
        <div>
          <button class="button" disabled={props.busy} onClick={props.onLibraries}>
            {t("guide.media_button")}
          </button>
        </div>
      </div>
    </>
  );
}

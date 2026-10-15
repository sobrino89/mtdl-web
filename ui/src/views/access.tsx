// Access (mtdl_web/auth.py): the first password with the setup code from the container log, the sign-in, and
// the root that shows them, the guide of the first start or the app. Polling for the shell starts only once this
// browser is in.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { accessError } from "../access-texts";
import { api, type AuthStatus } from "../api";
import { App } from "../app";
import { t } from "../i18n";
import { Logo } from "../icons";
import { go, useRoute } from "../router";
import { guideSteps } from "../setup-logic";
import { refreshAuth, setAuth, startPolling, stopPolling, useAuth } from "../store";
import { SetupGuide, StepBar } from "./setup";

export function Root(): JSX.Element {
  const auth = useAuth();
  const route = useRoute();
  useEffect(() => {
    void refreshAuth();
  }, []);
  const inside = !!auth?.authenticated;
  useEffect(() => {
    if (inside) startPolling();
    else stopPolling();
  }, [inside]);
  // A fresh installation starts with the guide, until it is finished or put off.
  useEffect(() => {
    if (inside && auth?.first_run && route.view !== "start") go("/start/ziel");
  }, [inside, auth?.first_run]);

  if (!auth) return <div class="auth-page" aria-busy="true" />;
  if (auth.authenticated) return route.view === "start" ? <SetupGuide route={route} /> : <App />;
  if (auth.setup_required) return <SetupScreen auth={auth} />;
  if (auth.mode === "proxy") return <ProxyMissing />;
  return <LoginScreen />;
}

function Card(props: { title: string; lead?: string; top?: JSX.Element; children: JSX.Element | JSX.Element[] }): JSX.Element {
  return (
    <div class="auth-page">
      <main class={`auth-card${props.top ? " wide" : ""}`}>
        <div class="auth-brand">
          <Logo size={28} />
          <span class="brand-name">
            mtdl<span class="brand-web">web</span>
          </span>
        </div>
        {props.top}
        <h1 class="auth-title">{props.title}</h1>
        {props.lead && <p class="auth-lead">{props.lead}</p>}
        {props.children}
      </main>
    </div>
  );
}

function useSubmit(run: () => Promise<AuthStatus>): [(e: Event) => void, boolean, string | null, (error: string | null) => void] {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = (e: Event) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    run().then(
      (status) => setAuth(status),
      (err: unknown) => {
        setError(accessError(err));
        setBusy(false);
      },
    );
  };
  return [submit, busy, error, setError];
}

function SetupScreen(props: { auth: AuthStatus }): JSX.Element {
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const first = useRef<HTMLInputElement>(null);
  useEffect(() => first.current?.focus(), []);
  const [submit, busy, error, setError] = useSubmit(() => {
    if (password !== repeat) return Promise.reject(new Error(t("auth.err.mismatch")));
    return api.setup(code, password);
  });
  const steps = props.auth.first_run ? <StepBar steps={guideSteps(props.auth)} current="passwort" /> : undefined;
  return (
    <Card title={t("auth.setup_title")} lead={t("auth.setup_lead")} top={steps}>
      <form class="auth-form" onSubmit={submit}>
        <label class="field-label" for="setup-code">
          {t("auth.setup_code")}
        </label>
        <input
          id="setup-code"
          ref={first}
          class="input mono"
          value={code}
          autoComplete="one-time-code"
          autoCapitalize="characters"
          spellcheck={false}
          placeholder="ABCD-EFGH"
          onInput={(e) => (setCode((e.target as HTMLInputElement).value), setError(null))}
        />
        <span class="small faint">
          {t("auth.setup_code_where")} <code>docker logs mtdl-web</code>
        </span>
        <input type="text" name="username" autoComplete="username" value="mtdl-web" readOnly hidden />
        <label class="field-label" for="setup-password">
          {t("auth.password")}
        </label>
        <input
          id="setup-password"
          type="password"
          class="input"
          value={password}
          autoComplete="new-password"
          onInput={(e) => (setPassword((e.target as HTMLInputElement).value), setError(null))}
        />
        <label class="field-label" for="setup-repeat">
          {t("auth.password_repeat")}
        </label>
        <input
          id="setup-repeat"
          type="password"
          class="input"
          value={repeat}
          autoComplete="new-password"
          onInput={(e) => (setRepeat((e.target as HTMLInputElement).value), setError(null))}
        />
        <span class="small faint">{t("auth.password_rules")}</span>
        {error && (
          <span role="alert" class="small err-text">
            {error}
          </span>
        )}
        <button class="button primary" type="submit" disabled={busy || !code.trim() || !password || !repeat}>
          {t("auth.setup_submit")}
        </button>
      </form>
    </Card>
  );
}

function LoginScreen(): JSX.Element {
  const [password, setPassword] = useState("");
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => field.current?.focus(), []);
  const [submit, busy, error, setError] = useSubmit(() => api.login(password));
  return (
    <Card title={t("auth.login_title")}>
      <form class="auth-form" onSubmit={submit}>
        <input type="text" name="username" autoComplete="username" value="mtdl-web" readOnly hidden />
        <label class="field-label" for="login-password">
          {t("auth.password")}
        </label>
        <input
          id="login-password"
          ref={field}
          type="password"
          class="input"
          value={password}
          autoComplete="current-password"
          onInput={(e) => (setPassword((e.target as HTMLInputElement).value), setError(null))}
        />
        {error && (
          <span role="alert" class="small err-text">
            {error}
          </span>
        )}
        <button class="button primary" type="submit" disabled={busy || !password}>
          {t("auth.login_submit")}
        </button>
        <details class="auth-forgot">
          <summary class="small faint">{t("auth.forgot")}</summary>
          <span class="small muted-text">{t("auth.forgot_text")}</span>
          <code class="command">docker exec -it mtdl-web python -m mtdl_web.auth reset</code>
        </details>
      </form>
    </Card>
  );
}

function ProxyMissing(): JSX.Element {
  return (
    <Card title={t("auth.proxy_title")} lead={t("auth.proxy_text")}>
      <button class="button" onClick={() => void refreshAuth()}>
        {t("auth.retry")}
      </button>
    </Card>
  );
}

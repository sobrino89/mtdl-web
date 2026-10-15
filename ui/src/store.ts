// Small shared state: the polled status for the shell, toasts, the colour theme.
import { useEffect, useState } from "preact/hooks";

import { api, type AuthStatus, type Info, type Library, onUnauthorized, type Settings, type Status, type SystemInfo } from "./api";

type Listener = () => void;

class Store<T> {
  private listeners = new Set<Listener>();

  constructor(private value: T) {}

  get(): T {
    return this.value;
  }

  set(value: T): void {
    this.value = value;
    this.listeners.forEach((l) => l());
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
}

function useStore<T>(store: Store<T>): T {
  const [, setTick] = useState(0);
  useEffect(() => store.subscribe(() => setTick((n) => n + 1)), [store]);
  return store.get();
}

// -- status (polled) --------------------------------------------------------------------------------------------------

const statusStore = new Store<Status | null>(null);
const infoStore = new Store<Info | null>(null);
let pollTimer: number | undefined;

// Failed polls in a row: from the third on (a few seconds) the shell says the server is not reachable.
const offlineStore = new Store<number>(0);

export async function refreshStatus(): Promise<void> {
  try {
    statusStore.set(await api.status());
    if (offlineStore.get()) offlineStore.set(0);
  } catch {
    offlineStore.set(offlineStore.get() + 1); // keep the last state; the next poll tries again
  }
}

export const useOffline = (): boolean => useStore(offlineStore) >= 3;

/** Info changes only with the server's setup, and with a TMDB token entered on the settings page. */
export async function refreshInfo(): Promise<void> {
  try {
    infoStore.set(await api.info());
  } catch {
    // keep the last state
  }
}

let polling = false;

/** Status and info for the shell, once signed in; stops when the sign-in ends. */
export function startPolling(): void {
  if (polling) return;
  polling = true;
  void api.info().then((info) => infoStore.set(info), () => undefined);
  const tick = async () => {
    if (!polling) return;
    if (!infoStore.get()) await refreshInfo(); // the first try failed (server restarting, say): until it works
    await refreshStatus();
    const busy = (statusStore.get()?.downloads.running.length ?? 0) > 0 || statusStore.get()?.filmlist.running;
    if (polling) pollTimer = window.setTimeout(tick, busy ? 1000 : 3000);
  };
  window.clearTimeout(pollTimer);
  void tick();
}

export function stopPolling(): void {
  polling = false;
  window.clearTimeout(pollTimer);
}

// -- access (who may use mtdl-web) ---------------------------------------------------------------------------

const authStore = new Store<AuthStatus | null>(null);

export async function refreshAuth(): Promise<AuthStatus | null> {
  try {
    authStore.set(await api.authStatus());
  } catch {
    // not reachable: keep the last state, the offline note says why
  }
  return authStore.get();
}

export function setAuth(status: AuthStatus): void {
  authStore.set(status);
}

export const useAuth = (): AuthStatus | null => useStore(authStore);

/** Signs out this browser; the shell then shows the sign-in. */
export async function logout(): Promise<void> {
  await api.logout().catch(() => undefined);
  await refreshAuth();
}

// A request that needs a sign-in (the session ended elsewhere): ask again, the shell then shows the sign-in.
onUnauthorized(() => void refreshAuth());

export const useStatus = (): Status | null => useStore(statusStore);
export const useInfo = (): Info | null => useStore(infoStore);

// -- settings (loaded on first use, replaced by every save) ----------------------------------------------------------

const settingsStore = new Store<Settings | null>(null);
let settingsLoading = false;

export function setSettings(settings: Settings): void {
  settingsStore.set(settings);
}

/** Saves, keeps the shared copy current and refreshes the status bar (the limit shows there). */
export async function saveSettings(changes: Partial<Settings>): Promise<Settings> {
  const saved = await api.saveSettings(changes);
  settingsStore.set(saved);
  await refreshStatus();
  return saved;
}

export function useSettings(): Settings | null {
  const settings = useStore(settingsStore);
  useEffect(() => {
    if (settingsStore.get() || settingsLoading) return;
    settingsLoading = true;
    void api
      .settings()
      .then(setSettings, () => undefined)
      .finally(() => {
        settingsLoading = false;
      });
  }, []);
  return settings;
}

// -- libraries (loaded on first use, refreshed after changes on the settings page) ------------------------------------

const librariesStore = new Store<Library[] | null>(null);
let librariesLoading = false;

export async function refreshLibraries(): Promise<Library[] | null> {
  try {
    librariesStore.set(await api.libraries());
  } catch {
    // keep the last state
  }
  return librariesStore.get();
}

export function useLibraries(): Library[] | null {
  const libraries = useStore(librariesStore);
  useEffect(() => {
    if (librariesStore.get() || librariesLoading) return;
    librariesLoading = true;
    void refreshLibraries().finally(() => {
      librariesLoading = false;
    });
  }, []);
  return libraries;
}

// -- toasts -------------------------------------------------------------------------------------------------------------

export interface Toast {
  text: string;
  action?: { label: string; run: () => void };
}

const toastStore = new Store<Toast | null>(null);
let toastTimer: number | undefined;

export function toast(text: string, action?: Toast["action"]): void {
  window.clearTimeout(toastTimer);
  toastStore.set({ text, action });
  toastTimer = window.setTimeout(() => toastStore.set(null), 6000);
}

export const closeToast = (): void => toastStore.set(null);
export const useToast = (): Toast | null => useStore(toastStore);

// -- theme (a per-viewer convenience: localStorage, never the location) ----------------------------------------------

export type ThemeName = "light" | "dark" | "system";
const THEME_KEY = "mtdl.theme";

function readTheme(): ThemeName {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "light" || saved === "dark" || saved === "system") return saved;
  } catch {
    // private window or blocked storage
  }
  return "system";
}

const themeStore = new Store<ThemeName>(readTheme());
const media = window.matchMedia?.("(prefers-color-scheme: dark)");

function applyTheme(): void {
  const name = themeStore.get();
  const dark = name === "dark" || (name === "system" && !!media?.matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
}

export function setTheme(name: ThemeName): void {
  try {
    localStorage.setItem(THEME_KEY, name);
  } catch {
    // nothing to do
  }
  themeStore.set(name);
  applyTheme();
}

export function initTheme(): void {
  applyTheme();
  media?.addEventListener?.("change", applyTheme);
}

export const useTheme = (): ThemeName => useStore(themeStore);

// -- viewport -----------------------------------------------------------------------------------------------------------

export type Layout = "mobile" | "rail" | "desktop";

export function useLayout(): { layout: Layout; width: number } {
  const [width, setWidth] = useState(window.innerWidth);
  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const layout: Layout = width < 640 ? "mobile" : width >= 1100 ? "desktop" : "rail";
  return { layout, width };
}

/** What runs where and the free space (settings, first start): loaded once, ``reload`` after a check. */
export function useSystem(): [SystemInfo | null, () => Promise<void>] {
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const load = async () => {
    try {
      setSystem(await api.system());
    } catch {
      // keep the last state
    }
  };
  useEffect(() => {
    void load();
  }, []);
  return [system, load];
}

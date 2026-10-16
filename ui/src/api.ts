// Typed client for /api/v1 (the backend's OpenAPI at /api/docs). Film ids travel as strings (63-bit).

export type Scope = "topic_title" | "title" | "topic" | "all";
export type Quality = "hd" | "normal" | "low";
export type Source = "filmlist" | "mediathekviewweb";
export type SpeedUnit = "Mbit/s" | "MB/s";
export type DownloadState = "queued" | "running" | "done" | "failed" | "cancelled";

export interface FilmStatus {
  download_id: number | null;
  state: DownloadState | null;
  progress: number | null;
  duplicate: boolean;
  subscription_id: number | null;
  subscription_name: string | null;
}

export interface Film {
  id: string;
  channel: string;
  topic: string;
  title: string;
  description: string;
  start: string | null;
  duration: number | null;
  size: number | null;
  url: string | null;
  url_low: string | null;
  url_hd: string | null;
  url_subtitle: string | null;
  url_website: string | null;
  geo: string[];
  status?: FilmStatus | null;
}

export interface SearchResult {
  source: Source;
  total: number;
  films: Film[];
}

export interface ImportInfo {
  kind: "full" | "diff";
  list_id: string;
  created: string;
  added: number;
  changed: number;
  removed: number;
  total: number;
}

export interface FilmlistStatus {
  films: number;
  last_import: ImportInfo | null;
  running: boolean;
  last_run: string | null;
  last_error: string | null;
  next_run: string | null;
  backoff_until: string | null;
  // While an update runs: bytes of the download, then films read; total as the server names it, or the films of
  // the last full list (none on the very first).
  progress: { phase: "download" | "import"; done: number; total: number | null } | null;
}

export interface Running {
  id: number;
  channel: string;
  topic: string;
  title: string;
  done: number;
  total: number | null;
  speed: number;
  phase: string;
}

export interface Status {
  version: string;
  filmlist: FilmlistStatus;
  downloads: { counts: Partial<Record<DownloadState, number>>; running: Running[]; speed: number; progress: number | null };
  limit: Limit;
  subscriptions: number;
  writable: Record<string, string | null>;
  problems: string[];
}

export interface Info {
  version: string;
  description_search: boolean;
  timezone: string;
  download_dir: string;
  uid: number;
  gid: number;
  workers: number;
  tmdb: boolean; // a TMDB token is set
}

/** The normal limit with its switch, and what is in effect now (schedule or switch). */
export interface Limit {
  bandwidth_limit: number;
  bandwidth_enabled: boolean;
  speed_unit: SpeedUnit;
  rate: number; // byte/s in effect now, 0 = unlimited
  source: "normal" | "schedule" | "paused"; // paused: the switch opened a schedule window early
  until: string | null; // next change by the schedule
}

export interface Settings {
  bandwidth_limit: number;
  bandwidth_enabled: boolean;
  speed_unit: SpeedUnit;
  schedule_enabled: boolean;
  schedule_days: number[]; // 0 = Monday
  schedule_start: string; // "08:00"
  schedule_end: string;
  schedule_limit: number; // byte/s, 0 = unlimited
  parallel_downloads: number;
  quality: Quality;
  subtitles: boolean;
  normalize_filenames: boolean;
  channel_logos: boolean;
  source: Source;
  naming_scheme: Scheme;
  naming_template: string | null;
  plex_url: string | null; // for the rescan of libraries that ask for it; the token is a secret
  mcp_enabled: boolean; // MCP server at /mcp, only with an API key
  setup_done: boolean; // the guide of the first start ran or was skipped
  exclude_special_versions: boolean; // subscriptions leave out audio description, sign language and the like
}

export interface SystemInfo {
  version: string;
  python: string;
  timezone: string;
  config_dir: string;
  download_dir: string;
  uid: number;
  gid: number;
  workers: number;
  films: number;
  film_db_bytes: number;
  state_db_bytes: number;
  download_free_bytes: number | null;
  download_total_bytes: number | null;
  writable: Record<string, string | null>;
}

export interface Download {
  id: number;
  state: DownloadState;
  film: Film;
  path: string; // relative to the library (library_id), else to the downloads folder
  file: string; // absolute, as the container sees it
  library_id: number | null;
  quality: Quality;
  subtitles: boolean;
  subscription_id: number | null;
  bytes_done: number;
  bytes_total: number | null;
  speed: number | null;
  phase: string | null;
  error: string | null;
  error_code: string | null;
  attempts: number;
  retry_at: string | null;
  position: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type Scheme = "episode_number" | "season_episode" | "date" | "template";

export type Target = "downloads" | "library";

export interface Naming {
  scheme: Scheme;
  show?: string | null;
  folder?: string | null;
  season?: number | null;
  template?: string | null;
  // "library": straight into the library library_id, named after TMDB. A subscription loads into a series
  // library (tmdb_id: the series), a single download into a movie library (tmdb_id: the movie).
  target?: Target;
  tmdb_id?: number | null;
  library_id?: number | null;
}

export type LibraryKind = "series" | "movies";

export interface LibraryDraft {
  name: string;
  kind: LibraryKind;
  path: string; // inside the container, a mount of its own
  template: string;
  normalize: boolean;
  plex_path: string | null; // the folder as Plex sees it, when the mount differs
  plex_refresh: boolean; // Plex reads the folder again after each download into it
}

export interface Library extends LibraryDraft {
  id: number;
  problem: string | null; // the last write test: missing, not mounted, not writable
  subscriptions: number[];
}

export interface Movie {
  tmdb_id: number;
  title: string;
  original_title: string;
  year: number | null;
  release_date: string | null;
  imdb_id: string | null;
  runtime: number | null; // minutes
  fetched_at: string;
  folders: string[]; // folders of the movie in the library (found by an ID in the name)
}

/** Where POST /downloads would put a film. */
export interface DownloadTarget {
  library_id: number | null;
  path: string;
  file: string;
  exists: boolean; // a file is there already
  videos: string[]; // in a library: other videos in the target folder (the movie in another version)
}

export interface DownloadBody {
  film_id?: string;
  film?: Omit<Film, "id" | "status">;
  quality?: Quality;
  naming?: Naming;
  force?: boolean;
}

export type Field = "topic" | "title" | "description" | "channel" | "date" | "duration";
export type Operator =
  | "is"
  | "is_not"
  | "contains"
  | "not_contains"
  | "any_of"
  | "none_of"
  | "after"
  | "before"
  | "at_least"
  | "at_most";

export interface Condition {
  field: Field;
  operator: Operator;
  value: string | string[] | number;
  regex?: boolean;
  enabled?: boolean;
}

export interface SubscriptionDraft {
  name: string;
  query: string;
  include_description?: boolean;
  source?: Source | null;
  conditions: Condition[];
  naming: Naming;
  quality?: Quality | null;
  subtitles?: boolean | null;
  state?: "active" | "paused";
  valid_until?: string | null; // the last day it searches (ISO date); then it pauses itself or goes
  on_expiry?: "pause" | "delete";
  special_versions?: SpecialVersions; // audio description, sign language: as in the settings, out, or loaded too
}

export interface Subscription extends SubscriptionDraft {
  id: number;
  created_at: string;
  updated_at: string;
  last_check: string | null;
  last_error: string | null;
  last_queued: number | null;
  stats: {
    done?: number;
    queued?: number;
    running?: number;
    failed?: number;
    cancelled?: number;
    skipped?: number;
    last_title?: string;
    last_finished?: number;
  };
}

export interface ConditionResult {
  passed: boolean;
  code: "ok" | "value_missing" | "regex_timeout" | "disabled";
  actual: string | number | null;
  span: [number, number] | null;
}

export type PreviewStatus =
  | "load"
  | "exists"
  | "excluded"
  | "duplicate"
  | "unavailable"
  | "failed"
  | "cancelled"
  | "skipped"
  | "unmatched";

export interface Episode {
  season: number;
  number: number;
  name: string;
  air_date: string | null;
  runtime: number | null;
}

export interface PreviewItem {
  film: Film;
  status: PreviewStatus;
  excluded_by: number | null;
  path: string | null;
  duplicate_of: string | null;
  download_id: number | null;
  results: ConditionResult[];
  // Library target: the TMDB episode and the signals that found it, or for "unmatched" why none fits.
  episode?: Episode | null;
  reason?: string | null;
  in_library?: boolean;
  special?: string | null; // the marker of a special version ("Audiodeskription"), excluded or not
}

export interface SecretState {
  configured: boolean;
  source: "env" | "settings" | null; // env: fixed by the container
}

export interface Integrations {
  tmdb: SecretState;
  plex_token: SecretState;
}

/** A series or a movie found on TMDB (a movie's title in name). */
export interface SeriesCandidate {
  tmdb_id: number;
  name: string;
  original_name: string;
  year: number | null;
  overview: string;
}

export interface Series {
  tmdb_id: number;
  name: string;
  original_name: string;
  year: number | null;
  imdb_id: string | null;
  tvdb_id: number | null;
  seasons: { number: number; episodes: number; first_air_date: string | null; last_air_date: string | null }[];
  fetched_at: string;
  folders: string[];
}

export interface PlexTest {
  version: string;
  sections: { key: string; title: string; type: string; paths: string[] }[];
}

export interface Preview {
  total: number;
  counts: Partial<Record<PreviewStatus, number>>;
  // kind "special_versions": the step after the conditions that leaves out audio description and the like
  steps: { index: number; enabled: boolean; before: number; excluded: number; remaining: number; kind?: "condition" | "special_versions" }[];
  items: PreviewItem[];
}

export type WebhookEvent =
  | "filmlist.updated"
  | "download.queued"
  | "download.started"
  | "download.completed"
  | "download.failed"
  | "subscription.expired";

export interface Webhook {
  id: number;
  url: string;
  events: WebhookEvent[];
  enabled: boolean;
  has_secret: boolean;
  header_names: string[]; // values never leave the server
  body_template: string | null;
  created_at: string;
  last_status: number | null;
  last_error: string | null;
  last_event: string | null;
  last_sent_at: string | null;
}

/** secret: undefined keeps the stored one, "" removes it. headers: undefined keeps all, a value null keeps that
 * header's stored value. body_template: "" for the default body. */
export interface WebhookDraft {
  url: string;
  events: WebhookEvent[];
  enabled: boolean;
  secret?: string;
  headers?: Record<string, string | null>;
  body_template?: string;
}

export interface HookRun {
  started_at: string;
  seconds: number;
  exit_code: number | null; // null: did not start or stopped after the timeout, see error
  output: string;
  error: string | null;
}

export interface HookScript {
  event: WebhookEvent;
  name: string;
  executable: boolean;
  last_run: HookRun | null;
}

export interface Hooks {
  directory: string;
  timeout: number;
  scripts: HookScript[];
}

export interface TakeoverItem {
  lines: number[];
  day: string | null;
  topic: string;
  title: string;
  url: string;
  how: "url" | "folder" | "title" | "ambiguous" | "missing";
  status: "new" | "known" | "missing" | "ambiguous";
  film: Film | null;
  repeats: number;
  subscription: number | null;
}

export interface TakeoverSubscription {
  name: string;
  query: string;
  conditions: Condition[];
  folder: string | null;
  include_description: boolean;
  active_before: boolean;
  notes: string[];
  error: string | null;
  exists: boolean;
}

export interface TakeoverReport {
  lines: number;
  counts: Record<string, number>;
  items: TakeoverItem[];
  subscriptions: TakeoverSubscription[];
  applied: boolean;
  imported_downloads: number;
  imported_subscriptions: number;
}

export type AuthMode = "password" | "local" | "proxy" | "none";

/** Who may use mtdl-web (mtdl_web/auth.py) and how this browser is in. */
export interface AuthStatus {
  mode: AuthMode;
  authenticated: boolean;
  via: "session" | "local" | "proxy" | "none" | null;
  setup_required: boolean; // no password yet: setup with the code from the container log
  password_set: boolean;
  password_from_env: boolean;
  user: string | null; // mode proxy
  outside: "peer" | "forwarded" | "proxy" | null; // mode none: a request from outside
  first_run: boolean; // the guide of the first start is still to come (views/setup.tsx)
}

export type SpecialVersions = "setting" | "exclude" | "include";

export type KeyScope = "read" | "write"; // read: GET and the previews; write: everything

export interface ApiKey {
  id: number;
  name: string;
  prefix: string;
  scope: KeyScope;
  created_at: string;
  last_used_at: string | null;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
  }

  get code(): string | undefined {
    if (typeof this.detail === "string") return this.detail;
    if (this.detail && typeof this.detail === "object" && "code" in this.detail) {
      return String((this.detail as { code: unknown }).code);
    }
    return undefined;
  }
}

/** A short text for toasts and banners: the API's message or code, else the HTTP status or network error. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail;
    if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
    return error.code ?? `HTTP ${error.status}`;
  }
  return error instanceof Error ? error.message : String(error);
}

type Params = Record<string, string | number | boolean | undefined | null | string[]>;

function url(path: string, params?: Params): string {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((item) => search.append(k, item));
    else search.set(k, String(v));
  }
  const qs = search.toString();
  return `/api/v1${path}${qs ? "?" + qs : ""}`;
}

let unauthorized: (() => void) | null = null;

/** Called when a request needs a sign-in (the session ended or never was): the shell shows the sign-in. */
export function onUnauthorized(callback: () => void): void {
  unauthorized = callback;
}

async function request<T>(method: string, path: string, body?: unknown, params?: Params): Promise<T> {
  const response = await fetch(url(path, params), {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? (data as { detail: unknown }).detail : data;
    if (response.status === 401 && !path.startsWith("/auth/")) unauthorized?.();
    throw new ApiError(response.status, detail);
  }
  return data as T;
}

export const api = {
  authStatus: () => request<AuthStatus>("GET", "/auth/status"),
  setup: (code: string, password: string) => request<AuthStatus>("POST", "/auth/setup", { code, password }),
  login: (password: string) => request<AuthStatus>("POST", "/auth/login", { password }),
  logout: () => request<void>("POST", "/auth/logout"),
  changePassword: (password: string, current: string) => request<AuthStatus>("POST", "/auth/password", { password, current }),
  endOtherSessions: () => request<{ ended: number }>("POST", "/auth/sessions/end-others"),
  apiKeys: () => request<ApiKey[]>("GET", "/api-keys"),
  createApiKey: (name: string, scope: KeyScope) => request<ApiKey & { key: string }>("POST", "/api-keys", { name, scope }),
  deleteApiKey: (id: number) => request<void>("DELETE", `/api-keys/${id}`),
  status: () => request<Status>("GET", "/status"),
  info: () => request<Info>("GET", "/info"),
  search: (params: { q: string; scope?: Scope; source?: Source; limit?: number; status?: boolean }) =>
    request<SearchResult>("GET", "/search", undefined, params),
  film: (id: string) => request<Film>("GET", `/films/${id}`),
  updateFilmlist: () => request<{ status: string }>("POST", "/filmlist/update"),
  settings: () => request<Settings>("GET", "/settings"),
  saveSettings: (changes: Partial<Settings>) => request<Settings>("PATCH", "/settings", changes),
  checkDirectories: () => request<Record<string, string | null>>("POST", "/checks/directories"),
  system: () => request<SystemInfo>("GET", "/system"),
  takeover: (body: { history: string; abo_history: string; settings: string; apply: boolean }) =>
    request<TakeoverReport>("POST", "/takeover/mediathekview", body),
  webhooks: () => request<Webhook[]>("GET", "/webhooks"),
  createWebhook: (draft: WebhookDraft) => request<Webhook>("POST", "/webhooks", draft),
  saveWebhook: (id: number, draft: WebhookDraft) => request<Webhook>("PUT", `/webhooks/${id}`, draft),
  deleteWebhook: (id: number) => request<void>("DELETE", `/webhooks/${id}`),
  testWebhook: (id: number) => request<{ ok: boolean; status: number | null; error: string | null }>("POST", `/webhooks/${id}/test`),
  hooks: () => request<Hooks>("GET", "/hooks"),
  pauseLimit: (paused: boolean) => request<Limit>("PUT", "/limit/pause", { paused }),
  namingPreview: (body: { naming: Naming; normalize?: boolean; films: Omit<Film, "id" | "status">[] }) =>
    request<{ paths: string[] }>("POST", "/naming/preview", body),
  downloads: (params: { state?: DownloadState[]; limit?: number; offset?: number; order?: "newest" | "queue" } = {}) =>
    request<{ counts: Partial<Record<DownloadState, number>>; items: Download[] }>("GET", "/downloads", undefined, params),
  download: (body: DownloadBody) => request<Download>("POST", "/downloads", body),
  downloadTarget: (body: DownloadBody) => request<DownloadTarget>("POST", "/downloads/target", body),
  cancelDownload: (id: number) => request<Download>("POST", `/downloads/${id}/cancel`),
  retryDownload: (id: number) => request<Download>("POST", `/downloads/${id}/retry`),
  /** Everything waiting or loading, or one subscription's; the ids allow an undo with retryDownload. */
  cancelDownloads: (subscription?: number) =>
    request<{ cancelled: number[] }>("POST", subscription == null ? "/downloads/cancel" : `/downloads/cancel?subscription=${subscription}`),
  deleteDownload: (id: number) => request<void>("DELETE", `/downloads/${id}`),
  moveDownload: (id: number, index: number) => request<void>("POST", `/downloads/${id}/move`, { index }),
  subscriptions: () => request<Subscription[]>("GET", "/subscriptions"),
  subscription: (id: number) => request<Subscription>("GET", `/subscriptions/${id}`),
  createSubscription: (draft: SubscriptionDraft & { skip_existing?: boolean }) =>
    request<Subscription>("POST", "/subscriptions", draft),
  saveSubscription: (id: number, draft: SubscriptionDraft & { skip_existing?: boolean }) =>
    request<Subscription>("PUT", `/subscriptions/${id}`, draft),
  releaseSkipped: (id: number) => request<{ released: number }>("DELETE", `/subscriptions/${id}/skipped`),
  deleteSubscription: (id: number) => request<void>("DELETE", `/subscriptions/${id}`),
  checkSubscription: (id: number) => request<{ queued: number }>("POST", `/subscriptions/${id}/check`),
  /** Its cancelled and failed episodes leave the history and are queued anew. */
  requeueSubscription: (id: number) => request<{ forgotten: number; queued: number }>("POST", `/subscriptions/${id}/requeue`),
  checkSubscriptions: () => request<{ checked: number; queued: number }>("POST", "/subscriptions/check"),
  preview: (draft: Omit<SubscriptionDraft, "name"> & { name?: string }, subscription?: number | null) =>
    request<Preview>("POST", "/subscriptions/preview", draft, { subscription }),
  integrations: () => request<Integrations>("GET", "/integrations"),
  saveSecrets: (secrets: { tmdb_token?: string | null; plex_token?: string | null }) =>
    request<Integrations>("PUT", "/integrations/secrets", secrets),
  testTmdb: () => request<{ ok: boolean }>("POST", "/integrations/tmdb/test"),
  testPlex: () => request<PlexTest>("POST", "/integrations/plex/test"),
  searchSeries: (q: string) => request<SeriesCandidate[]>("GET", "/metadata/series", undefined, { q }),
  series: (id: number, libraryId?: number | null, refresh = false) =>
    request<Series>("GET", `/metadata/series/${id}`, undefined, { refresh: refresh || undefined, library_id: libraryId }),
  searchMovies: (q: string, year?: number | null) => request<SeriesCandidate[]>("GET", "/metadata/movies", undefined, { q, year }),
  movie: (id: number, libraryId?: number | null) => request<Movie>("GET", `/metadata/movies/${id}`, undefined, { library_id: libraryId }),
  libraries: () => request<Library[]>("GET", "/libraries"),
  createLibrary: (draft: LibraryDraft) => request<Library>("POST", "/libraries", draft),
  saveLibrary: (id: number, draft: LibraryDraft) => request<Library>("PUT", `/libraries/${id}`, draft),
  deleteLibrary: (id: number) => request<void>("DELETE", `/libraries/${id}`),
  libraryPreview: (body: { kind: LibraryKind; template: string; normalize: boolean }) =>
    request<{ paths: string[] }>("POST", "/libraries/preview", body),
};

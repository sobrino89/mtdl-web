// The location lives in the URL hash: #/<view>[/<selection>][?filters]. Navigation pushes a history entry,
// filters replace it; reloading lands where you were.
import { useEffect, useState } from "preact/hooks";

export interface Route {
  view: string;
  params: string[];
  query: Record<string, string>;
  path: string; // without the query, encoded
}

const ROUTE_EVENT = "mtdl:route";

export function parse(hash: string): Route {
  const raw = hash.replace(/^#/, "");
  const at = raw.indexOf("?");
  const path = at < 0 ? raw : raw.slice(0, at);
  const queryString = at < 0 ? "" : raw.slice(at + 1);
  const segments = path
    .split("/")
    .filter(Boolean)
    .map((s) => {
      try {
        return decodeURIComponent(s);
      } catch {
        return s;
      }
    });
  const query: Record<string, string> = {};
  new URLSearchParams(queryString).forEach((v, k) => {
    query[k] = v;
  });
  return {
    view: segments[0] ?? "",
    params: segments.slice(1),
    query,
    path: "/" + segments.map(encodeURIComponent).join("/"),
  };
}

export function build(path: string, query: Record<string, string | number | boolean | null | undefined> = {}): string {
  const clean: Record<string, string> = {};
  for (const [k, v] of Object.entries(query)) {
    if (v === "" || v == null || v === false) continue;
    clean[k] = v === true ? "1" : String(v);
  }
  const qs = new URLSearchParams(clean).toString();
  return "#" + path + (qs ? "?" + qs : "");
}

export function current(): Route {
  return parse(location.hash);
}

/** Navigation: a new history entry. */
export function go(target: string): void {
  const hash = target.startsWith("#") ? target : "#" + target;
  if (location.hash !== hash) location.hash = hash;
}

/** Same place, other filters or selection: replaces the entry. */
export function replace(target: string): void {
  const hash = target.startsWith("#") ? target : "#" + target;
  if (location.hash === hash) return;
  history.replaceState(null, "", hash);
  window.dispatchEvent(new Event(ROUTE_EVENT));
}

export function setQuery(patch: Record<string, string | number | boolean | null | undefined>): void {
  const route = current();
  replace(build(route.path, { ...route.query, ...patch }));
}

export function useRoute(): Route {
  const [route, setRoute] = useState(current);
  useEffect(() => {
    const update = () => setRoute(current());
    window.addEventListener("hashchange", update);
    window.addEventListener(ROUTE_EVENT, update);
    return () => {
      window.removeEventListener("hashchange", update);
      window.removeEventListener(ROUTE_EVENT, update);
    };
  }, []);
  return route;
}

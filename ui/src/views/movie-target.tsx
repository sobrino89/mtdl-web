// "In die Filmbibliothek" in the search details: the person chooses the TMDB movie of a
// Mediathek film, sees where it would go, and loads it; the download itself runs through the search view (duplicates,
// toasts). A guess is only taken without asking when exactly one movie of that name and year exists.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";

import { api, ApiError, type DownloadBody, type DownloadTarget, errorMessage, type Library, type Naming, type SeriesCandidate } from "../api";
import { t, type Key } from "../i18n";
import { I, Icon } from "../icons";
import { autoSeries, errorKey, movieQuery } from "../library-logic";

function apiText(e: unknown): string {
  const key = errorKey(e instanceof ApiError ? e.code : undefined);
  return key ? t(key as Key) : errorMessage(e);
}

export function MovieTarget(props: {
  title: string;
  filmKey: string; // the film's id: a new film starts a new search
  body: DownloadBody; // the film as the API takes it (its id)
  libraries: Library[]; // the movie libraries, at least one
  onLoad: (naming: Naming) => void;
  onClose: () => void;
}): JSX.Element {
  const guess = movieQuery(props.title);
  const [libraryId, setLibraryId] = useState(props.libraries[0]?.id ?? 0);
  const [query, setQuery] = useState(guess.query);
  const [year, setYear] = useState(guess.year ? String(guess.year) : "");
  const [candidates, setCandidates] = useState<SeriesCandidate[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [chosen, setChosen] = useState<number | null>(null);
  const [target, setTarget] = useState<DownloadTarget | null>(null);
  const [error, setError] = useState<string | null>(null);

  const search = async (q: string, y: string, auto: boolean) => {
    if (!q.trim()) return;
    setSearching(true);
    setError(null);
    try {
      let found = await api.searchMovies(q.trim(), /^\d{4}$/.test(y) ? Number(y) : null);
      // TMDB dates a film by its first release; the Mediathek may name the year of another country's.
      if (!found.length && y) found = await api.searchMovies(q.trim());
      setCandidates(found);
      const only = auto ? autoSeries(found, q) : null;
      if (only) setChosen(only.tmdb_id);
    } catch (e) {
      setError(apiText(e));
    } finally {
      setSearching(false);
    }
  };
  useEffect(() => {
    setChosen(null);
    setCandidates(null);
    setQuery(guess.query);
    setYear(guess.year ? String(guess.year) : "");
    void search(guess.query, guess.year ? String(guess.year) : "", true);
  }, [props.filmKey]);

  const naming: Naming | null = chosen ? { scheme: "date", target: "library", library_id: libraryId, tmdb_id: chosen } : null;
  useEffect(() => {
    setTarget(null);
    if (!naming) return;
    let cancelled = false;
    api.downloadTarget({ ...props.body, naming }).then(
      (r) => !cancelled && setTarget(r),
      (e: unknown) => !cancelled && setError(apiText(e)),
    );
    return () => {
      cancelled = true;
    };
  }, [chosen, libraryId, props.filmKey]);

  const library = props.libraries.find((l) => l.id === libraryId);
  return (
    <div class="detail-movie">
      <div class="row">
        <strong class="grow">{t("mv.title")}</strong>
        <button class="icon-button" aria-label={t("mv.close")} title={t("mv.close")} onClick={props.onClose}>
          <Icon d={I.close} size={15} />
        </button>
      </div>
      {props.libraries.length > 1 && (
        <select
          class="input"
          value={String(libraryId)}
          aria-label={t("mv.library")}
          onChange={(e) => setLibraryId(Number((e.target as HTMLSelectElement).value))}
        >
          {props.libraries.map((l) => (
            <option key={l.id} value={String(l.id)}>
              {l.name}
            </option>
          ))}
        </select>
      )}
      <div class="row">
        <input
          class="input grow"
          value={query}
          placeholder={t("mv.placeholder")}
          aria-label={t("mv.placeholder")}
          onInput={(e) => setQuery((e.target as HTMLInputElement).value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void search(query, year, false);
          }}
        />
        <input
          class="input year-input"
          inputMode="numeric"
          maxLength={4}
          value={year}
          placeholder={t("mv.year")}
          aria-label={t("mv.year")}
          onInput={(e) => setYear((e.target as HTMLInputElement).value.replace(/\D/g, ""))}
          onKeyDown={(e) => {
            if (e.key === "Enter") void search(query, year, false);
          }}
        />
        <button class="button" disabled={searching || !query.trim()} onClick={() => void search(query, year, false)}>
          {t("mv.search")}
        </button>
      </div>
      {searching && <span class="small faint">{t("mv.searching")}</span>}
      {candidates !== null &&
        !searching &&
        (candidates.length ? (
          <div class="series-options">
            {candidates.map((c) => (
              <button key={c.tmdb_id} class={`series-option${c.tmdb_id === chosen ? " on" : ""}`} onClick={() => setChosen(c.tmdb_id)}>
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
          <span class="small faint">{t("mv.none")}</span>
        ))}
      {target && (
        <div class="example">
          <span class="stat-label">{t("mv.target")}</span>
          <code class="break">{target.file}</code>
          {target.exists ? (
            <span class="small warn-text row">
              <Icon d={I.warn} size={13} stroke={2} />
              {t("mv.exists")}
            </span>
          ) : (
            target.videos.length > 0 && (
              <span class="small warn-text row">
                <Icon d={I.warn} size={13} stroke={2} />
                {t("mv.other_version", { files: target.videos.join(", ") })}
              </span>
            )
          )}
        </div>
      )}
      {library?.problem && <span class="small err-text">{t("mv.library_bad", { reason: library.problem })}</span>}
      {error && <span class="small err-text">{error}</span>}
      <button class="button primary" disabled={!naming || !target || target.exists} onClick={() => naming && props.onLoad(naming)}>
        <Icon d={I.download} size={15} stroke={1.9} />
        {t("mv.load")}
      </button>
      <div class="attribution">
        <img src="/api/v1/attribution/tmdb" alt="TMDB" height={12} />
        <span class="small faint">{t("mv.tmdb")}</span>
      </div>
    </div>
  );
}

/**
 * Import the folders a library already holds (build 254).
 *
 * Found on the Pi: a library with anime in it left the watchlist empty, and Refresh could only list the folders.
 * This is the step that turns them into entries - and only on the person's say-so: every folder is listed, the
 * ones the AniDB title list matched *exactly* are ticked, the ones it could not settle wait for the person to
 * pick the anime from a search, and nothing happens until Import is pressed. Files are recorded where they are;
 * nothing is moved, renamed or downloaded.
 *
 * Each folder is its own request, one after another, so a long library shows progress and one failure does not
 * stop the rest.
 */
import { useEffect, useMemo, useState } from "react";

import {
  type AnidbMatch,
  type ImportCandidate,
  type ImportResult,
  importFolder,
  listImportCandidates,
  searchAnidbForImport,
} from "../api/client";
import { anidbUrl } from "../anidbUrl";
import {
  PAGE_SIZE,
  type Pick,
  type RowFilter,
  canImport,
  countStates,
  episodeRanges,
  filterRows,
  rowKey,
  searchTextFor,
  seed,
  sharedPicks,
  tickShown,
  toImport,
} from "../folderImport";
import { t } from "../i18n";
import { useLockedPageScroll } from "../useLockedPageScroll";

type Phase = "choose" | "running" | "done";

export function ImportFoldersDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  useLockedPageScroll();
  const [candidates, setCandidates] = useState<ImportCandidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picks, setPicks] = useState<Record<string, Pick>>({});
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [phase, setPhase] = useState<Phase>("choose");
  const [progress, setProgress] = useState(0);
  const [results, setResults] = useState<ImportResult[]>([]);
  // The folder whose anime is being searched for, and what that search is showing.
  const [searching, setSearching] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<AnidbMatch[] | null>(null);
  const [searchBusy, setSearchBusy] = useState(false);
  // What is on screen: a filter button, a typed name, and how many rows are drawn so far.
  const [filter, setFilter] = useState<RowFilter>("all");
  const [nameFilter, setNameFilter] = useState("");
  const [limit, setLimit] = useState(PAGE_SIZE);

  useEffect(() => {
    let live = true;
    listImportCandidates()
      .then((rows) => {
        if (!live) return;
        const start = seed(rows);
        setCandidates(rows);
        setPicks(start.picks);
        setTicked(new Set(start.ticked));
      })
      .catch((failure) => {
        if (live) setError(failure instanceof Error ? failure.message : String(failure));
      });
    return () => {
      live = false;
    };
  }, []);

  const rows = candidates ?? [];
  const chosen = useMemo(() => toImport(rows, picks, ticked), [rows, picks, ticked]);
  const shared = useMemo(() => sharedPicks(rows, picks, ticked), [rows, picks, ticked]);
  const counts = useMemo(() => countStates(rows, picks), [rows, picks]);
  const filtered = useMemo(() => filterRows(rows, picks, filter, nameFilter), [rows, picks, filter, nameFilter]);
  const visible = filtered.slice(0, limit);
  const libraries = [...new Set(visible.map((row) => row.library_name))];

  function toggle(key: string) {
    setTicked((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function startSearch(row: ImportCandidate) {
    const key = rowKey(row);
    if (searching === key) {
      setSearching(null);
      return;
    }
    setSearching(key);
    setQuery(searchTextFor(row.folder));
    setMatches(null);
  }

  async function runSearch() {
    if (query.trim().length < 2) return;
    setSearchBusy(true);
    try {
      setMatches(await searchAnidbForImport(query.trim()));
    } catch {
      setMatches([]);
    } finally {
      setSearchBusy(false);
    }
  }

  function use(row: ImportCandidate, match: AnidbMatch) {
    const key = rowKey(row);
    setPicks((current) => ({ ...current, [key]: { aid: match.aid, title: match.title } }));
    setTicked((current) => new Set(current).add(key));
    setSearching(null);
  }

  async function run() {
    const queue = chosen;
    setPhase("running");
    setProgress(0);
    const done: ImportResult[] = [];
    for (const { candidate, pick } of queue) {
      try {
        done.push(await importFolder(candidate.library_id, candidate.folder, pick.aid));
      } catch (failure) {
        done.push({
          folder: candidate.folder,
          outcome: "failed",
          detail: failure instanceof Error ? failure.message : String(failure),
          anime_id: null,
          name: null,
          files: 0,
        });
      }
      setProgress(done.length);
    }
    setResults(done);
    setPhase("done");
  }

  const imported = results.filter((result) => result.outcome === "imported").length;
  const outcomeText = (outcome: ImportResult["outcome"]) =>
    outcome === "imported" ? t("import.result.imported") : outcome === "skipped" ? t("import.result.skipped") : t("import.result.failed");

  function close() {
    if (imported > 0) onDone();
    onClose();
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog dialog-wide">
        <header className="dialog-head">
          <h2>{t("import.title")}</h2>
          <button className="close" onClick={phase === "running" ? undefined : close} aria-label={t("scan.close")} disabled={phase === "running"}>
            ×
          </button>
        </header>

        {phase === "choose" && (
          <>
            <p className="muted">{t("import.hint")}</p>
            {error ? <p className="error-message">{error}</p> : null}
            {candidates === null && !error ? <p className="muted">{t("import.loading")}</p> : null}
            {candidates !== null && rows.length === 0 ? <p className="muted">{t("import.none")}</p> : null}

            {rows.length > 0 ? (
              <div className="import-toolbar">
                <div className="import-filters" role="group" aria-label={t("import.filter.label")}>
                  {(["all", "found", "notfound", "tracked"] as const)
                    .filter((name) => name !== "tracked" || counts.tracked > 0)
                    .map((name) => (
                      <button
                        key={name}
                        type="button"
                        className={`import-filter${filter === name ? " active" : ""}`}
                        aria-pressed={filter === name}
                        onClick={() => {
                          setFilter(name);
                          setLimit(PAGE_SIZE);
                        }}
                      >
                        {t(`import.filter.${name}`)} <span>{counts[name]}</span>
                      </button>
                    ))}
                </div>
                <input
                  className="import-filter-text"
                  value={nameFilter}
                  placeholder={t("import.filter.text")}
                  aria-label={t("import.filter.text")}
                  onChange={(event) => {
                    setNameFilter(event.target.value);
                    setLimit(PAGE_SIZE);
                  }}
                />
                <span className="import-bulk">
                  <button type="button" className="button ghost" onClick={() => setTicked((now) => tickShown(now, filtered, picks, true))}>
                    {t("import.tickShown")}
                  </button>
                  <button type="button" className="button ghost" onClick={() => setTicked((now) => tickShown(now, filtered, picks, false))}>
                    {t("import.untickShown")}
                  </button>
                </span>
              </div>
            ) : null}
            {rows.length > 0 ? (
              <p className="muted import-count">
                {t("import.showing").replace("{shown}", String(visible.length)).replace("{total}", String(filtered.length))}
              </p>
            ) : null}

            {libraries.map((library) => (
              <section key={library} className="import-library">
                <h3>{library}</h3>
                <ul className="import-list">
                  {visible
                    .filter((row) => row.library_name === library)
                    .map((row) => {
                      const key = rowKey(row);
                      const pick = picks[key];
                      const open = searching === key;
                      const link = pick ? anidbUrl(pick.aid) : null;
                      return (
                        <li key={key} className={`import-row${row.tracked_as ? " tracked" : ""}`}>
                          <div className="import-line">
                            <input
                              type="checkbox"
                              checked={ticked.has(key) && canImport(row, picks)}
                              disabled={!canImport(row, picks)}
                              onChange={() => toggle(key)}
                              aria-label={row.folder}
                            />
                            <span className="import-name">
                              <b>{row.folder}</b>
                              <small>
                                {t("import.files").replace("{n}", String(row.files))}
                                {row.episodes.length > 0
                                  ? ` · ${t("import.episodes").replace("{list}", episodeRanges(row.episodes))}`
                                  : ""}
                              </small>
                              {row.tracked_as ? (
                                <small className="import-note">{t("import.tracked").replace("{name}", row.tracked_as)}</small>
                              ) : pick ? (
                                <small className="import-note matched">
                                  {pick.title} · AniDB {pick.aid}
                                  {link ? (
                                    <>
                                      {" · "}
                                      <a href={link} target="_blank" rel="noopener noreferrer">{t("import.openAnidb")}</a>
                                    </>
                                  ) : null}
                                </small>
                              ) : (
                                <small className="import-note unmatched">{t("import.unmatched")}</small>
                              )}
                              {shared.has(key) ? <small className="import-note warn">{t("import.shared")}</small> : null}
                            </span>
                            {row.tracked_as ? null : (
                              <button className="button secondary" onClick={() => startSearch(row)}>
                                {pick ? t("import.change") : t("import.pick")}
                              </button>
                            )}
                          </div>
                          {open ? (
                            <div className="import-search">
                              <div className="import-search-box">
                                <input
                                  value={query}
                                  placeholder={t("import.searchPlaceholder")}
                                  onChange={(event) => setQuery(event.target.value)}
                                  onKeyDown={(event) => {
                                    if (event.key === "Enter") void runSearch();
                                  }}
                                  autoFocus
                                />
                                <button className="button secondary" disabled={searchBusy || query.trim().length < 2} onClick={() => void runSearch()}>
                                  {searchBusy ? t("import.searching") : t("import.search")}
                                </button>
                              </div>
                              {matches !== null && matches.length === 0 ? <p className="muted">{t("import.noMatches")}</p> : null}
                              {matches !== null && matches.length > 0 && matches[0].via && matches[0].via !== "full" ? (
                                <p className="muted import-via">
                                  {t(matches[0].via === "clean" ? "import.via.clean" : "import.via.shorter").replace(
                                    "{text}",
                                    matches[0].via_text ?? "",
                                  )}
                                </p>
                              ) : null}
                              {matches?.map((match) => (
                                <div className="import-match" key={match.aid}>
                                  <span className="import-name">
                                    <b>{match.title}</b>
                                    <small>
                                      AniDB {match.aid}
                                      {match.romaji && match.romaji !== match.title ? ` · ${match.romaji}` : ""}
                                      {anidbUrl(match.aid) ? (
                                        <>
                                          {" · "}
                                          <a href={anidbUrl(match.aid) ?? undefined} target="_blank" rel="noopener noreferrer">
                                            {t("import.openAnidb")}
                                          </a>
                                        </>
                                      ) : null}
                                    </small>
                                  </span>
                                  <button className="button secondary" onClick={() => use(row, match)}>
                                    {t("import.use")}
                                  </button>
                                </div>
                              ))}
                            </div>
                          ) : null}
                        </li>
                      );
                    })}
                </ul>
              </section>
            ))}

            {filtered.length > visible.length ? (
              <div className="import-more">
                <button type="button" className="button secondary" onClick={() => setLimit((now) => now + PAGE_SIZE)}>
                  {t("import.showMore").replace("{n}", String(filtered.length - visible.length))}
                </button>
              </div>
            ) : null}

            {rows.length > 0 ? <p className="muted import-automate">{t("import.automateNote")}</p> : null}
            <div className="dialog-actions">
              <button className="button secondary" onClick={close}>
                {t("libraries.close")}
              </button>
              <button className="button primary" disabled={chosen.length === 0} onClick={() => void run()}>
                {t("import.run").replace("{n}", String(chosen.length))}
              </button>
            </div>
          </>
        )}

        {phase === "running" && (
          <>
            <p className="muted">{t("import.running").replace("{done}", String(progress)).replace("{total}", String(chosen.length))}</p>
            <div
              className="import-bar"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={chosen.length}
              aria-valuenow={progress}
            >
              <div className="import-bar-fill" style={{ width: `${chosen.length ? (progress / chosen.length) * 100 : 0}%` }} />
            </div>
          </>
        )}

        {phase === "done" && (
          <>
            <p className={imported === results.length ? "import-summary good" : "import-summary"}>
              {t("import.done").replace("{n}", String(imported)).replace("{total}", String(results.length))}
            </p>
            <ul className="import-list">
              {results.map((result) => (
                <li key={result.folder} className={`import-row ${result.outcome}`}>
                  <div className="import-line">
                    <span className="import-name">
                      <b>{result.name ?? result.folder}</b>
                      <small>
                        {outcomeText(result.outcome)}
                        {result.outcome === "imported" ? ` · ${t("import.files").replace("{n}", String(result.files))}` : ""}
                        {result.detail ? ` · ${result.detail}` : ""}
                      </small>
                    </span>
                  </div>
                </li>
              ))}
            </ul>
            <div className="dialog-actions">
              <button className="button primary" onClick={close}>
                {t("libraries.close")}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

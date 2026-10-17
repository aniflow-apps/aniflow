import { useEffect, useRef, useState } from "react";

import {
  type AnidbExportStatus,
  type AnidbProblemKind,
  type AnidbProblems,
  type AnidbTitlesStatus,
  type MylistAnswer,
  type MylistFilters,
  DEFAULT_MYLIST_FILTERS,
  MYLIST_TYPES,
  addMylistEntryToWatchlist,
  getAnidbExportStatus,
  getAnidbProblems,
  getAnidbTitles,
  getShokoAnidbMylist,
  refreshAnidbTitles,
  uploadAnidbExport,
} from "../api/client";
import { episodeRanges } from "../folderImport";
import { t } from "../i18n";
import { formatTimestamp } from "../time";
import { PageTabs } from "./PageTabs";

/**
 * Everything AniFlow knows from AniDB, on a page of its own (build 258).
 *
 * It lived on the Shoko Server page, which is about a different program. AniDB's two inputs - the weekly title list
 * AniFlow downloads and the My List export the user uploads - are read here, and the export answers more than the
 * old "incomplete series" list: the files section of each anime says which episodes were never watched, which files
 * AniDB lost track of, and where two release groups were both kept. AniFlow never asks AniDB itself; what a link
 * opens is the user's own browser.
 *
 * One tab at a time, so the page is not a long scroll. The tab is the URL's section (`#/anidb/unwatched`), which
 * is what lets the back button move between tabs.
 */
const TABS = ["library", "incomplete", "unwatched", "unknown", "groups"] as const;
type Tab = (typeof TABS)[number];

export function AnidbPage({
  section,
  onSection,
  onOpenAnime,
}: {
  section?: string;
  onSection?: (name: string | undefined) => void;
  onOpenAnime?: (animeId: string) => void;
}) {
  const tab: Tab = (TABS as readonly string[]).includes(section ?? "") ? (section as Tab) : "library";
  const go = (next: Tab) => onSection?.(next);

  return (
    <section className="settings-page">
      <p className="muted" style={{ margin: 0 }}>{t("anidb.page.hint")}</p>
      <PageTabs tabs={TABS.map((id) => ({ id, label: t(`anidb.tab.${id}`) }))} active={tab} onChange={go} />
      {tab === "library" && <AnidbLibraryTab />}
      {tab === "incomplete" && <AnidbIncompletePanel onOpenAnime={onOpenAnime} onGoToLibrary={() => go("library")} />}
      {(tab === "unwatched" || tab === "unknown" || tab === "groups") && (
        <AnidbProblemsPanel key={tab} kind={tab} onOpenAnime={onOpenAnime} onGoToLibrary={() => go("library")} />
      )}
    </section>
  );
}

// ------------------------------------------------------------------ Library

function AnidbLibraryTab() {
  return (
    <>
      <AnidbTitlesPanel />
      <AnidbExportPanel />
    </>
  );
}

/**
 * The title list's state, and the button that fetches it again.
 *
 * Four figures rather than a green tick, because "it works" is not what is being asked. The interesting number is
 * *linked* - how many watchlist anime the dump could actually put an id on. A dump with a quarter of a million
 * titles that identified none of your library is a failure that a tick would have hidden.
 */
function AnidbTitlesPanel() {
  const [status, setStatus] = useState<AnidbTitlesStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAnidbTitles().then(setStatus).catch(() => setStatus(null));
  }, []);

  function refresh() {
    setBusy(true);
    setError(null);
    refreshAnidbTitles()
      .then(setStatus)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(false));
  }

  return (
    <div className="detail-panel">
      <h2>{t("anidb.titles")}</h2>
      <button className="button secondary" disabled={busy} onClick={refresh}>
        {busy ? t("anidb.downloading") : t("anidb.refresh")}
      </button>
      <p className="muted">{t("anidb.hint")}</p>

      {status && !status.present ? (
        /* Not an error: the dump is optional, and the sentence says what is lost without it rather than
           demanding a button be pressed. */
        <p className="muted">{t("anidb.never")}</p>
      ) : (
        status && (
          <dl className="parsed-grid">
            <dt>{t("anidb.downloaded")}</dt>
            <dd>
              {formatTimestamp(status.downloaded_at) || "—"}
              {status.bytes_downloaded > 0 && <> · {(status.bytes_downloaded / 1024 ** 2).toFixed(1)} MB</>}
            </dd>
            <dt>{t("anidb.parsed")}</dt>
            <dd>
              {formatTimestamp(status.parsed_at) || "—"}
              {status.title_count > 0 && (
                <>
                  {" · "}
                  {t("anidb.counts")
                    .replace("{anime}", status.anime_count.toLocaleString())
                    .replace("{titles}", status.title_count.toLocaleString())}
                </>
              )}
            </dd>
            <dt>{t("anidb.nextRefresh")}</dt>
            <dd>{formatTimestamp(status.next_refresh_at) || "—"}</dd>
            <dt>{t("anidb.linked")}</dt>
            <dd>{t("anidb.linkedValue").replace("{linked}", String(status.linked)).replace("{total}", String(status.total))}</dd>
          </dl>
        )
      )}

      {status?.last_error && <p className="reasons">{status.last_error}</p>}
      {error && <p className="error-message">{error}</p>}
      {status && status.present && status.linked < status.total && (
        /* Said out loud, because an incomplete count looks like a bug and is not one: a show AniDB has not
           filed yet, or a name that points at two ids, is deliberately left without one. */
        <p className="muted">{t("anidb.unmatchedNote")}</p>
      )}
    </div>
  );
}

/** The My List export: what was uploaded, and the button to upload one (the file AniDB's Export page builds). */
function AnidbExportPanel() {
  const [status, setStatus] = useState<AnidbExportStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement | null>(null);

  function read() {
    return getAnidbExportStatus()
      .then(setStatus)
      .catch((err) => setMessage(err instanceof Error ? err.message : String(err)));
  }

  useEffect(() => {
    void read();
  }, []);

  async function upload(file: File) {
    setBusy(true);
    setMessage(null);
    try {
      const done = await uploadAnidbExport(file);
      setMessage(t("shoko.mylist.uploaded").replace("{n}", String(done.considered)));
      await read();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="detail-panel">
      <h2>{t("anidb.export.title")}</h2>
      <p className="muted">{t("anidb.export.hint")}</p>
      <div className="profile-actions">
        <input
          ref={picker}
          type="file"
          accept=".tgz,.gz,.tar.gz,application/gzip,application/x-gzip"
          style={{ display: "none" }}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) void upload(file);
          }}
        />
        <button className="button secondary" disabled={busy} onClick={() => picker.current?.click()}>
          {busy ? t("shoko.loading") : t("shoko.mylist.upload")}
        </button>
        {status?.has_export && status.uploaded_at && (
          <small className="muted">
            {t("shoko.mylist.from").replace("{when}", formatTimestamp(status.uploaded_at) || "—").replace("{n}", String(status.considered))}
          </small>
        )}
      </div>
      {message && <p className="muted">{message}</p>}
      {status && !status.has_export && <p className="muted">{t("shoko.mylist.howTo")}</p>}
      {status?.has_export && !status.files_read && <p className="reasons">{t("anidb.export.reupload")}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ Incomplete series

/**
 * AniDB's My List page, from the export the user uploads (build 244; the filter box is build 242's).
 *
 * *"Whatever this list is showing is totally wrong ... I would like to recreate this output."*  Shoko cannot see an
 * anime it holds no file of, so the list comes from AniDB's own Export file instead. The airing status is an
 * estimate (the export has no end date), and says so.
 */
const MYLIST_FILTERS_KEY = "aniflow.shoko.mylist";

function savedMylistFilters(): MylistFilters {
  try {
    const raw = window.localStorage.getItem(MYLIST_FILTERS_KEY);
    if (raw) return { ...DEFAULT_MYLIST_FILTERS, ...(JSON.parse(raw) as Partial<MylistFilters>) };
  } catch { /* private window, or a value that is not JSON: the defaults stand */ }
  return DEFAULT_MYLIST_FILTERS;
}

/** The watchlist button of a row: open the entry it has, or put it on the watchlist. */
function useAddToWatchlist(after: () => void | Promise<void>) {
  const [adding, setAdding] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function add(aid: number) {
    setAdding(aid);
    setMessage(null);
    try {
      const done = await addMylistEntryToWatchlist(aid);
      if (done.added) await after();
      else setMessage(done.detail ?? t("shoko.addFailed"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setAdding(null);
    }
  }
  return { adding, message, add };
}

function WatchlistCell({
  aid,
  watchlistId,
  adding,
  onAdd,
  onOpenAnime,
}: {
  aid: number;
  watchlistId: string | null;
  adding: number | null;
  onAdd: (aid: number) => void;
  onOpenAnime?: (animeId: string) => void;
}) {
  return (
    <td className="mylist-action">
      {watchlistId ? (
        <button className="button secondary" onClick={() => onOpenAnime?.(watchlistId)}>
          {t("shoko.mylist.open")}
        </button>
      ) : (
        <button className="button secondary" disabled={adding !== null} onClick={() => onAdd(aid)}>
          {adding === aid ? t("shoko.adding") : t("shoko.mylist.add")}
        </button>
      )}
    </td>
  );
}

/** A link the user's own browser follows: AniFlow never fetches anidb.net pages. */
function AnidbLink({ aid, title, english }: { aid: number; title: string; english?: string | null }) {
  return (
    <a href={`https://anidb.net/anime/${aid}`} target="_blank" rel="noopener noreferrer" title={english ?? undefined}>
      {title}
    </a>
  );
}

function NoExport({ onGoToLibrary }: { onGoToLibrary: () => void }) {
  return (
    <>
      <p className="muted">{t("anidb.noExport")}</p>
      <div className="profile-actions">
        <button className="button secondary" onClick={onGoToLibrary}>{t("anidb.goToLibrary")}</button>
      </div>
    </>
  );
}

function AnidbIncompletePanel({
  onOpenAnime,
  onGoToLibrary,
}: {
  onOpenAnime?: (animeId: string) => void;
  onGoToLibrary: () => void;
}) {
  const [filters, setFilters] = useState<MylistFilters>(savedMylistFilters);
  const [answer, setAnswer] = useState<MylistAnswer | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  function read() {
    setBusy(true);
    return getShokoAnidbMylist(filters)
      .then(setAnswer)
      .catch((err) => setMessage(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(false));
  }

  const { adding, message: addMessage, add } = useAddToWatchlist(read);

  useEffect(() => {
    try { window.localStorage.setItem(MYLIST_FILTERS_KEY, JSON.stringify(filters)); } catch { /* not kept */ }
    void read();
  }, [filters]);

  function pick<K extends keyof MylistFilters>(key: K, value: MylistFilters[K]) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  function toggleType(kind: string) {
    pick("types", filters.types.includes(kind) ? filters.types.filter((item) => item !== kind) : [...filters.types, kind]);
  }

  const choice = (key: "adult" | "airing" | "status", values: readonly string[]) => (
    <label className="field">
      <span>{t(`shoko.mylist.${key}`)}</span>
      <select value={filters[key]} onChange={(event) => pick(key, event.target.value as never)}>
        {values.map((value) => <option key={value} value={value}>{t(`shoko.mylist.${key}.${value}`)}</option>)}
      </select>
    </label>
  );

  return (
    <div className="detail-panel">
      <h2>{t("anidb.tab.incomplete")}</h2>
      <p className="muted">{t("anidb.incomplete.hint")}</p>
      {message && <p className="muted">{message}</p>}
      {answer && !answer.has_export && <NoExport onGoToLibrary={onGoToLibrary} />}
      {answer?.has_export && (
        <>
          <div className="mylist-filters">
            {choice("status", ["ignore", "complete", "incomplete"])}
            {choice("airing", ["ignore", "airing", "finished", "unaired"])}
            {choice("adult", ["ignore", "hide", "only"])}
          </div>
          <div className="mylist-types" role="group" aria-label={t("shoko.mylist.type")}>
            <span className="muted">{t("shoko.mylist.type")}</span>
            {MYLIST_TYPES.map((kind) => (
              <label key={kind} className="check">
                <input type="checkbox" checked={filters.types.includes(kind)} onChange={() => toggleType(kind)} />
                <span>{t(`shoko.mylist.type.${kind}`)}</span>
              </label>
            ))}
          </div>
          {busy && answer.items.length === 0 ? <p className="muted">{t("shoko.loading")}</p> : null}
          {answer.items.length === 0 ? (
            <p className="muted">{t("shoko.mylist.empty").replace("{n}", String(answer.considered))}</p>
          ) : (
            <>
              <p className="muted">{t("shoko.mylist.count").replace("{n}", String(answer.items.length)).replace("{total}", String(answer.considered))}</p>
              <table className="mylist-table">
                <thead>
                  <tr>
                    <th>{t("shoko.mylist.col.title")}</th>
                    <th>{t("shoko.mylist.col.type")}</th>
                    <th>{t("shoko.mylist.col.year")}</th>
                    <th>{t("shoko.mylist.col.eps")}</th>
                    <th>{t("shoko.mylist.col.watchlist")}</th>
                  </tr>
                </thead>
                <tbody>
                  {answer.items.map((item) => (
                    <tr key={item.aid}>
                      <td><AnidbLink aid={item.aid} title={item.title} english={item.english} /></td>
                      <td>{t(`shoko.mylist.type.${item.type}`)}</td>
                      <td>{item.year || "—"}</td>
                      <td>
                        {item.held}/{item.total}
                        {item.held_specials > 0 && `+${item.held_specials}`}
                      </td>
                      <WatchlistCell aid={item.aid} watchlistId={item.watchlist_id} adding={adding} onAdd={(aid) => void add(aid)} onOpenAnime={onOpenAnime} />
                    </tr>
                  ))}
                </tbody>
              </table>
              {addMessage && <p className="error-message">{addMessage}</p>}
              <p className="muted">{t("shoko.mylist.estimate")}</p>
            </>
          )}
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ Not watched / State unknown / Several groups

const ADULT_KEY = "aniflow.anidb.adult";
const ADULT_CHOICES = ["ignore", "hide", "only"] as const;

function savedAdult(): string {
  try {
    const stored = window.localStorage.getItem(ADULT_KEY);
    if (stored && (ADULT_CHOICES as readonly string[]).includes(stored)) return stored;
  } catch { /* not kept */ }
  return "hide";
}

/** What a row says is wrong with it, in words: the episodes, and for groups the groups beside them. */
function Details({ kind, row }: { kind: AnidbProblemKind; row: AnidbProblems["items"][number] }) {
  const episodes = row.episodes.length > 0 ? t("anidb.detail.episodes").replace("{list}", episodeRanges(row.episodes)) : "";
  const specials = row.specials > 0 ? t("anidb.detail.specials").replace("{n}", String(row.specials)) : "";
  if (kind !== "groups") return <>{[episodes, specials].filter(Boolean).join(" · ")}</>;
  return (
    <>
      {episodes ? <div>{t("anidb.detail.doubled").replace("{list}", episodeRanges(row.episodes))}</div> : null}
      <small className="muted">
        {row.groups.map((group, index) => (
          <span key={group.id}>
            {index > 0 ? " · " : ""}
            {/* The export names a group by AniDB's number only; the link is the user's own browser's. */}
            <a href={`https://anidb.net/group/${group.id}`} target="_blank" rel="noopener noreferrer">
              {t("anidb.detail.group").replace("{id}", group.id)}
            </a>{" "}
            ({t("anidb.detail.files").replace("{n}", String(group.files))})
          </span>
        ))}
      </small>
    </>
  );
}

function AnidbProblemsPanel({
  kind,
  onOpenAnime,
  onGoToLibrary,
}: {
  kind: AnidbProblemKind;
  onOpenAnime?: (animeId: string) => void;
  onGoToLibrary: () => void;
}) {
  const [adult, setAdult] = useState<string>(savedAdult);
  // Groups only: list a show when one episode is held twice from two groups, not for every show with two groups.
  const [doubled, setDoubled] = useState(true);
  const [answer, setAnswer] = useState<AnidbProblems | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function read() {
    setBusy(true);
    setError(null);
    return getAnidbProblems(kind, adult, doubled)
      .then(setAnswer)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(false));
  }

  const { adding, message: addMessage, add } = useAddToWatchlist(read);

  useEffect(() => {
    try { window.localStorage.setItem(ADULT_KEY, adult); } catch { /* not kept */ }
    void read();
  }, [kind, adult, doubled]);

  return (
    <div className="detail-panel">
      <h2>{t(`anidb.tab.${kind}`)}</h2>
      <p className="muted">{t(`anidb.${kind}.hint`)}</p>
      {error && <p className="error-message">{error}</p>}
      {answer && !answer.has_export && <NoExport onGoToLibrary={onGoToLibrary} />}
      {answer?.has_export && !answer.files_read && (
        <>
          <p className="reasons">{t("anidb.export.reupload")}</p>
          <div className="profile-actions">
            <button className="button secondary" onClick={onGoToLibrary}>{t("anidb.goToLibrary")}</button>
          </div>
        </>
      )}
      {answer?.has_export && answer.files_read && (
        <>
          <div className="mylist-filters">
            <label className="field">
              <span>{t("shoko.mylist.adult")}</span>
              <select value={adult} onChange={(event) => setAdult(event.target.value)}>
                {ADULT_CHOICES.map((value) => <option key={value} value={value}>{t(`shoko.mylist.adult.${value}`)}</option>)}
              </select>
            </label>
            {kind === "groups" ? (
              <label className="check">
                <input type="checkbox" checked={doubled} onChange={(event) => setDoubled(event.target.checked)} />
                <span>{t("anidb.groups.doubled")}</span>
              </label>
            ) : null}
          </div>
          {answer.items.length === 0 ? (
            <p className="muted">{busy ? t("shoko.loading") : t("anidb.empty").replace("{n}", String(answer.considered))}</p>
          ) : (
            <>
              <p className="muted">{t("anidb.count").replace("{n}", String(answer.items.length)).replace("{total}", String(answer.considered))}</p>
              <table className="mylist-table problems">
                <thead>
                  <tr>
                    <th>{t("shoko.mylist.col.title")}</th>
                    <th>{t("shoko.mylist.col.type")}</th>
                    <th>{t("shoko.mylist.col.year")}</th>
                    <th>{t("shoko.mylist.col.eps")}</th>
                    <th>{t("anidb.col.details")}</th>
                    <th>{t("shoko.mylist.col.watchlist")}</th>
                  </tr>
                </thead>
                <tbody>
                  {answer.items.map((row) => (
                    <tr key={row.aid}>
                      <td><AnidbLink aid={row.aid} title={row.title} english={row.english} /></td>
                      <td>{t(`shoko.mylist.type.${row.type}`)}</td>
                      <td>{row.year || "—"}</td>
                      <td>{row.held}/{row.total}</td>
                      <td><Details kind={kind} row={row} /></td>
                      <WatchlistCell aid={row.aid} watchlistId={row.watchlist_id} adding={adding} onAdd={(aid) => void add(aid)} onOpenAnime={onOpenAnime} />
                    </tr>
                  ))}
                </tbody>
              </table>
              {addMessage && <p className="error-message">{addMessage}</p>}
            </>
          )}
        </>
      )}
    </div>
  );
}

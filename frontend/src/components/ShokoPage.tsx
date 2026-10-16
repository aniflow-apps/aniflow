import { useEffect, useState } from "react";
import {
  getShokoOverview,
  addShokoSeriesToWatchlist,
  getShokoSeries,
  providerCoverUrl,
  shokoPosterUrl,
  type ShokoOverview,
  type ShokoSeries,
  type ShokoSeriesDetail,
} from "../api/client";
import { t } from "../i18n";
import { formatTimestamp } from "../time";

/**
 * What Shoko Server knows, as Shoko says it.
 *
 * AniFlow and Shoko have communicated through folders until now: a file is
 * dropped in, Shoko identifies it and moves it. That needs no credentials and
 * tells AniFlow nothing. With an API key Shoko will also say what it *knows*,
 * and the interesting part is the gap it can see and AniFlow cannot - AniDB
 * lists fourteen episodes, Shoko holds ten.
 *
 * Two different "missing" numbers are shown side by side rather than
 * reconciled into one. Shoko's dashboard counts what it is collecting by its
 * own rules; adding up the per-series gap counts everything AniDB lists,
 * including episodes that have not aired. Both are true answers to different
 * questions, and picking one silently would be a guess about which was meant.
 *
 * The AniDB title list and the My List export used to be reported here. They have a page of their own since
 * build 258 (AnidbPage.tsx): they are about AniDB, and this page is about Shoko Server.
 *
 * One page, four panels, in the order the user asked for (build 258): Library, Import folders, Take everything
 * onto the watchlist, Incomplete series on Shoko. Tabs were tried in 258 and taken out again in 259 - this page's
 * URL section is a Shoko series id (`#/shoko/123`, since build 180), and a tab name there was read as one: every
 * panel but the first answered "unable to parse NaN". The tabs belong to the AniDB page.
 *
 * Build 180: the series are cards like the watchlist's, each opening a page of
 * its own, and the answer is remembered - by the server for ten minutes and by
 * this module for as long as the tab lives - so visiting the page is no longer
 * a walk through Shoko's whole library. Refresh is the way to ask again.
 */

/** The last answer, kept across visits within one tab. */
let remembered: ShokoOverview | null = null;

export function ShokoPage({
  section,
  onSection,
  onOpenAnime,
}: {
  /** A Shoko series id, from the hash (`#/shoko/123`): open its page. */
  section?: string;
  onSection?: (name: string | undefined) => void;
  onOpenAnime?: (animeId: string) => void;
}) {
  const [overview, setOverview] = useState<ShokoOverview | null>(remembered);
  const [loading, setLoading] = useState(remembered === null);
  const [error, setError] = useState<string | null>(null);

  function load(refresh = false) {
    setLoading(true);
    setError(null);
    getShokoOverview(refresh)
      .then((answer) => { remembered = answer; setOverview(answer); })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  // Only when nothing is remembered: coming back to the page costs no request.
  useEffect(() => { if (remembered === null) load(); }, []);

  /* Taking Shoko's library onto the watchlist (build 120). The worker does
     this by itself at startup and every half hour; this is for the moment
     just after adding something to Shoko, when waiting is not the same as
     it working.

     Above the early returns below, and it has to be: build 120 put these two
     hooks *after* `if (loading && !overview) return`, so the first render
     ran four hooks and every later one ran six. React refuses that - the rule
     is every hook, every render, in the same order - and the page died with
     it. Reported as "clicking on the Shoko Server menu nothing happens, its
     like timing out", which is what a crashed component looks like from
     outside: no page, no error, nothing. */
  /* What is on the watchlist is downloaded by AniFlow from now on, so the incomplete list leaves it out
     unless asked (build 243). Kept in this browser. */
  const [hideWatched, setHideWatched] = useState<boolean>(() => {
    try { return window.localStorage.getItem("aniflow.shoko.hideWatched") !== "0"; } catch { return true; }
  });
  function changeHideWatched(next: boolean) {
    setHideWatched(next);
    try { window.localStorage.setItem("aniflow.shoko.hideWatched", next ? "1" : "0"); } catch { /* not kept */ }
  }
  const [adopting, setAdopting] = useState(false);
  const [adoptResult, setAdoptResult] = useState<string | null>(null);
  const [adoptProblems, setAdoptProblems] = useState<string[]>([]);

  async function takeEverything() {
    setAdopting(true);
    setAdoptResult(null);
    setAdoptProblems([]);
    try {
      const { adoptShokoLibrary } = await import("../api/client");
      const answer = await adoptShokoLibrary();
      setAdoptProblems(answer.problems ?? []);
      setAdoptResult(
        answer.detail
          ? answer.detail
          : t("dashboard.shokoTaken")
              .replace("{adopted}", String(answer.adopted))
              .replace("{enriched}", String(answer.enriched)),
      );
    } catch (err) {
      setAdoptResult(err instanceof Error ? err.message : String(err));
    } finally {
      setAdopting(false);
    }
  }

  // A series' own page. Above the loading return for the same hook-order reason.
  if (section) {
    return (
      <ShokoSeriesPage
        shokoId={Number(section)}
        summary={overview?.series.find((row) => String(row.shoko_id) === section) ?? null}
        onBack={() => onSection?.(undefined)}
        onOpenAnime={onOpenAnime}
        onAdded={() => load(true)}
      />
    );
  }

  if (loading && !overview) return <p className="loading">{t("shoko.loading")}</p>;

  return (
    <section className="settings-page">
      {/* No heading of its own: the shell already prints the page name from
          the navigation, and a second one read as a rendering fault. */}
      <div className="status-line" style={{ justifyContent: "space-between" }}>
        <p className="muted" style={{ margin: 0 }}>
          {t("shoko.hint")}
          {overview?.fetched_at && (
            <> {t("shoko.updated").replace("{time}", formatTimestamp(overview.fetched_at) || "—")}</>
          )}
        </p>
        <span className="profile-actions" style={{ margin: 0 }}>
          <button className="button secondary" disabled={loading} onClick={() => load(true)}>
            {loading ? t("shoko.loading") : t("shoko.refresh")}
          </button>
        </span>
      </div>

      {error && <p className="error-message">{error}</p>}

      {overview && !overview.reachable && (
        /* Not an error page: an unconfigured Shoko is the normal state for
           most installs, and the fix is a setting rather than a retry. */
        <div className="detail-panel">
          <h2>{t("shoko.notConnected")}</h2>
          <p className="muted">{overview.detail ?? t("shoko.notConfigured")}</p>
          <div className="profile-actions">
            <a className="button secondary" href="#/settings">{t("shoko.goToSettings")}</a>
          </div>
        </div>
      )}

      {overview?.reachable && (
        <>
          <div className="detail-panel">
            <h2>{t("shoko.library")}</h2>
            <dl className="parsed-grid">
              <dt>{t("shoko.state")}</dt>
              <dd>{overview.state ?? "—"}</dd>
              <dt>{t("shoko.series")}</dt>
              <dd>{overview.stats.SeriesCount ?? "—"}</dd>
              <dt>{t("shoko.files")}</dt>
              <dd>
                {overview.stats.FileCount ?? "—"}
                {typeof overview.stats.FileSize === "number" && (
                  <> · {(overview.stats.FileSize / 1024 ** 3).toFixed(1)} GB</>
                )}
              </dd>
              {/* The two numbers, labelled. See the note at the top. */}
              <dt>{t("shoko.missingShoko")}</dt>
              <dd>{overview.stats.MissingEpisodes ?? "—"}</dd>
              <dt>{t("shoko.missingGap")}</dt>
              <dd>{overview.missing_total}</dd>
              <dt>{t("shoko.unrecognised")}</dt>
              <dd>{overview.stats.UnrecognizedFiles ?? "—"}</dd>
            </dl>
            <p className="muted">{t("shoko.missingExplained")}</p>
          </div>

          <div className="detail-panel">
            <h2>{t("shoko.folders")}</h2>
            {/* Worth showing because AniFlow drops files into one of these:
                if its import root is not Shoko's *source* folder, nothing
                will ever be picked up and nothing will say so. */}
            {overview.import_folders.length === 0 ? (
              <p className="muted">{t("shoko.noFolders")}</p>
            ) : (
              <div className="storage-paths">
                {overview.import_folders.map((folder) => (
                  <div className="storage-path" key={String(folder.id)}>
                    <span className="storage-path-name">{String(folder.name ?? folder.id)}</span>
                    <code className="storage-path-host">{String(folder.path ?? "—")}</code>
                    <small className="muted storage-path-mount">{String(folder.drop_type ?? "—")}</small>
                    <span className={folder.watching ? "imported-tag" : "reasons"}>
                      {folder.watching ? t("shoko.watching") : t("shoko.notWatching")}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Moved here from the watchlist (build 120): it is a thing you do
              to the two libraries, and this is the page about them. The
              button sits under the title (build 180). */}
          <div className="detail-panel">
            <h2>{t("shoko.adoptTitle")}</h2>
            <button
              className="button secondary"
              disabled={adopting}
              onClick={() => void takeEverything()}
            >
              {adopting ? t("shoko.adopting") : t("dashboard.takeFromShoko")}
            </button>
            <p className="muted">{t("shoko.adoptHint")}</p>
            {adoptResult && <p className="muted">{adoptResult}</p>}
            {adoptProblems.length > 0 && (
              <>
                <p className="muted">{t("shoko.adoptProblems")}</p>
                <ul className="adopt-problems reasons">
                  {adoptProblems.map((line) => <li key={line}>{line}</li>)}
                </ul>
              </>
            )}
          </div>

          <div className="detail-panel">
            <h2>{t("shoko.seriesTitle")}</h2>
            <label className="check" style={{ margin: "4px 0 10px" }}>
              <input type="checkbox" checked={hideWatched} onChange={(event) => changeHideWatched(event.target.checked)} />
              <span>{t("shoko.hideWatched")}</span>
            </label>
            {/* Only what is incomplete (build 180): everything Shoko holds is
                on the watchlist anyway, where the complete ones already are. */}
            {overview.series.length === 0 ? (
              <p className="muted">{t("shoko.noSeries")}</p>
            ) : overview.series.every((series) => series.missing_count === 0) ? (
              <p className="muted">{t("shoko.allComplete")}</p>
            ) : overview.series.every((series) => series.missing_count === 0 || (hideWatched && series.watchlist_id)) ? (
              <p className="muted">{t("shoko.allWatched")}</p>
            ) : (
              <div className="shoko-grid">
                {overview.series.filter((series) => series.missing_count > 0 && !(hideWatched && series.watchlist_id)).map((series) => (
                  <ShokoSeriesCard
                    key={series.shoko_id}
                    series={series}
                    onOpen={() => onSection?.(String(series.shoko_id))}
                  />
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}

/** A series' picture: Shoko's own, then AniFlow's, then its first letter. */
function SeriesPicture({ shokoId, cover, name }: { shokoId: number; cover: string | null; name: string }) {
  // 0 = Shoko's poster, 1 = AniFlow's cover, 2 = nothing worked.
  const [stage, setStage] = useState(0);
  const source = stage === 0 ? shokoPosterUrl(shokoId) : stage === 1 ? providerCoverUrl(cover) : null;
  if (!source) return <div className="cover-placeholder">{name.slice(0, 1)}</div>;
  return (
    <img
      className="cover"
      src={source}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setStage((current) => (current === 0 && cover ? 1 : 2))}
    />
  );
}

/**
 * One series as a card, like the watchlist's (build 180).
 *
 * Same classes as the watchlist card on purpose - one card language - with
 * the numbers Shoko gives instead of the download state: what it holds of
 * what AniDB lists, what is missing, and the ids that tie it to the other
 * databases.
 */
function ShokoSeriesCard({ series, onOpen }: { series: ShokoSeries; onOpen: () => void }) {
  const ids = [
    series.anidb_id ? `AniDB ${series.anidb_id}` : null,
    series.mal_ids.length ? `MAL ${series.mal_ids.join(", ")}` : null,
  ].filter(Boolean).join(" · ");
  return (
    <article
      className="anime-card shoko-card"
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
    >
      <span className="anime-poster">
        <SeriesPicture shokoId={series.shoko_id} cover={series.cover_url} name={series.name} />
        <span className="calendar-card-text anime-card-text">
          <strong title={series.name}>{series.name}</strong>
        </span>
      </span>
      <p className="anime-meta">
        <span className={series.missing_count > 0 ? "shoko-card-gap" : "shoko-card-full"}>
          {series.local_episodes} / {series.total_episodes}
          {series.missing_count > 0
            ? ` · ${t("shoko.missingCount").replace("{n}", String(series.missing_count))}`
            : ` · ${t("shoko.complete")}`}
        </span>
      </p>
      {ids && <small className="muted shoko-card-ids">{ids}</small>}
      {series.episode_error && <small className="reasons shoko-card-ids">{series.episode_error}</small>}
    </article>
  );
}

/** How big a file is, in the unit a person reads. */
function sizeText(bytes: number | null): string {
  if (!bytes) return "";
  return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
}

/**
 * One series in full (build 180): the picture, the numbers, and every episode
 * AniDB lists - which of them Shoko holds, what the file is called, how big it
 * is and at what quality.
 *
 * Its own request, made when it is opened and remembered by the server like
 * the overview. The header is drawn from what the list already knew, so the
 * page is not empty while the episodes arrive.
 */
function ShokoSeriesPage({
  shokoId,
  summary,
  onBack,
  onOpenAnime,
  onAdded,
}: {
  shokoId: number;
  summary: ShokoSeries | null;
  onBack: () => void;
  onOpenAnime?: (animeId: string) => void;
  onAdded?: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [addedId, setAddedId] = useState<string | null>(null);
  const [addMessage, setAddMessage] = useState<string | null>(null);

  async function addToWatchlist() {
    setAdding(true);
    setAddMessage(null);
    try {
      const answer = await addShokoSeriesToWatchlist(shokoId);
      if (answer.added && answer.anime_id) {
        setAddedId(answer.anime_id);
        onAdded?.();
      } else {
        setAddMessage(answer.detail ?? t("shoko.addFailed"));
      }
    } catch (err) {
      setAddMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setAdding(false);
    }
  }

  const [detail, setDetail] = useState<ShokoSeriesDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  function load(refresh = false) {
    setLoading(true);
    setError(null);
    getShokoSeries(shokoId, refresh)
      .then(setDetail)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(() => { load(); }, [shokoId]);

  const name = detail?.name ?? summary?.name ?? "";
  const local = detail?.local_episodes ?? summary?.local_episodes ?? 0;
  const total = detail?.total_episodes ?? summary?.total_episodes ?? 0;
  const anidb = detail?.anidb_id ?? summary?.anidb_id ?? null;
  const mal = detail?.mal_ids ?? summary?.mal_ids ?? [];
  const cover = detail?.cover_url ?? summary?.cover_url ?? null;
  const watchlistId = addedId ?? detail?.watchlist_id ?? summary?.watchlist_id ?? null;

  return (
    <section className="detail">
      <button className="back-link" onClick={onBack}>← {t("shoko.back")}</button>
      <div className="detail-hero">
        <div className="shoko-detail-poster">
          <SeriesPicture shokoId={shokoId} cover={cover} name={name || "?"} />
        </div>
        <div>
          <p className="eyebrow">{t("shoko.title")}</p>
          <h1>{name}</h1>
          <p>
            {local} / {total}
            {total > local ? ` · ${t("shoko.missingCount").replace("{n}", String(total - local))}` : ` · ${t("shoko.complete")}`}
          </p>
          <p className="muted">
            {[anidb ? `AniDB ${anidb}` : null, mal.length ? `MAL ${mal.join(", ")}` : null]
              .filter(Boolean).join(" · ")}
          </p>
          <div className="profile-actions">
            {watchlistId ? (
              <button className="button primary" onClick={() => onOpenAnime?.(watchlistId)}>
                {total > local ? t("shoko.findMissing") : t("shoko.openWatchlist")}
              </button>
            ) : (
              <>
                <button className="button primary" disabled={adding} onClick={() => void addToWatchlist()}>
                  {adding ? t("shoko.adding") : t("shoko.addToWatchlist")}
                </button>
                <small className="muted">{t("shoko.notOnWatchlist")}</small>
              </>
            )}
            <button className="button secondary" disabled={loading} onClick={() => load(true)}>
              {loading ? t("shoko.loading") : t("shoko.refresh")}
            </button>
          </div>
        </div>
      </div>

      {error && <p className="error-message">{error}</p>}
      {addMessage && <p className="error-message">{addMessage}</p>}
      {detail?.error && <p className="error-message">{detail.error}</p>}

      <div className="detail-panel">
        <h2>{t("shoko.episodes")}</h2>
        {/* Missing episodes are fetched with Choose release on the watchlist
            entry, which already downloads a full list or just the gaps. */}
        {total > local && watchlistId && <p className="muted">{t("shoko.findMissingHint")}</p>}
        {loading && !detail ? (
          <p className="loading">{t("shoko.loading")}</p>
        ) : detail && detail.episodes.length === 0 ? (
          <p className="muted">{t("shoko.noEpisodes")}</p>
        ) : (
          <ul className="shoko-episodes">
            {detail?.episodes.map((episode, index) => (
              <li key={episode.number ?? `x${index}`} className={episode.missing ? "missing" : "held"}>
                <span className="shoko-episode">{episode.number !== null ? `#${episode.number}` : "#?"}</span>
                <span className="shoko-episode-title">
                  <strong>{episode.title ?? "—"}</strong>
                  {episode.files.map((file) => (
                    <small key={file.name} className="muted shoko-episode-file" title={file.name}>
                      {[file.name, file.height ? `${file.height}p` : null, sizeText(file.size)]
                        .filter(Boolean).join(" · ")}
                    </small>
                  ))}
                </span>
                <small className="muted">{episode.air_date ?? ""}</small>
                <span className={episode.missing ? "reasons" : "imported-tag"}>
                  {episode.missing ? t("shoko.episodeMissing") : t("shoko.episodeHeld")}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { useLockedPageScroll } from "../useLockedPageScroll";
import {
  clearReleases,
  createSource,
  deleteSource,
  getRssStatus,
  listReleases,
  listSources,
  triggerScan,
  updateSource,
  type FeedSource,
  type ReleaseItem,
  type RssStatus,
} from "../api/client";
import { t } from "../i18n";
import { SubgroupCoverageDialog } from "./SubgroupCoverageDialog";
import { matchFailureText } from "../reasons";
import { formatTime } from "../time";

/** Watchlist hits only, and only the two decisions worth reading (169):
 *  *"It should only show MATCH and REJECTED items in the list."*  The
 *  untracked remainder - most of a season feed - is not a filter option here
 *  any more; the per-feed counts in "More info" already answer "is this feed
 *  finding anything at all" without it. */
const SHOWN_DECISIONS = "MATCHED,REJECTED";

/** Same cap as the In Progress Archive, and the same reason: a discovery log
 *  the size of "everything a feed ever offered" is not a list, it is a
 *  database with a scrollbar. */
const RELEASE_LIMIT = 150;

function sortSources(sources: FeedSource[]): FeedSource[] {
  return [...sources].sort((a, b) => Number(b.is_default) - Number(a.is_default) || a.name.localeCompare(b.name));
}

function SourceDialog({
  initial,
  onClose,
  onSaved,
}: {
  initial: FeedSource | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [url, setUrl] = useState(initial?.url ?? "");
  const [isDefault, setIsDefault] = useState(initial?.is_default ?? false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(false);

  async function save() {
    if (!name.trim() || !url.trim()) return;
    setSaving(true);
    setError(false);
    try {
      if (initial) await updateSource(initial.id, { name: name.trim(), url: url.trim(), is_default: isDefault });
      else await createSource(name.trim(), url.trim());
      // if isDefault was set on create, need to patch it (createSource doesn't accept is_default)
      if (!initial && isDefault) {
        // find created by listing? simpler: update after create via name lookup
        const all = await listSources();
        const created = all.find((s) => s.url === url.trim());
        if (created) await updateSource(created.id, { is_default: true });
      }
      onSaved();
      onClose();
    } catch {
      setError(true);
    } finally {
      setSaving(false);
    }
  }

  useLockedPageScroll();

  return (
    <div className="dialog-backdrop" role="presentation">
      <section className="dialog" role="dialog" aria-modal="true" aria-label={t("rss.sources.title")}>
        <header>
          <h2>{initial ? t("profiles.editTitle") : t("profiles.newTitle")}</h2>
          <button className="close" onClick={onClose} aria-label={t("add.cancel")}>×</button>
        </header>
        <div className="settings-grid two" style={{ marginTop: 16 }}>
          <label className="field">
            <span>{t("rss.sources.namePlaceholder")}</span>
            <input value={name} autoFocus onChange={(e) => setName(e.target.value)} placeholder={t("rss.sources.namePlaceholder")} />
          </label>
          <label className="field">
            <span>{t("rss.sources.urlPlaceholder")}</span>
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder={t("rss.sources.urlPlaceholder")} />
          </label>
        </div>
        <label className="check" style={{ marginTop: 14 }}>
          <input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} />
          <span>{t("rss.sources.defaultSource")}</span>
        </label>
        {error && <p className="error-message">{t("profiles.error")}</p>}
        <div className="dialog-actions">
          <button className="button secondary" onClick={onClose}>{t("add.cancel")}</button>
          <button className="button primary" disabled={saving || !name.trim() || !url.trim()} onClick={() => void save()}>
            {saving ? t("settings.saving") : t("settings.save")}
          </button>
        </div>
      </section>
    </div>
  );
}

/** One row of the discovery history.
 *
 * Watchlist hits get the full detail line; the untracked remainder - which is
 * most of a season feed - collapses to a name and the reason it was skipped.
 */
function ReleaseRow({ release }: { release: ReleaseItem }) {
  // Was the raw codes - "subgroup_mismatch, resolution_mismatch" - which
  // are the right thing in a column and the wrong thing on a page.
  const reasons = release.failure_reasons.map(matchFailureText).join(", ");

  if (release.anime_name === null) {
    return (
      <li className="release-row compact">
        <span className={`chip ${release.decision.toLowerCase()}`}>{t(`rss.decision.${release.decision}`)}</span>
        <strong className="release-name" title={release.raw_name}>
          {release.parsed_title ?? release.raw_name}
        </strong>
        <small className="muted">
          {[release.source_name, reasons || t(`rss.decision.${release.decision}`)]
            .filter(Boolean)
            .join(" · ")}
        </small>
      </li>
    );
  }

  return (
    <li className="release-row">
      <span className={`chip ${release.decision.toLowerCase()}`}>{t(`rss.decision.${release.decision}`)}</span>
      <div className="release-main">
        <strong title={release.raw_name}>{release.parsed_title ?? release.raw_name}</strong>
        <small className="muted">
          {[
            release.anime_name,
            release.episode !== null ? `${t("profile.episode")} ${release.episode}` : null,
            release.subgroup,
            release.resolution,
            `v${release.version}`,
            release.source_name,
          ]
            .filter(Boolean)
            .join(" · ")}
        </small>
        {reasons && <small className="reasons">{reasons}</small>}
      </div>
      <small className="muted">{formatTime(release.created_at)}</small>
    </li>
  );
}

export function RssSourcesPage({ onOpenIndexer }: {
  /** Jump to the indexer that owns a managed feed. */
  onOpenIndexer?: (indexerId: string) => void;
} = {}) {
  const [sources, setSources] = useState<FeedSource[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);
  const [editing, setEditing] = useState<FeedSource | null>(null);
  const [creating, setCreating] = useState(false);

  const [status, setStatus] = useState<RssStatus | null>(null);
  const [releases, setReleases] = useState<ReleaseItem[]>([]);
  const [releasesOpen, setReleasesOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [subgroupsOpen, setSubgroupsOpen] = useState(false);
  const [scanning, setScanning] = useState(false);
  const pollRef = useRef<number | null>(null);

  const loadReleases = useCallback(async () => {
    try {
      setReleases(await listReleases(RELEASE_LIMIT, true, SHOWN_DECISIONS));
    } catch {
      setError(true);
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const [sourceList, rssStatus] = await Promise.all([listSources(), getRssStatus()]);
      setSources(sortSources(sourceList));
      setStatus(rssStatus);
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
    void loadReleases();
    return () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current);
    };
  }, [load, loadReleases]);

  function startPolling() {
    if (pollRef.current !== null) return;
    pollRef.current = window.setInterval(async () => {
      try {
        const current = await getRssStatus();
        setStatus(current);
        if (!current.scanning && !current.scan_requested) {
          if (pollRef.current !== null) window.clearInterval(pollRef.current);
          pollRef.current = null;
          await load();
          await loadReleases();
        }
      } catch {
        /* keep polling; transient API hiccups are fine */
      }
    }, 2000);
  }

  async function runScan() {
    setScanning(true);
    try {
      const result = await triggerScan();
      if (result.started) {
        await getRssStatus().then(setStatus).catch(() => undefined);
        startPolling();
      }
    } catch {
      setError(true);
    } finally {
      setScanning(false);
    }
  }

  /** Tidy the list. The rows stay behind as the scan's de-duplication memory,
   *  so nothing is re-reported as a new find on the next scan. */
  async function clearAllReleases() {
    if (!window.confirm(t("rss.releases.clearConfirm").replace("{n}", String(releases.length)))) return;
    try {
      await clearReleases();
      await loadReleases();
      await load();
    } catch {
      setError(true);
    }
  }

  async function markDefault(source: FeedSource) {
    try {
      await updateSource(source.id, { is_default: true });
      await load();
    } catch {
      setError(true);
    }
  }

  async function remove(source: FeedSource) {
    if (!window.confirm(t("rss.deleteConfirm"))) return;
    try {
      await deleteSource(source.id);
      await load();
      await loadReleases();
    } catch {
      setError(true);
    }
  }

  const scanPending = status?.scanning || status?.scan_requested || false;
  const enabledSources = sources.filter((source) => source.enabled).length;

  return (
    <section className="profiles-page">
      <div className="rss-status detail-panel">
        <div className="status-line">
          <span className={`health-dot ${status?.worker_running ? "healthy" : ""}`} />
          <strong>{t("rss.status.title")}</strong>
          <span className="muted">
            {status
              ? [
                  status.enabled
                    ? `${t("rss.status.every")} ${status.interval_minutes} ${t("rss.status.minutes")}`
                    : t("rss.status.paused"),
                  // The feed count belongs here rather than in a sentence of
                  // its own: it is one fact, not an explanation.
                  enabledSources === 0
                    ? t("rss.status.noFeeds")
                    : t("rss.status.feedsActive").replace("{n}", String(enabledSources)),
                  `${t("rss.status.lastScan")}: ${formatTime(status.last_scan_at) || t("rss.status.never")}`,
                  `${t("rss.status.nextScan")}: ${formatTime(status.next_scan_at) || "—"}`,
                ].join(" · ")
              : ""}
          </span>
          <button className="button secondary" onClick={() => setInfoOpen(true)}>
            {t("rss.status.moreInfo")}
          </button>
          <button className="button primary" disabled={scanning || scanPending || enabledSources === 0} onClick={() => void runScan()}>
            {scanPending ? t("rss.scanning") : t("rss.scanNow")}
          </button>
        </div>
        {status?.last_scan_error && <p className="error-message">{t("rss.status.error")}: {status.last_scan_error}</p>}
      </div>

      {!loaded ? null : (
        <>
          <div className="profiles-intro">
            <p className="muted" style={{ margin: 0 }}>{t("rss.sources.hint")}</p>
            <button className="button primary" onClick={() => setCreating(true)}>{t("profiles.new")}</button>
          </div>
          {error && <p className="error-message">{t("activity.error")}</p>}
          {sources.length === 0 ? (
            <section className="empty-state">
              <h2>{t("rss.sources.title")}</h2>
              <p>{t("rss.sources.empty")}</p>
            </section>
          ) : (
            <div className="template-grid">
              {sources.map((source) => (
                <article className="template-card" key={source.id}>
                  <strong>
                    {source.name}
                    {source.is_default && <span className="default-badge">{t("rss.isDefault")}</span>}
                  </strong>
                  <small style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{source.url}</small>
                  <small className="muted">
                    {[
                      source.enabled ? t("rss.sources.enabled") : t("rss.sources.disabled"),
                      `${source.release_count} ${t("rss.sources.releases")}`,
                      source.last_release_at
                        ? `${t("rss.sources.lastRelease")}: ${formatTime(source.last_release_at)}`
                        : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </small>
                  <div className="action-stack horizontal">
                    {!source.is_default && (
                      <button className="button secondary" onClick={() => void markDefault(source)}>{t("rss.setDefault")}</button>
                    )}
                    {source.indexer_id ? (
                      /* Owned by an indexer: editing or deleting it here would
                         let two descriptions of one site drift apart, so the
                         only action is to go to the thing that owns it. */
                      <button
                        className="button accent"
                        title={t("rss.sources.managedHint").replace("{name}", source.indexer_name ?? "")}
                        onClick={() => onOpenIndexer?.(source.indexer_id as string)}
                      >
                        {t("rss.sources.fromIndexer")}
                      </button>
                    ) : <>
                      <button className="button secondary" onClick={() => setEditing(source)}>{t("profiles.edit")}</button>
                      <button className="button danger" onClick={() => void remove(source)}>{t("rss.sources.delete")}</button>
                    </>}
                  </div>
                </article>
              ))}
            </div>
          )}
        </>
      )}

      {/* Its own bubble rather than a list on the page (169): *"It should
          only show MATCH and REJECTED items in the list and should behave
          like the In Progress Archive. So the stuff goes directly into the
          Archive ... User needs to press a button to pop up the window to
          see them."*  The count still rides along, same as In Progress's own
          Archive button. */}
      <div className="detail-panel">
        <button className="button secondary" onClick={() => setReleasesOpen(true)}>
          {t("rss.releases.open")} ({releases.length})
        </button>
        {/* Which subgroups you use, and whether any feed carries them (build 193). */}
        <button className="button secondary" style={{ marginLeft: 10 }} onClick={() => setSubgroupsOpen(true)}>
          {t("rss.subgroups.open")}
        </button>
      </div>

      {subgroupsOpen && <SubgroupCoverageDialog onClose={() => setSubgroupsOpen(false)} />}

      {releasesOpen && (
        <div className="dialog-backdrop" role="presentation" onClick={() => setReleasesOpen(false)}>
          <section
            className="dialog dialog-wide"
            role="dialog"
            aria-modal="true"
            aria-label={t("rss.releases.title")}
            onClick={(event) => event.stopPropagation()}
          >
            <header>
              <h2>{t("rss.releases.title")}</h2>
              <button className="close" onClick={() => setReleasesOpen(false)} aria-label={t("add.cancel")}>×</button>
            </header>
            <div className="status-line" style={{ marginTop: 8 }}>
              <p className="muted" style={{ margin: 0 }}>{t("rss.releases.hint")}</p>
              {releases.length > 0 && (
                /* Green: clearing this list cannot lose anything - the rows
                   stay as the scan's memory and pending downloads keep their
                   place. */
                <button className="button success" onClick={() => void clearAllReleases()} title={t("rss.releases.clearHint")}>
                  {t("rss.releases.clearAll").replace("{n}", String(releases.length))}
                </button>
              )}
            </div>
            <div className="modal-scroll">
              {releases.length === 0 ? (
                <p className="muted">{t("rss.releases.empty")}</p>
              ) : (
                <ul className="release-list">
                  {releases.map((release) => (
                    <ReleaseRow key={release.id} release={release} />
                  ))}
                </ul>
              )}
            </div>
          </section>
        </div>
      )}

      {infoOpen && (
        <div className="dialog-backdrop" role="presentation" onClick={() => setInfoOpen(false)}>
          <section
            className="dialog dialog-wide"
            role="dialog"
            aria-modal="true"
            aria-label={t("rss.info.title")}
            onClick={(event) => event.stopPropagation()}
          >
            <header>
              <h2>{t("rss.info.title")}</h2>
              <button className="close" onClick={() => setInfoOpen(false)} aria-label={t("add.cancel")}>×</button>
            </header>
            <div className="modal-scroll">
              <p className="muted">{t("rss.info.hint")}</p>
              <dl className="info-grid">
                <dt>{t("rss.info.schedule")}</dt>
                <dd>
                  {status?.enabled
                    ? `${t("rss.status.every")} ${status.interval_minutes} ${t("rss.status.minutes")}`
                    : t("rss.status.paused")}
                </dd>
                <dt>{t("rss.status.lastScan")}</dt>
                <dd>{formatTime(status?.last_scan_at ?? null) || t("rss.status.never")}</dd>
                <dt>{t("rss.status.nextScan")}</dt>
                <dd>{formatTime(status?.next_scan_at ?? null) || "—"}</dd>
                <dt>{t("rss.info.worker")}</dt>
                <dd>{status?.worker_running ? t("rss.info.workerOn") : t("rss.info.workerOff")}</dd>
                {status?.last_scan_error && (
                  <>
                    <dt>{t("rss.status.error")}</dt>
                    <dd className="error-message" style={{ margin: 0 }}>{status.last_scan_error}</dd>
                  </>
                )}
              </dl>

              <h3 style={{ margin: "18px 0 8px" }}>{t("rss.info.perFeed")}</h3>
              <p className="muted" style={{ marginTop: 0 }}>{t("rss.info.perFeedHint")}</p>
              <ul className="release-list">
                {sources.map((source) => (
                  <li key={source.id} className="release-row">
                    {/* A feed that discovers plenty and matches nothing is the
                        case worth spotting, so the two counts stay separate. */}
                    <span className={`chip ${source.matched_count > 0 ? "matched" : source.release_count > 0 ? "ignored" : "rejected"}`}>
                      {source.matched_count > 0
                        ? t("rss.info.working")
                        : source.release_count > 0
                          ? t("rss.info.noMatches")
                          : t("rss.info.nothingYet")}
                    </span>
                    <div className="release-main">
                      <strong>
                        {source.name}
                        {source.is_default && <span className="default-badge">{t("rss.isDefault")}</span>}
                      </strong>
                      <small className="muted" style={{ wordBreak: "break-all" }}>{source.url}</small>
                      <small className="muted">
                        {[
                          source.enabled ? t("rss.sources.enabled") : t("rss.sources.disabled"),
                          t("rss.info.discoveries").replace("{n}", String(source.release_count)),
                          t("rss.info.matches").replace("{n}", String(source.matched_count)),
                        ].join(" · ")}
                      </small>
                      <small className="muted">
                        {[
                          `${t("rss.sources.lastRelease")}: ${formatTime(source.last_release_at) || t("rss.status.never")}`,
                          `${t("rss.info.lastMatch")}: ${formatTime(source.last_match_at) || t("rss.status.never")}`,
                        ].join(" · ")}
                      </small>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </section>
        </div>
      )}

      {(creating || editing) && (
        <SourceDialog initial={editing} onClose={() => { setCreating(false); setEditing(null); }} onSaved={() => void load()} />
      )}
    </section>
  );
}

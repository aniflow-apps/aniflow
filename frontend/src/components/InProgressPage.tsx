/**
 * Where every currently-moving episode actually is (build 164).
 *
 * The reason this exists: *"the download page is very useful for like 1min
 * ... for the rest it is very useless. Activity spams with 100s of issues ...
 * Attention seems to be the most useful one, but its sometimes stuck in semi
 * attentions which are already fixed but not removed."*
 *
 * One card per episode instead of three separate vocabularies - the same
 * state the watchlist reads (`episode.view`, services/episode_view.py),
 * updated in place rather than appended to. Collapsed to a line until asked,
 * the same shape AttentionPage settled on for the same reason: a list of
 * things should read as a list.
 *
 * The fix buttons are not new - they are the Attention page's own actions
 * (services/attention.py:actions_for), surfaced here for whichever record
 * this episode already has open, so a working button appears in both places
 * for the same underlying record rather than being built twice.
 */
import { useCallback, useEffect, useState } from "react";
import {
  approveNewVersion,
  deleteDownloadedFile,
  deleteNewerFile,
  deleteOlderFile,
  dismissProgress,
  listProgress,
  listProgressArchive,
  moveFileNow,
  replaceLibraryFile,
  retryDownloadNow,
  runShokoImport,
  type AnimeSummary,
  type ProgressItem,
} from "../api/client";
import { remedyFor, resolvesItself } from "../attentionRemedy";
import { t } from "../i18n";
import { ShokoOpenLink } from "./ShokoHelpButtons";
import { stateClass } from "../progressState";
import { qbStateText, reasonText } from "../reasons";
import { formatTime } from "../time";
import { ArchiveDialog } from "./ArchiveDialog";
import { NoClientNotice } from "./NoClientNotice";

const POLL_INTERVAL_MS = 5000;

/**
 * States nothing more is going to happen to on its own - safe to acknowledge
 * without hiding a problem that is still live. A stuck download (missing,
 * stalled, waiting on Shoko) is deliberately *not* dismissible here: this
 * page exists so that does not quietly disappear.
 */
const DISMISSIBLE_STATES = new Set(["complete", "failed", "file_gone", "new_version"]);

/** "downloading" carries the client's own word; a state with more to say
 *  otherwise carries one of services/reasons.py's codes. */
function detailText(state: string, detail: string | null): string | null {
  if (!detail) return null;
  return state === "downloading" ? qbStateText(detail) : reasonText(detail);
}

/**
 * Whether this row has one of the real buttons below, as opposed to an
 * attention whose only action is its own Dismiss - which this page does not
 * expose (it means "close the record", not "I have seen this"; the two read
 * the same for most kinds, but not for a self-healing one like a missing
 * torrent, where dismissing the record would not stop it recurring).
 */
const REAL_ACTIONS = ["retry_now", "move_now", "delete_download", "replace_library", "approve", "delete_older", "delete_newer"];
function hasRealAction(item: ProgressItem): boolean {
  return item.actions.some((action) => REAL_ACTIONS.includes(action));
}

/**
 * One "X all" button per action actually offered right now (build 166).
 *
 * Asked for directly: *"If something appears on the in progress page, which
 * has a button ... can you enable a 'retry all' button ... If there are
 * multiple option, enable multiple buttons."*  So this is a small table
 * rather than one hard-coded button: whichever of these a currently-visible
 * card offers gets a matching bulk button in the header, and one nobody
 * currently needs simply does not appear.
 *
 * Runs each item's own single-item action, one by one - the same endpoints
 * the per-card buttons already call, just looped, so a bulk press can never
 * do anything a person pressing every button by hand could not have.
 */
interface BulkAction {
  id: string;
  labelKey: string;
  match: (item: ProgressItem) => boolean;
  run: (item: ProgressItem) => Promise<unknown>;
  /** Shown as a window.confirm before running, {n} replaced with the count -
   *  only the two destructive ones need this; the per-card button already
   *  confirms replace_library the same way. */
  confirmKey?: string;
}

const BULK_ACTIONS: BulkAction[] = [
  {
    id: "retry_now", labelKey: "progress.retryAll",
    match: (item) => item.actions.includes("retry_now") && Boolean(item.attention_id),
    run: (item) => retryDownloadNow(item.attention_id!),
  },
  {
    id: "move_now", labelKey: "progress.moveAll",
    match: (item) => item.actions.includes("move_now") && Boolean(item.attention_id),
    run: (item) => moveFileNow(item.attention_id!),
  },
  {
    id: "approve", labelKey: "progress.approveAll",
    match: (item) => item.actions.includes("approve") && Boolean(item.attention_id),
    run: (item) => approveNewVersion(item.attention_id!),
  },
  {
    id: "delete_download", labelKey: "progress.deleteAll",
    match: (item) => item.actions.includes("delete_download") && Boolean(item.attention_id),
    run: (item) => deleteDownloadedFile(item.attention_id!),
    confirmKey: "progress.deleteAllConfirm",
  },
  {
    id: "replace_library", labelKey: "progress.replaceAll",
    match: (item) => item.actions.includes("replace_library") && Boolean(item.attention_id),
    run: (item) => replaceLibraryFile(item.attention_id!),
    confirmKey: "progress.replaceAllConfirm",
  },
  {
    id: "archive", labelKey: "progress.archiveAll",
    match: (item) => DISMISSIBLE_STATES.has(item.state),
    run: (item) => dismissProgress(item.episode_id),
  },
];

/**
 * Whether Shoko Server is what an item is waiting on (build 178).
 *
 * Shoko files what AniFlow hands over on its own schedule, which can be a
 * long time apart, and a Run Import is what usually clears a file that is
 * waiting or "not found". So the button appears when something is in that
 * position - and not otherwise, since it does nothing for a download.
 */
function waitsOnShoko(item: ProgressItem): boolean {
  return item.state === "with_shoko"
    || item.state === "moving_to_shoko"
    || Boolean(item.detail?.startsWith("shoko_"));
}

export function InProgressPage({ anime = [], onOpenAnime }: {
  /** The watchlist, to know whether an anime is set to Automate (build 212). */
  anime?: AnimeSummary[];
  /** Go to an anime's page: the place every fix for an episode ends up. */
  onOpenAnime?: (animeId: string) => void;
}) {
  const [items, setItems] = useState<ProgressItem[]>([]);
  const [archivedCount, setArchivedCount] = useState<number | null>(null);
  const [error, setError] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [importNote, setImportNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [importing, setImporting] = useState(false);

  const load = useCallback(async () => {
    try {
      setItems(await listProgress());
      setError(false);
    } catch {
      setError(true);
    }
    // Its own try: the archive count is a nice-to-have on this page, and a
    // failure here must not blank out the list above it.
    try {
      setArchivedCount((await listProgressArchive()).length);
    } catch {
      /* leave the previous count showing rather than replace it with an error */
    }
  }, []);

  useEffect(() => {
    void load();
    let timer: number | null = null;
    const start = () => {
      if (timer === null) timer = window.setInterval(() => void load(), POLL_INTERVAL_MS);
    };
    const stop = () => {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    };
    // A background tab polls for nobody - same reasoning as the Downloads
    // page this is replacing.
    const onVisibilityChange = () => {
      if (document.hidden) stop();
      else {
        void load();
        start();
      }
    };
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [load]);

  function toggle(id: string) {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function act(episodeId: string, action: () => Promise<unknown>) {
    setBusyId(episodeId);
    setError(false);
    try {
      await action();
      await load();
    } catch {
      setError(true);
    } finally {
      setBusyId(null);
    }
  }

  async function runBulk(bulkAction: BulkAction, matching: ProgressItem[]) {
    if (bulkAction.confirmKey) {
      const message = t(bulkAction.confirmKey).replace("{n}", String(matching.length));
      if (!window.confirm(message)) return;
    }
    setBulkBusy(bulkAction.id);
    setError(false);
    try {
      // One at a time and each failure caught on its own: a duplicate for
      // episode 4 must not stop episode 7 from being retried, and the reload
      // at the end shows honestly whatever is left afterwards either way.
      for (const item of matching) {
        await bulkAction.run(item).catch(() => undefined);
      }
      await load();
    } finally {
      setBulkBusy(null);
    }
  }

  async function runImport() {
    setImporting(true);
    setImportNote(null);
    try {
      const answer = await runShokoImport();
      setImportNote(answer.started
        ? { ok: true, text: t("progress.importStarted") }
        : { ok: false, text: answer.detail ?? t("progress.importFailed") });
    } catch (err) {
      setImportNote({ ok: false, text: err instanceof Error ? err.message : t("progress.importFailed") });
    } finally {
      setImporting(false);
    }
  }

  return (
    <section className="activity-page">
      <div className="detail-panel">
        <NoClientNotice />
        <div className="status-line">
          <h2>{t("progress.title")}</h2>
          {items.some(waitsOnShoko) && (
            <button
              className="button primary"
              disabled={importing}
              title={t("progress.runImportHint")}
              onClick={() => void runImport()}
            >
              {importing ? t("progress.importing") : t("progress.runImport")}
            </button>
          )}
          {items.some(waitsOnShoko) && <ShokoOpenLink />}
          {BULK_ACTIONS.map((bulkAction) => {
            const matching = items.filter(bulkAction.match);
            if (matching.length < 2) return null; // one is what the card's own button is for
            return (
              <button
                key={bulkAction.id}
                className="button secondary"
                disabled={bulkBusy !== null}
                onClick={() => void runBulk(bulkAction, matching)}
              >
                {t(bulkAction.labelKey).replace("{n}", String(matching.length))}
              </button>
            );
          })}
        </div>
        <p className="muted" style={{ margin: "10px 0 16px" }}>{t("progress.hint")}</p>
        {importNote && (
          <p className={importNote.ok ? "muted" : "error-message"} style={{ margin: "0 0 16px" }}>{importNote.text}</p>
        )}
        {error && <p className="error-message">{t("activity.error")}</p>}
        {items.length === 0 ? (
          <p className="muted">{t("progress.empty")}</p>
        ) : (
          <ul className="attention-list">
            {items.map((item) => {
              const busy = busyId === item.episode_id;
              const detail = detailText(item.state, item.detail);
              const isOpen = open.has(item.episode_id);
              return (
                <li key={item.episode_id} className={`attention-row ${stateClass(item.state)}`}>
                  <span className={`chip ${stateClass(item.state)}`}>{t(`episode.state.${item.state}`)}</span>
                  <div className="attention-main">
                    <strong title={item.anime_name}>
                      {item.anime_name} — {t("profile.episode")} {item.episode_number}
                    </strong>
                    {detail && <small className="muted" title={detail}>{detail}</small>}
                    {item.progress !== null && (
                      <progress max={1} value={item.progress} style={{ width: "100%", maxWidth: 320, height: 8 }} />
                    )}
                  </div>
                  <div className="attention-aside">
                    {item.since && (
                      <small className="muted">{t("progress.since")} {formatTime(item.since)}</small>
                    )}
                    {onOpenAnime && (
                      <button className="button secondary attention-details-toggle" onClick={() => onOpenAnime(item.anime_id)}>
                        {t("progress.openAnime")}
                      </button>
                    )}
                    <button
                      className="button secondary attention-details-toggle"
                      aria-expanded={isOpen}
                      onClick={() => toggle(item.episode_id)}
                    >
                      {isOpen ? t("attention.hideDetails") : t("attention.details")}
                    </button>
                  </div>

                  {isOpen && (
                    <div className="attention-body">
                      {/* The same facts the Attention page's own Details
                          section shows for this record - episode, release
                          name, the two files a duplicate is about, when it
                          was noticed. */}
                      <dl className="attention-facts">
                        <dt>{t("profile.episode")}</dt>
                        <dd>{item.episode_number}</dd>
                        {item.release_label && (
                          <>
                            <dt>{t("attention.releaseName")}</dt>
                            <dd><code>{item.release_label}</code></dd>
                          </>
                        )}
                        {item.download_file && (
                          <>
                            <dt>{t("attention.fileDownloaded")}</dt>
                            <dd><code>{item.download_file}</code></dd>
                          </>
                        )}
                        {item.library_file && (
                          <>
                            <dt>{t("attention.fileInLibrary")}</dt>
                            <dd><code>{item.library_file}</code></dd>
                          </>
                        )}
                        {(item.older_files ?? []).map((name) => (
                          <div key={name} style={{ display: "contents" }}>
                            <dt>{t("progress.olderFile")}</dt>
                            <dd><code>{name}</code></dd>
                          </div>
                        ))}
                        {item.since && (
                          <>
                            <dt>{t("attention.noticed")}</dt>
                            <dd>{formatTime(item.since)}</dd>
                          </>
                        )}
                      </dl>

                      {item.attention_kind && remedyFor(item.attention_kind) && (
                        <p className="attention-fix">
                          {resolvesItself(item.attention_kind) && (
                            <span className="chip resolves">{t("attention.resolvesItself")}</span>
                          )}
                          {t(remedyFor(item.attention_kind)!.message)}
                        </p>
                      )}

                      {item.history.length > 0 && (
                        <>
                          <strong style={{ display: "block", marginTop: 10 }}>{t("progress.history")}</strong>
                          <dl className="attention-facts">
                            {item.history.map((entry, index) => (
                              <div key={index} style={{ display: "contents" }}>
                                <dt>{formatTime(entry.created_at)}</dt>
                                <dd>{t(`episode.state.${entry.to_state}`)}</dd>
                              </div>
                            ))}
                          </dl>
                        </>
                      )}

                      {/* Every button here is the Attention page's own - same
                          id, same endpoint, same record. Nothing new is being
                          decided here about what fixes what. */}
                      <ul className="attention-actions">
                        {item.attention_id && item.actions.includes("retry_now") && (
                          <li>
                            <div className="attention-action-what">
                              <strong>{t("attention.retryNow")}</strong>
                              <small className="muted">{t("attention.explain.retry_now")}</small>
                            </div>
                            <button className="button primary" disabled={busy}
                                    onClick={() => void act(item.episode_id, () => retryDownloadNow(item.attention_id!))}>
                              {t("attention.retryNow")}
                            </button>
                          </li>
                        )}
                        {item.attention_id && item.actions.includes("move_now") && (
                          <li>
                            <div className="attention-action-what">
                              <strong>{t("attention.moveNow")}</strong>
                              <small className="muted">{t("attention.explain.move_now")}</small>
                            </div>
                            <button className="button primary" disabled={busy}
                                    onClick={() => void act(item.episode_id, () => moveFileNow(item.attention_id!))}>
                              {t("attention.moveNow")}
                            </button>
                          </li>
                        )}
                        {item.attention_id && item.actions.includes("delete_download") && (
                          <li>
                            <div className="attention-action-what">
                              <strong>{t("attention.deleteDownload")}</strong>
                              <small className="muted">{t("attention.explain.delete_download")}</small>
                            </div>
                            <button className="button secondary" disabled={busy}
                                    onClick={() => void act(item.episode_id, () => deleteDownloadedFile(item.attention_id!))}>
                              {t("attention.deleteDownload")}
                            </button>
                          </li>
                        )}
                        {item.attention_id && item.actions.includes("replace_library") && (
                          <li>
                            <div className="attention-action-what">
                              <strong>{t("attention.replaceLibrary")}</strong>
                              <small className="muted">{t("attention.explain.replace_library")}</small>
                            </div>
                            <button className="button secondary" disabled={busy}
                                    onClick={() => void act(item.episode_id, () => replaceLibraryFile(item.attention_id!))}>
                              {t("attention.replaceLibrary")}
                            </button>
                          </li>
                        )}
                        {item.attention_id && item.actions.includes("delete_older") && (
                          <li>
                            <div className="attention-action-what">
                              <strong>{t("attention.deleteOlder")}</strong>
                              <small className="muted">{t("attention.explain.delete_older")}</small>
                            </div>
                            <button className="button secondary" disabled={busy}
                                    onClick={() => void act(item.episode_id, () => deleteOlderFile(item.attention_id!))}>
                              {t("attention.deleteOlder")}
                            </button>
                          </li>
                        )}
                        {item.attention_id && item.actions.includes("delete_newer") && (
                          <li>
                            <div className="attention-action-what">
                              <strong>{t("attention.deleteNewer")}</strong>
                              <small className="muted">{t("attention.explain.delete_newer")}</small>
                            </div>
                            <button className="button secondary" disabled={busy}
                                    onClick={() => void act(item.episode_id, () => deleteNewerFile(item.attention_id!))}>
                              {t("attention.deleteNewer")}
                            </button>
                          </li>
                        )}
                        {item.attention_id && item.actions.includes("approve") && (
                          <li>
                            <div className="attention-action-what">
                              <strong>{t("attention.approve")}</strong>
                              <small className="muted">{t("attention.explain.approve")}</small>
                            </div>
                            <button className="button primary" disabled={busy}
                                    onClick={() => void act(item.episode_id, () => approveNewVersion(item.attention_id!))}>
                              {t("attention.approve")}
                            </button>
                          </li>
                        )}
                        {DISMISSIBLE_STATES.has(item.state) && (
                          <li>
                            <div className="attention-action-what">
                              <strong>{t("progress.dismiss")}</strong>
                              <small className="muted">{t("progress.explainDismiss")}</small>
                            </div>
                            <button className="button secondary" disabled={busy}
                                    onClick={() => void act(item.episode_id, () => dismissProgress(item.episode_id))}>
                              {t("progress.dismiss")}
                            </button>
                          </li>
                        )}
                        {!hasRealAction(item) && !DISMISSIBLE_STATES.has(item.state) && (
                          // Nothing to press yet - AniFlow is still working on
                          // this by itself (services/attentionRemedy.ts calls
                          // this kind "waits"). Said plainly rather than left
                          // blank, which reads as the page having missed
                          // something.
                          /* With Automate off nothing is working on it, whatever the card said before
                             (build 212): say so, and offer the way to the switch. */
                          anime.find((entry) => entry.id === item.anime_id)?.automation_enabled === false ? (
                            <li>
                              <div className="attention-action-what">
                                <strong>{t("progress.manualOnly")}</strong>
                                <small className="muted">{t("progress.manualOnlyHint")}</small>
                              </div>
                              {onOpenAnime && (
                                <button className="button primary" onClick={() => onOpenAnime(item.anime_id)}>
                                  {t("progress.openAnime")}
                                </button>
                              )}
                            </li>
                          ) : (
                            <li className="muted">{t("progress.waiting")}</li>
                          )
                        )}
                      </ul>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
      {/* Its own bubble below the list rather than a button in the header
          (167): *"Can we not just have it below In progress in its own
          Bubble, its a button, if you press it a new window will pop up ...
          and you can close it with x."*  The count still rides along, same
          as before. */}
      <div className="detail-panel">
        <button className="button secondary" onClick={() => setArchiveOpen(true)}>
          {t("progress.archive")}{archivedCount !== null ? ` (${archivedCount})` : ""}
        </button>
      </div>
      {archiveOpen && <ArchiveDialog onClose={() => setArchiveOpen(false)} />}
    </section>
  );
}

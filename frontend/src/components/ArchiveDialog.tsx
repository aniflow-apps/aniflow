/**
 * Episodes done with (build 165, turned into its own window in 167).
 *
 * The other half of the workflow the In Progress page asked for: *"it will
 * appear in the In progress, do the stuff, if there is an issue, it will get
 * stuck user will notice it. If everything goes well it will be moved into a
 * archive window."*  A clean finish leaves In Progress on its own; a stuck
 * episode the user is done looking at can be sent here by hand from that
 * page's own Archive bubble. Nothing here can still be acted on - it is a
 * record, not a queue - so there are no fix buttons, only history.
 *
 * A window rather than a page (167): *"Can we not just have it below In
 * progress in its own Bubble, its a button, if you press it a new window
 * will pop up like the 'set up release matching' window - and you can close
 * it with x."*  Same `dialog-backdrop`/`dialog-head` shape as every other
 * dialog in the app (see FindFilesDialog), instead of the page route it used
 * to be - `#/progress/archive` and the "Back" button are gone with it.
 */
import { useCallback, useEffect, useState } from "react";
import { listProgressArchive, type ProgressItem } from "../api/client";
import { t } from "../i18n";
import { stateClass } from "../progressState";
import { reasonText } from "../reasons";
import { formatTime } from "../time";
import { useLockedPageScroll } from "../useLockedPageScroll";

export function ArchiveDialog({ onClose }: { onClose: () => void }) {
  const [items, setItems] = useState<ProgressItem[]>([]);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());

  useLockedPageScroll();

  const load = useCallback(async () => {
    try {
      setItems(await listProgressArchive());
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function toggle(id: string) {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog wide tall">
        <header className="dialog-head">
          <h2>{t("progress.archiveTitle")}</h2>
          <button className="close" onClick={onClose} aria-label={t("scan.close")}>
            ×
          </button>
        </header>
        <p className="muted">{t("progress.archiveHint")}</p>
        {error && <p className="error-message">{t("activity.error")}</p>}
        {items.length === 0 ? (
          <p className="muted">{t("progress.archiveEmpty")}</p>
        ) : (
          <ul className="attention-list">
            {items.map((item) => {
              const isOpen = open.has(item.episode_id);
              return (
                <li key={item.episode_id} className={`attention-row ${stateClass(item.state)}`}>
                  <span className={`chip ${stateClass(item.state)}`}>{t(`episode.state.${item.state}`)}</span>
                  <div className="attention-main">
                    <strong title={item.anime_name}>
                      {item.anime_name} — {t("profile.episode")} {item.episode_number}
                    </strong>
                    {item.detail && <small className="muted">{reasonText(item.detail)}</small>}
                  </div>
                  <div className="attention-aside">
                    {item.archived_at && (
                      <small className="muted">{t("progress.archivedAt")} {formatTime(item.archived_at)}</small>
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
                      <dl className="attention-facts">
                        <dt>{t("profile.episode")}</dt>
                        <dd>{item.episode_number}</dd>
                        {item.release_label && (
                          <>
                            <dt>{t("attention.releaseName")}</dt>
                            <dd><code>{item.release_label}</code></dd>
                          </>
                        )}
                      </dl>
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
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

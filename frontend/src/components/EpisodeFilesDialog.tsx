import { useState } from "react";
import { deleteEpisodeCopy, type EpisodeSummary } from "../api/client";
import { fileRows } from "../episodeFiles";
import { t } from "../i18n";
import { useLockedPageScroll } from "../useLockedPageScroll";

/**
 * Every episode with its full name and every file it has (build 189).
 *
 * The list on the detail page cuts a long title short to keep the rows even;
 * this is where the whole name is read, wrapped onto as many lines as it
 * needs. Under each episode go all its files, each with a tag that says what
 * it is - Target, Source, Backup, an older version, a kept original - and a
 * delete button for the ones that are copies beside the episode's own.
 *
 * Asked for as: *"you see all the versions you might have in there, and
 * behind every one of them is a delete button. So for example you can delete
 * the backup file or the v1 file directly in there."*  The episode's own file
 * and the release it came from have no button here: deleting those is what
 * the status and re-download actions are for, and a stray click must not
 * empty an episode.
 */
export function EpisodeFilesDialog({
  animeId,
  episodes,
  onClose,
  onChanged,
}: {
  animeId: string;
  episodes: EpisodeSummary[];
  onClose: () => void;
  /** A file went: the page reloads the anime to show what is left. */
  onChanged: () => void;
}) {
  useLockedPageScroll();
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  async function remove(episodeId: string, copyId: string) {
    if (confirming !== copyId) {
      setConfirming(copyId);
      return;
    }
    setConfirming(null);
    setBusy(true);
    setFailed(null);
    try {
      await deleteEpisodeCopy(animeId, episodeId, copyId);
      onChanged();
    } catch (error) {
      setFailed(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog wide tall episode-files-dialog">
        <header className="dialog-head">
          <h2>{t("files.title")}</h2>
          <button className="close" onClick={onClose} aria-label={t("scan.close")}>
            ×
          </button>
        </header>
        <p className="muted episode-files-hint">{t("files.hint")}</p>
        {failed && <p className="error-message">{failed}</p>}
        <div className="modal-scroll episode-files-list">
          {episodes.map((episode) => {
            const rows = fileRows(episode);
            return (
              <section key={episode.id} className="episode-files-item">
                <div className="episode-files-head">
                  <strong className="episode-files-number">#{episode.number}</strong>
                  {episode.title && <span className="episode-files-title">{episode.title}</span>}
                </div>
                {rows.length === 0 && <p className="muted episode-files-none">{t("files.none")}</p>}
                {rows.map((row) => (
                  <div key={row.key} className="file-row">
                    <span className="file-tags">
                      <span className={`file-tag ${row.tag}`}>{t(`files.tag.${row.tag}`)}</span>
                      {row.extras.map((extra) => (
                        <span key={extra} className={`file-tag ${extra}`}>{t(`files.tag.${extra}`)}</span>
                      ))}
                    </span>
                    <span className="file-name">{row.filename}</span>
                    {row.copyId ? (
                      <button
                        className="button secondary file-delete"
                        disabled={busy}
                        onClick={() => void remove(episode.id, row.copyId!)}
                      >
                        {confirming === row.copyId ? t("files.deleteConfirm") : t("files.delete")}
                      </button>
                    ) : (
                      <span className="file-delete-space" />
                    )}
                  </div>
                ))}
              </section>
            );
          })}
        </div>
      </div>
    </div>
  );
}

import { useEffect, useMemo, useState } from "react";
import { assignFolderFiles, listFolderFiles, type FolderFiles } from "../api/client";
import {
  assignedCount,
  chosenTwice,
  cleared,
  episodeChoices,
  initialSelection,
  inOrder,
  toAssignments,
  type Selection,
} from "../assignFiles";
import { t } from "../i18n";
import { useLockedPageScroll } from "../useLockedPageScroll";
import { shortSize } from "../selectionView";

/**
 * Say by hand which file is which episode (build 213), for names no scan can read.
 *
 * Opened from the result of "Scan the folder" when some files could not be matched. It lists every
 * video in the folder; each gets a dropdown of episodes. Saving only records - no file is renamed,
 * moved or deleted.
 */
export function AssignFilesDialog({
  animeId,
  onClose,
  onSaved,
}: {
  animeId: string;
  onClose: () => void;
  /** Called after a save, so the page can read the anime again. */
  onSaved: () => void;
}) {
  useLockedPageScroll();
  const [listing, setListing] = useState<FolderFiles | null>(null);
  const [selection, setSelection] = useState<Selection>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let current = true;
    listFolderFiles(animeId)
      .then((answer) => {
        if (!current) return;
        setListing(answer);
        setSelection(initialSelection(answer.files));
      })
      .catch((reason) => { if (current) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { current = false; };
  }, [animeId]);

  const twice = useMemo(() => chosenTwice(selection), [selection]);
  const choices = useMemo(
    () => (listing ? episodeChoices(listing.episodes, listing.episode_count, listing.files.length) : []),
    [listing],
  );

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await assignFolderFiles(animeId, toAssignments(selection));
      onSaved();
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog wide assign-dialog">
        <header className="dialog-head">
          <h2>{t("assign.title")}</h2>
          <button className="close" onClick={onClose} aria-label={t("scan.close")}>×</button>
        </header>
        <p className="muted">{t("assign.hint")}</p>

        {!listing && !error && <p className="loading">{t("setup.loading")}</p>}
        {error && <p className="error-message">{error}</p>}

        {listing && (
          <>
            <div className="assign-tools">
              <button className="button secondary" onClick={() => setSelection(inOrder(listing.files))}>
                {t("assign.inOrder")}
              </button>
              <button className="button secondary" onClick={() => setSelection(cleared(listing.files))}>
                {t("assign.clear")}
              </button>
              <small className="muted">
                {t("assign.count").replace("{n}", String(assignedCount(selection))).replace("{total}", String(listing.files.length))}
              </small>
            </div>
            {listing.files.length === 0 ? (
              <p>{t("assign.empty")}</p>
            ) : (
              <ul className="assign-list">
                {listing.files.map((file) => {
                  const value = selection[file.filename];
                  const clash = value !== null && value !== undefined && twice.includes(value);
                  return (
                    <li key={file.filename} className={clash ? "assign-row clash" : "assign-row"}>
                      <span className="assign-name" title={file.filename}>
                        {file.filename}
                        {file.size ? <small className="muted"> · {shortSize(file.size)}</small> : null}
                      </span>
                      <select
                        value={value ?? ""}
                        aria-label={t("assign.episode")}
                        onChange={(event) =>
                          setSelection({ ...selection, [file.filename]: event.target.value === "" ? null : Number(event.target.value) })
                        }
                      >
                        <option value="">{t("assign.none")}</option>
                        {choices.map((number) => (
                          <option key={number} value={number}>{t("assign.episodeN").replace("{n}", String(number))}</option>
                        ))}
                      </select>
                    </li>
                  );
                })}
              </ul>
            )}
            {twice.length > 0 && (
              <p className="warning-message">{t("assign.twice").replace("{n}", twice.join(", "))}</p>
            )}
          </>
        )}

        <div className="dialog-actions">
          <button className="button secondary" onClick={onClose}>{t("assign.cancel")}</button>
          <button className="button primary" disabled={!listing || saving || twice.length > 0} onClick={() => void save()}>
            {saving ? t("assign.saving") : t("assign.save")}
          </button>
        </div>
      </div>
    </div>
  );
}

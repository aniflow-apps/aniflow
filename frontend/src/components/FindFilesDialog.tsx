/**
 * Find an anime's files in the library (build 139).
 *
 * For a basic entry, whose files are very often already on disk - put there
 * by hand, by an older tool, or by somebody's own sorting. Two screens in one
 * dialog, and the order is the point:
 *
 * 1. **which folder** - the library's folders, scored against every name the
 *    anime has, best first, with the rest a click away for a folder named
 *    nothing like the show;
 * 2. **what is in it** - every file, what it would become, and plainly which
 *    ones AniFlow will not touch.
 *
 * Nothing is written until the last button. That is not politeness: the
 * preview promises to show what will happen, and a preview that had already
 * happened would make that a lie.
 */
import { useEffect, useState } from "react";

import {
  type FolderCandidate,
  type LibraryPreview,
  libraryFolders,
  libraryPreview,
  linkLibraryFolder,
} from "../api/client";
import { useLockedPageScroll } from "../useLockedPageScroll";
import { t } from "../i18n";

export function FindFilesDialog({
  animeId,
  animeName,
  onClose,
  onLinked,
}: {
  animeId: string;
  animeName: string;
  onClose: () => void;
  onLinked: () => void;
}) {
  const [folders, setFolders] = useState<FolderCandidate[] | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [chosen, setChosen] = useState<string | null>(null);
  const [plan, setPlan] = useState<LibraryPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useLockedPageScroll();

  useEffect(() => {
    setFolders(null);
    void libraryFolders(animeId, showAll)
      .then((rows) => {
        setFolders(rows);
        // Only a confident match is preselected. Anything less is a guess,
        // and a preselected guess is how the wrong folder gets linked.
        if (!showAll && rows.length && rows[0].score >= 85) setChosen(rows[0].name);
      })
      .catch(() => setError(t("findFiles.error")));
  }, [animeId, showAll]);

  useEffect(() => {
    if (!chosen) {
      setPlan(null);
      return;
    }
    setBusy(true);
    void libraryPreview(animeId, chosen)
      .then(setPlan)
      .catch(() => setError(t("findFiles.error")))
      .finally(() => setBusy(false));
  }, [animeId, chosen]);

  async function confirm() {
    if (!chosen) return;
    setBusy(true);
    setError(null);
    try {
      await linkLibraryFolder(animeId, chosen);
      onLinked();
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("findFiles.error"));
    } finally {
      setBusy(false);
    }
  }

  const unplaced = plan?.plans.filter((entry) => !entry.placed) ?? [];

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog find-files">
        {/* `dialog-head`, not `status-line`: build 124 settled where a close
            button lives, and the heading pushes it to the right by itself. */}
        <header className="dialog-head">
          <h2>{t("findFiles.title")}</h2>
          <button className="close" onClick={onClose} aria-label={t("scan.close")}>
            ×
          </button>
        </header>
        <p className="muted">{t("findFiles.hint").replace("{name}", animeName)}</p>

        {error ? <p className="reasons">{error}</p> : null}

        <h3 className="find-files-sub">{t("findFiles.folders")}</h3>
        {folders === null ? (
          <p className="muted">{t("findFiles.looking")}</p>
        ) : folders.length === 0 ? (
          <p className="muted">{t("findFiles.noFolders")}</p>
        ) : (
          <div className="find-files-list">
            {folders.map((folder) => (
              <label
                key={folder.name}
                className={"find-files-row" + (chosen === folder.name ? " chosen" : "")}
              >
                <input
                  type="radio"
                  name="folder"
                  checked={chosen === folder.name}
                  onChange={() => setChosen(folder.name)}
                />
                <span className="find-files-name">
                  {folder.name}
                  <small>
                    {t("findFiles.counts")
                      .replace("{files}", String(folder.files))
                      .replace("{episodes}", String(folder.episodes))}
                    {folder.matched ? ` · ${t("findFiles.matched")} ${folder.matched}` : ""}
                  </small>
                </span>
                {/* The score is shown because the person has to decide whether
                    to trust it, and a bare "best match" hides how close the
                    second one was. */}
                <span className={"find-files-score" + (folder.score >= 85 ? " strong" : "")}>
                  {folder.score > 0 ? `${folder.score}%` : t("findFiles.noScore")}
                </span>
              </label>
            ))}
          </div>
        )}
        {!showAll && (
          <button className="link-button" onClick={() => setShowAll(true)}>
            {t("findFiles.showAll")}
          </button>
        )}

        {plan ? (
          <>
            <h3 className="find-files-sub">{t("findFiles.whatIsIn")}</h3>
            {plan.plans.length === 0 ? (
              <p className="muted">{t("findFiles.empty")}</p>
            ) : (
              <>
                <div className="find-files-plan">
                  {plan.plans.map((entry) => (
                    <div
                      key={entry.filename}
                      className={"find-files-file" + (entry.placed ? "" : " skipped")}
                    >
                      <span className="find-files-episode">
                        {entry.episode !== null ? `#${entry.episode}` : "—"}
                      </span>
                      <span className="find-files-filename">{entry.filename}</span>
                      <span className="find-files-tags">
                        {[entry.quality, entry.subgroup].filter(Boolean).join(" · ")}
                        {entry.placed ? "" : ` · ${t(`findFiles.reason.${entry.reason}`)}`}
                      </span>
                    </div>
                  ))}
                </div>
                <p className="muted">
                  {t("findFiles.summary")
                    .replace("{placed}", String(plan.placeable))
                    .replace("{left}", String(plan.unplaceable))}
                </p>
                {plan.subgroup || plan.quality ? (
                  <p className="imported-tag" style={{ fontSize: ".82rem" }}>
                    {t("findFiles.learned")
                      .replace("{subgroup}", plan.subgroup ?? "—")
                      .replace("{quality}", plan.quality ?? "—")}
                  </p>
                ) : null}
                {unplaced.length > 0 ? (
                  <p className="note-inline">{t("findFiles.leftAlone")}</p>
                ) : null}
              </>
            )}
          </>
        ) : null}

        <div className="action-stack horizontal" style={{ marginTop: 16 }}>
          <button
            className="button primary"
            disabled={!chosen || busy || (plan?.placeable ?? 0) === 0}
            onClick={() => void confirm()}
          >
            {busy ? t("profile.saving") : t("findFiles.link")}
          </button>
          <button className="button secondary" onClick={onClose}>
            {t("add.cancel")}
          </button>
        </div>
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import { browseFolders, type FolderBrowsing, type Library } from "../api/client";
import { t } from "../i18n";
import { presetFromShokoFolder, type LibraryPreset } from "../libraries";
import { useLockedPageScroll } from "../useLockedPageScroll";

/**
 * Add libraries from the subfolders of one folder.
 *
 * The user picks the parent (``/media``, ``/media2``...) first, so a huge
 * media folder with things AniFlow has nothing to do with is never listed
 * unasked. Each subfolder has an Add button that opens the Add library window
 * with the folder filled in; one that already is a library says so instead.
 */
export function SubfoldersDialog({
  parent,
  libraries,
  onAdd,
  onClose,
}: {
  parent: string;
  libraries: Library[];
  onAdd: (preset: LibraryPreset) => void;
  onClose: () => void;
}) {
  useLockedPageScroll();
  const [view, setView] = useState<FolderBrowsing | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    browseFolders(parent)
      .then((answer) => {
        if (!cancelled) setView(answer);
      })
      .catch((failure) => {
        if (!cancelled) setError(failure instanceof Error ? failure.message : String(failure));
      });
    return () => {
      cancelled = true;
    };
  }, [parent]);

  const sameFolder = (a: string, b: string) => a.replace(/\/+$/, "") === b.replace(/\/+$/, "");

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog wide">
        <header className="dialog-head">
          <h2>{t("libraries.subfoldersTitle").replace("{path}", parent)}</h2>
          <button className="close" onClick={onClose} aria-label={t("scan.close")}>
            ×
          </button>
        </header>
        <p className="muted">{t("libraries.subfoldersHint")}</p>
        {error && <p className="error-message">{error}</p>}
        {view && view.entries.length === 0 && <p className="muted">{t("libraries.subfoldersNone")}</p>}
        <div className="library-match-list">
          {view?.entries.map((entry) => {
            const linked = libraries.find((library) => library.has_folder && sameFolder(library.folder, entry.path));
            return (
              <div className="library-match-row library-match-short" key={entry.path}>
                <span className="library-match-name">{entry.name}</span>
                <code className="storage-path-host">{entry.path}</code>
                <span className="library-match-action">
                  {linked ? (
                    <small className="muted">{t("libraries.matchLinked").replace("{name}", linked.name)}</small>
                  ) : (
                    <button
                      className="button secondary"
                      onClick={() => onAdd(presetFromShokoFolder({ id: null, name: entry.name, path: entry.path }, entry.path))}
                    >
                      {t("libraries.matchAdd")}
                    </button>
                  )}
                </span>
              </div>
            );
          })}
        </div>
        <div className="dialog-actions">
          <button className="button secondary" onClick={onClose}>
            {t("libraries.close")}
          </button>
        </div>
      </div>
    </div>
  );
}

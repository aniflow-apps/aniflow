import { useEffect, useState } from "react";
import { browseFolders, type Library, type ShokoImportFolder, type ShokoImportFolders } from "../api/client";
import { t } from "../i18n";
import {
  canBeALibrary,
  dropTypeLabel,
  folderCandidates,
  libraryOfShokoFolder,
  presetFromShokoFolder,
  type LibraryPreset,
} from "../libraries";
import { useLockedPageScroll } from "../useLockedPageScroll";

/**
 * Match Shoko folders (Shoko mode only).
 *
 * Lists Shoko's import folders with their state, and beside each Destination or
 * Both folder a button that opens the Add library window with the Shoko folder
 * already assigned. A Source folder is the drop point, not a library. The
 * AniFlow folder is pre-picked only when AniFlow can really see one that fits.
 */
export function ShokoFoldersDialog({
  shoko,
  libraries,
  onAdd,
  onClose,
}: {
  shoko: ShokoImportFolders;
  libraries: Library[];
  onAdd: (preset: LibraryPreset) => void;
  onClose: () => void;
}) {
  useLockedPageScroll();
  /* Which AniFlow folder each Shoko folder was found at, by Shoko folder id. */
  const [found, setFound] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      for (const folder of shoko.folders) {
        if (folder.id === null || !canBeALibrary(folder.drop_type)) continue;
        for (const candidate of folderCandidates(folder)) {
          if (cancelled) return;
          try {
            const view = await browseFolders(candidate);
            if (!view.selectable) continue;
            setFound((current) => ({ ...current, [String(folder.id)]: view.path }));
            break;
          } catch {
            // Not a folder AniFlow can see; try the next guess.
          }
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [shoko.folders]);

  function row(folder: ShokoImportFolder) {
    const linked = libraryOfShokoFolder(libraries, folder.id);
    return (
      <div className="library-match-row" key={String(folder.id)}>
        <span className="library-match-name">{folder.name ?? folder.id}</span>
        <code className="storage-path-host">{folder.path ?? "—"}</code>
        <small className="muted">{dropTypeLabel(folder.drop_type)}</small>
        <span className={folder.watching ? "imported-tag" : "reasons"}>
          {folder.watching ? t("shoko.watching") : t("shoko.notWatching")}
        </span>
        <span className="library-match-action">
          {linked ? (
            <small className="muted">{t("libraries.matchLinked").replace("{name}", linked.name)}</small>
          ) : canBeALibrary(folder.drop_type) ? (
            <button
              className="button secondary"
              onClick={() => onAdd(presetFromShokoFolder(folder, found[String(folder.id)] ?? ""))}
            >
              {t("libraries.matchAdd")}
            </button>
          ) : (
            <small className="muted">{t("libraries.matchSource")}</small>
          )}
        </span>
      </div>
    );
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog wide">
        <header className="dialog-head">
          <h2>{t("libraries.shokoFolders")}</h2>
          <button className="close" onClick={onClose} aria-label={t("scan.close")}>
            ×
          </button>
        </header>
        <p className="muted">{t("libraries.matchHint")}</p>
        <p className="muted library-shoko-note">{t("libraries.shokoMoveOnImport")}</p>
        <div className="library-match-list">{shoko.folders.map(row)}</div>
        <div className="dialog-actions">
          <button className="button secondary" onClick={onClose}>
            {t("libraries.close")}
          </button>
        </div>
      </div>
    </div>
  );
}

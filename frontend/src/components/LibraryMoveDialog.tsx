import { useState } from "react";
import { deleteLibrary, moveToLibrary, type Library, type LibraryMoveResult } from "../api/client";
import { t } from "../i18n";
import { useLockedPageScroll } from "../useLockedPageScroll";

export interface MoveCandidate {
  id: string;
  name: string;
}

/** A library's name as the user knows it: the built-in ones are translated. */
export function libraryName(library: Pick<Library, "key" | "name">): string {
  return library.key ? t(`libraries.system.${library.key}`) : library.name;
}

/**
 * Move anime to another library (build 192): one from its page, or many at once
 * from the Libraries page - tick the ones to go, leave the rest.
 *
 * The files move with them, and an anime that cannot be moved (its folder
 * already exists over there, say) says why and stays where it was, without
 * stopping the others.
 */
export function LibraryMoveDialog({
  candidates,
  initiallySelected,
  libraries,
  currentLibraryId,
  onClose,
  onDone,
}: {
  candidates: MoveCandidate[];
  initiallySelected: string[];
  libraries: Library[];
  /** Left out of the destinations: moving to where it already is does nothing. */
  currentLibraryId: number | null;
  onClose: () => void;
  onDone: () => void;
}) {
  useLockedPageScroll();
  const destinations = libraries.filter((l) => l.id !== currentLibraryId);
  const [selected, setSelected] = useState<Set<string>>(new Set(initiallySelected));
  const [destination, setDestination] = useState<number | null>(destinations[0]?.id ?? null);
  /* Untick when the files are already in the new folder (a folder was
     repointed): then only the library changes (build 194). */
  const [moveFiles, setMoveFiles] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<LibraryMoveResult[] | null>(null);

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function move() {
    if (destination === null || selected.size === 0) return;
    setBusy(true);
    setError(null);
    try {
      // In the order shown, so the results read the way the list did.
      setResults(
        await moveToLibrary(candidates.filter((c) => selected.has(c.id)).map((c) => c.id), destination, moveFiles),
      );
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }

  function finish() {
    onDone();
    onClose();
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog wide">
        <header className="dialog-head">
          <h2>{t("libraries.moveTitle")}</h2>
          <button className="close" onClick={results ? finish : onClose} aria-label={t("scan.close")}>
            ×
          </button>
        </header>

        {results === null ? (
          <>
            <label className="field">
              {t("libraries.moveDestination")}
              <select
                value={destination ?? ""}
                onChange={(event) => setDestination(Number(event.target.value))}
              >
                {destinations.map((library) => (
                  <option key={library.id} value={library.id}>
                    {libraryName(library)}{library.has_folder ? ` — ${library.folder}` : ""}
                  </option>
                ))}
              </select>
            </label>

            {candidates.length > 1 && (
              <div className="library-move-tools">
                <button className="button secondary" onClick={() => setSelected(new Set(candidates.map((c) => c.id)))}>
                  {t("libraries.moveSelectAll")}
                </button>
                <button className="button secondary" onClick={() => setSelected(new Set())}>
                  {t("libraries.moveSelectNone")}
                </button>
                <span className="muted">
                  {t("libraries.moveCount")
                    .replace("{n}", String(selected.size))
                    .replace("{total}", String(candidates.length))}
                </span>
              </div>
            )}

            <ul className="library-move-list">
              {candidates.map((candidate) => (
                <li key={candidate.id}>
                  <label className="check library-move-row">
                    <input
                      type="checkbox"
                      checked={selected.has(candidate.id)}
                      onChange={() => toggle(candidate.id)}
                    />
                    {candidate.name}
                  </label>
                </li>
              ))}
              {candidates.length === 0 && <li className="muted">{t("libraries.moveNothing")}</li>}
            </ul>

            <label className="check">
              <input type="checkbox" checked={moveFiles} onChange={(event) => setMoveFiles(event.target.checked)} />
              {t("libraries.moveFilesToo")}
            </label>
            <p className="muted">{t("libraries.moveHint")}</p>
            {error && <p className="error-message">{error}</p>}
            <div className="dialog-actions">
              <button className="button secondary" onClick={onClose}>
                {t("libraries.cancel")}
              </button>
              <button
                className="button primary"
                disabled={busy || destination === null || selected.size === 0}
                onClick={() => void move()}
              >
                {t("libraries.moveConfirm")}
              </button>
            </div>
          </>
        ) : (
          <>
            <ul className="library-move-list">
              {results.map((result) => (
                <li key={result.anime_id} className="library-move-row">
                  <span className={`chip sev-${result.outcome === "refused" ? "error" : result.outcome === "unchanged" ? "notice" : "success"}`}>
                    {t(`libraries.moveResult.${result.outcome}`)}
                  </span>
                  <span>
                    {result.name}
                    {result.detail && <small className="muted"> — {result.detail}</small>}
                  </span>
                </li>
              ))}
            </ul>
            <div className="dialog-actions">
              <button className="button primary" onClick={finish}>
                {t("libraries.moveDone")}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/**
 * Removing a library that still holds anime: they have to go somewhere first
 * (build 194). They used to fall to the default library without a word, which
 * is rarely where somebody who has just repointed a folder means them to be.
 *
 * Whether the files move too is decided here: when the destination uses the
 * same folder they are already in place and only the library changes.
 */
export function LibraryRemoveDialog({
  library,
  libraries,
  onClose,
  onDone,
}: {
  library: Library;
  libraries: Library[];
  onClose: () => void;
  onDone: () => void;
}) {
  useLockedPageScroll();
  // A library with no folder (Upcoming) cannot take an anime that has files.
  const destinations = libraries.filter((l) => l.id !== library.id && l.has_folder);
  const [destination, setDestination] = useState<number | null>(destinations[0]?.id ?? null);
  const target = destinations.find((l) => l.id === destination);
  const sameFolder = target?.folder === library.folder;
  const [moveFiles, setMoveFiles] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    if (destination === null) return;
    setBusy(true);
    setError(null);
    try {
      await deleteLibrary(library.id, { moveTo: destination, moveFiles: sameFolder ? false : moveFiles });
      onDone();
      onClose();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog">
        <header className="dialog-head">
          <h2>{t("libraries.removeTitle")}</h2>
          <button className="close" onClick={onClose} aria-label={t("scan.close")}>
            ×
          </button>
        </header>
        <p>
          {t("libraries.removeWhere")
            .replace("{n}", String(library.anime_count))
            .replace("{name}", libraryName(library))}
        </p>
        <label className="field">
          {t("libraries.moveDestination")}
          <select value={destination ?? ""} onChange={(event) => setDestination(Number(event.target.value))}>
            {destinations.map((l) => (
              <option key={l.id} value={l.id}>
                {libraryName(l)}{l.has_folder ? ` — ${l.folder}` : ""}
              </option>
            ))}
          </select>
        </label>
        {sameFolder ? (
          <p className="muted">{t("libraries.removeSameFolder")}</p>
        ) : (
          <label className="check">
            <input type="checkbox" checked={moveFiles} onChange={(event) => setMoveFiles(event.target.checked)} />
            {t("libraries.moveFilesToo")}
          </label>
        )}
        {error && <p className="error-message">{error}</p>}
        <div className="dialog-actions">
          <button className="button secondary" onClick={onClose}>
            {t("libraries.cancel")}
          </button>
          <button className="button danger" disabled={busy || destination === null} onClick={() => void remove()}>
            {t("libraries.removeConfirmMove")}
          </button>
        </div>
      </div>
    </div>
  );
}

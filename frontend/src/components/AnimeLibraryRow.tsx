import { useEffect, useState } from "react";
import { listLibraries, pinAnimeInLibrary, updateAnime, type Library } from "../api/client";
import { t } from "../i18n";
import { LibraryMoveDialog, libraryName } from "./LibraryMoveDialog";

/**
 * "Library" on an anime's page, with the button that moves it (build 192).
 *
 * Two table cells so it sits in the same list as the media folder above it.
 */
export function AnimeLibraryRow({
  animeId,
  animeName,
  libraryId,
  pinned = false,
  onMoved,
}: {
  animeId: string;
  animeName: string;
  libraryId: number | null;
  /** The season check leaves this anime where it is (build 205). */
  pinned?: boolean;
  onMoved: () => void;
}) {
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [moving, setMoving] = useState(false);

  useEffect(() => {
    void listLibraries(true)
      .then((answer) => setLibraries(answer.libraries))
      .catch(() => undefined);
  }, [libraryId]);

  const current = libraries.find((library) => library.id === libraryId);

  return (
    <div className="config-item">
      <dt>{t("libraries.field")}</dt>
      <dd>
        {current ? libraryName(current) : "—"}
        {libraries.length > 1 && (
          <button className="button secondary library-row-move" onClick={() => setMoving(true)}>
            {t("libraries.moveTo")}
          </button>
        )}
        {pinned && <small className="muted"> {t("libraries.pinned")}</small>}
        {pinned && (
          <button
            className="button secondary library-row-move"
            onClick={() => void pinAnimeInLibrary([animeId], false).then(onMoved)}
          >
            {t("libraries.unpin")}
          </button>
        )}
        {moving && (
          <LibraryMoveDialog
            candidates={[{ id: animeId, name: animeName }]}
            initiallySelected={[animeId]}
            libraries={libraries}
            currentLibraryId={libraryId}
            onClose={() => setMoving(false)}
            onDone={onMoved}
          />
        )}
      </dd>
    </div>
  );
}

/** "Adult" on an anime's page: the flag the watchlist's hide button reads (build 192). */
export function AnimeAdultRow({
  animeId,
  isAdult,
  viaLibrary = false,
  onChanged,
}: {
  animeId: string;
  /** The user's own flag. */
  isAdult: boolean;
  /** Adult anyway, because its library is marked Adult. */
  viaLibrary?: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  async function change(next: boolean) {
    setBusy(true);
    try {
      await updateAnime(animeId, { is_adult: next });
      onChanged();
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="config-item">
      <dt>{t("libraries.adultLabel")}</dt>
      <dd>
        <label className="check">
          <input type="checkbox" checked={isAdult} disabled={busy} onChange={(event) => void change(event.target.checked)} />
          {t("libraries.adultMark")}
        </label>
        {viaLibrary && !isAdult && <small className="muted">{t("libraries.adultViaLibrary")}</small>}
      </dd>
    </div>
  );
}

/**
 * Choose the AniDB entry an anime really is (build 229).
 *
 * An AniDB id found by *name* is a guess: two shows can share one (Anemone
 * 2011 and Anemone (2027)), and a guess nobody checked looked exactly like a
 * certainty. This is where it gets checked - the entries the anime's names
 * match, what the offline id list knows about each, and a link to AniDB's own
 * page (a link the person's browser follows; AniFlow itself never fetches
 * anidb.net) to look at the year and the cover.
 *
 * Whatever is picked, or kept, is recorded as *checked by a person*, which is
 * what clears the flag.
 */
import { useEffect, useState } from "react";

import { type AnidbChoices, chooseAnidb, getAnidbChoices } from "../api/client";
import { anidbUrl } from "../anidbUrl";
import { t } from "../i18n";
import { useLockedPageScroll } from "../useLockedPageScroll";

export function AnidbChooserDialog({
  animeId,
  animeName,
  onClose,
  onChosen,
}: {
  animeId: string;
  animeName: string;
  onClose: () => void;
  /** Called after an id was stored, so the page can read the anime and its checks again. */
  onChosen: () => void;
}) {
  useLockedPageScroll();
  const [choices, setChoices] = useState<AnidbChoices | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");

  useEffect(() => {
    let live = true;
    void getAnidbChoices(animeId)
      .then((found) => {
        if (live) setChoices(found);
      })
      .catch(() => {
        if (live) setError(t("anidb.choose.error"));
      });
    return () => {
      live = false;
    };
  }, [animeId]);

  async function use(aid: number) {
    setBusy(true);
    setError(null);
    try {
      await chooseAnidb(animeId, aid);
      onChosen();
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("anidb.choose.error"));
    } finally {
      setBusy(false);
    }
  }

  const typedId = /^[1-9][0-9]{0,8}$/.test(typed.trim()) ? Number(typed.trim()) : null;

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog anidb-choose">
        <header className="dialog-head">
          <h2>{t("anidb.choose.title")}</h2>
          <button className="close" onClick={onClose} aria-label={t("scan.close")}>
            ×
          </button>
        </header>
        <p className="muted">{t("anidb.choose.hint").replace("{name}", animeName)}</p>

        {choices ? (
          <p className={`anidb-choose-state ${choices.state}`}>
            <b>{t(`anidb.choose.state.${choices.state}`)}</b> {choices.reason}
          </p>
        ) : null}
        {error ? <p className="reasons">{error}</p> : null}

        {choices === null && !error ? <p className="muted">{t("anidb.choose.loading")}</p> : null}
        {choices && choices.candidates.length === 0 ? <p className="muted">{t("anidb.choose.none")}</p> : null}

        {choices ? (
          <div className="anidb-choose-list">
            {choices.candidates.map((entry) => {
              const other = Object.keys(entry.belongs_to).length > 0;
              const link = anidbUrl(entry.aid);
              return (
                <div
                  key={entry.aid}
                  className={"anidb-choose-row" + (entry.current ? " current" : "") + (other ? " other" : "")}
                >
                  <span className="anidb-choose-name">
                    {entry.title}
                    <small>
                      AniDB {entry.aid}
                      {entry.names.length > 0 ? ` · ${entry.names.join(" · ")}` : ""}
                    </small>
                    <small className="anidb-choose-tags">
                      {entry.current ? <em className="anidb-choose-tag">{t("anidb.choose.current")}</em> : null}
                      {entry.agrees ? <em className="anidb-choose-tag good">{t("anidb.choose.agrees")}</em> : null}
                      {other ? (
                        <em className="anidb-choose-tag warn">
                          {t("anidb.choose.other").replace(
                            "{ids}",
                            Object.entries(entry.belongs_to)
                              .map(([kind, value]) => `${kind} ${value}`)
                              .join(", "),
                          )}
                        </em>
                      ) : null}
                    </small>
                  </span>
                  <span className="anidb-choose-actions">
                    {link ? (
                      <a className="link-button" href={link} target="_blank" rel="noopener noreferrer">
                        {t("anidb.choose.open")}
                      </a>
                    ) : null}
                    <button className="button secondary" disabled={busy} onClick={() => void use(entry.aid)}>
                      {entry.current ? t("anidb.choose.keep") : t("anidb.choose.use")}
                    </button>
                  </span>
                </div>
              );
            })}
          </div>
        ) : null}

        <div className="anidb-choose-manual">
          <label>
            <span>{t("anidb.choose.manual")}</span>
            <input
              value={typed}
              inputMode="numeric"
              placeholder="12345"
              onChange={(event) => setTyped(event.target.value)}
            />
          </label>
          <button className="button secondary" disabled={busy || typedId === null} onClick={() => typedId && void use(typedId)}>
            {t("anidb.choose.use")}
          </button>
        </div>
      </div>
    </div>
  );
}

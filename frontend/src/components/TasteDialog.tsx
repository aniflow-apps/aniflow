import { useState } from "react";
import type { AnimeSummary } from "../api/client";
import { t } from "../i18n";
import { useLockedPageScroll } from "../useLockedPageScroll";
import { genreCounts } from "../watchlistLibraries";

/** How many genres show before "Show all": the long tail says little. */
const TOP = 12;

/**
 * What the watchlist says about the user's taste (build 208): its genres, most
 * watched first, each with a bar. The same tally is what a recommendation
 * would be based on, so what is shown here is what a suggestion would use.
 */
export function TasteDialog({ anime, onClose }: { anime: AnimeSummary[]; onClose: () => void }) {
  useLockedPageScroll();
  const [all, setAll] = useState(false);
  const counts = genreCounts(anime);
  const withGenres = anime.filter((item) => (item.genres ?? []).length > 0).length;
  const most = counts[0]?.count ?? 1;
  const shown = all ? counts : counts.slice(0, TOP);

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog taste-dialog">
        <header className="dialog-head">
          <h2>{t("taste.title")}</h2>
          <button className="close" onClick={onClose} aria-label={t("scan.close")}>
            ×
          </button>
        </header>
        <p className="muted">{t("taste.hint")}</p>
        {counts.length === 0 ? (
          <p>{t("taste.empty")}</p>
        ) : (
          <>
            <ul className="taste-list">
              {shown.map((row) => (
                <li key={row.genre} className="taste-row">
                  <span className="taste-name">{row.genre}</span>
                  <span className="taste-bar" role="img" aria-label={`${row.count}`}>
                    <span className="taste-fill" style={{ width: `${Math.max(4, (row.count / most) * 100)}%` }} />
                  </span>
                  <span className="taste-count">
                    {t(row.count === 1 ? "taste.animeOne" : "taste.animeMany").replace("{n}", String(row.count))}
                  </span>
                </li>
              ))}
            </ul>
            {counts.length > TOP && (
              <button className="button secondary" onClick={() => setAll((current) => !current)}>
                {all ? t("taste.showTop") : t("taste.showAll").replace("{n}", String(counts.length))}
              </button>
            )}
            {withGenres < anime.length && (
              <p className="muted">
                {t("taste.missing").replace("{n}", String(anime.length - withGenres))}
              </p>
            )}
          </>
        )}
        <div className="dialog-actions">
          <button className="button secondary" onClick={onClose}>
            {t("libraries.close")}
          </button>
        </div>
      </div>
    </div>
  );
}

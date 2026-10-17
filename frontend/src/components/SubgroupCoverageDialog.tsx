import { useCallback, useEffect, useState } from "react";
import { getSubgroupOverview, type SubgroupOverview } from "../api/client";
import { t } from "../i18n";
import { formatTime } from "../time";
import { useLockedPageScroll } from "../useLockedPageScroll";

const WINDOWS = [7, 14, 30];

/**
 * Which subgroups you use and whether any feed carries them (build 193).
 *
 * Everything is read from what AniFlow has already stored - opening this asks
 * no feed anything. The hint under the title says what that means: a feed
 * only offers its latest items, so "not seen" is a reason to look, not proof.
 */
export function SubgroupCoverageDialog({ onClose }: { onClose: () => void }) {
  useLockedPageScroll();
  const [days, setDays] = useState(14);
  const [data, setData] = useState<SubgroupOverview | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await getSubgroupOverview(days));
      setError(false);
    } catch {
      setError(true);
    }
  }, [days]);

  useEffect(() => {
    void load();
  }, [load]);

  const unseen = data?.subgroups.filter((row) => !row.seen_on_a_feed).length ?? 0;

  return (
    <div className="dialog-backdrop" role="presentation">
      <section className="dialog dialog-wide" role="dialog" aria-modal="true" aria-label={t("rss.subgroups.title")}>
        <header>
          <h2>{t("rss.subgroups.title")}</h2>
          <button className="close" onClick={onClose} aria-label={t("add.cancel")}>×</button>
        </header>
        <p className="muted">{t("rss.subgroups.hint")}</p>
        {data?.history_since && (
          <p className="muted">
            {t("rss.subgroups.since")
              .replace("{date}", formatTime(data.history_since))
              .replace("{n}", String(data.feeds))}
          </p>
        )}
        {error && <p className="error-message">{t("activity.error")}</p>}

        <div className="modal-scroll">
          {data && (
            <>
              <h3 className="subgroup-section">
                {t("rss.subgroups.listTitle")}
                {unseen > 0 && <span className="chip sev-warning">{t("rss.subgroups.unseenCount").replace("{n}", String(unseen))}</span>}
              </h3>
              {data.subgroups.length === 0 ? (
                <p className="muted">{t("rss.subgroups.empty")}</p>
              ) : (
                <ul className="subgroup-list">
                  <li className="subgroup-row subgroup-head muted">
                    <span>{t("rss.subgroups.colGroup")}</span>
                    <span>{t("rss.subgroups.colAnime")}</span>
                    <span>{t("rss.subgroups.colEpisodes")}</span>
                    <span>{t("rss.subgroups.colWanted")}</span>
                    <span>{t("rss.subgroups.colFeeds")}</span>
                  </li>
                  {data.subgroups.map((row) => (
                    <li className="subgroup-row" key={row.subgroup}>
                      <strong>{row.subgroup}</strong>
                      <span>{row.anime}</span>
                      <span>{row.episodes}</span>
                      <span>{row.wanted_by}</span>
                      <span className="subgroup-feeds">
                        {row.feeds.length === 0 ? (
                          <span className="chip sev-warning">{t("rss.subgroups.unseen")}</span>
                        ) : (
                          row.feeds.map((feed) => (
                            <span className="chip sev-success" key={feed.feed_id ?? "none"}>
                              {feed.feed_name} · {feed.items}
                              {feed.last_seen ? ` · ${formatTime(feed.last_seen)}` : ""}
                            </span>
                          ))
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}

              <h3 className="subgroup-section">
                {t("rss.subgroups.silentTitle").replace("{days}", String(days))}
                <span className="subgroup-window">
                  {WINDOWS.map((option) => (
                    <button
                      key={option}
                      className={`button ${days === option ? "primary" : "secondary"}`}
                      onClick={() => setDays(option)}
                    >
                      {option}
                    </button>
                  ))}
                </span>
              </h3>
              <p className="muted">{t("rss.subgroups.silentHint")}</p>
              {data.silent_anime.length === 0 ? (
                <p className="muted">{t("rss.subgroups.silentNone")}</p>
              ) : (
                <ul className="subgroup-list">
                  {data.silent_anime.map((anime) => (
                    <li className="subgroup-silent" key={anime.anime_id}>
                      <strong>{anime.name}</strong>
                      <span className="muted">
                        {anime.last_seen
                          ? t("rss.subgroups.lastHeard").replace("{date}", formatTime(anime.last_seen))
                          : t("rss.subgroups.neverHeard")}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      </section>
    </div>
  );
}

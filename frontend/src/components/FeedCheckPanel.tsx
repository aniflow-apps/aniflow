import { useCallback, useState } from "react";
import {
  connectRelease,
  getAnime,
  linkAnimeFolder,
  runAnalyze,
  type AnalyzeItem,
  type AnimeDetail,
} from "../api/client";
import { t } from "../i18n";
import { AnalyzeResultsModal } from "./AnalyzeResultsModal";
import { profileMisfit, releasesFor } from "../feedForAnime";

/**
 * "Is this show findable in my feeds, and would my profile accept it?"
 *
 * Replaces the feed-source panel, which asked which feed to read and stored the
 * answer per anime. That answer was never used: a scan reads every enabled feed
 * and matches each item against every tracked anime, so a per-anime feed only
 * implied a relationship that does not exist. The check now reads all of them,
 * which is the same thing the scheduler does.
 *
 * Picking a result still links the library folder rather than downloading -
 * that is how an anime gets a folder derived from a real release name.
 */
export function FeedCheckPanel({
  anime,
  onUpdatedDetail,
}: {
  anime: AnimeDetail;
  onUpdatedDetail?: (anime: AnimeDetail) => void;
}) {
  const [items, setItems] = useState<AnalyzeItem[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [emptyRun, setEmptyRun] = useState(false);

  const runCheck = useCallback(async () => {
    setSearching(true);
    setError(null);
    setEmptyRun(false);
    try {
      // No source ids: every enabled feed.
      const result = await runAnalyze([]);
      // Only what is about this anime (build 153) - see feedForAnime.ts.
      const mine = releasesFor(result.items, anime.id);
      setItems(mine);
      if (mine.length === 0) setEmptyRun(true);
      else setModalOpen(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSearching(false);
    }
  }, [anime.id]);

  async function linkFolderFrom(item: AnalyzeItem) {
    setError(null);
    try {
      await linkAnimeFolder(anime.id, item.raw_name);
      setModalOpen(false);
      onUpdatedDetail?.(await getAnime(anime.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function teachTitle(item: AnalyzeItem) {
    try {
      await connectRelease(anime.id, item.raw_name);
      await runCheck();  // re-run, so the taught title now reads MATCHED
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return <>
    <section className="detail-panel">
      <div className="status-line">
        <h2>{t("feed.checkTitle")}</h2>
        <button className="button secondary" disabled={searching} onClick={() => void runCheck()}>
          {searching ? t("feed.searching") : t("feed.check")}
        </button>
      </div>
      <p className="muted">{t("feed.checkHint")}</p>
      {emptyRun && <p className="muted">{t("feed.checkNothingForThis")}</p>}
      {error && <p className="error-message">{error}</p>}
    </section>

    <AnalyzeResultsModal
      open={modalOpen}
      items={items}
      highlightAnimeId={anime.id}
      onClose={() => setModalOpen(false)}
      onSelect={(item) => void linkFolderFrom(item)}
      onAccept={(item) => void teachTitle(item)}
      note={(item) => {
        const misfit = profileMisfit(item, anime.profile);
        return misfit ? t(`feed.misfit.${misfit}`) : null;
      }}
    />
  </>;
}

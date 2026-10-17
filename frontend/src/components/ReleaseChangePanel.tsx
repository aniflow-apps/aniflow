import { useCallback, useEffect, useState } from "react";
import {
  deleteLeftovers,
  getAnime,
  getAppSettings,
  listLeftovers,
  reacquirePlan,
  refillMetadata,
  scanAnimeFiles,
  unskipEpisodes,
  updateAnime,
  updateAppSettings,
  type AnimeDetail,
  type AnimeSummary,
  type LeftoverFile,
  type ReacquirePlan,
  type RefillResult,
  type ScanFilesResult,
} from "../api/client";
import { t } from "../i18n";
import { AssignFilesDialog } from "./AssignFilesDialog";

/**
 * Changing which release an anime is kept in (build 87).
 *
 * The situation: you took whichever group was fastest, watched a season, and
 * now you would rather have the group that encodes better - or the same group
 * at 1080p, or anything at all that is not 10-bit because your television
 * transcodes it.
 *
 * Three buttons, because there are three questions and they want different
 * defaults carried into the search:
 *
 * * **higher quality** keeps the subgroup and raises the resolution;
 * * **lower quality** keeps the subgroup and lowers it;
 * * **change subgroup** keeps the resolution and clears the subgroup.
 *
 * All three open the same in-between dialog the add-anime flow uses, so
 * nothing new has to be learned and both can be changed at once if that is
 * what you meant.
 *
 * **What happens to the files you already have** is chosen here rather than
 * assumed, because both answers are destructive: *replace* deletes them and
 * *backup* renames them to .backup and leaves them for you. The panel says
 * how many files, by name, before either button is pressed - "delete eleven
 * files" is not something to discover afterwards.
 *
 * That choice is **remembered** (build 90). It was local state, so it went
 * back to "backup" on every visit - which sounds harmless until you notice
 * the other direction: somebody who has settled on one answer re-picks it
 * every time, and re-picking a destructive setting from a dropdown is how it
 * eventually gets picked wrong. It is saved to aniflow.yaml as
 * ``reacquire_mode``, the moment it changes, because a preference that needs
 * a Save button somewhere else is not remembered in any useful sense.
 */

/** The ladder the quality buttons step along. */
const QUALITIES = ["480p", "720p", "1080p", "2160p"] as const;

/**
 * What can happen to the files you already have. Two answers (build 124).
 *
 * "Keep them until the new release arrives" was the third, added in build
 * 116, and it is gone from here on request: *"We should remove the 'Keep them
 * until the new release' option. This is a bit confusing."*  It is - it is
 * the only one where both copies sit in the library at once, which Shoko and
 * Jellyfin then both show.
 *
 * Removed from the *choice*, not from the server: an installation may have
 * files awaiting their replacement right now, and those still resolve. What
 * this does is stop anybody choosing it again.
 */
/* "keep" (build 189) is the one that was removed for the reason above, back
   by request and with the reason said out loud: it is offered last, never
   chosen for you, and choosing it shows a red note that the episode will be
   listed twice by a media server such as Jellyfin. Somebody running AniFlow
   with Shoko Server alone does not mind, which is why it is allowed at all. */
const MODES = ["backup", "replace", "keep"] as const;
type Mode = (typeof MODES)[number];

export function ReleaseChangePanel({
  anime,
  onStart,
  onFixMissing,
  onMatchOnly,
  onUpdated,
}: {
  anime: AnimeDetail;
  /** Open the "fill in what is missing" dialog (build 117). */
  onFixMissing?: () => void;
  /** Open the same dialog in match-only mode (build 123). */
  onMatchOnly?: () => void;
  /** The anime came back changed - the ignore switch writes it. */
  onUpdated?: (anime: AnimeSummary) => void;
  /** Open the in-between dialog with these defaults. */
  onStart: (options: {
    mode: string;
    keepSubgroup: boolean;
    resolution: string | null;
  }) => void;
}) {
  /* Null until the saved answer arrives, so the first paint cannot show
     "backup" to somebody who chose "replace" and then swap under them. */
  const [mode, setMode] = useState<string | null>(null);
  /* Old files an earlier swap renamed and left (build 119). Shown here
     because this is the panel that made them, and because a renamed file
     nobody is told about is a manual step nobody knows they have. */
  const [kept, setKept] = useState<LeftoverFile[]>([]);
  const [clearing, setClearing] = useState(false);
  const [plan, setPlan] = useState<ReacquirePlan | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [ignoring, setIgnoring] = useState(false);
  /* The episodes the AniDB list said you have (item 36b), and the press that wants them after all. */
  const [unskipping, setUnskipping] = useState(false);
  const skipped = anime.episodes.filter((episode) => episode.skip_reason).length;
  /* Scanning the folder (build 198): what it found, shown under the buttons. */
  const [scanning, setScanning] = useState(false);
  const [scanned, setScanned] = useState<ScanFilesResult | null>(null);
  /* The window for saying by hand which file is which episode (build 213). */
  const [assigning, setAssigning] = useState(false);
  /* Fix missing details (build 207): ask the calendar and the provider again, and fill in the list. */
  const [refilling, setRefilling] = useState(false);
  const [refilled, setRefilled] = useState<RefillResult | null>(null);
  const [refillError, setRefillError] = useState<string | null>(null);

  async function fixDetails() {
    setRefilling(true);
    setRefilled(null);
    setRefillError(null);
    try {
      setRefilled(await refillMetadata(anime.id));
      onUpdated?.(await getAnime(anime.id));
    } catch (error) {
      setRefillError(error instanceof Error ? error.message : String(error));
    } finally {
      setRefilling(false);
    }
  }

  async function scanFolder() {
    setScanning(true);
    try {
      setScanned(await scanAnimeFiles(anime.id));
      onUpdated?.(await getAnime(anime.id));
    } catch (error) {
      setScanned({
        folder: null, files_scanned: 0, episodes_found: 0, completed: 0, created: 0, vanished: 0,
        found_subgroup: null, found_resolution: null, release_name: null, filled: [],
        problems: [error instanceof Error ? error.message : String(error)],
      });
    } finally {
      setScanning(false);
    }
  }

  useEffect(() => {
    getAppSettings()
      .then((settings) => {
        const saved = settings.reacquire_mode as Mode;
        if (MODES.includes(saved)) return setMode(saved);
        /* A mode that is no longer offered - "when_replaced" from build 116.
           Showing the dropdown blank would be one problem and showing
           "Keep as .backup" over a setting that still says otherwise would be
           a worse one, so it is written back to the safe answer. Backup
           rather than replace: this runs without anybody asking, and the one
           that deletes files is not a thing to switch somebody to. */
        chooseMode("backup");
      })
      .catch(() => setMode("backup"));
    // Once, on mount. chooseMode is stable for the life of this panel.
  }, []);

  useEffect(() => {
    if (mode === null) return;
    reacquirePlan(anime.id, mode).then(setPlan).catch(() => setPlan(null));
  }, [anime.id, mode]);

  const readKept = useCallback(() => {
    void listLeftovers(anime.id)
      .then((answer) => setKept(answer.files.filter((file) => file.kind === "renamed")))
      .catch(() => setKept([]));
  }, [anime.id]);
  useEffect(readKept, [readKept]);

  async function removeKept() {
    setClearing(true);
    try {
      await deleteLeftovers(anime.id);
      readKept();
    } catch {
      /* the list stays, so it can be tried again */
    } finally {
      setClearing(false);
    }
  }

  /**
   * Stop - or resume - looking for releases of this anime (build 123).
   *
   * Waited for, unlike the mode preference below: this one changes what the
   * server does on its next pass, and a checkbox that springs back because
   * the write failed is better than one that lies.
   */
  async function setIgnored(next: boolean) {
    setIgnoring(true);
    try {
      onUpdated?.(await updateAnime(anime.id, { releases_ignored: next }));
    } catch {
      /* the checkbox stays where it was */
    } finally {
      setIgnoring(false);
    }
  }

  async function fetchSkipped() {
    setUnskipping(true);
    try {
      await unskipEpisodes(anime.id);
      onUpdated?.(await getAnime(anime.id));
    } catch {
      /* the mark stays, so it can be tried again */
    } finally {
      setUnskipping(false);
    }
  }

  /** Chosen and saved. The save is not waited for - the panel is usable
   *  either way, and a failed write is the next visit's problem, not this
   *  click's. */
  function chooseMode(next: string) {
    setMode(next);
    // "keep" is never remembered (build 189): it puts a second copy of every
    // episode in the library, so it is chosen for the change in hand and the
    // panel opens on a safe answer next time.
    if (next === "keep") return;
    void updateAppSettings({ reacquire_mode: next as Mode }).catch(() => undefined);
  }

  /* The resolution the episodes are actually in, so the buttons can name the
     step rather than saying "higher" and leaving you to work out from what.
     Read from the profile first - that is what AniFlow searches for - and
     from a stored filename when no profile says. */
  const current = anime.profile?.resolution ?? guessResolution(anime) ?? null;
  const index = current ? QUALITIES.indexOf(current as (typeof QUALITIES)[number]) : -1;
  const higher = index >= 0 && index < QUALITIES.length - 1 ? QUALITIES[index + 1] : null;
  const lower = index > 0 ? QUALITIES[index - 1] : null;

  return (
    <>
    <aside className="detail-panel">
      <h2>{t("release.title")}</h2>

      <label className="field" style={{ marginBottom: 10 }}>
        <span>{t("release.mode")}</span>
        <select
          value={mode ?? "backup"}
          disabled={mode === null}
          onChange={(event) => chooseMode(event.target.value)}
        >
          {MODES.map((option) => (
            <option key={option} value={option}>{t(`release.mode.${option}`)}</option>
          ))}
        </select>
      </label>

      {mode === "keep" && (
        <p className="keep-warning" role="alert">{t("release.keepWarning")}</p>
      )}

      {/* What it will do to the files, before anything is pressed. */}
      {/* Yellow whichever it is (build 124). It was yellow only for
          "delete", which made the other answer read as the harmless one -
          and renaming twenty-one files is not nothing. *"Make all 3 of them
          yellow."*  The sentence lost its second half too: the episode count
          is on screen already, and *"Long text is hard to read."* */}
      {plan && plan.episodes > 0 && mode === "replace" && (
        <p className="warning-message">
          {t("release.willDelete")
            .replace("{files}", String(plan.files.length))
            .replace("{episodes}", String(plan.episodes))}
          {plan.files.length > 0 && (
            <>
              {" "}
              <button
                type="button"
                className="link-button"
                onClick={() => setExpanded(!expanded)}
              >
                {expanded ? t("release.hideFiles") : t("release.showFiles")}
              </button>
            </>
          )}
          {expanded && (
            <small className="muted" style={{ display: "block", marginTop: 6 }}>
              {plan.files.join(" · ")}
            </small>
          )}
        </p>
      )}
      {plan && plan.episodes === 0 && <p className="muted">{t("release.nothingOwned")}</p>}

      {/* What a previous change left behind, and the one button that
          deals with it. Above the buttons that would make more. */}
      {kept.length > 0 && (
        <div className="leftover-note" title={t("release.leftoversHint")}>
          <p className="muted" style={{ margin: "0 0 6px" }}>
            {t("release.leftovers").replace("{n}", String(kept.length))}
          </p>
          <small className="muted" style={{ display: "block", marginBottom: 8 }}>
            {kept.slice(0, 3).map((file) => file.filename).join(" · ")}
            {kept.length > 3 && " …"}
          </small>
          <button className="button secondary" disabled={clearing} onClick={() => void removeKept()}>
            {t("release.deleteLeftovers")}
          </button>
        </div>
      )}

      <div className="action-stack">
        <button
          className="button secondary"
          disabled={!higher}
          title={higher ? undefined : t("release.noHigher")}
          onClick={() => onStart({ mode: mode ?? "backup", keepSubgroup: true, resolution: higher })}
        >
          {higher
            ? t("release.toHigher").replace("{quality}", higher)
            : t("release.atBest")}
        </button>
        <button
          className="button secondary"
          disabled={!lower}
          title={lower ? undefined : t("release.noLower")}
          onClick={() => onStart({ mode: mode ?? "backup", keepSubgroup: true, resolution: lower })}
        >
          {lower ? t("release.toLower").replace("{quality}", lower) : t("release.atWorst")}
        </button>
        {/* One label, whatever the current group is (build 94). It read
            "Change subgroup (now Erai-raws)", which grew the button to two
            lines and put the group in two places - the profile above says it
            already. */}
        <button
          className="button secondary"
          onClick={() => onStart({ mode: mode ?? "backup", keepSubgroup: false, resolution: current })}
        >
          {t("release.changeSubgroup")}
        </button>
      </div>
    </aside>

    {/* A box of its own (build 124), for the three things that change what
        AniFlow *knows* rather than what is on disk. Nothing in here deletes,
        renames or downloads over anything, which is precisely what was not
        obvious while they sat under a dropdown offering to delete
        twenty-one files. */}
    <aside className="detail-panel">
      <h2>{t("release.fixTitle")}</h2>
      <p className="muted">{t("release.fixHint")}</p>

      <div className="action-stack">
        {/* Fills the gaps: the episodes with nothing behind them, and only
            those. An episode that already has its file is not touched. */}
        <button
          className="button secondary"
          onClick={() => onFixMissing?.()}
          title={t("release.fixMissingHint")}
        >
          {t("release.fixMissing")}
        </button>
        {/* Works out what the files already here were published as, and
            fetches nothing (build 123). */}
        <button
          className="button secondary"
          onClick={() => onMatchOnly?.()}
          title={t("release.matchOnlyHint")}
        >
          {t("release.matchOnly")}
        </button>
        {/* Reads the folder this anime lives in: which episode each file is,
            who released it, at what quality. Reads only (build 198). */}
        <button
          className="button secondary"
          disabled={scanning}
          onClick={() => void scanFolder()}
          title={t("release.scanFilesHint")}
        >
          {scanning ? t("release.scanning") : t("release.scanFiles")}
        </button>
        {/* What the other three do not: the facts about the show - episode count, genres, cover,
            airing time. Never overwrites what the user set. */}
        <button
          className="button secondary"
          disabled={refilling}
          onClick={() => void fixDetails()}
          title={t("release.fixDetailsHint")}
        >
          {refilling ? t("release.fixDetailsRunning") : t("release.fixDetails")}
        </button>
      </div>
      {(refilled || refillError) && (
        <div className="scan-files-result">
          {refillError && <p className="reasons">{refillError}</p>}
          {refilled && (
            <p className={refilled.filled.length ? "episode-hint" : "muted"}>
              {refilled.unanswerable
                ? t("debug.refillUnanswerable")
                : refilled.filled.length
                  ? t("debug.refillFilled").replace("{fields}", refilled.filled.map((code) => t(`health.missing.${code}`)).join(", "))
                  : t("debug.refillNothing")}
              {refilled.conflicts > 0 && ` ${t("debug.refillConflicts")}`}
            </p>
          )}
        </div>
      )}
      {scanned && (
        <div className="scan-files-result">
          {scanned.files_scanned > 0 && (
            <p className="muted">
              {t("release.scanFilesDone")
                .replace("{files}", String(scanned.files_scanned))
                .replace("{episodes}", String(scanned.episodes_found))
                .replace("{group}", scanned.found_subgroup ?? "—")
                .replace("{quality}", scanned.found_resolution ?? "—")}
            </p>
          )}
          {scanned.filled.length > 0 && (
            <p className="muted">
              {t("release.scanFilesFilled").replace("{what}", scanned.filled.map((item) => t(`release.scanFilled.${item}`)).join(", "))}
            </p>
          )}
          {scanned.problems.map((problem) => (
            <p className="reasons" key={problem}>{problem}</p>
          ))}
          {/* Some files could not be matched to an episode: the person can say which is which. */}
          {scanned.files_scanned > scanned.episodes_found && (
            <button className="button secondary" onClick={() => setAssigning(true)} title={t("release.assignByHandHint")}>
              {t("release.assignByHand")}
            </button>
          )}
        </div>
      )}
      {assigning && (
        <AssignFilesDialog
          animeId={anime.id}
          onClose={() => setAssigning(false)}
          onSaved={() => {
            setScanned(null);
            void getAnime(anime.id).then((fresh) => onUpdated?.(fresh));
          }}
        />
      )}

      {skipped > 0 && (
        <div className="scan-files-result">
          <p className="muted">{t("release.skippedTitle").replace("{n}", String(skipped))}</p>
          <button className="button secondary" disabled={unskipping} onClick={() => void fetchSkipped()} title={t("release.skippedHint")}>
            {t("release.skippedButton")}
          </button>
        </div>
      )}
      {/* And the answer that is none of the above: stop asking. */}
      <label className="switch-row">
        <input
          type="checkbox"
          checked={!!anime.releases_ignored}
          disabled={ignoring}
          onChange={(event) => void setIgnored(event.target.checked)}
        />
        <span>
          {t("release.ignore")}
          <small className="muted">{t("release.ignoreHint")}</small>
        </span>
      </label>
    </aside>
    </>
  );
}

/**
 * The resolution the files on disk are in, when no profile states one.
 *
 * Read off a stored filename, which is where a release puts it. A guess, and
 * only used to label the buttons - what actually gets searched for is what
 * the dialog is given and what the user leaves in it.
 */
function guessResolution(anime: AnimeDetail): string | null {
  for (const episode of anime.episodes) {
    const name = episode.media.filename ?? episode.download?.filename ?? "";
    const found = /\b(480p|720p|1080p|2160p|4k)\b/i.exec(name);
    if (found) return found[1].toLowerCase() === "4k" ? "2160p" : found[1].toLowerCase();
  }
  return null;
}

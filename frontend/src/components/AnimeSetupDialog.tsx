import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  addAnime,
  listLibraries,
  type Library,
  convertAnime,
  getAnime,
  inspectSearch,
  resolveNumbering,
  type Numbering,
  getAppSettings,
  inspectNames,
  listIndexers,
  listSources,
  listTemplates,
  fixMissingEpisodes,
  getBatchFiles,
  matchOnly,
  reacquireAnime,
  scanSelection,
  type AnimeSearchResult,
  type MatchOnlyResult,
  type AnimeSummary,
  type BatchPreview,
  type ScannedRelease,
  type FeedSource,
  type Indexer,
  type InspectedRelease,
  type ProfileTemplate,
  type SelectionScan,
  type InspectName,
  type StorageMode,
} from "../api/client";
import { t } from "../i18n";
import { bestSeeders } from "../seeders";
import { SeederMark } from "./SeederMark";
import { batchEpisodes, batchRows, leftAlone, pickBatch } from "../batchChoice";
import { fixIsPossible } from "../fixPossible";
import {
  DEFAULT_MODE,
  SEARCH_MODES,
  namesOfCandidate,
  pagesFor,
  runsFor,
  type SearchMode,
} from "../searchModes";
import {
  addSetup,
  convertBody,
  fixMissingBody,
  reacquireBody,
  type SetupChoice,
} from "../setupPayload";
import { readsAsAdult } from "../calendarView";
import { factsOf, rankLibraries, suggestedLibrary } from "../libraries";
import { useLockedPageScroll } from "../useLockedPageScroll";
import {
  groupReleases,
  LANGUAGE_TAGS,
  languageTagsOf,
  otherGroupHints,
  parsedTags,
  resolutionsFound,
  subgroupsFound,
  type InspectorFilters,
  type InspectorGroup,
  type ParsedFields,
} from "../inspectorRows";
import { RESOLUTIONS, normalizeGroup, qualityRank, rankOf, wantedQuality } from "../setupRows";
import {
  emptyReason,
  episodeRanges,
  mergeScans,
  numbering,
  scanQueries,
  termsThatFound,
} from "../selectionView";
import { anidbUrl } from "../anidbUrl";
import { formatDate } from "../formatWhen";
import { Cover } from "./Cover";
import { SelectionFilesPanel } from "./SelectionFilesPanel";
import { TemplateDialog } from "./TemplateDialog";

/**
 * The step between finding an anime and adding it (reworked in build 82).
 *
 * The job is unchanged - decide which releases *are* this show - but the way
 * it asks the tracker is, because the old way could not see enough to answer
 * honestly.
 *
 * **One broad search was never enough.** A tracker's feed page holds a fixed
 * number of items sorted by seeders, so a single query for a show spends that
 * budget on the most-seeded few of *every* quality. A real search returned two
 * 1080p, two 720p and two 480p files and stopped; the same search with "720p"
 * appended returned all ten 720p files. At 1080p, where the pool is crowded
 * with groups, whole groups were being pushed off the page.
 *
 * So this searches each name with each quality appended and merges the
 * answers. Twelve requests, one at a time, a second apart - trackers
 * rate-limit bursts - which takes about twelve seconds, and is why there is a
 * progress bar. Rows appear as they arrive rather than after all of it.
 *
 * **The file counts are gone.** Even fanned out, what comes back is a sample
 * capped by the page size, so "2 files" was a number nobody could trust: it
 * read as "this group released two episodes" when it meant "two is all I
 * managed to see". The real count arrives after adding, when the search can be
 * fully specific - name, group, quality and anything else that was picked -
 * and that answer lands on the detail page.
 *
 * **The filters narrow the answer, not the question.** Subgroup, quality and
 * one for anything else, all applied in the browser over what came back, so
 * widening one is instant and asks the tracker nothing. They start empty: a
 * filter that matches nothing now shows nothing, because it was typed on
 * purpose.
 */


/** Matches services/discovery.INDEXER_PAUSE_SECONDS, for the same reason. */
const PAUSE_MS = 1000;

/** A tag as a row shows it: the language tags in words, anything else as the parser wrote it. */
function tagLabel(tag: string): string {
  return (LANGUAGE_TAGS as readonly string[]).includes(tag) ? t(`setup.lang.${tag}`) : tag;
}

export function AnimeSetupDialog({
  candidate,
  convertingId,
  reacquire,
  fixMissing,
  startIndexerId,
  onClose,
  onAdded,
}: {
  candidate: AnimeSearchResult;
  /**
   * The indexer an existing anime was chosen from (item 44). The dropdown opens on it instead of on the
   * default, and what is chosen is saved back on the anime. Null for an anime that follows the default.
   */
  startIndexerId?: string | null;
  /**
   * Set when this is converting an anime that already exists rather than
   * adding a new one. The questions are identical - which releases are this
   * show - so the dialog is the same; only where the answer goes differs.
   */
  convertingId?: string;
  /**
   * Changing which release an existing anime is kept in (build 87).
   *
   * The same dialog, a third job: the anime already exists and what changes
   * is the group or the quality. The filters open with what the button
   * implied - a quality change keeps the group, a group change keeps the
   * quality - and confirming clears the episodes and deals with the old
   * files before the normal search takes over.
   */
  reacquire?: {
    animeId: string;
    /** replace deletes the old files; backup renames them to .backup. */
    mode: string;
    subgroup: string | null;
    resolution: string | null;
  };
  /**
   * Filling the gaps in an entry that already exists (build 117).
   *
   * The fourth job, and the gentlest: nothing is cleared, nothing is
   * deleted, and an episode that already has its file is not touched. It
   * opens with what the entry already knows - its group, its quality, its
   * name - so the ordinary case is opening it and pressing the button.
   *
   * Needed because the "Only in Shoko" category went away: an adopted series
   * goes straight to its detail page, and one missing four of ten episodes
   * needs something to press that is not "change the release" - that one
   * clears the episodes it has, which is the opposite of what is wanted.
   */
  fixMissing?: {
    animeId: string;
    subgroup: string | null;
    resolution: string | null;
    /** The release name the entry is matched by (build 210), typos and all: the best search term there is. */
    releaseName?: string | null;
    /**
     * Match and write down, download nothing (build 123).
     *
     * The fifth job, and a flag on the fourth rather than a prop of its own
     * because the *questions* are identical - which release is this, from
     * which group, at what quality - and only the answer's destination
     * differs. Asked for by somebody whose adopted library had a folder name
     * where its release name should be: *"we need to make a new scan and
     * match the subgroup and quality to this files, but not download
     * anything, just matching."*
     */
    matchOnly?: boolean;
  };
  onClose: () => void;
  onAdded: (anime: AnimeSummary) => void;
}) {
  const [templates, setTemplates] = useState<ProfileTemplate[]>([]);
  const [sources, setSources] = useState<FeedSource[]>([]);
  /** Which search this dialog makes. The quickest good one to begin with. */
  /* Fixing an entry that already has a release name opens on that name, as a custom search with its
     group and quality added (build 210): the name is already known, every typo and variation of it. */
  /* Without a release name (an anime Shoko added) there is nothing known to search by, and an older show's
     releases are not on the first page of results sorted by seeders - which is all Quick reads. Full reads every
     page, and fixing missing episodes is a deliberate act that can afford it (build 213). */
  const [mode, setMode] = useState<SearchMode>(
    fixMissing ? (fixMissing.releaseName ? "custom" : "full") : DEFAULT_MODE,
  );
  /* Which site to search (build 219). Offered only when more than one is switched on; empty means the default,
     which is what every search asked before there could be a second. */
  /* The episodes this anime already has a file for, when the dialog is filling its gaps (build 221): the list
     shows what the group offers, and these are dimmed in it rather than looking like work to do. */
  const [owned, setOwned] = useState<ReadonlySet<number>>(() => new Set());
  useEffect(() => {
    if (!fixMissing || fixMissing.matchOnly) return;
    let current = true;
    void getAnime(fixMissing.animeId)
      .then((found) => {
        if (!current) return;
        setOwned(new Set(found.episodes.filter((episode) => episode.media.filename).map((episode) => episode.number)));
      })
      .catch(() => { /* nothing is dimmed; the list is still right */ });
    return () => { current = false; };
  }, [fixMissing?.animeId, fixMissing?.matchOnly]);
  const [indexers, setIndexers] = useState<Indexer[]>([]);
  const [indexerId, setIndexerId] = useState("");
  /** Every name AniFlow holds for this show, for the modes to build from. */
  const [names, setNames] = useState<InspectName[]>(() => namesOfCandidate(candidate));
  /** Whether the library is AniFlow's or Shoko Server's, for the folder label. */
  const [storage, setStorage] = useState<StorageMode>("standalone");
  /* The page behind stays where it is while this is open (build 120,
     shared in 124). This was written here and nowhere else, so the other
     eight dialogs all scrolled the page behind them - reported against the
     configuration one. See useLockedPageScroll.ts. */
  useLockedPageScroll();

  /* Whether this show's first episode exists yet (build 121).
     Reported after adding something from a future season and being walked
     through release matching for releases nobody has published: *"The set up
     release matching which pops up should match this and should for example
     not do a name search."*

     The date decides, not the lifecycle - providers update that late, and a
     show that started on Tuesday is airing whatever the record says. No date
     at all is not treated as unaired: plenty of finished shows have none. */
  const notAiredYet = (() => {
    if (!candidate.air_start_date) return false;
    const starts = new Date(candidate.air_start_date);
    return !Number.isNaN(starts.getTime()) && starts.getTime() > Date.now();
  })();
  /* Only for a brand new anime. Changing the release of something already on
     the watchlist, or converting it, is a deliberate act about releases - and
     the dialog should do what it was opened for. */
  const asFuture = notAiredYet && !reacquire && !fixMissing && !convertingId;

  /** Set when Cancel is pressed while a search is running. */
  const cancelled = useRef(false);
  /** What "fix missing episodes" found and fetched, once it has run. */
  const [fixResult, setFixResult] = useState<{
    found: number;
    queued: number;
    skipped: number;
    batch_queued?: number;
    batch_episodes?: number[];
    batch_error?: string | null;
  } | null>(null);
  /** What a match-only scan wrote down, once it has run (build 123). */
  const [matchResult, setMatchResult] = useState<MatchOnlyResult | null>(null);
  /** Set once the start number is typed in, so nothing writes over it. */
  const startTouched = useRef(false);
  /**
   * Where this season begins, when the selected row is numbered from the
   * start of the franchise (build 112).
   *
   * Null until something works it out. `start` is what will be written down -
   * typed in, or resolved - and the offset stored on the anime is `start - 1`.
   */
  const [resolved, setResolved] = useState<Numbering | null>(null);
  const [startAt, setStartAt] = useState("");
  const [resolving, setResolving] = useState(false);
  const [numberingError, setNumberingError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [feedSourceId, setFeedSourceId] = useState("");

  // The season designation and any colon are already off: release names carry
  // neither, and searching for the provider's exact wording finds nothing.
  const [searchName, setSearchName] = useState(
    fixMissing?.releaseName
      || candidate.search_hint || candidate.title_romaji || candidate.title_english || candidate.display_name,
  );
  // Once the name is edited it is an instruction, so the search stops adding
  // the provider's other spelling of its own accord.
  const nameTouched = useRef(Boolean(fixMissing?.releaseName));
  // The folder follows title_folder_order, not what the card is called: the
  // display name has its own setting since build 85, and a name on screen is
  // not a name on disk.
  const [manualName, setManualName] = useState(candidate.folder_hint || candidate.display_name);
  /* Which library it goes into (build 192). Only when adding: the other
     jobs of this dialog act on an anime that already has one. */
  const [libraryList, setLibraryList] = useState<Library[]>([]);
  const [libraryId, setLibraryId] = useState<number | null>(null);
  const libraryTouched = useRef(false);
  // Once the folder is typed in, the selection stops writing to it.
  const folderTouched = useRef(false);

  /* Opened with what the button meant: a quality change keeps the group and
     names the new resolution, a group change keeps the resolution and leaves
     the group empty so there is something to search for. Both are still
     editable - this is a starting point, not a decision. */
  const [filters, setFilters] = useState<InspectorFilters>({
    subgroup: reacquire?.subgroup ?? fixMissing?.subgroup ?? "",
    resolution: reacquire?.resolution ?? fixMissing?.resolution ?? "",
    tag: "",
  });

  /* The result filter (build 176), apart from the search fields above it.
     The search asks the tracker; this only narrows what came back, so it
     costs nothing to change. Opened with the group and quality a "change the
     release" button meant, as the search fields used to be. */
  const [resultText, setResultText] = useState(reacquire?.subgroup ?? fixMissing?.subgroup ?? "");
  const [resultQuality, setResultQuality] = useState(reacquire?.resolution ?? fixMissing?.resolution ?? "");
  const [releases, setReleases] = useState<InspectedRelease[]>([]);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  // Distinct from "found nothing": the indexer never answered.
  const [indexerError, setIndexerError] = useState<string | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  // Whether the current selection is AniFlow's guess or the user's decision.
  const pickedForYou = useRef(true);
  /** Set by "Clear subgroup": nothing is picked and nothing is picked for you. */
  const [noneChosen, setNoneChosen] = useState(false);

  /* What the selected row actually holds (build 95).
     The broad search above is a sample - a tracker sorts a page by seeders and
     cuts it off - so the row could only ever say which groups exist. This asks
     the narrow question for the row in front of you, and it is the same
     question the server asks when the anime is added. */
  const [scan, setScan] = useState<SelectionScan | null>(null);
  const [scanning, setScanning] = useState(false);
  /* How far along the scan is, in steps the browser can count - one request
     per name. A bar that measures nothing is a decoration; this one is the
     number of answers in hand out of the number expected. */
  const [scanStep, setScanStep] = useState<{ done: number; total: number }>({ done: 0, total: 0 });
  const [scanError, setScanError] = useState<string | null>(null);
  /* The second pane is open whenever a scan has something to show. It can
     be closed, which narrows the dialog back to one column - the list is the
     part you are working in, and somebody comparing rows may want the room. */
  // Which scan the answers coming back belong to. Changing rows while one is
  // in flight must not paint the old row's episodes into the new row.
  const scanFor = useRef<string | null>(null);
  /* What every row scanned so far found, kept until the dialog closes.
     Going back to a row costs nothing and asks the tracker nothing, and every
     row that has been looked at keeps its episode count - so the list shows
     what is known rather than only what is selected. Keyed by the question,
     so a row whose edition or search name changed is asked again. */
  const scanned = useRef(new Map<string, SelectionScan>());
  // Bumped when the store changes, because a ref does not re-render.
  const [scanRevision, setScanRevision] = useState(0);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(false);
  // Seeds the profile editor from whatever row is in front of you.
  const [templateSeed, setTemplateSeed] = useState<{ subgroups: string[]; resolution: string | null } | null>(null);

  // Guards the search that runs on open, so editing a field cannot retrigger it.
  const autoSearched = useRef(false);

  const defaultTemplate = useMemo(
    () => templates.find((tpl) => tpl.is_default) ?? (templates.length === 1 ? templates[0] : undefined),
    [templates],
  );

  useEffect(() => {
    void Promise.all([listTemplates(), listSources()])
      .then(([tpls, srcs]) => {
        setTemplates(tpls);
        setSources(srcs);
        const defaultSource = srcs.find((src) => src.is_default) ?? (srcs.length === 1 ? srcs[0] : undefined);
        setFeedSourceId(defaultSource?.id ?? "");
      })
      .finally(() => setReady(true));
    void listIndexers()
      .then((found) => {
        const on = found.filter((item) => item.enabled);
        setIndexers(on);
        // Starts on the default, the site every search asked until now.
        setIndexerId((current) => current
          || (startIndexerId && on.some((item) => item.id === startIndexerId) ? startIndexerId : "")
          || (on.find((item) => item.is_default) ?? on[0])?.id
          || "");
      })
      .catch(() => { /* no choice is offered; the default is asked, as before */ });
    // Every name the show goes by, and which library this deployment owns.
    // Both are local answers; neither is worth failing the dialog for.
    void inspectNames(candidate)
      .then((answer) => { if (answer.names.length) setNames(answer.names); })
      .catch(() => { /* the candidate's own three names will do */ });
    void getAppSettings()
      .then((settings) => setStorage(settings.mode))
      .catch(() => { /* the label falls back to the media folder */ });
  }, [candidate]);

  useEffect(() => {
    if (!ready || autoSearched.current) return;
    if (asFuture) {
      // Nothing has been published for a show that has not started, so
      // there is nothing to search for. The tracker is not asked at all -
      // twelve terms returning nothing is a slow way to say "not yet".
      autoSearched.current = true;
      return;
    }
    // Nothing is searched on open (build 175): *"stop the auto search when
    // you (or the app) opens the set up release matching"*. The Search
    // button is the way to start one.
    autoSearched.current = true;
  }, [ready]);

  /** Groups in the default profile - the ones you said you prefer. */
  const mine = useMemo(
    () => new Set((defaultTemplate?.subgroups ?? []).map(normalizeGroup).filter(Boolean)),
    [defaultTemplate],
  );
  /** Groups in every other saved profile. */
  const known = useMemo(() => {
    const names = new Set<string>();
    for (const tpl of templates) {
      if (tpl.id === defaultTemplate?.id) continue;
      for (const name of tpl.subgroups) {
        const normalized = normalizeGroup(name);
        if (normalized && !mine.has(normalized)) names.add(normalized);
      }
    }
    return names;
  }, [templates, defaultTemplate, mine]);

  /**
   * The searches this mode makes for this show (build 111).
   *
   * The dialog used to ask its own way - both provider spellings, each with
   * every quality appended, one page each - and the Release Inspector spent
   * builds 105 to 110 measuring what that is worth. It is worth 77 releases
   * where a well-formed name finds 700, because a tracker matches words and a
   * catalogue spelling is full of words no release group types.
   *
   * So the modes are shared now, and this picks one. An **edited name still
   * replaces the lot**: it is what the user is telling the tracker to look
   * for, and quietly searching two other things as well would make the answer
   * impossible to reason about.
   */
  const runs = useMemo(() => {
    const base = runsFor(
      mode,
      searchName,
      nameTouched.current ? [{ kind: "clean", value: searchName.trim(), native: false }] : names,
      false,
    );
    if (mode !== "custom") return base;
    /* In Custom the group, quality and extra words are part of what is asked
       of the tracker (build 176): the name alone, and the name with each
       group (or none) followed by the quality and the extra words. The server
       always searches the plain name first, so nothing is lost by asking. */
    const tail = [filters.resolution, filters.tag.trim()].filter(Boolean).join(" ");
    const groups = filters.subgroup.split(",").map((name) => name.trim()).filter(Boolean);
    const extras = (groups.length ? groups : [""])
      .map((group) => [group, tail].filter(Boolean).join(" "))
      .filter(Boolean);
    return extras.length ? base.map((run) => ({ ...run, extraTerms: extras })) : base;
  }, [mode, searchName, names, filters.subgroup, filters.resolution, filters.tag]);

  /** The AniDB id of the show being added, when the card carries one. */
  const anidbId = Number(candidate.provider_ids?.anidb ?? 0) || null;

  const scanNumbering = useMemo(
    () => numbering(scan?.episodes ?? [], candidate.episode_count),
    [scan, candidate.episode_count],
  );
  /**
   * Whether the selected row is numbered from the start of the franchise.
   *
   * Its own numbers say so: they run past what this season holds. Nothing
   * about the show decides it - two rows of the same show disagree, which is
   * the whole difficulty (build 114).
   */
  const rowIsAbsolute = scanNumbering.absolute;

  /**
   * How many episodes this season has, for working out its last number.
   *
   * The resolved chain first - it is AniDB's own count for this entry - then
   * the card's, which is whichever metadata source the calendar used.
   */
  const [countTyped, setCountTyped] = useState("");
  const selectedCount = Number(countTyped) || (resolved?.episode_count ?? candidate.episode_count ?? null);
  /**
   * How far the *selected row's* numbering runs past episode 1 (build 114).
   *
   * A property of the row, not of the show. The same dialog lists SubsPlease,
   * who count from the start of the franchise, and Erai-raws, who restart
   * every season - so a start number looked up for one must not be subtracted
   * from the other, which would leave the row with no episodes at all.
   */
  const offset = Math.max(0, (Number(startAt) || 0) - 1);
  /** This season's first episode, in the group's own numbering. */
  const firstNumber = offset + 1;
  /** And its last, which is the first plus the season's own length. */
  const lastNumber = selectedCount ? firstNumber + selectedCount - 1 : null;

  /**
   * What is already known about this show's numbering, asked on open.
   *
   * Reported: a show whose numbering had been worked out once still opened at
   * "Episodes 1-19", because nothing consulted the pool until the button was
   * pressed - which made a pool that remembers things look like one that
   * does not. This asks the moment the dialog opens, and reaches nobody: the
   * pool first, then Shoko's own cache, and never AniDB.
   */
  useEffect(() => {
    if (!anidbId) return;
    let current = true;
    resolveNumbering(anidbId, false)
      .then((found) => { if (current && found.absolute_start) setResolved(found); })
      .catch(() => { /* nothing known yet is the ordinary case */ });
    return () => { current = false; };
  }, [anidbId]);

  /**
   * Fill the start in for a row that needs one, until somebody types.
   *
   * Only for a row whose numbers say so: a per-season row gets nothing, and
   * picking one after the other clears what the first put there.
   */
  useEffect(() => {
    if (startTouched.current) return;
    if (rowIsAbsolute && resolved?.absolute_start) setStartAt(String(resolved.absolute_start));
    else if (!rowIsAbsolute) setStartAt("");
  }, [rowIsAbsolute, resolved]);

  async function resolveStart() {
    if (!anidbId) return;
    setResolving(true);
    setNumberingError(null);
    try {
      const found = await resolveNumbering(anidbId);
      setResolved(found);
      if (found.absolute_start) setStartAt(String(found.absolute_start));
      else setNumberingError(found.detail ?? t("setup.numbering.unknown"));
    } catch (err) {
      setNumberingError(err instanceof Error ? err.message : String(err));
    } finally {
      setResolving(false);
    }
  }

  async function runSearch() {
    const list = runs;
    if (!list.length) return;
    setSearching(true);
    cancelled.current = false;
    setSearchError(null);
    setIndexerError(null);
    setReleases([]);
    setProgress({ done: 0, total: list.length });
    const byId = new Map<string, InspectedRelease>();
    try {
      for (const [index, run] of list.entries()) {
        // One term per request so there is something honest to show progress
        // with, the mode's own page depth, and a pause between them because
        // trackers rate-limit bursts. Cancel is checked on both sides of that
        // pause: a search of eighty pages has to be stoppable.
        if (cancelled.current) break;
        if (index > 0) await new Promise((resume) => setTimeout(resume, PAUSE_MS));
        if (cancelled.current) break;
        const answer = await inspectSearch(run.base, {
          extraTerms: run.extraTerms,
          native: run.native,
          pages: pagesFor(mode),
          indexerId: indexerId || null,
        });
        for (const release of answer.releases) {
          const existing = byId.get(release.id);
          if (!existing) {
            byId.set(release.id, release);
            continue;
          }
          for (const found of release.found_by) {
            if (!existing.found_by.includes(found)) existing.found_by.push(found);
          }
        }
        // Painted as they arrive: twelve seconds of an empty box would read as
        // a broken dialog.
        setReleases([...byId.values()]);
        setProgress({ done: index + 1, total: list.length });
        if (!answer.reached_provider) {
          setIndexerError(answer.failures[0] ?? t("setup.noIndexer"));
          break;
        }
        if (answer.failures.length) setIndexerError((current) => current ?? answer.failures[0]);
      }
      setSearched(true);
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : String(err));
    } finally {
      setSearching(false);
    }
  }

  const groups = useMemo(() => {
    const rows = groupReleases(
      releases,
      { subgroup: "", resolution: resultQuality, tag: "" },
      { splitByFolder: true, splitByEdition: true },
    );
    // The text matches the group, the quality, the name and the file names
    // alike: every word typed has to be found somewhere in the row.
    const words = resultText.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return rows;
    return rows.filter((row) => {
      const haystack = [
        row.subgroup, row.resolution, row.title, row.folderName,
        ...row.edition, ...row.releases.map((release) => release.raw_name),
      ].filter(Boolean).join(" ").toLowerCase();
      return words.every((word) => haystack.includes(word));
    });
  }, [releases, resultText, resultQuality]);

  /**
   * Best row first: a group from your default profile, then one from any other
   * saved profile, then everything else - and within each, the best quality.
   */
  const ranked = useMemo(
    () =>
      [...groups].sort(
        (a, b) =>
          rankOf(a, mine, known) - rankOf(b, mine, known)
          // The default profile's quality leads (build 209): 720p by default means the 720p row is the pick.
          || wantedQuality(a, defaultTemplate?.resolution) - wantedQuality(b, defaultTemplate?.resolution)
          || qualityRank(a.resolution) - qualityRank(b.resolution)
          || (a.subgroup ?? "").localeCompare(b.subgroup ?? ""),
      ),
    [groups, mine, known, defaultTemplate],
  );

  // Pre-selected, never sticky: while nobody has clicked a row, the best row
  // stays selected as the filters move. A row the user chose is left alone
  // until it disappears from the list entirely.
  useEffect(() => {
    if (noneChosen) return;
    const stillThere = selectedKey !== null && ranked.some((row) => row.key === selectedKey);
    if (stillThere && !pickedForYou.current) return;
    const next = ranked[0]?.key ?? null;
    if (next === selectedKey) return;
    pickedForYou.current = true;
    setSelectedKey(next);
  }, [ranked, selectedKey, noneChosen]);

  const selected = noneChosen ? null : ranked.find((row) => row.key === selectedKey) ?? null;

  // The folder follows the selection until somebody types in it.
  useEffect(() => {
    if (!selected || folderTouched.current) return;
    setManualName(
      selected.folderName ?? selected.title ?? candidate.folder_hint ?? candidate.display_name,
    );
  }, [selected, candidate.folder_hint, candidate.display_name]);

  /**
   * What identifies one row's question, so an answer can be remembered
   * against it. The row (which carries its group, quality and edition), the
   * folder it matched, and the names being searched - change any of them and
   * it is a different question.
   */
  function tokenFor(row: InspectorGroup, reference: string, names: string[]): string {
    // The site is part of the question: the same group on another indexer is another answer.
    return `${indexerId}|${row.key}|${reference}|${names.join("|")}`;
  }

  /**
   * The names to ask under: the box's, plus the provider's other spelling.
   *
   * Decided in selectionView.ts, not here. The bug this build fixes was
   * exactly this list: the broad search used both names and the server used
   * one, so an anime whose group publishes under the other spelling was found
   * on screen and lost on the way to the database.
   */
  const queries = useMemo(() => {
    // A name the person typed is an instruction. Otherwise the group is asked about with the searches
    // that found it (build 212), not with the long provider title in the box, which the tracker cannot match.
    if (!nameTouched.current && selected) {
      const found = termsThatFound(selected.releases.map((release) => release.found_by ?? []));
      if (found.length) return found;
    }
    return scanQueries(searchName, candidate.title_english, { nameEdited: nameTouched.current });
  }, [searchName, candidate.title_english, selected]);

  /* The selected row, asked about specifically.
     Only once the broad search has finished: both go to the same tracker, and
     a scan racing twelve fan-out requests is the burst the pacing exists to
     avoid. */
  useEffect(() => {
    const reference = selected?.folderName ?? selected?.title ?? null;
    if (!selected || !reference || searching || !queries.length) return;
    const token = tokenFor(selected, reference, queries);
    // The key already carries the edition, so picking a different copy of the
    // same group and quality is a different question and gets asked.
    if (scanFor.current === token) return;
    scanFor.current = token;
    const remembered = scanned.current.get(token);
    if (remembered) {
      // Asked and answered. No request, no bar, no second visit to the
      // tracker for a question whose answer has not changed.
      setScan(remembered);
      setScanError(null);
      setScanning(false);
      return;
    }
    setScan(null);
    setScanError(null);
    setScanning(true);
    setScanStep({ done: 0, total: queries.length });

    void (async () => {
      let merged: SelectionScan | null = null;
      try {
        for (const [index, name] of queries.entries()) {
          // A pause between requests, because they all go to the same
          // tracker - the same rule the broad search above obeys.
          if (index > 0) await new Promise((resume) => setTimeout(resume, PAUSE_MS));
          // Ignore an answer to a question nobody is asking any more.
          if (scanFor.current !== token) return;
          const answer = await scanSelection({
            queries: [name],
            reference,
            // "" rather than null for a row whose releases credit no
            // group: null means "any group", and asking that for the Unknown
            // row brought back every other group's files as well.
            subgroup: selected.subgroup ?? "",
            resolution: selected.resolution,
            edition: selected.edition,
            editionNot: selected.editionNot,
            animeId: reacquire?.animeId ?? fixMissing?.animeId ?? convertingId ?? null,
            indexerId: indexerId || null,
          });
          if (scanFor.current !== token) return;
          merged = mergeScans(merged, answer);
          scanned.current.set(token, merged);
          setScanRevision((count) => count + 1);
          // Painted as they arrive rather than at the end: the first name
          // usually has the whole season, and waiting for the second to show
          // it would be a second of nothing for no reason.
          setScan(merged);
          setScanStep({ done: index + 1, total: queries.length });
        }
      } catch (err) {
        if (scanFor.current !== token) return;
        setScanError(err instanceof Error ? err.message : String(err));
      } finally {
        if (scanFor.current === token) setScanning(false);
      }
    })();
  }, [selected, searching, queries, indexerId, reacquire?.animeId, fixMissing?.animeId, convertingId]);

  /* A group that only has the season as one torrent (build 213, docs/batch-episodes-plan.md). Once its scan has
     come back, the batch's files are read (from its .torrent file, on the server) and the ones this anime is
     missing are named, so the button can fetch just those. Only for "Fix the missing episodes": adding and
     changing a release keep preferring single episodes. */
  const [batch, setBatch] = useState<{
    release: ScannedRelease;
    preview: BatchPreview | null;
    loading: boolean;
    error: string | null;
  } | null>(null);
  const batchRelease = useMemo(
    () => (fixMissing && !fixMissing.matchOnly ? pickBatch(scan?.releases) : null),
    [fixMissing, scan],
  );
  const batchRequest = batchRelease && scan ? `${batchRelease.id}|${scan.scan_ids.join(",")}|${offset}` : "";
  useEffect(() => {
    if (!batchRelease || !fixMissing || !scan || scanning || !batchRelease.id) {
      setBatch(null);
      return;
    }
    let current = true;
    setBatch({ release: batchRelease, preview: null, loading: true, error: null });
    getBatchFiles(fixMissing.animeId, {
      scan_ids: scan.scan_ids,
      release_id: batchRelease.id,
      episode_offset: offset > 0 ? offset : null,
    })
      .then((preview) => { if (current) setBatch({ release: batchRelease, preview, loading: false, error: null }); })
      .catch((reason) => {
        if (current) setBatch({ release: batchRelease, preview: null, loading: false, error: reason instanceof Error ? reason.message : String(reason) });
      });
    return () => { current = false; };
  }, [batchRequest, scanning, fixMissing?.animeId]);
  /** The episodes the batch would give that single files of the same group do not already cover. */
  const batchTake = useMemo(
    () => batchEpisodes(batch?.preview ?? null, scan?.episodes ?? [], offset),
    [batch, scan, offset],
  );

  /* What the episode numbers say about how this group counts. Derived here
     from the merged answer, because the scan arrives in pieces. */
  /* Both columns, always (build 120).
     They used to appear only once a row was selected and its scan had come
     back, so the dialog changed width and shape while being used. Asked for:
     *"lets add the Subgroup and Files screen always visible, not after you
     select the subgroup, both splits should be always visible"*. The files
     side says what it is waiting for until there is something to show. */
  const splitOpen = true;

  /* Where this season starts and ends. Rendered by the files panel since
     build 120 - *"move the First episode is / last episode will be also in
     this files screen"* - and built here, because the pool lookup, the manual
     override and the resolve button are all this component's state. */
  const numberingGroup = (
    <div className="setup-numbering">
      <label className="field">
        <span>{t("setup.numbering.start")}</span>
        <input
          value={startAt}
          inputMode="numeric"
          placeholder="1"
          onChange={(event) => {
            startTouched.current = true;
            setStartAt(event.target.value.replace(/[^0-9]/g, ""));
          }}
        />
      </label>
      <label className="field">
        <span>{t("setup.numbering.count")}</span>
        {/* The provider's count is sometimes wrong (a 12-episode season
            listed as 4), so this is the one place to correct it. */}
        <input
          value={countTyped || (selectedCount ?? "")}
          inputMode="numeric"
          onChange={(event) => setCountTyped(event.target.value.replace(/[^0-9]/g, ""))}
        />
      </label>
      <label className="field">
        <span>{t("setup.numbering.end")}</span>
        {/* Worked out from the other two, so it cannot disagree with them. */}
        <input value={lastNumber ?? ""} readOnly disabled />
      </label>
      {anidbId && (
        <button
          className="button secondary"
          disabled={resolving}
          title={t("setup.numbering.resolveHint")}
          onClick={() => void resolveStart()}
        >
          {resolving ? t("setup.numbering.resolving") : t("setup.numbering.resolve")}
        </button>
      )}
      {numberingError && <p className="warning-message">{numberingError}</p>}
    </div>
  );



  /**
   * Whether this row's numbering has to be decided at all (build 113).
   *
   * A group that restarts each season needs nothing and is asked nothing.
   * Once a start has been set - looked up or typed - the fields stay, because
   * that is where a correction goes.
   */
  const needsNumbering = true;

  /** What a row found last time it was asked, if it has been. */
  const rememberedFor = useCallback(
    (row: InspectorGroup): SelectionScan | null => {
      const reference = row.folderName ?? row.title ?? null;
      if (!reference || !queries.length) return null;
      return scanned.current.get(tokenFor(row, reference, queries)) ?? null;
    },
    // scanRevision is the dependency that matters: the store is a ref, so
    // this has to be re-made when something is written to it.
    [queries, scanRevision],
  );

  function choose(row: InspectorGroup) {
    pickedForYou.current = false;
    setNoneChosen(false);
    setSelectedKey(row.key);
    if (!folderTouched.current)
      setManualName(
        row.folderName ?? row.title ?? candidate.folder_hint ?? candidate.display_name,
      );
  }

  /**
   * What else the selection carries, beyond its group and quality.
   *
   * Only what was filtered on: everything the parser recognised would be a
   * wall of codecs and checksums, and this is the part that gets appended to
   * the search once the anime exists.
   */
  const selectedExtras = useMemo(() => {
    const needle = filters.tag.trim().toLowerCase();
    if (!selected || !needle) return [] as string[];
    const tags = new Set<string>();
    for (const release of selected.releases) {
      for (const tag of parsedTags(release.parsed as ParsedFields | null)) {
        if (tag.includes(needle)) tags.add(tag);
      }
    }
    return [...tags];
  }, [selected, filters.tag]);

  const qualities = useMemo(() => resolutionsFound(releases), [releases]);
  const groupsSeen = useMemo(() => subgroupsFound(releases), [releases]);

  /**
   * The profile whose rules the new anime gets.
   *
   * A profile is more than a group and a quality - it carries the censor
   * preference and the require-known checks too. So a pick that belongs to one
   * of your profiles is created under that profile's rules; anything else
   * inherits the default profile's rules with the picked group and quality
   * written over them.
   */
  function templateFor(subgroup: string | null): ProfileTemplate | undefined {
    const name = normalizeGroup(subgroup);
    if (!name) return defaultTemplate;
    if (defaultTemplate?.subgroups.some((entry) => normalizeGroup(entry) === name)) return defaultTemplate;
    return (
      templates.find((tpl) => tpl.subgroups.some((entry) => normalizeGroup(entry) === name)) ?? defaultTemplate
    );
  }

  /* Adding a new anime is the one job that needs a library chosen. */
  /* A new entry, a future one, or a tracker entry being converted: the three
     that are about to have files, so they need a library with a folder.
     Not the jobs that act on an anime that already has one. */
  const choosesLibrary = !reacquire && !fixMissing;
  useEffect(() => {
    if (!choosesLibrary) return;
    void listLibraries(true)
      .then((answer) => {
        setLibraryList(answer.libraries);
      })
      .catch(() => undefined);
  }, [choosesLibrary]);
  const rankedLibraries = useMemo(
    () => rankLibraries(libraryList, factsOf(candidate, readsAsAdult(candidate)), asFuture),
    [libraryList, candidate, asFuture],
  );
  // Opens on the best fit; once the user has chosen, their choice stays.
  useEffect(() => {
    if (libraryTouched.current || rankedLibraries.length === 0) return;
    setLibraryId(suggestedLibrary(rankedLibraries)?.id ?? null);
  }, [rankedLibraries]);

  async function confirm() {
    // The folder is whatever the user typed - selecting a row only pre-fills
    // it. The release name is the selection itself and is never overwritten
    // by a folder rename.
    const folderName = manualName.trim();
    if (!folderName) return;
    /* Everything the three payloads are built from, in one place. They used
       to be three hand-written literals sharing four local variables, and
       the release-change one quietly lacked the search query for a whole
       build - see setupPayload.ts. */
    const choice: SetupChoice = {
      folderName,
      searchName,
      extras: selectedExtras,
    // The scan already ran for this row; its tickets let the server write
    // down what was shown instead of asking the tracker again (build 100).
    scanIds: scan?.scan_ids ?? [],
      // What the numbering panel decided, if it was asked anything at all.
      episodeOffset: offset,
      // Only what was typed: the provider's own count needs no telling.
      episodeCount: Number(countTyped) || null,
      // And which AniDB entry it decided it about (build 116).
      anidbId,
      selected,
      subgroupFilter: filters.subgroup,
      resolutionFilter: filters.resolution,
    };
    const extra = {
      templateId: templateFor(choice.selected?.subgroup ?? filters.subgroup.split(",")[0]?.trim() ?? null)?.id ?? null,
      feedSourceId,
    };
    /* The site is remembered on the anime only when it is a choice: one that already had its own, or one
       picked away from the default. An anime that follows the default keeps following it (item 44). */
    const defaultIndexerId = (indexers.find((item) => item.is_default) ?? indexers[0])?.id ?? "";
    const rememberedIndexer = indexerId && (startIndexerId || indexerId !== defaultIndexerId) ? indexerId : null;
    setSaving(true);
    setSaveError(false);
    try {
      if (fixMissing?.matchOnly) {
        // Writes the name, the group and the quality, attaches what it
        // found, and stops. Nothing is queued, nothing is deleted and no
        // episode that has a file is touched - which is the whole reason
        // this is not one of the Change-the-release buttons.
        const result = await matchOnly(fixMissing.animeId, fixMissingBody(choice, null, rememberedIndexer));
        onAdded(await getAnime(fixMissing.animeId));
        setMatchResult(result);
        return;
      }
      if (fixMissing) {
        // Additive: find what the empty episodes can use, attach it, and
        // hand it to the torrent client. The ones that already have a file
        // are skipped by the same rules the Download button follows.
        const result = await fixMissingEpisodes(
          fixMissing.animeId,
          fixMissingBody(
            choice,
            batch?.release.id && scan && batchTake.length > 0
              ? {
                  scan_ids: scan.scan_ids,
                  release_id: batch.release.id,
                  episodes: batchTake,
                  episode_offset: offset > 0 ? offset : null,
                }
              : null,
            rememberedIndexer,
          ),
        );
        onAdded(await getAnime(fixMissing.animeId));
        setFixResult(result);
        return;
      }
      if (reacquire) {
        // Clear the episodes, deal with the files, and let the server look
        // for the new group's back catalogue - the same step, with the same
        // query, that adding an anime does.
        await reacquireAnime(reacquire.animeId, reacquireBody(choice, reacquire.mode, rememberedIndexer));
        // The detail page wants the whole anime back, and the summary it
        // needs is the front of it.
        onAdded(await getAnime(reacquire.animeId));
        return;
      }
      if (convertingId) {
        // The row exists; this gives it the one thing it was missing.
        onAdded(await convertAnime(convertingId, convertBody(choice, { ...extra, libraryId, indexerId: rememberedIndexer })));
        return;
      }
      onAdded(
        await addAnime(candidate, folderName, {
          ...addSetup(choice, extra),
          // No release name, no profile, no feed source - the server writes
          // none of them for a future entry, and sending them would only
          // look like they were considered.
          future: asFuture,
          library_id: choosesLibrary ? libraryId : undefined,
          indexer_id: indexerId || undefined,
        }),
      );
    } catch {
      setSaveError(true);
    } finally {
      setSaving(false);
    }
  }

  /* "Fix the missing episodes" has nothing to fix until the group shows episodes (build 207): a group
     with only batches, or none yet, left the button blue and pressable and it did nothing. Finding
     the release name only does not download, so it keeps its button. */
  const nothingToFix = Boolean(fixMissing && !fixMissing.matchOnly) && !fixIsPossible(scan, scanning, batchTake.length);
  const confirmDisabled = saving || !manualName.trim() || nothingToFix;
  /* What the fix did, in a sentence. Shown rather than closing the dialog:
     "nothing happened" and "four episodes are downloading" look identical
     from outside, and the first one needs the reason that comes with it. */
  const fixSummary = fixResult
    ? t("setup.fixResult")
        .replace("{found}", String(fixResult.found))
        .replace("{queued}", String(fixResult.queued))
        + (fixResult.batch_queued
          ? ` ${t("setup.fixBatchResult").replace("{n}", String(fixResult.batch_queued)).replace("{list}", (fixResult.batch_episodes ?? []).join(", "))}`
          : fixResult.batch_error
            ? ` ${t("setup.fixBatchError").replace("{reason}", fixResult.batch_error)}`
            : "")
    : matchResult
      ? (matchResult.stored.length
          ? t("setup.matchResult")
              .replace(
                "{fields}",
                matchResult.stored.map((code) => t(`health.missing.${code}`)).join(", "),
              )
              .replace("{matched}", String(matchResult.matched))
          : t("setup.matchNothing"))
      : null;
  const hasDefaultTemplate = !!defaultTemplate;
  const hasDefaultSource = sources.some((src) => src.is_default) || sources.length === 1;
  const seedRow = selected ?? ranked[0] ?? null;

  return (
    <div className="dialog-backdrop dialog-locks-scroll" role="presentation">
      <section className={`dialog wide tall${splitOpen ? " split" : ""}`} role="dialog" aria-modal="true" aria-label={t("setup.title")}>
        <header>
          <h2>{t("setup.title")}</h2>
          <button className="close" onClick={onClose} aria-label={t("add.cancel")}>×</button>
        </header>

        {/* The title, what this season is numbered, and - when the numbers
            need deciding - the two fields that decide them. All three belong
            together at the top: the range is the thing every other number on
            this page is derived from, and it used to be stated four times
            further down and nowhere up here (build 113). */}
        <div className="setup-header">
          <div className="selected-anime">
            <div style={{ width: 56 }}><Cover anime={candidate} /></div>
            <div className="setup-header-titles">
              <strong>{candidate.display_name}</strong>
              <small className="muted">
                {[candidate.title_romaji, candidate.title_native].filter(Boolean).join(" · ")}
              </small>
              <small className="setup-header-episodes">
                {/* Both numberings when they differ (build 114): this season
                    counts 1 to 19 and the selected group calls those files 67
                    to 85. Asked for, and it is the honest line - a row that
                    numbers per season simply has nothing in brackets. */}
                {(offset > 0 ? t("setup.episodeRange.offset") : t("setup.episodeRange"))
                  .replace("{first}", String(firstNumber))
                  .replace("{last}", String(lastNumber ?? "?"))
                  .replace("{n}", String(selectedCount ?? "?"))}
              </small>
            </div>
          </div>
          {/* The numbering boxes now live in the files column, beside the
              files they are about (build 120) - see `numberingGroup`. */}
        </div>
        {numberingError && <p className="warning-message">{numberingError}</p>}

        {!ready ? (
          <p className="loading">{t("setup.loading")}</p>
        ) : (
          <>
            {(!hasDefaultTemplate || !hasDefaultSource) && (
              <p className="muted" style={{ fontSize: ".85rem" }}>
                {!hasDefaultTemplate && (
                  <>{t("setup.noDefaultTemplate")} <a href="#/profiles">{t("setup.goToProfiles")}</a>. </>
                )}
                {!hasDefaultSource && (
                  <>{t("setup.noDefaultSource")} <a href="#/downloads">{t("setup.goToDownloads")}</a>.</>
                )}
              </p>
            )}

            <div className="setup-search-row">
            <label className="field">
              <span>{t("setup.searchName")}</span>
              {/* A small x inside the box rather than a Clear button beside
                  it: emptying it is the most common thing done here and does
                  not deserve a button the size of an action. */}
              <span className="input-clear">
                <input
                  value={searchName}
                  placeholder={candidate.display_name}
                  onChange={(event) => { nameTouched.current = true; setSearchName(event.target.value); }}
                />
                {searchName !== "" && (
                  <button
                    type="button"
                    aria-label={t("setup.clearFilter")}
                    title={t("setup.clearFilter")}
                    onClick={() => { nameTouched.current = true; setSearchName(""); }}
                  >
                    ×
                  </button>
                )}
              </span>
            </label>
            {/* Which search to make. The modes were worked out in the Release
                Inspector against the real tracker; this is where they earn
                their keep (build 111). */}
            <label className="field setup-mode">
              <span>{t("setup.mode")}</span>
              <select
                value={mode}
                disabled={searching}
                onChange={(event) => setMode(event.target.value as SearchMode)}
              >
                {SEARCH_MODES.map((name) => (
                  <option key={name} value={name}>{t(`inspector.mode.${name}`)}</option>
                ))}
              </select>
            </label>
            {/* Which site to ask, when there is more than one (build 219). A
                different site is a different set of results, so the old ones
                are cleared rather than left under the wrong name. */}
            {(
              <label className="field setup-indexer" title={indexers.length > 1 ? undefined : t("setup.indexerOnlyOne")}>
                <span>{t("setup.indexer")}</span>
                <select
                  value={indexerId}
                  disabled={searching || indexers.length < 2}
                  onChange={(event) => {
                    setIndexerId(event.target.value);
                    setReleases([]);
                    setSearched(false);
                    setSelectedKey(null);
                  }}
                >
                  {indexers.length === 0 && <option value="">{t("setup.indexerDefault")}</option>}
                  {indexers.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.adult ? `${item.name} · 18+` : item.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {/* Beside the field it acts on rather than at the bottom of the
                dialog. Grey while it is running, because a primary-coloured
                button that cannot be pressed reads as the thing to press. */}
            {searching ? (
              /* "Checking…" said what was already obvious from the bar under
                 it. The one useful thing a button can offer while a search
                 runs is a way out of it (build 111). */
              <button
                className="button secondary"
                title={t("setup.cancelSearchHint")}
                onClick={() => { cancelled.current = true; }}
              >
                {t("setup.cancelSearch")}
              </button>
            ) : (
              <button className="button primary" onClick={() => void runSearch()}>
                {t("setup.search")}
              </button>
            )}
            </div>

            {searchError &&<p className="error-message">{searchError}</p>}
            {indexerError && (
              indexerError.toLowerCase().includes("no indexer")
                ? <p className="warning-message">{t("setup.noIndexer")}</p>
                : <p className="error-message">
                    {t("setup.indexerUnreachable")}
                    <small className="muted" style={{ display: "block", marginTop: 4 }}>{indexerError}</small>
                  </p>
            )}

            {/* Only in Custom (build 176). They are part of the search there -
                the group, the quality and the extra words are appended to the
                name - so in the other modes, which build their own terms, they
                would be boxes that do nothing. Narrowing what came back is
                the filter above the results. */}
            {mode === "custom" && (
              <div className="settings-grid three setup-filters">
                <label className="field">
                  <span>{t("setup.subgroup")}</span>
                  <input
                    value={filters.subgroup}
                    placeholder={groupsSeen.slice(0, 3).join(", ")}
                    onChange={(event) => setFilters({ ...filters, subgroup: event.target.value })}
                  />
                </label>
                <label className="field">
                  <span>{t("setup.quality")}</span>
                  <select
                    value={filters.resolution}
                    onChange={(event) => {
                      setFilters({ ...filters, resolution: event.target.value });
                      // The quality box over the results started from the stored profile (480p) and stayed
                      // there when this one was set to Any, so the list was still narrowed by a rule the
                      // person had just removed.
                      setResultQuality(event.target.value);
                    }}
                  >
                    <option value="">{t("profile.any")}</option>
                    {RESOLUTIONS.map((value) => (
                      <option key={value} value={value}>{value}</option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>{t("inspector.filterTag")}</span>
                  <input
                    value={filters.tag}
                    placeholder="web, multi, eac3…"
                    onChange={(event) => setFilters({ ...filters, tag: event.target.value })}
                  />
                </label>
              </div>
            )}

            {/* Part of the search, so under its fields (build 210). Twelve requests a second apart is twelve seconds of waiting.
                Saying which term it is on makes that a process rather than a
                hang - and the rows underneath fill in as it goes. */}
            {progress && (searching || progress.done < progress.total) && (
              <div className="setup-progress">
                <progress max={progress.total} value={progress.done} />
                <small className="muted">
                  {t("setup.searchProgress")
                    .replace("{done}", String(progress.done))
                    .replace("{total}", String(progress.total))}
                </small>
              </div>
            )}

            {searched && !indexerError && releases.length === 0 && (
              <p className="muted">{t("setup.noneFound")}</p>
            )}
            {/* The part of the stored profile the boxes above do not show (item 48). */}
            {scan?.stored_rules && scan.stored_rules.length > 0 && (
              <p className="muted">
                {t("setup.storedRules").replace(
                  "{rules}",
                  scan.stored_rules.map((rule) => `${t(`setup.storedRule.${rule.kind}`)} ${rule.value}`).join(", "),
                )}
              </p>
            )}
            {releases.length > 0 && ranked.length === 0 && (
              <p className="muted">{t("inspector.groupedEmpty")}</p>
            )}

            <div className={`setup-split${splitOpen ? " open" : ""}${releases.length === 0 ? " no-results" : ""}`}>
            {/* The second subgroup / quality pair narrows what came back, so it belongs with the results
                and appears with them (build 210) - above, before any search, it only looked like the
                fields repeated. */}
            {releases.length > 0 && (
            <div className="setup-split-list carded">
            <div className="setup-result-filter">
              <input
                value={resultText}
                placeholder={t("setup.resultFilter")}
                aria-label={t("setup.resultFilter")}
                onChange={(event) => setResultText(event.target.value)}
              />
              <select
                value={resultQuality}
                aria-label={t("setup.quality")}
                onChange={(event) => setResultQuality(event.target.value)}
              >
                <option value="">{t("setup.anyQuality")}</option>
                {(qualities.length ? qualities : RESOLUTIONS).map((value) => (
                  <option key={value} value={value}>{value}</option>
                ))}
              </select>
            </div>
            {ranked.length > 0 && (
              <div className="setup-rows tall">
                {ranked.map((row) => {
                  const rank = rankOf(row, mine, known);
                  const classes = ["setup-row"];
                  if (rank === 0) classes.push("mine");
                  else if (rank === 1) classes.push("known");
                  if (row.key === selectedKey) classes.push("selected");
                  return (
                    <button type="button" key={row.key} className={classes.join(" ")} onClick={() => choose(row)}>
                      <span className="setup-row-name">
                        <strong>{row.subgroup ?? t("setup.noGroup")}</strong>
                        <span className="setup-row-quality">{row.resolution ?? t("setup.noQuality")}</span>
                        <SeederMark seeders={bestSeeders(row.releases)} />
                        {languageTagsOf(row).map((tag) => (
                          <span key={tag} className="setup-row-lang">{t(`setup.lang.${tag}`)}</span>
                        ))}
                      </span>
                      {/* The name and what was found for it on one line
                          (build 124): the episode range had a line of its
                          own, which made every row three lines tall for one
                          short string.

                          No file count from the broad search on purpose: what
                          came back is a sample capped by the tracker's page
                          size, and a number nobody can trust reads as a fact.
                          The selected row asks the narrow question instead,
                          and that answer is complete. */}
                      <span className="setup-row-second">
                      <small className="muted">
                        {row.folderName ?? row.title}
                        {/* What this copy is, and what it is not. The second
                            half matters: two rows can differ only by one row
                            lacking a tag, and without it they read alike. */}
                        {(row.edition.length > 0 || row.editionNot.length > 0) && (
                          <span className="setup-row-edition">
                            {row.edition.map((tag) => (
                              <span key={`+${tag}`} className="setup-row-tag">{tagLabel(tag)}</span>
                            ))}
                            {row.editionNot.map((tag) => (
                              <span key={`-${tag}`} className="setup-row-tag not">
                                {t("setup.tagNot").replace("{tag}", tagLabel(tag))}
                              </span>
                            ))}
                          </span>
                        )}
                      </small>
                      {(() => {
                        /* The selected row shows the scan in progress; every
                           row that has been scanned keeps its answer, so the
                           list says what is known rather than only what is
                           selected. */
                        const selectedHere = row.key === selectedKey;
                        const known = selectedHere ? scan : rememberedFor(row);
                        if (selectedHere && scanning) {
                          return (
                            <span className="setup-row-scan">
                              <progress max={scanStep.total || 1} value={scanStep.done} />
                            </span>
                          );
                        }
                        if (!known) return null;
                        return (
                          <span className="setup-row-scan">
                            {known.episodes.length > 0 ? (
                              <small className="setup-row-episodes">
                                {/* This season's episodes, once the header
                                    says where it starts (build 113). */}
                                {(() => {
                                  const mine = known.episodes
                                    .map((number) => number - offset)
                                    .filter((number) => number >= 1);
                                  return t("scan.episodesFound")
                                    .replace("{n}", String(mine.length))
                                    .replace("{list}", episodeRanges(mine));
                                })()}
                              </small>
                            ) : (
                              <small className="reasons">
                                {t(`scan.empty.${emptyReason(known)}`)}
                              </small>
                            )}
                          </span>
                        );
                      })()}
                      </span>
                      {row.key === selectedKey && (
                        <>
                          {otherGroupHints(row, ranked).map((hint) => (
                            <small key={`${hint.subgroup}|${hint.resolution}|${hint.edition.join("+")}`} className="setup-row-other">
                              {t("setup.otherGroupHas")
                                .replace(
                                  "{group}",
                                  (hint.subgroup ?? t("setup.noGroup")) +
                                    (hint.edition.length ? ` (${hint.edition.map(tagLabel).join(", ")})` : ""),
                                )
                                .replace("{quality}", hint.resolution ?? t("setup.noQuality"))
                                .replace("{n}", String(hint.missing.length))
                                .replace("{list}", episodeRanges(hint.missing))}
                            </small>
                          ))}
                        </>
                      )}
                      {row.key === selectedKey && <span className="imported-tag">{t("setup.selected")}</span>}
                    </button>
                  );
                })}
              </div>
            )}

            </div>
            )}
            <div className="setup-split-files">
              {scan ? (
                <SelectionFilesPanel
                  scan={scan}
                  episodeCount={selectedCount}
                  offset={offset}
                  owned={owned}
                  batch={batch
                    ? {
                        loading: batch.loading,
                        error: batch.error,
                        name: batch.preview?.name ?? null,
                        rows: batchRows(batch.preview, batchTake),
                        leftAlone: leftAlone(batch.preview, batchTake),
                      }
                    : null}
                  numbering={needsNumbering ? numberingGroup : null}
                />
              ) : (
                /* The column is there from the start, so it says what it is
                   for rather than being an empty half of a dialog. The
                   numbering boxes are here too: the range is a decision
                   about the season, not about a scan. */
                <>
                  {numberingGroup}
                  <p className="muted setup-files-waiting">
                    {searching ? t("setup.searching") : t("setup.filesWaiting")}
                  </p>
                </>
              )}
            </div>
            </div>

            {/* What the selection amounts to, on one line rather than three
                labelled boxes: it is a summary of a choice already made, and
                the only part worth typing in is the folder. */}
            <div className="setup-summary">
              <span className="setup-summary-line">
                <strong>{selected?.folderName ?? selected?.title ?? t("setup.releaseNamePlaceholder")}</strong>
                <small className="muted">
                  {[
                    selected?.subgroup ?? t("setup.noGroup"),
                    selected?.resolution ?? t("setup.noQuality"),
                    ...selectedExtras,
                  ].join(" · ")}
                </small>
                {/* Which numbering this group uses (build 95). Erai-raws
                    restarts at 1 every season and writes the season into the
                    name; SubsPlease never writes a season and keeps counting,
                    so the same episode is "01" to one and "51" to the other.
                    Said rather than silently corrected - renumbering needs
                    every earlier season's length and being wrong renames a
                    library. */}
                {scanNumbering.absolute && offset === 0 && (
                  <small className="reasons" title={t("scan.absoluteHint")}>
                    ⚠ {t("scan.absolute")
                      .replace("{first}", String(scanNumbering.first))
                      .replace("{last}", String(scanNumbering.last))
                      .replace("{offset}", String(scanNumbering.offset))}
                  </small>
                )}
                {scanError && <small className="reasons">{scanError}</small>}
              </span>
              {/* Label in front of the field, not above it (build 124):
                  above, this panel was two lines tall for one short input. */}
              <label className="field setup-summary-folder inline">
                {/* Which folder this is depends on who owns the library: in
                    Shoko Server mode AniFlow writes into the hand-over folder
                    and Shoko decides where it ends up, so calling it the
                    media folder there would be a lie (build 111). */}
                <span>
                  {storage === "shokoserver"
                    ? t("setup.manualName.import")
                    : t("setup.manualName.media")}
                </span>
                <input
                  value={manualName}
                  onChange={(event) => { folderTouched.current = true; setManualName(event.target.value); }}
                />
              </label>
              {choosesLibrary && rankedLibraries.length > 0 && (
                <label className="field setup-summary-folder inline">
                  <span>{t("libraries.field")}</span>
                  <select
                    value={libraryId ?? ""}
                    onChange={(event) => { libraryTouched.current = true; setLibraryId(Number(event.target.value)); }}
                  >
                    {rankedLibraries.map(({ library, fit }) => (
                      <option key={library.id} value={library.id}>
                        {library.key ? t(`libraries.system.${library.key}`) : library.name}
                        {fit === "fits" ? ` · ${t("libraries.suggested")}` : ""}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>

            {asFuture && (
              /* Said once, plainly, where the missing list would have been.
                 Without it an empty dialog reads as a broken search. */
              <p className="muted setup-future-note">
                {t("setup.futureNote").replace(
                  "{date}",
                  formatDate(candidate.air_start_date ?? ""),
                )}
              </p>
            )}
            {saveError && <p className="error-message">{t("add.error")}</p>}
            {/* What the fix did (build 117). Said rather than closing the
                dialog: "nothing happened" and "four episodes are downloading"
                look identical from outside. */}
            {fixSummary && <p className="muted">{fixSummary}</p>}
            {/* Two groups, not four buttons: what the search does on the left,
                what the dialog does on the right. On a narrow window the pair
                wraps as a pair, so Cancel never ends up on its own line beside
                the button that adds the anime. */}
            <div className="dialog-actions setup-actions">
              <span className="setup-actions-group">
                {/* A search that turned up an unknown group is exactly when a
                    new profile is worth saving, so the button stays within
                    reach. */}
                {seedRow && (
                  <button
                    className="button secondary"
                    onClick={() => setTemplateSeed({
                      subgroups: seedRow.subgroup ? [seedRow.subgroup] : [],
                      resolution: seedRow.resolution,
                    })}
                  >
                    {t("setup.addTemplate")}
                  </button>
                )}
                {/* Adding without a group: the list always picks its best row
                    for you and typed filters count as a choice too, so
                    without this there was no way to say "none". */}
                {(selected || filters.subgroup.trim()) && (
                  <button
                    className="button secondary"
                    onClick={() => {
                      setNoneChosen(true);
                      setSelectedKey(null);
                      setFilters((current) => ({ ...current, subgroup: "" }));
                    }}
                  >
                    {t("setup.clearSubgroup")}
                  </button>
                )}
                {/* The files switch is gone (build 124). It had already
                    stopped working: the column renders whenever a scan
                    exists, so the button only changed its own label. */}
              </span>
              <span className="setup-actions-group">
                {/* The AniDB page for this show (build 221), for checking its names and the groups that release
                    it without leaving the dialog. A link the person's own browser follows - AniFlow never
                    fetches anidb.net pages itself. */}
                {anidbUrl(anidbId) && (
                  <a
                    className="button secondary"
                    href={anidbUrl(anidbId) ?? undefined}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {t("config.openAnidb")}
                  </a>
                )}
                {!anidbUrl(anidbId) && (
                  <span className="muted setup-no-anidb" title={t("setup.noAnidbTitle")}>{t("setup.noAnidb")}</span>
                )}
                <button className="button secondary" onClick={onClose}>{t("add.cancel")}</button>
                <button
                  className="button primary"
                  disabled={confirmDisabled}
                  title={nothingToFix && !scanning ? t("setup.fixNothingYet") : undefined}
                  onClick={() => void confirm()}
                >
                  {saving
                    ? t("settings.saving")
                    : fixMissing?.matchOnly
                      ? t("setup.matchConfirm")
                      : fixMissing
                      ? t("setup.fixConfirm")
                      : asFuture
                        ? t("setup.addFuture")
                        : convertingId || reacquire
                          /* The anime is already on the watchlist; "Add to
                             watchlist" on it was wrong (build 188). */
                          ? t("setup.updateConfirm")
                          : t("add.confirm")}
                </button>
              </span>
            </div>
          </>
        )}
      </section>

      {templateSeed && (
        <TemplateDialog
          initial={null}
          seed={{ subgroups: templateSeed.subgroups, resolution: templateSeed.resolution }}
          onClose={() => setTemplateSeed(null)}
          onSaved={(saved) => {
            // Adopt it immediately: it describes the very releases on screen,
            // so the rows stay valid and are deliberately not re-fetched.
            setTemplates((current) => [...current.filter((tpl) => tpl.id !== saved.id), saved]);
          }}
        />
      )}
    </div>
  );
}

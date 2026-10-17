import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getCalendar,
  inspectNames,
  inspectSearch,
  searchProgress,
  type AnimeSearchResult,
  type InspectName,
  type InspectQuery,
  type InspectedRelease,
  type InspectSearch,
  type ScannedRelease,
  type SelectionScan,
} from "../api/client";
import { t } from "../i18n";
import {
  DEFAULT_MODE,
  SEARCH_MODES,
  clearSymbols,
  pagesFor,
  runsFor,
  type SearchMode,
} from "../searchModes";
import { seasonLabel, type SeasonName } from "../seasons";
import { Cover } from "./Cover";
import { SelectionFilesPanel } from "./SelectionFilesPanel";
import {
  groupReleases,
  parsedTags,
  resolutionsFound,
  subgroupsFound,
  type InspectorFilters,
  type InspectorGroup,
  type ParsedFields,
} from "../inspectorRows";
import { normalizeGroup, rankOf } from "../setupRows";
import { listTemplates, type ProfileTemplate } from "../api/client";

/**
 * The Release Inspector: the search with its lid off (build 80, rebuilt 105,
 * cut down to four modes in 170).
 *
 * It exists for one question - *the indexer has this release, so why does
 * AniFlow not show it?* - and build 105 is about making it answer that
 * without the rest of AniFlow being involved:
 *
 * * **everything needed to search is on this page** - and, since 170,
 *   actually visible without picking a show first. Build 105 said this and
 *   did not quite deliver it: the box itself still lived behind `{picked &&
 *   ...}`, so typing your own name and pressing Search still needed an anime
 *   selected for no reason the search itself cared about. Picking one still
 *   fills the box and the name list; it is just no longer the price of
 *   admission.
 * * **every name the show goes by** is listed, and each is a search of its
 *   own. Which spelling a release group used is the whole question, and the
 *   card only knows three of them - AniDB and each source publish their own.
 * * **four modes, not eight** (170) - `quick`, `full`, `japanese` and
 *   `custom`, in `../searchModes.ts`. `custom` is the searcher's own list,
 *   built with the `+`/`×` controls beside the search box, each term its own
 *   row in the terms table below - and its own page depth, set beside the
 *   mode picker, rather than a fixed number decided here.
 * * **the results read like the Set up release matching window**, because
 *   that is the window whose behaviour is being debugged: the groups on the
 *   left, the files of the selected group on the right. Nothing is selected
 *   for you - picking the row is how the number of searches stays at the
 *   minimum.
 *
 * Still nothing here writes anything: no adding, no profiles, no folders.
 */

/** Which name fills the search box when a show is picked, best first. */
const PREFERRED_FIRST = ["romaji", "english", "clean"];

/** Matches services/discovery.INDEXER_PAUSE_SECONDS, for the same reason. */
const PAUSE_MS = 1000;

/**
 * What a row's files say about themselves beyond the group and the quality.
 *
 * Asked for: *"add all of the misc behind the 720p - whatever you detect"*.
 * The source, the codecs, the edition tags - everything the parser
 * recognised, minus the two things the row already says and minus the
 * per-file noise (the checksum, the version, the episode), because those
 * differ file by file and a row is about what its files have in common.
 */
export function miscOf(group: InspectorGroup): string[] {
  const counted = new Map<string, number>();
  for (const release of group.releases) {
    for (const tag of parsedTags(release.parsed as ParsedFields | null)) {
      if (/^[0-9a-f]{8}$/i.test(tag)) continue;   // a checksum
      if (/^v\\d+$/i.test(tag)) continue;          // a version
      if (/^s\\d+$/i.test(tag)) continue;          // the season, already in the name
      counted.set(tag, (counted.get(tag) ?? 0) + 1);
    }
  }
  // Only what most of the row carries: a tag on one file of twenty describes
  // that file, not this copy of the show.
  const enough = Math.max(1, Math.ceil(group.releases.length / 2));
  return [...counted.entries()]
    .filter(([, count]) => count >= enough)
    .map(([tag]) => tag)
    .sort();
}

/**
 * The order the rows are listed in, which is the add step's order (build 109).
 *
 * Asked for, and it is the same judgement that page makes: the groups you
 * chose come first, then the ones you have chosen elsewhere, then everybody
 * else by name - and the releases whose group the parser could not read go
 * last, because "unknown" is not a group and never the answer being looked
 * for.
 */
export function orderRows(
  rows: InspectorGroup[],
  mine: Set<string>,
  known: Set<string>,
): InspectorGroup[] {
  return [...rows].sort((left, right) => {
    const unknown = Number(!left.subgroup) - Number(!right.subgroup);
    if (unknown !== 0) return unknown;
    const rank = rankOf(left, mine, known) - rankOf(right, mine, known);
    if (rank !== 0) return rank;
    const name = (left.subgroup ?? "").localeCompare(right.subgroup ?? "", undefined, {
      sensitivity: "base",
    });
    if (name !== 0) return name;
    // Same group, several rows: the folder tells them apart, and that is
    // exactly the line the eye is looking at when it happens.
    return (left.folderName ?? left.title ?? "").localeCompare(
      right.folderName ?? right.title ?? "",
    );
  });
}

/**
 * How far a search has got, as a fraction of the whole run.
 *
 * The browser knows how many terms it will ask; the server knows how many
 * pages the term in flight has taken and - once the site has told it - how
 * many that term should take. Between them that is a bar rather than a
 * spinner, and it moves on every request rather than once a term.
 *
 * Two things made it jump forward and fall back again (build 110), and both
 * are about reading a number that was true a moment ago:
 *
 * * between two terms the counter still holds the **finished** term's
 *   position - fifteen pages of fifteen - which read as a whole extra term
 *   done until the next request reset it. So a reading is only believed when
 *   it is about the term actually being waited for.
 * * a term whose length nothing knows yet was counted as half done, and the
 *   moment the site's count arrived it became one page of fifteen. Hence
 *   ``floor``: a bar may stand still, and it may not go backwards.
 *
 * Still deliberately an estimate, and still capped just short of full, so
 * that "finished" is something the search says rather than something the bar
 * guesses.
 */
export function progressOf(
  answered: number,
  total: number,
  current: { done: number; expected: number; term_index: number } | null,
  floor = 0,
): number {
  if (total < 1) return 0;
  // A reading about another term is a reading about the past.
  const here = current && current.term_index === answered ? current : null;
  const within = here
    ? here.expected > 0
      ? Math.min(here.done / here.expected, 1)
      : here.done > 0 ? 0.15 : 0
    : 0;
  return Math.max(floor, Math.min((answered + within) / total, 0.99));
}

/** Seconds, as something short enough to put in a table cell. */
export function shortSeconds(value: number): string {
  if (value >= 60) return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
  if (value >= 10) return `${value.toFixed(0)}s`;
  return `${value.toFixed(1)}s`;
}

/**
 * Put the addresses on the clipboard, and say which row was copied.
 *
 * The clipboard is not always reachable: browsers refuse it outside a secure
 * context, and AniFlow is usually served over plain http on a home network.
 * So the addresses are shown on the page as well - the copy that always
 * works is the one you can select yourself.
 */
async function copyUrls(urls: string[], remember: (url: string) => void): Promise<void> {
  const text = urls.join(String.fromCharCode(10));
  try {
    await navigator.clipboard?.writeText(text);
  } catch {
    /* shown below instead */
  }
  remember(urls[0]);
}

/** The episode this file is, or null for a batch, a range or an oddity. */
export function episodeNumber(label: string | null | undefined): number | null {
  const text = (label ?? "").trim();
  if (!text) return null;
  // A range ("01-12") is a batch: it is not one episode and must not be
  // counted as one, or a season of twelve reads as thirteen.
  if (/[-~]/.test(text)) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

/**
 * One group's hits in the shape the files panel speaks.
 *
 * No second search. The setup window asks the tracker again about the row it
 * selected, because it is deciding what to store; this page already holds
 * every hit the tracker gave and decides nothing, so asking again would spend
 * a request on a question that is already answered.
 */
export function scanOfGroup(group: InspectorGroup): SelectionScan {
  const releases: ScannedRelease[] = group.releases.map((release) => {
    const parsed = release.parsed as ParsedFields | null;
    return {
      raw_name: release.raw_name,
      subgroup: parsed?.subgroup ?? null,
      resolution: parsed?.resolution ?? null,
      season: parsed?.season ?? null,
      episode: parsed?.episode ?? null,
      number: episodeNumber(parsed?.episode),
      version: parsed?.version ?? 1,
      crc32: parsed?.crc32 ?? null,
      folder_name: release.folder_name ?? null,
      tags: parsed?.unknown_tags ?? [],
      size: release.size,
      seeders: release.seeders,
      // Everything in a row belongs to that row - the grouping is what put it
      // there. "Refused" is a judgement the add step makes against a profile,
      // and this page has no profile and makes no judgement.
      accepted: true,
      reason: null,
      detail: [],
    };
  });
  const episodes = [
    ...new Set(
      releases
        .map((release) => release.number)
        .filter((number): number is number => number !== null),
    ),
  ].sort((first, second) => first - second);
  return {
    scan_ids: [],
    releases,
    episodes,
    asked: [],
    pages: 0,
    failures: [],
    reached_provider: true,
    no_indexer: false,
  };
}

export function ReleaseInspectorPage() {
  const [season, setSeason] = useState<AnimeSearchResult[]>([]);
  const [label, setLabel] = useState("");
  const [seasonError, setSeasonError] = useState<string | null>(null);
  // Build 171: a season is fifty-odd cards, and finding one by eye was the
  // complaint. Client-side only - the whole season is already on the page.
  const [seasonFilter, setSeasonFilter] = useState("");
  const [picked, setPicked] = useState<AnimeSearchResult | null>(null);
  const [names, setNames] = useState<InspectName[]>([]);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>(DEFAULT_MODE);
  const [native, setNative] = useState(false);
  // The custom mode's own list, built with the + button, and how many pages
  // each of its terms reads (build 170). The other three modes decide their
  // own page depth; this is the one mode where that is the searcher's call.
  const [customTerms, setCustomTerms] = useState<string[]>([]);
  const [customPages, setCustomPages] = useState(1);
  const [releases, setReleases] = useState<InspectedRelease[]>([]);
  const [queries, setQueries] = useState<InspectQuery[]>([]);
  const [searched, setSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [failures, setFailures] = useState<string[]>([]);
  const [filters, setFilters] = useState<InspectorFilters>({ subgroup: "", resolution: "", tag: "" });
  const [rowKey, setRowKey] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  // Set when Cancel is pressed. The loop checks it between terms, and the
  // request in flight is aborted - a search that walks eighty pages has to be
  // stoppable, or the only way out is the browser's reload button.
  const cancelled = useRef(false);
  const [elapsed, setElapsed] = useState<number | null>(null);
  // What the indexer's definition file lets a search do. Without a listing
  // the search reads the feed, which on Nyaa serves one page whatever is
  // asked of it - and the page should say so rather than look broken.
  const [templates, setTemplates] = useState<ProfileTemplate[]>([]);
  const [ability, setAbility] = useState<
    { listing: boolean; total: boolean; file: string | null } | null
  >(null);

  // The subgroup profiles, for the order the rows are listed in. Failing to
  // read them costs the order and nothing else.
  useEffect(() => {
    let current = true;
    listTemplates()
      .then((found) => { if (current) setTemplates(found); })
      .catch(() => { if (current) setTemplates([]); });
    return () => { current = false; };
  }, []);

  // The season, once. The same line-up the Calendar draws, and nothing else
  // about that page: no filters, no sorting, and picking one adds nothing.
  useEffect(() => {
    let current = true;
    getCalendar()
      .then((answer) => {
        if (!current) return;
        setSeason(answer.anime);
        setLabel(seasonLabel({ year: answer.year, season: answer.season as SeasonName }));
      })
      .catch((err) => {
        if (current) setSeasonError(err instanceof Error ? err.message : String(err));
      });
    return () => { current = false; };
  }, []);

  // Picking a show asks for its names - all of them local, no request to
  // anybody - and fills the box with the one release groups most often use.
  useEffect(() => {
    if (!picked) return;
    let current = true;
    setReleases([]);
    setQueries([]);
    setSearched(false);
    setFailures([]);
    setRowKey(null);
    setNative(false);
    setQuery(picked.title_romaji ?? picked.display_name);
    inspectNames(picked)
      .then((answer) => {
        if (!current) return;
        setNames(answer.names);
        const first = PREFERRED_FIRST
          .map((kind) => answer.names.find((name) => name.kind === kind))
          .find(Boolean);
        if (first) { setQuery(first.value); setNative(first.native); }
      })
      .catch(() => { if (current) setNames([]); });
    return () => { current = false; };
  }, [picked]);

  const runs = useMemo(
    () => runsFor(mode, query, names, native, customTerms),
    [mode, query, names, native, customTerms],
  );

  /** The season, narrowed to whatever the filter box matches. */
  const filteredSeason = useMemo(() => {
    const needle = seasonFilter.trim().toLowerCase();
    if (!needle) return season;
    return season.filter((item) => item.display_name.toLowerCase().includes(needle));
  }, [season, seasonFilter]);

  async function search() {
    if (!runs.length) return;
    setLoading(true);
    setError(null);
    setProgress(0);
    setElapsed(null);
    // A name for this run, so the server can be asked how far it has got
    // while the request it is answering is still open.
    const token = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    const startedAt = performance.now();
    cancelled.current = false;
    let answered = 0;
    let shown = 0;
    const follow = window.setInterval(() => {
      void searchProgress(token)
        .then((where) => {
          shown = progressOf(answered, runs.length, where, shown);
          setProgress(shown);
        })
        .catch(() => { /* decoration: a failed poll changes nothing */ });
    }, 500);
    try {
      // One after another, never Promise.all. Each call already walks the
      // tracker's pages on the server side, spaced out on purpose - firing
      // the calls together would undo that and arrive as the burst a rate
      // limiter exists to catch.
      const answers: InspectSearch[] = [];
      for (const run of runs) {
        if (cancelled.current) break;
        if (answers.length) await new Promise((resume) => setTimeout(resume, PAUSE_MS));
        if (cancelled.current) break;
        answers.push(
          await inspectSearch(run.base, {
            extraTerms: run.extraTerms,
            native: run.native,
            pages: pagesFor(mode, customPages),
            progressToken: token,
            termIndex: answers.length,
            termCount: runs.length,
          }),
        );
        answered = answers.length;
        shown = progressOf(answered, runs.length, null, shown);
        setProgress(shown);
      }
      // Merged here rather than server-side: each run is its own request, and
      // a release several of them returned is one release that several terms
      // found - which is what the breakdown underneath is for.
      const byId = new Map<string, InspectedRelease>();
      for (const answer of answers) {
        for (const release of answer.releases) {
          const existing = byId.get(release.id);
          if (!existing) { byId.set(release.id, release); continue; }
          for (const term of release.found_by) {
            if (!existing.found_by.includes(term)) existing.found_by.push(term);
          }
        }
      }
      setAbility({
        listing: answers.every((answer) => answer.reads_listing),
        total: answers.every((answer) => answer.reports_total),
        file: answers[0]?.definition_file ?? null,
      });
      setReleases([...byId.values()]);
      setQueries(answers.flatMap((answer) => answer.queries));
      setFailures(answers.flatMap((answer) => answer.failures));
      setRowKey(null);
      setSearched(true);
      if (answers.some((answer) => !answer.reached_provider)) setError(t("inspector.noIndexer"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      window.clearInterval(follow);
      setElapsed((performance.now() - startedAt) / 1000);
      setProgress(0);
      setLoading(false);
    }
  }

  /** Groups in the default profile - the ones you said you prefer. */
  const mine = useMemo(() => {
    const preferred = templates.find((tpl) => tpl.is_default)
      ?? (templates.length === 1 ? templates[0] : undefined);
    return new Set((preferred?.subgroups ?? []).map(normalizeGroup).filter(Boolean));
  }, [templates]);
  /** Groups in every other saved profile. */
  const known = useMemo(() => {
    const names = new Set<string>();
    for (const tpl of templates) {
      for (const name of tpl.subgroups) {
        const normalized = normalizeGroup(name);
        if (normalized && !mine.has(normalized)) names.add(normalized);
      }
    }
    return names;
  }, [templates, mine]);

  // Split by folder and by edition, exactly as the setup window splits them:
  // the point of this pane is to show what that window would show.
  const rows = useMemo(
    () => orderRows(
      groupReleases(releases, filters, { splitByFolder: true, splitByEdition: true }),
      mine,
      known,
    ),
    [releases, filters, mine, known],
  );
  // Offered by the filters, from what this search actually returned -
  // never a fixed list, which would offer qualities nothing here has.
  const qualities = useMemo(() => resolutionsFound(releases), [releases]);
  const groupsSeen = useMemo(() => subgroupsFound(releases), [releases]);
  const row = rows.find((candidate) => candidate.key === rowKey) ?? null;
  const scan = useMemo(() => (row ? scanOfGroup(row) : null), [row]);
  const useName = useCallback((name: InspectName) => {
    setQuery(name.value);
    // The Japanese title only exists in the tracker's non-English category,
    // so choosing it switches categories too. The toggle stays visible: a
    // guess about which spelling was typed is only a guess.
    setNative(name.native);
  }, []);

  /**
   * Add whatever is in the box to the custom list, and switch to it.
   *
   * The box is cleared afterwards - not left for the next search to repeat,
   * which is the whole reason a term moves into the list rather than staying
   * typed (build 170).
   */
  const addTerm = useCallback(() => {
    const term = query.trim();
    if (term.length < 2) return;
    setCustomTerms((current) =>
      current.some((existing) => existing.toLowerCase() === term.toLowerCase())
        ? current
        : [...current, term],
    );
    setQuery("");
    setMode("custom");
  }, [query]);

  const removeTerm = useCallback((term: string) => {
    setCustomTerms((current) => current.filter((existing) => existing !== term));
  }, []);

  return <section className="inspector">
    <p className="warning-message">{t("inspector.warning")}</p>

    {/* Two rows of the season, scrollable. A copy of the calendar's data and
        nothing else about that page: picking here selects what to search for
        and opens no dialog, because this page adds nothing. */}
    <div className="detail-panel">
      <div className="status-line">
        <h2>{t("inspector.season")}</h2>
        <span className="muted">{label}</span>
        {/* Client-side only - the whole season is already on the page, so
            narrowing it costs nothing and needs no request (build 171). */}
        <input
          className="inspector-season-filter"
          value={seasonFilter}
          onChange={(event) => setSeasonFilter(event.target.value)}
          placeholder={t("inspector.seasonFilterPlaceholder")}
        />
      </div>
      <p className="muted">{t("inspector.seasonHint")}</p>
      {seasonError && <p className="error-message">{seasonError}</p>}
      {!seasonError && season.length === 0 && <p className="loading">{t("calendar.loading")}</p>}
      {!seasonError && season.length > 0 && filteredSeason.length === 0 && (
        <p className="muted">{t("inspector.seasonFilterEmpty")}</p>
      )}
      <div className="inspector-season">
        {filteredSeason.map((item) => {
          const chosen =
            picked?.provider === item.provider && picked?.provider_id === item.provider_id;
          return (
            <article
              className={"inspector-season-card" + (chosen ? " selected" : "")}
              key={`${item.provider}:${item.provider_id}`}
            >
              <span className="inspector-season-art"><Cover anime={item} /></span>
              <strong title={item.display_name}>{item.display_name}</strong>
              <button
                className={"button " + (chosen ? "primary" : "secondary")}
                onClick={() => setPicked(item)}
              >
                {t("inspector.select")}
              </button>
            </article>
          );
        })}
      </div>
    </div>

    {/* Always here, picked or not (build 170): a show fills the box and the
        name list, but typing your own name and pressing Search never needed
        one - the prerequisite was the bug, not a feature. */}
    <div className="detail-panel">
      {picked ? <h2>{picked.display_name}</h2> : <p className="muted">{t("inspector.pickFirst")}</p>}

      {picked && (
        <>
          {/* Every spelling AniFlow holds, labelled, and each one a search:
              the three the card carries, AniDB's own, each source's own, and
              the name AniFlow itself would search with. */}
          <dl className="inspector-names">
            {names.map((name) => (
              <div key={`${name.kind}:${name.value}`} style={{ display: "contents" }}>
                <dt>{t(`inspector.name.${name.kind}`)}</dt>
                <dd>
                  <button
                    className="link-button"
                    onClick={() => useName(name)}
                    title={t("inspector.useName")}
                  >
                    {name.value}
                  </button>
                </dd>
              </div>
            ))}
          </dl>
          {names.length === 0 && <p className="muted">{t("inspector.nameMissing")}</p>}
        </>
      )}

      <div className="setup-search-row">
        <label className="field">
          <span>{t("inspector.searchName")}</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("inspector.searchPlaceholder")}
          />
        </label>
        {/* Adds the box to the custom list below and switches to it (build
            170). The list is how several terms become several searches -
            "Youjo Senki II" and "Youjo Senki 2" are two different questions
            to a tracker, and this is how both get asked. */}
        <button
          type="button"
          className="button secondary"
          title={t("inspector.addTermHint")}
          disabled={query.trim().length < 2}
          onClick={addTerm}
        >
          {t("inspector.addTerm")}
        </button>
      </div>

      {customTerms.length > 0 && (
        <ul className="inspector-custom-terms">
          {customTerms.map((term) => (
            <li key={term}>
              <span>{term}</span>
              <button
                type="button"
                title={t("inspector.removeTermHint")}
                onClick={() => removeTerm(term)}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="inspector-search-row">
        <div className="filter-row">
          {names.map((name) => (
            <button
              key={`button:${name.kind}:${name.value}`}
              className="button secondary"
              title={name.value}
              onClick={() => useName(name)}
            >
              {t(`inspector.name.${name.kind}`)}
            </button>
          ))}
          {/* The box as it stands, minus the characters no release group
              types and no tracker indexes. */}
          <button
            className="button secondary"
            title={t("inspector.clearSymbolsHint")}
            disabled={clearSymbols(query) === query.trim()}
            onClick={() => setQuery(clearSymbols(query))}
          >
            {t("inspector.clearSymbols")}
          </button>
        </div>
        <div className="filter-row">
          {/* The mode is what the search *is*, so it sits with the button
              that runs it. */}
          <label className="field inspector-mode">
            <span>{t("inspector.mode")}</span>
            <select value={mode} onChange={(event) => setMode(event.target.value as SearchMode)}>
              {SEARCH_MODES.map((name) => (
                <option key={name} value={name}>{t(`inspector.mode.${name}`)}</option>
              ))}
            </select>
          </label>
          {/* Only custom has a depth to set - the other three decide their
              own, having been measured against the real tracker already. */}
          {mode === "custom" && (
            <label className="field inspector-pages" title={t("inspector.pagesHint")}>
              <span>{t("inspector.pages")}</span>
              <input
                type="number"
                min={1}
                max={200}
                value={customPages}
                onChange={(event) => {
                  const parsed = Math.round(Number(event.target.value));
                  if (Number.isFinite(parsed)) setCustomPages(Math.min(200, Math.max(1, parsed)));
                }}
              />
            </label>
          )}
          {loading ? (
              /* The same button, saying the only useful thing it can while a
                 search runs. A walk through eighty pages has to be stoppable
                 (build 111). */
              <button
                className="button secondary"
                title={t("inspector.cancelHint")}
                onClick={() => { cancelled.current = true; }}
              >
                {t("inspector.cancel")}
              </button>
            ) : (
              <button
                className="button primary"
                disabled={runs.length === 0}
                title={t("inspector.runHint").replace("{n}", String(runs.length))}
                onClick={() => void search()}
              >
                {t("inspector.search")}
              </button>
            )}
          </div>
        </div>
        <p className="muted inspector-mode-hint">
          {t(`inspector.modeHint.${mode}`).replace("{n}", String(runs.length))}
        </p>
        {/* Every page of every term is a request, and the deep modes make
            eighty of them - a page that shows nothing for a minute looks
            broken whatever it is doing (build 109). */}
        {loading && (
          <div className="inspector-progress">
            <progress max={100} value={Math.round(progress * 100)} />
            <small className="muted">{Math.round(progress * 100)}%</small>
          </div>
        )}

        <label
          className={"toggle-check" + (native ? " on" : "")}
          style={{ marginTop: 12 }}
          title={t("inspector.nativeHint")}
        >
          <input type="checkbox" checked={native} onChange={(event) => setNative(event.target.checked)} />
          <span>{t("inspector.native")}</span>
        </label>

        {error && <p className="error-message">{error}</p>}
        {failures.map((failure, index) => (
          <p className="warning-message" key={index}>{failure}</p>
        ))}

        {/* What each term achieved, and what the site says it should have.
            The two to read together are the last ones: a site reporting 700
            against 75 kept is a search that is not reaching what is there. */}
        {queries.length > 0 && (
          <>
            <table className="inspector-terms">
              <thead>
                <tr>
                  <th>{t("inspector.term")}</th>
                  <th title={t("inspector.termFoundHint")}>{t("inspector.termFound")}</th>
                  <th title={t("inspector.termNewHint")}>{t("inspector.termNew")}</th>
                  <th title={t("inspector.termPagesHint")}>{t("inspector.termPages")}</th>
                  <th title={t("inspector.termSiteHint")}>{t("inspector.termSite")}</th>
                  <th title={t("inspector.termTimeHint")}>{t("inspector.termTime")}</th>
                </tr>
              </thead>
              <tbody>
                {queries.map((entry) => (
                  <tr key={entry.term} className={entry.error ? "unreadable" : entry.new === 0 ? "muted" : ""}>
                    <td>
                      {entry.term}
                      {/* The addresses this term was actually asked at. Two
                          URLs side by side are what found the paging fault,
                          so the page hands them over rather than making them
                          be reconstructed (build 107). */}
                      {entry.urls.length > 0 && (
                        <button
                          className="link-button inspector-url"
                          title={entry.urls.join("\n")}
                          onClick={() => void copyUrls(entry.urls, setCopied)}
                        >
                          {copied === entry.urls[0] ? t("inspector.copied") : t("inspector.copyUrl")}
                        </button>
                      )}
                      {copied === entry.urls[0] && (
                        /* Shown as well as copied: a page served over plain
                           http cannot reach the clipboard at all in some
                           browsers, and then this is the copy. */
                        <code className="inspector-url-text">{entry.urls.join("\n")}</code>
                      )}
                    </td>
                    <td>{entry.found}</td>
                    <td>{entry.new}</td>
                    <td>
                      {entry.error ? <span className="reasons">{entry.error}</span> : entry.pages}
                      {/* The indexer answered with a page it had already
                          sent, so the walk stopped there. Worth saying: it
                          means this feed cannot be paged, and whatever the
                          site reports beyond the first page is out of
                          reach until that is fixed. */}
                      {entry.repeated && (
                        <span className="reasons" title={t("inspector.repeatedHint")}>
                          {" "}⚠ {t("inspector.repeated")}
                        </span>
                      )}
                    </td>
                    <td className="inspector-time">
                      {entry.seconds > 0 ? shortSeconds(entry.seconds) : "—"}
                    </td>
                    <td>
                      {entry.site_total === null ? "—" : entry.site_total}
                      {entry.site_total !== null && entry.site_total > entry.new && (
                        <small className="muted">
                          {" "}
                          {t("inspector.termMissing").replace(
                            "{n}", String(entry.site_total - entry.new),
                          )}
                        </small>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted inspector-mode-hint">
              {t("inspector.termsHint")}
              {elapsed !== null && (
                <strong className="inspector-total">
                  {" "}
                  {t("inspector.termTotal")
                    .replace("{time}", shortSeconds(elapsed))
                    .replace("{n}", String(releases.length))}
                </strong>
              )}
            </p>
            {/* Why a search that should reach ten pages reached one. The file
                named here lives in the deployment's own config folder, which
                a deploy must not overwrite - it holds credentials - so an
                improvement to it arrives by hand or not at all (build 108). */}
            {ability && !ability.listing && (
              <p className="notice-message">
                {t("inspector.noListing").replace("{file}", ability.file ?? "indexers/…")}
                <code className="inspector-url-text">
                  {'listing_url: "{base}/?c=1_2&f=0&q={query}&s=seeders&o=desc&p={page}"\n'}
                  {'listing_item: "/view/"'}
                </code>
              </p>
            )}
            {ability && ability.listing && !ability.total && (
              <p className="notice-message">
                {t("inspector.noTotal").replace("{file}", ability.file ?? "indexers/…")}
              </p>
            )}
          </>
        )}
    </div>

    {searched && (
      <div className="detail-panel">
        <div className="status-line">
          <h2>{t("inspector.grouped")}</h2>
          <span className="muted">
            {t("inspector.groupCount")
              .replace("{groups}", String(rows.length))
              .replace("{n}", String(releases.length))}
          </span>
        </div>
        <p className="muted">{t("inspector.rowsHint")}</p>

        {/* Beside what they filter (build 106). They used to sit in the panel
            above, a screen away from the list that changes when they are
            typed in - which is most of "the filter does not work". */}
        <div className="settings-grid three inspector-filters">
          <label className="field">
            <span>{t("inspector.filterSubgroup")}</span>
            <input
              value={filters.subgroup}
              placeholder={groupsSeen.slice(0, 3).join(", ")}
              onChange={(event) => setFilters({ ...filters, subgroup: event.target.value })}
            />
            <small className="muted">{t("inspector.filterSubgroupHint")}</small>
          </label>
          {/* Quality is its own box rather than a word in the one beside it:
              the tag filter matches what the parser *recognised as a tag*,
              and a resolution is a field of its own - typing 1080p there
              matched nothing, which is the reported fault (build 106). */}
          <label className="field">
            <span>{t("inspector.filterQuality")}</span>
            <select
              value={filters.resolution}
              onChange={(event) => setFilters({ ...filters, resolution: event.target.value })}
            >
              <option value="">{t("inspector.filterAny")}</option>
              {qualities.map((quality) => <option key={quality} value={quality}>{quality}</option>)}
            </select>
            <small className="muted">{t("inspector.filterQualityHint")}</small>
          </label>
          <label className="field">
            <span>{t("inspector.filterTag")}</span>
            <input
              value={filters.tag}
              placeholder="web, multi, eac3…"
              onChange={(event) => setFilters({ ...filters, tag: event.target.value })}
            />
            <small className="muted">{t("inspector.filterTagHint")}</small>
          </label>
        </div>

        {rows.length === 0 && <p className="muted">{t("inspector.groupedEmpty")}</p>}

        <div className={`setup-split${row ? " open" : ""}`}>
          <div className="setup-split-list">
            {rows.length > 0 && (
              <div className="setup-rows tall">
                {rows.map((candidate) => (
                  <button
                    type="button"
                    key={candidate.key}
                    className={[
                      "setup-row",
                      candidate.key === rowKey ? "selected" : "",
                      rankOf(candidate, mine, known) === 0 ? "mine" : "",
                      rankOf(candidate, mine, known) === 1 ? "known" : "",
                    ].filter(Boolean).join(" ")}
                    onClick={() => setRowKey(candidate.key === rowKey ? null : candidate.key)}
                  >
                    <span className="setup-row-name">
                      <strong>{candidate.subgroup ?? t("setup.noGroup")}</strong>
                      <span className="setup-row-quality">
                        {candidate.resolution ?? t("setup.noQuality")}
                      </span>
                      {/* Everything else the parser recognised about these
                          files - the source, the codecs, what makes this copy
                          this copy. Two rows of one group differ by exactly
                          this, so it belongs on the line you compare. */}
                      {miscOf(candidate).length > 0 && (
                        <span className="setup-row-misc">{miscOf(candidate).join(", ")}</span>
                      )}
                    </span>
                    {/* Not muted (build 109): with several rows of one group
                        this line is the difference between them, so it reads
                        like a name rather than like a footnote. */}
                    <small className="setup-row-folder">
                      {candidate.folderName ?? candidate.title}
                      {(candidate.edition.length > 0 || candidate.editionNot.length > 0) && (
                        <span className="setup-row-edition">
                          {[
                            ...candidate.edition,
                            ...candidate.editionNot.map((tag) => `no ${tag}`),
                          ].join(" · ")}
                        </span>
                      )}
                    </small>
                  </button>
                ))}
              </div>
            )}
          </div>
          {row && scan && (
            <div className="setup-split-files">
              <SelectionFilesPanel
                scan={scan}
                episodeCount={picked?.episode_count ?? null}
                onClose={() => setRowKey(null)}
              />
            </div>
          )}
        </div>
        {rows.length > 0 && !row && <p className="muted">{t("inspector.pickRow")}</p>}
      </div>
    )}
  </section>;
}

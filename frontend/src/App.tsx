import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { addAnime, authState, setupState, deleteAnime, getAnime, getAppInfo, getAppSettings, getCalendar, calendarStatus, updateCalendar, rebuildCalendar, resetCalendar, calendarResetPreview, getAnimeHealth, getHealth, listAnime, listProgress, markAnimeSeen, markWatchlistVisit, searchAnime, signOut, updateAnime, updateAppSettings, type AnimeDetail, type AnimeSearchResult, type AnimeSummary, type AppInfo, type AuthState, type CalendarSeason, type CalendarStatus, type CalendarUpdateScope, type EpisodeSummary, type Health, type StorageMode, type WatchlistSort } from "./api/client";
import { ActivityPage } from "./components/ActivityPage";
import { NoClientNotice } from "./components/NoClientNotice";
import { AttentionPage } from "./components/AttentionPage";
import { RolloverBanner } from "./components/RolloverBanner";
import { airsLabel, dateBubbleText, hasStarted, setupDue } from "./airsLabel";
import { keepIfSame } from "./sameJson";
import { knownLibraries, rememberLibraries } from "./libraryCache";
import { IndexerProfilesPage } from "./components/IndexerProfilesPage";
import { AnimeSetupDialog } from "./components/AnimeSetupDialog";
import { CalendarDebugPage } from "./components/CalendarDebug";
import { CalendarUpdateDialog } from "./components/CalendarUpdateDialog";
import { FindFilesDialog } from "./components/FindFilesDialog";
import { anidbUrl } from "./anidbUrl";
import { malUrl } from "./malUrl";
import { BackToTop } from "./components/BackToTop";
import { asCandidate } from "./animeCandidate";
import { EpisodeFilesDialog } from "./components/EpisodeFilesDialog";
import { LifecycleDialog, currentStatusText } from "./components/LifecycleDialog";
import { LoginPage } from "./components/LoginPage";
import { SetupWizard } from "./components/SetupWizard";
import { AnimeAdultRow, AnimeLibraryRow } from "./components/AnimeLibraryRow";
import { listLibraries, providerCoverUrl, type Library } from "./api/client";
import { applyAdultFilter, currentLibraryIds, FOCUS_LIBRARY_KEY, groupByLibrary, matchesName } from "./watchlistLibraries";
import { TasteDialog } from "./components/TasteDialog";
import { AdultIcon, TasteIcon } from "./components/WatchlistIcons";
import { InputClearButton } from "./components/InputClearButton";
import { sortByRecommendation, suggestionsFor, tasteProfile } from "./taste";
import { browserScroll, recallScroll, rememberScroll, restoreScroll } from "./scrollMemory";
import { ImportFoldersPrompt } from "./components/ImportFoldersPrompt";
import { LibrariesPage } from "./components/LibrariesPage";
import { AnidbPage } from "./components/AnidbPage";
import { ShokoPage } from "./components/ShokoPage";
import { Cover } from "./components/Cover";
import { InProgressPage } from "./components/InProgressPage";
import { AnimeConfigDialog } from "./components/AnimeConfigDialog";
import { NavIcon } from "./components/NavIcon";
import { ProfilesPage } from "./components/ProfilesPage";
import { ReleaseChangePanel } from "./components/ReleaseChangePanel";
import { ShokoHelpButtons } from "./components/ShokoHelpButtons";
import { ReleaseInspectorPage } from "./components/ReleaseInspectorPage";
import { RssSourcesPage } from "./components/RssSourcesPage";
import { SchedulePage } from "./components/SchedulePage";
import { SettingsPage } from "./components/SettingsPage";
import { levelOf, openChecks, problems, type HealthGroups } from "./configChecks";
import { AttentionCorner, NewEpisodeCorner } from "./components/CardCorners";
import { progressText } from "./cardCorners";
import { checksBadge, progressBadge, watchlistBadge, type NavBadge } from "./navBadges";
import { ChecksPage } from "./components/ChecksPage";
import { getSystemChecks, type SystemChecks } from "./api/client";
import { getLanguage, setLanguage, t } from "./i18n";
import { airingNow, currentSeason, notYetAiring, seasonIsAfter, seasonLabel, seasonStartDate } from "./seasons";
import type { Season, SeasonName } from "./seasons";
import { formatClock, setWhenFormats } from "./formatWhen";
import { airLine } from "./episodeAirDate";
import { isHiddenKind, kindLabel, kindShort, matchesGenres, parseGenres, readsAsAdult, starText, starsIn, stepText, thisSeason, withRelatedFirst } from "./calendarView";
import { navigation, parseHash, parseSeason, viewToHash, type Navigation, type View } from "./routing";
import { CalendarDetailDialog } from "./components/CalendarDetailDialog";
import { episodeNumberSize, episodeRowView, hasTwoFiles } from "./episodeRow";
import { reasonText } from "./reasons";
import { useLockedPageScroll } from "./useLockedPageScroll";
import { tracksAlready } from "./library";
import { profileSummary } from "./profileText";
import { ToastProvider, useToast, type ToastCorner, type ToastKind } from "./toasts";

/** What every announcing handler here needs: a way to say one thing. */
type ShowToast = (message: string, kind?: ToastKind) => void;

/**
 * The dot beside the Shoko Server line, when there is one.
 *
 * Amber for a refused key rather than red: the address is right and something
 * is answering, which is a different errand from a machine that is not there.
 */
const SHOKO_DOT: Record<Health["shoko"], string> = {
  ok: "healthy",
  unauthorised: "warn",
  unreachable: "down",
  unknown: "idle",
  disabled: "idle",
};

/* "attention" sits before "activity" (build 125): one is a to-do list of
   unresolved conditions, the other a diary of what happened, and the
   to-do list is the one somebody needs to see. They shared a page until
   this build - *"currently Attention does not exist as seperate window,
   we should add it."* */
/** Only meaningful when Shoko Server owns the library (build 137). */
const SHOKO_PAGES: Navigation[] = ["shoko"];

/**
 * The pages that only make sense once something can be downloaded.
 *
 * Hidden in Basic mode rather than shown and empty: In Progress with no
 * indexer behind it is not "nothing to show yet", it is a promise AniFlow
 * cannot keep. Indexers stays visible, because adding one is how you leave
 * Basic mode - hiding the way out would be a trap.
 */
const DOWNLOAD_PAGES: Navigation[] = ["profiles", "rssSources", "progress", "inspector"];

/**
 * The pages that exist for developing AniFlow, hidden unless aniflow.yaml
 * says debug_mode. The Release Inspector reads a release name apart and
 * explains the decision - useful while the matcher is being written, noise
 * for somebody who just wants their episodes. Attention and Activity joined
 * it in build 177: the user asked for both to be debug-only panels.
 */
const DEBUG_PAGES: Navigation[] = ["inspector", "attention", "activity"];

/** How the calendar orders a season. Popularity is the order it arrives in. */
type CalendarSort = "airing" | "name" | "popularity" | "recommended";

/** The one notification an update writes to, however many polls it takes. */
const UPDATE_TOAST = "calendar-update";

/**
 * Nothing is rendered until the server says who you are.
 *
 * The check is one request, and it is the same request that tells the login
 * screen which shape to take. A flash of the real interface before the answer
 * arrives would be a flash of somebody else's library, so the gate waits.
 */
/**
 * Hands a parent a way to raise notifications.
 *
 * Renders nothing. It exists because the provider must wrap everything that
 * announces, so the component holding the handlers sits *above* the hook -
 * and lifting the provider higher would put it above the settings that decide
 * where notifications appear.
 */
function ToastBridge({ target }: { target: { current: ShowToast } }) {
  const { show } = useToast();
  useEffect(() => { target.current = show; }, [target, show]);
  return null;
}

export function App() {
  const [auth, setAuth] = useState<AuthState | null>(null);
  //: null until somebody has asked, so a wizard left half-finished is resumed
  //: after a reload instead of skipped (build 137).
  const [setupDone, setSetupDone] = useState<boolean | null>(null);

  useEffect(() => {
    if (!auth?.authenticated) return;
    void setupState()
      .then((state) => setSetupDone(state.completed))
      // An install that has been running is set up by definition, and an
      // older build has no such endpoint - either way, not the wizard.
      .catch(() => setSetupDone(true));
  }, [auth?.authenticated]);

  useEffect(() => {
    void authState()
      .then(setAuth)
      // An unreachable server is not an open door: with no answer, the login
      // screen is the safe thing to show.
      .catch(() => setAuth({ needs_setup: false, authenticated: false, username: null }));
  }, []);

  if (auth === null) return <p className="loading login-waiting">{t("auth.checking")}</p>;
  // A fresh install goes through the wizard rather than a login form: there is
  // no account to log into yet, and the account is only the first of the four
  // things AniFlow has to be told (build 137).
  if (auth.needs_setup || (auth.authenticated && setupDone === false)) {
    return (
      <SetupWizard
        onDone={(next) => {
          setSetupDone(true);
          setAuth(next);
        }}
      />
    );
  }
  if (!auth.authenticated) return <LoginPage state={auth} onAuthenticated={setAuth} />;
  return <Workspace auth={auth} onSignedOut={setAuth} />;
}

function Workspace({ auth, onSignedOut }: { auth: AuthState; onSignedOut: (next: AuthState) => void }) {
  const [view, setView] = useState<View>(() => parseHash(window.location.hash));
  const [health, setHealth] = useState<Health | null>(null);
  const [appInfo, setAppInfo] = useState<AppInfo | null>(null);
  const [anime, setAnime] = useState<AnimeSummary[]>([]);
  /* The latest list, for effects that must read it without re-running when it changes. */
  const animeRef = useRef(anime);
  animeRef.current = anime;
  const [selectedAnime, setSelectedAnime] = useState<AnimeDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [setupCandidate, setSetupCandidate] = useState<AnimeSearchResult | null>(null);
  const [addError, setAddError] = useState<string | null>(null);
  // Where notifications appear. Read once with the other settings; the
  // default matches the server's so the first render is not a guess.
  const [toastCorner, setToastCorner] = useState<ToastCorner>("top-right");
  // Whether there is a Shoko Server to compare the watchlist against. Both
  // halves are needed: a URL with no key reads nothing.
  const [shokoLinked, setShokoLinked] = useState(false);
  // Off until the server says otherwise - the same reasoning as basicMode
  // below: a slow first request must not flash a page that is about to be
  // hidden. aniflow.yaml is the only place this can be turned on.
  const [debugMode, setDebugMode] = useState(false);
  const [addQuery, setAddQuery] = useState("");
  const announce = useRef<ShowToast>(() => undefined);
  // Basic until the server says otherwise, so a slow first request cannot
  // flash pages that are about to be hidden.
  const basicMode = appInfo?.app_mode === "basic";
  const hidden = new Set<Navigation>([
    ...(basicMode ? DOWNLOAD_PAGES : []),
    ...(debugMode ? [] : DEBUG_PAGES),
    // Standalone mode has no Shoko Server in the pipeline, so a page about
    // one describes machinery that is not there. Hidden on the mode rather
    // than on whether credentials happen to be filled in: a URL left over
    // from an experiment is not a reason to offer it.
    ...(appInfo?.mode === "shokoserver" ? [] : SHOKO_PAGES),
  ]);
  const visibleNavigation = navigation.filter((item) => !hidden.has(item));
  const [language, setLanguageState] = useState<"en" | "de">(getLanguage());

  const currentPage = view.page;
  const page = view.page;

  /** Navigate through browser history so mouse back/forward stay in-app. */
  /* Where the watchlist was scrolled to, taken as we leave it - the old page is
     still on screen at this point, so the position is the real one (build 198). */
  const pageRef = useRef(view.page);
  pageRef.current = view.page;
  function leavingTheWatchlist() {
    if (pageRef.current === "dashboard") rememberScroll("watchlist", window.scrollY);
  }

  function go(next: View) {
    const target = viewToHash(next);
    if (window.location.hash === target) {
      leavingTheWatchlist();
      setView(next);
      return;
    }
    window.location.hash = target; // fires hashchange -> setView
  }

  function changeLanguage(next: "en" | "de") {
    setLanguage(next);
    setLanguageState(next);
  }

  useEffect(() => {
    const onHash = () => {
      leavingTheWatchlist();
      setView(parseHash(window.location.hash));
    };
    window.addEventListener("hashchange", onHash);
    // Normalise the initial URL so refresh/reopen restores the same view.
    const target = viewToHash(parseHash(window.location.hash));
    if (window.location.hash !== target) window.history.replaceState(null, "", target);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  /* Back on the watchlist: where it was. Anywhere else: the top, instead of
     whatever the old position clamps to on a shorter page (build 198). */
  useEffect(() => {
    if (page === "dashboard") return restoreScroll(recallScroll("watchlist"), browserScroll);
    window.scrollTo(0, 0);
    return undefined;
  }, [page]);

  useEffect(() => {
    void getHealth()
      .then(async (first) => {
        setHealth(first);
        /* "Shoko Server not checked yet" is true and useless (build 118).
           Nothing had asked, and nothing would until the worker's next tick.
           So the first page load asks - one cheap request, and only when the
           answer is genuinely unknown. */
        if (first.shoko !== "unknown") return;
        const { checkShoko } = await import("./api/client");
        await checkShoko().catch(() => undefined);
        setHealth(await getHealth().catch(() => first));
      })
      .catch(() => setHealth(null));
    void getAppInfo().then(setAppInfo).catch(() => setAppInfo(null));
    /* Opening AniFlow is a visit: what arrived since the last one is new for a day (build 239). */
    void markWatchlistVisit()
      .catch(() => undefined)
      .then(() => listAnime())
      .then(setAnime)
      .catch(() => setAnime([]));
    // Three things are read here, all of which decide what the *frame* looks
    // like: where a message raised from anywhere lands, whether there is a
    // Shoko Server to compare against, and whether the developer pages exist.
    // Everything else on the Settings page belongs to that page.
    void getAppSettings()
      .then((settings) => {
        setToastCorner(settings.toast_corner);
        setShokoLinked(Boolean(settings.shoko_url && settings.has_shoko_api_key));
        setDebugMode(settings.debug_mode);
        // How this installation writes a date and a time. Set once, here,
        // rather than threaded through every view that shows one.
        setWhenFormats(settings.time_format, settings.date_format);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (view.page !== "anime" || !view.animeId) {
      setSelectedAnime(null);
      return;
    }
    let cancelled = false;
    /* Opening an anime is what clears its blue corner (build 210). Cleared here at once as well, so
       the card and the sidebar number agree without waiting for the next refresh. */
    const opened = view.animeId;
    if ((animeRef.current.find((item) => item.id === opened)?.new_episode_count ?? 0) > 0) {
      void markAnimeSeen(opened).catch(() => undefined);
      setAnime((items) => items.map((item) => item.id === opened ? { ...item, new_episode_count: 0 } : item));
    }
    setLoadingDetail(true);
    getAnime(view.animeId)
      .then((detail) => { if (!cancelled) setSelectedAnime(detail); })
      .catch(() => { if (!cancelled) go({ page: "dashboard" }); })
      .finally(() => { if (!cancelled) setLoadingDetail(false); });
    return () => { cancelled = true; };
  }, [view]);

  /* The numbers on the sidebar (build 210). Watchlist adds up what is new; In Progress counts what
     failed or waits on you. Re-read every minute while the tab is in front, so a number is never
     much older than the page behind it. In Progress is skipped without an indexer: it is hidden. */
  const [progressAlert, setProgressAlert] = useState<NavBadge | null>(null);
  /* The checklist's number (build 232): read once when the app opens and every ten minutes, because it asks the
     torrent client and writes a test file in each library; the Checks page refreshes it when it is open. */
  const [checksSummary, setChecksSummary] = useState<{ errors: number; warnings: number } | null>(null);
const keepChecks = useCallback((result: SystemChecks) => {
    // Without the live rows: the background refresh does not ask the indexers, so a number that included them
    // would change every time the page was opened.
    const local = result.checks.filter((check) => !check.live);
    setChecksSummary({
      errors: local.filter((check) => check.state === "error").length,
      warnings: local.filter((check) => check.state === "warning").length,
    });
  }, []);
  useEffect(() => {
    const read = () => {
      if (!document.hidden) void getSystemChecks().then(keepChecks).catch(() => undefined);
    };
    const first = window.setTimeout(read, 4_000);
    const timer = window.setInterval(read, 600_000);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(timer);
    };
  }, [keepChecks]);
  const readProgress = useCallback(() => {
    void listProgress()
      .then((items) => setProgressAlert((current) => keepIfSame(current, progressBadge(items))))
      .catch(() => undefined);
  }, []);

  /** A refreshed anime: the list row and, if open, the detail view. */
  const refreshList = useCallback(() => {
    // Replaced only when it differs: an equal list drew every card again and flickered (build 207).
    void listAnime().then((next) => setAnime((current) => keepIfSame(current, next))).catch(() => undefined);
    // Also re-read the server's clock: the weekly schedule highlights today,
    // and a tab left open overnight would otherwise mark yesterday.
    void getAppInfo().then((next) => setAppInfo((current) => keepIfSame(current, next))).catch(() => undefined);
  }, []);

  /* Keeps the sidebar numbers fresh while the tab is in front (build 210). */
  useEffect(() => {
    const tick = () => {
      if (document.hidden) return;
      refreshList();
      if (appInfo?.app_mode !== "basic") readProgress();
    };
    if (appInfo?.app_mode !== "basic") readProgress();
    const timer = window.setInterval(tick, 60_000);
    return () => window.clearInterval(timer);
  }, [refreshList, readProgress, appInfo?.app_mode]);

  /**
   * Add an anime, or ask the questions first.
   *
   * In Advanced mode the setup dialog is where the release name is chosen, and
   * that choice cannot be made for the user. In Basic mode there is no indexer
   * to search and nothing to choose from, so the dialog would be a form with
   * one field and no purpose - the anime is added straight away.
   */
  async function addCandidate(candidate: AnimeSearchResult, viewed?: Season) {
    /* A show that has not premiered goes straight in as a future entry
       (build 155): *"it opens the set up release matching. This is wrong, as
       the anime does not exist yet, therefore there can not be a release
       group out there."*  In both modes: Shoko names the folder in one, and
       in the other it is taken from the release's name when release
       matching is set up - from the entry's own page, once the season has
       started. */
    // The premiere date when there is one; a calendar entry announced
    // without a date still says "not yet aired".
    /* The season the card was picked from counts too (build 174): *"Adding
       an anime from a future season somehow ended up in airing now."*  A
       card on a future season's page is a future show whatever its own
       record says - a missing or stale date is the provider's gap, and
       walking the user through release matching for it was the result. */
    const fromFutureSeason = viewed !== undefined && seasonIsAfter(viewed, currentSeason());
    const unaired = notYetAiring(candidate) || (!candidate.air_start_date && candidate.airing_status === "NOT_YET_AIRED")
      || fromFutureSeason;
    const future = unaired;
    if (fromFutureSeason && !notYetAiring(candidate)) {
      // Dated in the past (or not at all) yet listed in a later season: the
      // season's first day is the honest lower bound, and keeps the entry in
      // "upcoming" rather than "airing now" until the provider corrects it.
      candidate = { ...candidate, air_start_date: seasonStartDate(viewed) };
    }
    if (!basicMode && !future) {
      setSetupCandidate(candidate);
      return;
    }
    setAddError(null);
    try {
      // No setup at all: the server records it as a tracker entry - or, for
      // a future show, as a future entry - and writes no release name,
      // profile or feed.
      const added = future
        ? await addAnime(candidate, candidate.folder_hint || candidate.display_name, {
            subgroup: null, resolution: null, template_id: null, feed_source_id: null, future: true,
          })
        : await addAnime(candidate, candidate.display_name);
      setAnime((items) => [added, ...items]);
      announce.current(t("toast.animeAdded").replace("{name}", added.display_name));
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      setAddError(reason);
      announce.current(t("toast.failed").replace("{reason}", reason), "error");
    }
  }

  /* adoptShokoSeries lived here until build 117.
   *
   * It was the in-between step for a series that was only in Shoko: look it
   * up by its AniDB id, open the setup dialog, add it one at a time. There
   * is no such series any more - the server adopts Shoko's library by
   * itself, and fills in more than this path could from the browser. See
   * services/shoko_adopt.py.
   */

  function updateSummary(updated: AnimeSummary | AnimeDetail) {
    setAnime((items) => items.map((item) => item.id === updated.id ? { ...item, ...updated } : item));
    setSelectedAnime((item) => item?.id === updated.id ? { ...item, ...updated } : item);
    /* A summary has no episodes (build 210). Convert, Edit, the cover and the rest answer with one, so
       the merge above left the old episode list on screen until you left the page and came back. Read
       the whole anime again, whichever dialog it was. A full answer (it has the episodes) needs no
       second request, which is also what stops this calling itself. */
    if (!("episodes" in updated)) {
      void getAnime(updated.id)
        .then((full) => {
          setAnime((items) => items.map((item) => item.id === full.id ? { ...item, ...full } : item));
          setSelectedAnime((item) => item?.id === full.id ? full : item);
        })
        .catch(() => undefined);
    }
  }


  return <ToastProvider corner={toastCorner}>
    {/* The provider has to be above everything that might announce, which
        means the shell cannot use the hook itself. This hands the handlers a
        way to speak without moving the provider into a parent that knows
        nothing about settings. */}
    <ToastBridge target={announce} />
    {/* The x inside any text field with text in it, on every page and dialog (build 210). */}
    <InputClearButton />
    <main className="app-shell">
    <aside className="sidebar" aria-label={t("nav.label")}>
      <div className="brand">
        {/* The full logo, with the word "AniFlow" in it. Served at 2x so it stays
            crisp on HiDPI. The browser-tab icons are the text-free mark. */}
        <img className="brand-mark" src="/logo-mark.png" alt={t("app.name")} width={52} height={52} />
        <div><strong>{t("app.name")}</strong><small>{t("app.tagline")}</small></div>
      </div>
      <nav>{visibleNavigation.map((item) => (
        <button
          className={currentPage === item ? "nav-item active" : "nav-item"}
          key={item}
          onClick={() => go({ page: item })}
        >
          <NavIcon name={item} />
          <span>{t(`nav.${item}`)}</span>
          {(() => {
            /* The small numbers (build 210): new episodes beside Watchlist, failures beside In Progress. */
            const badge: NavBadge | null = item === "dashboard" ? watchlistBadge(anime) : item === "progress" ? progressAlert : item === "checks" ? checksBadge(checksSummary) : null;
            return badge && (
              <span className={`nav-badge ${badge.tone}`} title={t(`nav.badge.${badge.tone}`).replace("{n}", String(badge.count))}>
                {badge.text}
              </span>
            );
          })()}
        </button>
      ))}</nav>
      {/* Who you are, at the foot of the sidebar with the status line - the
          two belong together, and neither is what you came to the page for. */}
      <div className="sidebar-account">
        {/* An initial rather than an avatar: it needs no upload, no storage
            and no default image, and it does the one job the icon has, which
            is to stop the name floating there on its own. */}
        <span className="account-avatar" aria-hidden="true">
          {(auth.username ?? "?").trim().charAt(0).toUpperCase()}
        </span>
        <strong title={auth.username ?? undefined}>{auth.username}</strong>
        <button
          className="link-button"
          onClick={() => void signOut().then(onSignedOut).catch(() => undefined)}
        >
          {t("auth.signOut")}
        </button>
      </div>
      {/* Two things can be down independently, and one line saying
          "Connected" hid that: the metadata provider was refusing every
          request while this still read green, because it only ever described
          the backend. The metadata source is not named here on purpose - which
          database answered is AniFlow's business, and the interesting part is
          whether the data is complete. */}
      <div className="health-block">
        {/* The metadata line is gone (build 94). It described whether a
            provider answered, which stopped being the interesting question in
            build 86 when the calendar started drawing downloaded data: AniList
            has been refusing for weeks and nothing here depends on it. A
            permanent amber dot for a condition with no consequence is a dot
            people learn to ignore, which costs the two below their meaning. */}
        <div className="health" title={health ? health.timestamp : undefined}>
          <span className={`health-dot ${health?.status === "ok" ? "healthy" : "down"}`} />
          {t(health?.status === "ok" ? "status.backendOnline" : "status.backendOffline")}
        </div>
        {/* Only when there is a Shoko Server to talk about. It is optional -
            the file hand-over works through folders without it - so a line
            saying "disabled" would be a line about nothing. */}
        {health && health.shoko !== "disabled" && (
          <div className="health" title={t(`status.shokoHint.${health.shoko}`)}>
            <span className={`health-dot ${SHOKO_DOT[health.shoko]}`} />
            {t(`status.shoko.${health.shoko}`)}
          </div>
        )}
        {/* Its own line now that there are two indicators above it: squeezed
            beside one of them it wrapped, and a version broken across two
            lines is unreadable at this size. */}
        <small className="version-tag" title={appInfo ? `${appInfo.build_string} · ${appInfo.environment}` : undefined}>{appInfo ? `v.${appInfo.version} b.${appInfo.build}` : "v?"}</small>
      </div>
      {/* The badge above asks the server which build it is, so it says nothing
          about the page you are looking at. When the two disagree the browser
          is showing an old bundle - which looks exactly like a change that did
          not work, and has been mistaken for one before. */}
      {appInfo && __BUILD__ > 0 && appInfo.build !== __BUILD__ ? (
        <button className="stale-bundle" onClick={() => window.location.reload()}>
          {t("status.staleBundle").replace("{page}", String(__BUILD__)).replace("{server}", String(appInfo.build))}
        </button>
      ) : null}
    </aside>
    <section className="content">
      {page === "anime" ? (
        selectedAnime
          ? <AnimePage anime={selectedAnime} mode={appInfo?.mode ?? "standalone"} advanced={!basicMode} debugMode={debugMode} onBack={() => go({ page: "dashboard" })} onUpdated={updateSummary} onDeleted={(id) => { setAnime((items) => items.filter((item) => item.id !== id)); go({ page: "dashboard" }); }} />
          : <p className="loading">{loadingDetail ? t("detail.loading") : t("detail.loading")}</p>
      ) : <>
        <header className="page-header"><div><p className="eyebrow">{t("app.name")}</p><h1>{currentPage === "dashboard" ? t("dashboard.title") : currentPage === "calendar" ? t("calendar.title") : t(`nav.${currentPage}`)}</h1></div><div className="actions">{currentPage === "dashboard" && <button className="button primary" onClick={() => setAddOpen(true)}>{t("dashboard.add")}</button>}</div></header>
        {page === "dashboard" && <Dashboard anime={anime} onSelect={(id) => go({ page: "anime", animeId: id })} onVisible={refreshList} todayWeekday={appInfo?.today_weekday ?? null} advanced={!basicMode} shokoLinked={shokoLinked} />}
        {page === "calendar" && (
          <Calendar onPick={(candidate, viewed) => void addCandidate(candidate, viewed)} library={anime} onOpenSettings={() => go({ page: "settings", section: "calendar" })} onOpenDebug={() => go({ page: "calendarDebug" })} debugMode={debugMode} section={view.section} onSeason={(year, season) => go(year && season ? { page: "calendar", section: `${year}/${season}` } : { page: "calendar" })} />
        )}
        {page === "calendarDebug" && (
          <CalendarDebugPage onBack={() => go({ page: "calendar" })} />
        )}
        {page === "libraries" && <LibrariesPage shokoMode={appInfo?.mode === "shokoserver"} />}
        {page === "shoko" && <ShokoPage section={view.section} onSection={(name) => go({ page: "shoko", section: name })} onOpenAnime={(id) => go({ page: "anime", animeId: id })} />}
        {page === "anidb" && <AnidbPage section={view.section} onSection={(name) => go({ page: "anidb", section: name })} onOpenAnime={(id) => go({ page: "anime", animeId: id })} />}
        {page === "inspector" && debugMode && <ReleaseInspectorPage />}
        {page === "indexers" && (
          <IndexerProfilesPage
            configDir={appInfo?.config_dir ?? "/config"}
            editId={view.indexerId ?? null}
            // Drop the id from the URL once the editor is open, so a reload or
            // a later visit does not reopen it.
            onEditHandled={() => go({ page: "indexers" })}
          />
        )}
        {page === "progress" && <InProgressPage anime={anime} onOpenAnime={(id) => go({ page: "anime", animeId: id })} />}
        {page === "profiles" && <ProfilesPage />}
        {page === "rssSources" && (
          <RssSourcesPage onOpenIndexer={(id) => go({ page: "indexers", indexerId: id })} />
        )}
        {page === "attention" && <AttentionPage debugMode={debugMode} />}
        {page === "activity" && <ActivityPage debugMode={debugMode} />}
        {page === "schedule" && <SchedulePage />}
        {page === "checks" && <ChecksPage onResult={keepChecks} />}
        {page === "settings" && <SettingsPage section={view.section} onSection={(name) => go({ page: "settings", section: name })} basicMode={basicMode} language={language} onLanguageChange={changeLanguage} username={auth.username} onAccountChanged={onSignedOut} onSaved={(stored) => { setToastCorner(stored.toast_corner); setShokoLinked(Boolean(stored.shoko_url && stored.has_shoko_api_key)); }} />}
      </>}
    </section>
    {addError && <p className="error-message add-error">{addError}</p>}
    {addOpen && <AddAnimeDialog library={anime} initialQuery={addQuery} onClose={() => { setAddOpen(false); setAddQuery(""); }} onSelect={(candidate) => { setAddOpen(false); setAddQuery(""); void addCandidate(candidate); }} />}
    {setupCandidate && (
      <AnimeSetupDialog
        candidate={setupCandidate}
        onClose={() => setSetupCandidate(null)}
        onAdded={(item) => {
          setAnime((items) => [item, ...items]);
          setSetupCandidate(null);
          announce.current(t("toast.animeAdded").replace("{name}", item.display_name));
        }}
      />
    )}
  <BackToTop /></main></ToastProvider>;
}

/** Monday-first, matching the backend's `airs_weekday` (0 = Monday). */
const WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;

/** How many cards fit across. Bounded because neither extreme is usable: below
 *  three the covers are absurd on a wide screen, above eight the titles clip. */
const MIN_PER_ROW = 2;
const MAX_PER_ROW = 9;
const DEFAULT_PER_ROW = 7;

const SORT_KEY = "aniflow.watchlist.sort";
const ADULT_KEY = "aniflow.watchlist.hideAdult";
const SIZE_KEY = "aniflow.watchlist.perRow";
const QUERY_KEY = "aniflow.watchlist.query";

function storedSort(): WatchlistSort {
  const raw = window.localStorage.getItem(SORT_KEY);
  return raw === "name" || raw === "added" || raw === "schedule" || raw === "library" ? raw : "added";
}

function storedPerRow(): number {
  // getItem returns null when nothing is stored, and Number(null) is 0 - which
  // is finite, so a naive check clamped every first visit to the minimum.
  const stored = window.localStorage.getItem(SIZE_KEY);
  if (stored === null) return DEFAULT_PER_ROW;
  const raw = Number(stored);
  if (!Number.isFinite(raw)) return DEFAULT_PER_ROW;
  return Math.min(MAX_PER_ROW, Math.max(MIN_PER_ROW, Math.round(raw)));
}

/** One watchlist card.
 *
 * `showProgress` is off in the weekly view: the timetable is there to answer
 * "what airs when", the airing time is already on the cover, and at seven
 * columns the episode count is what pushes a card to three lines.
 */
function AnimeCard({ item, onSelect, showProgress = true, needsConversion = false }: {
  item: AnimeSummary;
  onSelect: (id: string) => void;
  showProgress?: boolean;
  /** True when an indexer exists but this entry has no release name yet. */
  needsConversion?: boolean;
}) {
  const airs = airsLabel(item.air_start_date);
  const kind = kindLabel(item.format);
  return (
    /* An <article> with a button's manners rather than a <button> (build
       151): the red corner is a button of its own now, and a button inside a
       button is not valid HTML. Same pattern as the calendar card. */
    <article
      className="anime-card"
      role="button"
      tabIndex={0}
      onClick={() => onSelect(item.id)}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect(item.id);
        }
      }}
    >
      {/* The name on the picture, where the calendar puts it (build 151):
          same place, same white, same gradient and stroke behind it - one
          card language across the two pages. No genres: the calendar shows
          them because it is for choosing, and this is a list you already
          chose. */}
      <span className="anime-poster" data-unaired={airs ? "true" : undefined}>
        <Cover anime={item} />
        <span className="calendar-card-text anime-card-text">
          {/* When it starts, above the name (build 206): a countdown in the last week, then the
              date as Settings writes dates. Only for a show whose first episode is still ahead. */}
          {airs && (
            <span className="anime-airs-pill" title={airs.title}>
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <rect x="3" y="5" width="18" height="16" rx="2" />
                <path d="M3 10h18M8 3v4M16 3v4" />
              </svg>
              {airs.text}
            </span>
          )}
          {/* In the pill's place once the date has passed (build 222): the countdown ends the day the show
              starts, and an anime added before it started and then forgotten had nothing left on its card. */}
          {!airs && setupDue(item) && (
            <span className="anime-airs-pill due" title={t("airs.setupDueTitle")}>
              {t("airs.setupDue")}
            </span>
          )}
          <strong title={item.display_name}>{item.display_name}</strong>
        </span>
        {/* The four health icons that sat under the progress line (builds
            120-150) are these two corners now: attention top left, the
            count of new episodes top right, nothing when all is well. */}
        <AttentionCorner health={item.health} onOpen={() => onSelect(item.id)} />
        <NewEpisodeCorner count={item.new_episode_count} newestAt={item.newest_episode_at} />
      </span>
      {/* One line instead of two: state, then progress. The space this frees
          goes to the cover. */}
      <p className="anime-meta">
        {/* Between parts, the card says so instead of "Active" (build 205). */}
        <span className={`anime-state state-${item.waiting_for_next_part ? "waiting" : item.lifecycle.toLowerCase()}`}>
          {item.waiting_for_next_part ? t("status.WAITING") : t(`status.${item.lifecycle}`)}
        </span>
        {/* The separator travels with the count: on a narrow card the line
            wraps, and a pipe left dangling at the end of the first line reads
            as a rendering fault. */}
        {/* What it is, between the state and the count (build 208). Dropped by the stylesheet
            when the card is too narrow for it (the size slider at its small end). */}
        {showProgress && kind && (
          <span className="anime-kind">
            <span className="anime-sep">|</span>
            <span className="kind-full">{kind}</span>
            <span className="kind-short">{kindShort(item.format)}</span>
          </span>
        )}
        {showProgress && (
          <span className={needsConversion ? "anime-progress needs-conversion" : "anime-progress"}>
            <span className="anime-sep">|</span>
            {/* A basic entry counts its episodes like any other now. It used
                to say "Basic" here, on the grounds that nothing was looking
                for releases so there was nothing to count - but the count is
                a fact about the *show*, and the files may well be on disk
                already. What it is stays visible: the card is still marked in
                red while an indexer exists and this entry has not been
                converted. */}
            {/* One form for every entry (build 156). A future entry said
                "Upcoming - no downloads set up yet", which broke the card's
                line; it sits under the Upcoming heading already, and its
                page says what is left to set up. An unknown count is
                `? / ?`, or `3 / ?` when files are on disk (build 151). */}
            {progressText(item.owned_episodes, item.episode_count)}
            {/* "+1" and nothing more (build 157): *"available is again a very
                long name"*, and "new" is the blue corner's word. The hover
                says what it counts. */}
            {item.available_episodes > 0 && (
              <span className="anime-available" title={t("dashboard.availableHint").replace("{n}", String(item.available_episodes))}>
                {" "}+{item.available_episodes}
              </span>
            )}
          </span>
        )}
      </p>
    </article>
  );
}

/* ShokoGapCard lived here until build 117.
 *
 * It was a card shaped like an anime card and deliberately not one: dashed
 * border, no id, nothing looking for its episodes, and a click that started
 * an adoption dialog. It went with the "Only in Shoko" category it belonged
 * to. Shoko's series are watchlist entries now - adopted by the server, with
 * everything it can know filled in - and the difference between the two
 * libraries is a colour on the ordinary card rather than a section of
 * different ones. See services/shoko_adopt.py.
 */

/** Anime that air on the same weekday, earliest first.
 *
 * A show with a weekday but no time (only a premiere date is known) sorts
 * after the timed ones rather than at midnight, which would put it first and
 * imply a slot it does not have.
 */
function byAiringTime(a: AnimeSummary, b: AnimeSummary): number {
  if (a.airs_time && b.airs_time) return a.airs_time.localeCompare(b.airs_time) || a.display_name.localeCompare(b.display_name);
  if (a.airs_time) return -1;
  if (b.airs_time) return 1;
  return a.display_name.localeCompare(b.display_name);
}

function Dashboard({ anime, onSelect, onVisible, todayWeekday, advanced = false, shokoLinked = false }: {
  anime: AnimeSummary[];
  onSelect: (id: string) => void;
  onVisible: () => void;
  /** Today in the *server's* timezone, so it agrees with the airing days it
   *  is drawn beside. Null while the app has not heard back yet. */
  todayWeekday: number | null;
  /** True in Advanced mode, where a tracker entry is the thing holding an
   *  anime back rather than simply what it is. */
  advanced?: boolean;
  /** Whether Shoko Server is configured, which is what puts the "take
   *  everything from Shoko" button on the page. */
  shokoLinked?: boolean;
}) {
  // "3 / 13" moves as episodes are imported, so the numbers are re-read whenever
  // the watchlist is opened - not only once at application start.
  useEffect(() => { onVisible(); }, [onVisible]);

  /* The blue counters no longer reset when the watchlist is left (build 210): an episode is new for a
     day, and opening that anime's page clears it - see the effect that opens an anime in App. */

  const [sort, setSort] = useState<WatchlistSort>(storedSort);
  const [perRow, setPerRow] = useState<number>(storedPerRow);
  /* Adult shows can be put out of sight with one button (build 192). The
     choice is remembered, and the count of what is out of sight is always
     on the button: hidden is not the same as gone. */
  const [hideAdult, setHideAdult] = useState<boolean>(() => window.localStorage.getItem(ADULT_KEY) === "1");
  useEffect(() => { window.localStorage.setItem(ADULT_KEY, hideAdult ? "1" : "0"); }, [hideAdult]);
  const [libraryList, setLibraryList] = useState<Library[]>(knownLibraries);
  useEffect(() => {
    void listLibraries()
      .then((answer) => {
        rememberLibraries(answer.libraries);
        setLibraryList((current) => keepIfSame(current, answer.libraries));
      })
      .catch(() => undefined);
  }, []);
  /* The search box (build 208): narrows as you type, by every name AniFlow holds. Kept for the
     session, so opening an anime and coming back finds the list as it was left. */
  const [query, setQuery] = useState<string>(() => {
    try { return window.sessionStorage.getItem(QUERY_KEY) ?? ""; } catch { return ""; }
  });
  useEffect(() => {
    try { window.sessionStorage.setItem(QUERY_KEY, query); } catch { /* blocked */ }
  }, [query]);
  const [tasteOpen, setTasteOpen] = useState(false);
  const searching = query.trim() !== "";
  const named = searching ? anime.filter((item) => matchesName(item, query)) : anime;
  const { shown: adultShown, hidden: adultHidden } = applyAdultFilter(named, hideAdult);
  /* The Weekly schedule is for what airs now: the libraries tagged Current season, the others
     stepped aside for as long as it is open (build 208). With none tagged it is greyed out, and
     a remembered choice of it falls back to "Recently added" rather than showing an empty week. */
  const currentIds = currentLibraryIds(libraryList);
  const weeklyUnavailable = currentIds !== null && currentIds.size === 0;
  const view: WatchlistSort = sort === "schedule" && weeklyUnavailable ? "added" : sort;
  const shown = view === "schedule" && currentIds !== null
    ? adultShown.filter((item) => item.library_id !== null && currentIds.has(item.library_id))
    : adultShown;
  /* Coming from a library's "Show in watchlist": scroll to its group once it is drawn. */
  useEffect(() => {
    if (sort !== "library" || anime.length === 0 || libraryList.length === 0) return;
    let wanted: string | null = null;
    try { wanted = window.sessionStorage.getItem(FOCUS_LIBRARY_KEY); } catch { /* blocked */ }
    if (!wanted) return;
    const timer = window.setTimeout(() => {
      try { window.sessionStorage.removeItem(FOCUS_LIBRARY_KEY); } catch { /* blocked */ }
      document.getElementById(`library-group-${wanted}`)?.scrollIntoView({ block: "start" });
    }, 150);
    return () => window.clearTimeout(timer);
  }, [sort, anime.length, libraryList.length]);

  useEffect(() => { window.localStorage.setItem(SORT_KEY, sort); }, [sort]);
  useEffect(() => { window.localStorage.setItem(SIZE_KEY, String(perRow)); }, [perRow]);

  /* takeFromShoko lived here until build 120. It moved to the Shoko Server
     page, which is the page about the two libraries - and the watchlist's
     controls are for looking at the watchlist. */

  const emptyState = (
    <section className="empty-state"><div className="empty-icon">✦</div><h2>{t("dashboard.empty.title")}</h2><p>{t("dashboard.empty.body")}</p><ImportFoldersPrompt onImported={onVisible} /></section>
  );

  // The list arrives newest-first from the API, which is the "added" order.
  const ordered = view === "name"
    ? [...shown].sort((a, b) => a.display_name.localeCompare(b.display_name))
    : shown;

  const sortButton = (option: WatchlistSort, disabled = false, title?: string) => (
    <button
      key={option}
      className={`button ${view === option ? "primary" : "secondary"}`}
      onClick={() => setSort(option)}
      disabled={disabled}
      title={title}
    >
      {t(`dashboard.sort.${option}`)}
    </button>
  );

  const controls = (
    <>
    <RolloverBanner onChanged={onVisible} />
    <div className="watchlist-controls">
      {/* The three ways to arrange the list sit together; the Weekly schedule stands apart,
          because it is not an arrangement of the same list - it is the airing libraries only
          (build 208). */}
      <div className="filter-row" role="group" aria-label={t("dashboard.sortLabel")}>
        {sortButton("added")}
        {sortButton("name")}
        {sortButton("library")}
        <span className="sort-splitter" aria-hidden="true" />
        {sortButton("schedule", weeklyUnavailable, weeklyUnavailable ? t("dashboard.weeklyNeedsTag") : t("dashboard.weeklyHint"))}
      </div>
      {/* "Take everything from Shoko" moved to the Shoko Server page in
          build 120 - it is a thing you do to the two libraries, and that is
          the page about them. */}
      {/* Same box as the calendar's: no button, no Enter, the cards narrow as you type. */}
      {anime.length > 0 && (
        <div className="watchlist-find">
          <div className="calendar-filters watchlist-search">
            <input
              className="calendar-filter"
              type="search"
              value={query}
              placeholder={t("dashboard.searchPlaceholder")}
              aria-label={t("dashboard.searchPlaceholder")}
              title={t("dashboard.searchHint")}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          {/* Beside the search, where the other filtering is (build 263), as icons: they were text buttons at the
              foot of a long page. The adult filter is lit while it is on (adult hidden); the heart opens the taste
              window. */}
          <button
            className={`button secondary watchlist-icon${hideAdult ? " active" : ""}`}
            onClick={() => setHideAdult((current) => !current)}
            aria-pressed={hideAdult}
            title={hideAdult ? t("dashboard.adultFilterOn") : t("dashboard.adultFilterOff")}
            aria-label={hideAdult ? t("dashboard.adultFilterOn") : t("dashboard.adultFilterOff")}
          >
            <AdultIcon hidden={hideAdult} />
          </button>
          <button
            className="button secondary watchlist-icon"
            onClick={() => setTasteOpen(true)}
            title={t("taste.open")}
            aria-label={t("taste.open")}
          >
            <TasteIcon />
          </button>
          {tasteOpen && <TasteDialog anime={anime} onClose={() => setTasteOpen(false)} />}
        </div>
      )}
      <label className="size-slider" title={t("dashboard.sizeHint")}>
        <span className="muted">{t("dashboard.size")}</span>
        <input
          type="range"
          min={MIN_PER_ROW}
          max={MAX_PER_ROW}
          step={1}
          value={perRow}
          onChange={(event) => setPerRow(Number(event.target.value))}
          aria-label={t("dashboard.sizeHint")}
        />
        <span className="size-count">{perRow}</span>
      </label>
    </div>
    </>
  );

  /* Only the count is left down here (build 263). "Show everything" was a debugging aid, and the adult filter and
     the taste window moved up beside the search box: a button at the foot of a long page is one nobody finds.
     What a search or the Weekly schedule leaves out is said by the count itself. */
  const footer = anime.length > 0 && (
    <div className="watchlist-footer">
      <p className="muted watchlist-count">
        {t("dashboard.count").replace("{shown}", String(shown.length)).replace("{total}", String(anime.length))}
      </p>
    </div>
  );

  if (searching && anime.length > 0 && shown.length === 0) {
    return <>
      {controls}
      <p className="muted watchlist-nomatch">
        {adultHidden > 0
          ? t("dashboard.searchOnlyAdult").replace("{query}", query.trim()).replace("{n}", String(adultHidden))
          : t("dashboard.searchNone").replace("{query}", query.trim())}
      </p>
      {footer}
    </>;
  }

  if (view === "library" && shown.length > 0) {
    const byLibrary = groupByLibrary(ordered, libraryList);
    return <>
      {controls}
      {byLibrary.map((group) => (
        <section key={group.library?.id ?? "none"} id={`library-group-${group.library?.id ?? "none"}`}>
          <h3 className="week-head">
            {group.library ? (group.library.key ? t(`libraries.system.${group.library.key}`) : group.library.name) : t("dashboard.noLibrary")}
            <span className="muted">{group.items.length}</span>
          </h3>
          <section className="anime-grid tight" style={{ "--per-row": perRow } as CSSProperties}>
            {group.items.map((item) => (
              <AnimeCard item={item} key={item.id} onSelect={onSelect} needsConversion={advanced && item.entry_mode === "basic"} />
            ))}
          </section>
        </section>
      ))}
      {footer}
    </>;
  }

  if (view === "schedule") {
    const scheduled = ordered.filter((item) => item.airs_weekday !== null);
    const unscheduled = ordered.filter((item) => item.airs_weekday === null);
    return <>
      {controls}
      {/* Seven columns, Monday to Sunday, each a stack in airing order. The
          weekday is the column and the time is the position in it, so the whole
          week reads as a timetable rather than a list. */}
      <section className="week-grid" style={{ "--per-row": 7 } as CSSProperties}>
        {WEEKDAY_KEYS.map((key, weekday) => {
          const showing = scheduled.filter((item) => item.airs_weekday === weekday).sort(byAiringTime);
          return (
            <div className={`week-column${weekday === todayWeekday ? " today" : ""}`} key={key}>
              <h3 className="week-head">
                {t(`dashboard.weekday.${key}`)}
                {weekday === todayWeekday && <span className="today-chip">{t("dashboard.today")}</span>}
                <span className="muted">{showing.length || ""}</span>
              </h3>
              <div className="week-stack">
                {showing.map((item) => (
                  <div className="week-slot" key={item.id}>
                    {item.airs_time && <span className="slot-time">{formatClock(item.airs_time)}</span>}
                    <AnimeCard item={item} onSelect={onSelect} showProgress={false} />
                  </div>
                ))}
                {showing.length === 0 && <p className="week-empty muted">—</p>}
              </div>
            </div>
          );
        })}
      </section>
      {unscheduled.length > 0 && (
        <section className="week-unscheduled">
          <h3 className="week-head">{t("dashboard.noSchedule")}<span className="muted">{unscheduled.length}</span></h3>
          <p className="muted" style={{ margin: "0 0 12px" }}>{t("dashboard.noScheduleHint")}</p>
          <div className="anime-grid tight" style={{ "--per-row": perRow } as CSSProperties}>
            {unscheduled.map((item) => (
              <AnimeCard item={item} key={item.id} onSelect={onSelect} showProgress={false} />
            ))}
          </div>
        </section>
      )}
      {footer}
    </>;
  }

  if (anime.length === 0) {
    // The controls stay reachable with an empty watchlist: a first run with a
    // full Shoko Server is exactly when "take everything from Shoko" is the
    // thing you want, and there is nothing of your own to sort yet.
    return <>
      {shokoLinked && controls}
      {emptyState}
    </>;
  }

  /* Three groups (build 117): airing now, not started yet, and everything
     else. Split here rather than in the sort, because the order within each
     part is still whatever was chosen.

     "Airing now" means the season that is running plus the one before it,
     since a two-cour show is at its most-watched in its second half - see
     seasons.ts. "Upcoming" is a show whose first episode is still in the
     future: neither of the other two is true of it, and putting it with the
     finished ones reads as a mistake while putting it with the airing ones
     quietly claims episodes exist. An anime with no date at all is not
     guessed at - it goes with the earlier ones. */
  /* A show of this season is "airing now" whether or not its first episode
     has aired (build 190) - a fall anime added in summer belongs under Fall
     2026 on the day the season starts, not in "upcoming" until its premiere.
     "Upcoming" is what is still to come in a later season. */
  const nowAiring = ordered.filter((item) => airingNow(item));
  const upcoming = ordered.filter((item) => !airingNow(item) && notYetAiring(item));
  const earlier = ordered.filter((item) => !airingNow(item) && !notYetAiring(item));
  const groups = [nowAiring, upcoming, earlier].filter((group) => group.length > 0);
  const split = groups.length > 1;

  const grid = (items: typeof ordered) => (
    <section className="anime-grid tight" style={{ "--per-row": perRow } as CSSProperties}>
      {items.map((item) => (
        <AnimeCard
          item={item}
          key={item.id}
          onSelect={onSelect}
          needsConversion={advanced && item.entry_mode === "basic"}
        />
      ))}
    </section>
  );

  return <>
    {controls}
    {/* Only headed when there is a second section to tell it apart from: a
        heading over the whole list says nothing. */}
    {split && (
      <h3 className="week-head">
        {t("dashboard.airingNow").replace("{season}", seasonLabel(currentSeason()))}
        <span className="muted">{nowAiring.length}</span>
      </h3>
    )}
    {nowAiring.length > 0 && grid(nowAiring)}
    {upcoming.length > 0 && split && (
      <h3 className="week-head">
        {t("dashboard.upcoming")}
        <span className="muted">{upcoming.length}</span>
      </h3>
    )}
    {upcoming.length > 0 && grid(upcoming)}
    {earlier.length > 0 && split && (
      <h3 className="week-head">
        {t("dashboard.earlier")}
        <span className="muted">{earlier.length}</span>
      </h3>
    )}
    {earlier.length > 0 && grid(earlier)}
    {footer}
  </>;
}

const SEASONS = ["WINTER", "SPRING", "SUMMER", "FALL"];

/**
 * The season calendar (build 79: the cache moved to the server).
 *
 * This used to keep its own copy of each season in localStorage and paint from
 * it while a request ran underneath. That was the wrong place for it twice
 * over: a cache in the browser cannot be refreshed on purpose - a reload just
 * showed the same stored answer - and it is per browser, so the phone and the
 * desktop each kept their own idea of the season.
 *
 * The server caches the line-up now, once a day, in the data folder
 * (services/calendar.py). It survives a restart, it is the same for every
 * browser, it is what makes an AniList outage a matter of freshness rather
 * than an error page, and it can be refreshed by pressing a button - which is
 * the line under the calendar saying when it was last filled.
 */
function Calendar({ onPick, library, onOpenSettings, onOpenDebug, debugMode, section, onSeason }: {
  onPick: (candidate: AnimeSearchResult, viewed?: Season) => void;
  /** The watchlist, so "already in your library" is current rather than cached. */
  library: AnimeSummary[];
  /** Where the filters live now: Settings, on its Calendar tab. */
  onOpenSettings?: () => void;
  /** The v2 engine's own debug page - what it is doing, and what it holds. */
  onOpenDebug?: () => void;
  /** Whether to offer the v1 comparison page at all (build 145). */
  debugMode?: boolean;
  /** The season on screen, from the hash: `2025/SPRING` (build 146). */
  section?: string;
  /** Go to a season, or to the current one when given nothing. */
  onSeason?: (year?: number, season?: string) => void;
}) {
  const [calendar, setCalendar] = useState<CalendarSeason | null>(null);
  /** What the calendar is doing, while it is doing it. */
  const [job, setJob] = useState<CalendarStatus | null>(null);
  /** Set when the calendar database itself needs a person to decide. */
  const [maintenance, setMaintenance] = useState<string | null>(null);
  /** Open while an update somebody pressed is running, so it can be watched. */
  const [watching, setWatching] = useState(false);
  const toast = useToast();
  /* Where "Current season" goes. Seeded from the browser so a link straight
     to an old season still has a way back, and corrected by the server the
     first time it answers without being given a season - the two can only
     differ in the last hours of a quarter. */
  const [home, setHome] = useState<{ year: number; season: string } | null>(() => thisSeason());
  const [hideInLibrary, setHideInLibrary] = useState(true);
  const [hideAdult, setHideAdult] = useState(true);
  const [hideNoCover, setHideNoCover] = useState(false);
  /* Which kinds to leave off the grid: MUSIC, CM, PV by default.
     Applied here since build 145. v1 stripped these during the merge *and*
     offered them as a setting, so the three boxes a person could untick were
     the three unticking could not bring back. v2 stores everything, which
     made the setting the only mechanism - and then nothing applied it, so
     music videos arrived on the calendar and the switch did nothing. */
  const [hiddenKinds, setHiddenKinds] = useState<string[]>([]);
  /** Typed genres, comma or plus separated. Empty means "do not filter". */
  const [genreFilter, setGenreFilter] = useState("");
  /* The season arrives most-watched first, so "popularity" is the order the
     server sent and the other two are sorted here.
     Null until the saved preference has been read (build 88): starting on a
     guess meant the page painted "Airing date", then switched to whatever was
     saved a few frames later - two visible steps to reach one answer. */
  const [sort, setSort] = useState<CalendarSort | null>(null);
  const [markSequels, setMarkSequels] = useState(true);
  const [showPopularity, setShowPopularity] = useState(true);
  /* The entry whose detail view is open. Everything it shows was downloaded
     with the season, so opening it asks for nothing. */
  const [detail, setDetail] = useState<AnimeSearchResult | null>(null);
  // Deliberately not persisted, unlike the two checkboxes beside it: those say
  // what you never want to see, this one is a question you are asking now, and
  // a season that opened pre-filtered would look half empty for no visible
  // reason.
  const [nameFilter, setNameFilter] = useState("");
  // The reason, not just the fact: a rate limit and an unreachable host need
  // different things from the user, and used to read identically.
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // The filters are the user's choice, so they live in the settings file.
  useEffect(() => {
    void getAppSettings()
      .then((settings) => {
        setHideInLibrary(settings.calendar_hide_in_library);
        setHideAdult(settings.calendar_hide_adult);
        setHideNoCover(settings.calendar_hide_no_cover);
        setHiddenKinds(
          (settings.calendar_hidden_kinds ?? "")
            .split(",")
            .map((kind) => kind.trim().toUpperCase())
            .filter(Boolean),
        );
        setSort(settings.calendar_sort as CalendarSort);
        setMarkSequels(settings.calendar_mark_sequels);
        setShowPopularity(settings.calendar_show_popularity);
      })
      // A failure still has to leave the page usable, so it falls back to the
      // default rather than staying blank.
      .catch(() => setSort("airing"));
  }, []);

  function persistFilter(update: {
    calendar_hide_in_library?: boolean;
    calendar_hide_adult?: boolean;
    calendar_hide_no_cover?: boolean;
    calendar_sort?: CalendarSort;
  }) {
    void updateAppSettings(update).catch(() => undefined);
  }

  /**
   * Ask the server for a season.
   *
   * Always one round trip and never a provider request: since build 143 the
   * server answers from its own database, so an arrow is as fast when every
   * source is down as when none is. A season nobody has loaded comes back
   * empty with `status: "loading"` and a fetch already running behind it.
   */
  const load = useCallback(async (year?: number, season?: string, quiet = false) => {
    /* `quiet` for a refresh nobody asked for - the poll below picking a
       season up as it lands. The buttons are disabled while `loading`, so
       letting a background refresh set it made them flicker and refuse
       clicks for as long as an update ran. */
    if (!quiet) setLoading(true);
    setError(null);
    try {
      const result = await getCalendar(year, season);
      setCalendar(result);
      if (!year && !season) setHome({ year: result.year, season: result.season });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  /* The season asked for by the URL, or none, meaning "now". */
  const asked = parseSeason(section);
  const askedYear = asked?.year;
  const askedSeason = asked?.season;

  useEffect(() => { void load(askedYear, askedSeason); }, [load, askedYear, askedSeason]);

  /**
   * While something is being fetched, watch it and pick the season up again
   * when it lands.
   *
   * Polled rather than pushed because it is two seconds of one small request
   * and only while work is actually happening. The season is re-read once the
   * run finishes, so a page left open fills itself in.
   */
  /* How many seasons this run had already finished last time we looked. A
     ref, not a local: the effect is rebuilt whenever the season on screen
     changes, and a local reset to -1 made the next tick reload immediately,
     which changed the season object, which rebuilt the effect. */
  const picked = useRef(-1);
  const busy = calendar?.status === "loading" || calendar?.status === "updating";
  const shownYear = calendar?.year;
  const shownSeason = calendar?.season;
  /** A pick, told which season's page it came from (build 174). */
  const pickFromSeason = (candidate: AnimeSearchResult) =>
    onPick(candidate, calendar
      ? { year: calendar.year, season: calendar.season.toUpperCase() as SeasonName }
      : undefined);

  useEffect(() => {
    if (!busy || !shownYear || !shownSeason) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const state = await calendarStatus();
        if (cancelled) return;
        setJob(state);
        // Hiding the window does not stop the update, so something has to go
        // on saying so. A pinned notification with the same bar: it waits for
        // the work rather than fading after four seconds, and then takes the
        // ordinary four seconds once the work is done.
        if (!watching && state.running) {
          const artwork = state.step === "artwork";
          toast.progress(UPDATE_TOAST, {
            message: artwork
              ? t("calendar.stepArtwork").replace("{count}", String(state.pictures))
              : t("calendarUpdate.title").replace("{season}", `${state.season} ${state.year}`),
            done: artwork ? state.pictures : state.sources_done,
            total: artwork ? state.pictures_total : state.sources_total,
          });
        }
        // A season finishing is worth picking up straight away: the first one
        // is usually the one on screen, and waiting for the rest to download
        // their posters would be minutes of nothing for no reason.
        if (state.done !== picked.current) {
          picked.current = state.done;
          if (state.done > 0) void load(shownYear, shownSeason, true);
        }
        if (!state.running) {
          window.clearInterval(timer);
          picked.current = -1;
          toast.finish(
            UPDATE_TOAST,
            state.error
              ? t("calendarUpdate.failed").replace("{error}", state.error)
              : t("calendarUpdate.finished").replace("{summary}", state.summary),
          );
          void load(shownYear, shownSeason, true);
        }
      } catch {
        // A status poll failing is not worth a message: the line-up on screen
        // is unaffected, and the next tick tries again.
      }
    }, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [busy, shownYear, shownSeason, load, watching, toast]);

  /* Leaving the page takes the notification with it. The update carries on -
     it is the server's, not the page's - but nothing would be updating the
     bar any more, and a bar that has stopped moving is worse than none. */
  useEffect(() => () => { toast.finish(UPDATE_TOAST); }, [toast]);

  /**
   * Press Update, for one of the three things it can mean (build 148).
   *
   * `season` is the season on screen. The other two are measured from the
   * season airing now, whatever is on screen - the server decides that, and
   * says so on the endpoint, because "fill the calendar in" is not a question
   * about where somebody has navigated to.
   */
  async function runUpdate(scope: CalendarUpdateScope = "season") {
    if (!calendar) return;
    setError(null);
    try {
      setJob(await updateCalendar(calendar.year, calendar.season, scope));
      // Opened rather than left to a line of text on the page: an update is
      // five sources and then a few hundred posters, and until build 145 the
      // only place any of that was visible was the debug page.
      setWatching(true);
      await load(calendar.year, calendar.season);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  /* Through the URL rather than straight to `load`, so the season survives
     the page being unmounted - which is what opening the debug page does. */
  function show(year: number, season: string) {
    onSeason?.(year, season);
  }

  function shift(direction: -1 | 1) {
    if (!calendar) return;
    const index = SEASONS.indexOf(calendar.season) + direction;
    const season = SEASONS[(index + 4) % 4];
    const year = index < 0 ? calendar.year - 1 : index > 3 ? calendar.year + 1 : calendar.year;
    show(year, season);
  }

  const offHome = !!home && !!calendar && (calendar.year !== home.year || calendar.season !== home.season);
  /* One answer for all three Update buttons: there is one job, so pressing
     any of them while one runs means the same thing. */
  const busyUpdating = !calendar || calendar.status === "updating" || calendar.status === "loading";
  // Every name a show goes by, because which one you remember is a coin toss:
  // the card says "Iron Wok Jan!" and you type "Tetsunabe", or the reverse.
  const needle = nameFilter.trim().toLowerCase();
  const wantedGenres = parseGenres(genreFilter);
  /* Every genre this season actually has, for the suggestions. Sorted, and
     from the unfiltered season so the list does not shrink as you type. */
  const genresInSeason = [...new Set((calendar?.anime ?? []).flatMap((item) => item.genres))].sort();
  const filtered = calendar?.anime.filter((item) =>
    !(hideInLibrary && tracksAlready(library, item))
    && !(hideAdult && readsAsAdult(item))
    && !(hideNoCover && !item.cover_url)
    && !isHiddenKind(item, hiddenKinds)
    && matchesGenres(item, wantedGenres)
    && (!needle || [item.display_name, item.title_english, item.title_romaji, item.title_native]
      .some((name) => name?.toLowerCase().includes(needle))),
  ) ?? [];
  /* The server hands the season over most-watched first, so "popularity" is
     the order it arrives in and needs no sorting here. The other two are a
     comparison each. */
  /* "Recommended" (build 208): the genres on the watchlist against the genres of each show, plus
     popularity. Greyed out while the watchlist has no genres to learn from. */
  const taste = tasteProfile(library);
  const hasTaste = taste.size > 0;
  const shown = sort === "name"
    ? [...filtered].sort((a, b) => a.display_name.localeCompare(b.display_name))
    : sort === "airing"
      ? [...filtered].sort((a, b) => (a.air_start_date ?? "9999").localeCompare(b.air_start_date ?? "9999"))
      : sort === "recommended"
        ? sortByRecommendation(filtered, calendar?.anime ?? [], taste)
        : filtered;
  /* The marked entries lifted within whatever order was chosen, and the
     stars worked out across the whole season rather than the filtered view -
     hiding the adult entries should not change how many stars the rest get. */
  const ordered = markSequels ? withRelatedFirst(shown) : shown;
  const stars = starsIn(calendar?.anime ?? []);
  const hiddenAdult = calendar?.anime.filter(readsAsAdult).length ?? 0;
  const hiddenNoCover = calendar?.anime.filter((item) => !item.cover_url).length ?? 0;
  const hiddenByKind = calendar?.anime.filter((item) => isHiddenKind(item, hiddenKinds)).length ?? 0;
  const hiddenByGenre = calendar?.anime.filter((item) => !matchesGenres(item, wantedGenres)).length ?? 0;

  return <section className="calendar">
    {/* Inside a filter-row, which is what sizes the watchlist's own buttons -
        so the two pages' controls are the same size rather than nearly. */}
    <div className="calendar-controls filter-row">
      {/* The two step buttons are the actions; "current season" is only worth
          emphasis once you have wandered off it. */}
      <button className="button primary" onClick={() => shift(-1)} disabled={!calendar || loading}>{t("calendar.previous")}</button>
      <div className="calendar-now">
        <button
          className={"button " + (offHome ? "primary" : "secondary")}
          disabled={!calendar || loading || !offHome}
          onClick={() => onSeason?.()}
        >
          {offHome ? t("calendar.backToCurrent") : t("calendar.current")}
        </button>
        <strong className="calendar-season">{calendar ? `${calendar.season} ${calendar.year}` : "…"}</strong>
      </div>
      <button className="button primary" onClick={() => shift(1)} disabled={!calendar || loading}>{t("calendar.next")}</button>
    </div>

    {/* One row: sorting, then the filter, then what is hidden and the
        actions. The filter had a row of its own and did not need one - it
        answers the same kind of question as the sort buttons, which is what
        this page shows and in what order. */}
    <div className="calendar-tools">
      <div className="filter-row" role="group" aria-label={t("calendar.sortLabel")}>
        {(["airing", "name", "popularity", "recommended"] as CalendarSort[]).map((option) => (
          <button
            key={option}
            /* Nothing is highlighted until the saved preference has arrived:
               highlighting a guess first is the flicker. */
            className={`button ${sort === option ? "primary" : "secondary"}`}
            disabled={sort === null || (option === "recommended" && !hasTaste)}
            title={option === "recommended" ? (hasTaste ? t("calendar.recommendedHint") : t("calendar.recommendedNeedsTaste")) : undefined}
            onClick={() => { setSort(option); persistFilter({ calendar_sort: option }); }}
          >
            {t(`calendar.sort.${option}`)}
          </button>
        ))}
      </div>
      {/* No button and no Enter: the list narrows as you type, which is the
          only behaviour that makes sense for a filter over a page you can
          already see. */}
      {/* Two filters, side by side and centred. Name narrows as you type;
          genre takes more than one, because "Action and Drama" is the
          question people actually have. */}
      <div className="calendar-filters">
        <input
          className="calendar-filter"
          type="search"
          value={nameFilter}
          placeholder={t("calendar.filterPlaceholder")}
          aria-label={t("calendar.filterPlaceholder")}
          onChange={(event) => setNameFilter(event.target.value)}
        />
        <input
          className="calendar-filter"
          type="search"
          list="calendar-genres"
          value={genreFilter}
          placeholder={t("calendar.genrePlaceholder")}
          aria-label={t("calendar.genrePlaceholder")}
          title={t("calendar.genreHint")}
          onChange={(event) => setGenreFilter(event.target.value)}
        />
        {/* Offered from the season on screen rather than a fixed list: the
            genres that exist are the ones worth suggesting. */}
        <datalist id="calendar-genres">
          {genresInSeason.map((name) => <option key={name} value={name} />)}
        </datalist>
      </div>
      <div className="filter-row">
        {/* The three checkboxes moved into Settings (build 101). They were
            already at the width they could afford, and the options asked for
            alongside them - which kinds to show, the marks, the stars - would
            have been a wall above the season. What stays here is the count of
            what is being hidden, because that is about *this* season rather
            than about the preference. */}
        {/* The count first, the button after it (build 120). The text
            says why the season looks short; the button is what you press
            about it, so it reads in that order. */}
        {/* Build 123: *"the 20 hidden by your filters and the settings
            button look a bit offset ... maybe we can make the text a bit
            bigger, or center it more to the button."*  The button stays put -
            it lines up with the sort buttons above it - so the row centres
            what is in it and the note is given a size of its own. */}
        {(hideAdult && hiddenAdult > 0) || (hideNoCover && hiddenNoCover > 0) || hiddenByKind > 0 || hiddenByGenre > 0 ? (
          <small className="muted filter-note">
            {t("calendar.hiddenCount")
              .replace("{n}", String(
                (hideAdult ? hiddenAdult : 0) + (hideNoCover ? hiddenNoCover : 0) + hiddenByKind + hiddenByGenre,
              ))}
          </small>
        ) : null}
        {/* Three things Update can mean, and the third disappears once it
            has been used (build 148).

            The season on screen is the ordinary case and keeps the emphasis.
            "Upcoming" is the one people were pressing the arrows to fake:
            this season and the two whose announcements are arriving.
            "Fill the calendar" is six seasons and thirty provider requests -
            worth it exactly once, on an installation that has nothing, which
            is why the server refuses a second one rather than trusting this
            button to stop offering it. */}
        {/* One update button (build 179): the upcoming seasons, labelled
            plain "Update calendar". The season-only update lives in the
            "not loaded yet" notice below, and the one-off first fill is
            still available to the code - only its buttons are gone. */}
        <button
          className="button primary"
          onClick={() => void runUpdate("upcoming")}
          disabled={busyUpdating}
          title={t("calendar.updateUpcomingHint")}
        >
          {calendar?.status === "updating" ? t("calendar.updating") : t("calendar.updateUpcoming")}
        </button>
        <button className="button secondary" onClick={() => onOpenSettings?.()}>
          {t("calendar.settings")}
        </button>
        {/* Behind the switch, like the comparison page beside it: it is a
            debug page and says so on itself. */}
        {debugMode && (
          <button className="button secondary" onClick={() => onOpenDebug?.()}>
            {t("calendar.debugPage")}
          </button>
        )}
      </div>
    </div>

    {/* The stale-provider warning has gone (build 87). This page draws
        downloaded data, so a provider being unreachable is neither news nor
        actionable here - and when it does matter, the Refresh now page is
        where it is reported, next to the button that does something about
        it. */}
    {error && !calendar?.stale && (
      /* Shown even with a season on screen: the alternative was a button that
         silently did nothing whenever the provider was unreachable. */
      <p className="error-message">
        {t("calendar.error")}
        <small className="muted" style={{ display: "block", marginTop: 4 }}>{error}</small>
      </p>
    )}
    {/* The v2 states (build 143). "Loading" and "updating" are different
        things and say so: one means there is nothing yet, the other means
        what you are looking at is about to get better. Neither is an error,
        and neither empties the page. */}
    {calendar?.status === "loading" && (
      <p className="notice-message">{t("calendar.fetchingSeason")}</p>
    )}
    {calendar?.status === "updating" && (
      <p className="notice-message">
        {/* Why it is happening, before how far along it is. An update nobody
            started looks alarming when it is labelled the same as one you
            pressed a button for - reported from the NAS, where the worker
            began filling a fresh calendar and the page said only
            "Updating...". */}
        {job?.kind === "automatic"
          ? t("calendar.updatingAutomatic")
          : job?.kind === "season"
          ? t("calendar.fetchingSeason")
          : job && job.total > 1
          ? t("calendar.updatingCount")
              .replace("{done}", String(Math.min(job.done + 1, job.total)))
              .replace("{total}", String(job.total))
          : t("calendar.updating")}
        {/* The step, because a season is five sources and then up to two
            hundred pictures - and "season 1 of 3" can stand unchanged for
            minutes while every bit of that happens. */}
        {job?.step === "artwork"
          ? <small className="muted" style={{ display: "block", marginTop: 4 }}>
              {t("calendar.stepArtwork").replace("{count}", String(job.pictures))}
            </small>
          : job?.step
          ? <small className="muted" style={{ display: "block", marginTop: 4 }}>
              {stepText(job.step, debugMode)}
            </small>
          : null}
      </p>
    )}
    {calendar?.status === "empty" && (
      <p className="notice-message">
        {t("calendar.neverLoaded")}
        <button className="button secondary" onClick={() => void runUpdate()}>
          {t("calendar.update")}
        </button>
      </p>
    )}
    {/* No "not updated for a while" banner (build 177). It was driven by a
        24-hour staleness limit while the calendar refreshes weekly on its
        own, so it appeared a day after every load and offered a button
        nobody needed. An empty calendar still says so, above. */}
    {/* The database itself could not be opened. Said here, with the buttons
        that fix it, because a recovery step nobody is told about is a bug. */}
    {calendar?.status === "error" && calendar.unavailable_reason && (
      <p className="error-message">
        {calendar.unavailable_reason}
        <span className="action-stack horizontal" style={{ marginTop: 8 }}>
          <button className="button secondary" onClick={async () => {
            setMaintenance(t("calendar.rebuilding"));
            try {
              const done = await rebuildCalendar();
              setMaintenance(t("calendar.rebuilt").replace("{kept}", String(done.kept_ids)));
              await load(calendar.year, calendar.season);
            } catch (err) {
              setMaintenance(err instanceof Error ? err.message : String(err));
            }
          }}>{t("calendar.rebuild")}</button>
          <button className="button danger" onClick={async () => {
            const preview = await calendarResetPreview().catch(() => null);
            const detail = preview?.summary ? `\n\n${preview.summary}` : "";
            if (!window.confirm(t("calendar.resetConfirm") + detail)) return;
            setMaintenance(null);
            try {
              await resetCalendar();
              await load(calendar.year, calendar.season);
            } catch (err) {
              setMaintenance(err instanceof Error ? err.message : String(err));
            }
          }}>{t("calendar.reset")}</button>
        </span>
        {maintenance && <small className="muted" style={{ display: "block", marginTop: 6 }}>{maintenance}</small>}
      </p>
    )}
    {watching && calendar && (
      <CalendarUpdateDialog
        season={calendar.season}
        year={calendar.year}
        debugMode={debugMode}
        onClose={() => setWatching(false)}
        onFinished={() => { void load(calendar.year, calendar.season, true); }}
      />
    )}
    {loading && !calendar && <p className="loading">{t("calendar.loading")}</p>}
    <div className="calendar-grid">{ordered.map((item) => (
      /* The card shows the same answer the filter used, so a card cannot say
         "Add anime" for something the filter counts as yours. */
      <CalendarCard
        candidate={tracksAlready(library, item) ? { ...item, in_library: true } : item}
        /* The provider as well as the id (build 88). A bare id is not unique:
           the line-up is drawn from five databases with five numbering
           schemes, and Shikimori's ids *are* MyAnimeList's - so four cards in
           SUMMER 2026 collided. React answers a duplicate key by mishandling
           the reorder, which is why toggling between Name and Popularity made
           entries appear twice. */
        key={`${item.provider}:${item.provider_id}`}
        onPick={pickFromSeason}
        onOpen={setDetail}
        stars={showPopularity ? stars(item) : 0}
        marks={markSequels}
        year={calendar?.year ?? null}
      />
    ))}</div>
    {calendar && shown.length === 0 && (
      /* Three different empty screens. "Everything is in your library" for a
         season you have already added is encouraging; the same line under a
         filter that matched nothing would just be wrong. */
      <p>{needle
        ? t("calendar.noMatches").replace("{query}", nameFilter.trim())
        : calendar.anime.length > 0 ? t("calendar.allInLibrary") : t("calendar.empty")}</p>
    )}
    {/* The cache line and its refresh button have gone (build 87). Both said
        something about the *provider's* 24-hour cache, which stopped being
        what this page draws; when it was downloaded, and the button that
        downloads it, both live on the Refresh now page. */}
    {detail && (
      <CalendarDetailDialog
        item={detail}
        related={markSequels ? detail.related : null}
        stars={showPopularity ? stars(detail) : 0}
        onAdd={(item) => { setDetail(null); pickFromSeason(item); }}
        onClose={() => setDetail(null)}
      />
    )}
  </section>;
}


function CalendarCard({ candidate, onPick, onOpen, stars, marks, year }: {
  candidate: AnimeSearchResult;
  onPick: (candidate: AnimeSearchResult) => void;
  onOpen: (candidate: AnimeSearchResult) => void;
  /** One to five, relative to the season on screen. Zero for unknown. */
  stars: number;
  /** Whether to outline seasons of shows already tracked. */
  marks: boolean;
  /** The season on screen's year: what a card with no date still knows. */
  year: number | null;
}) {
  return (
    /* The whole card opens the detail view - the card itself carries the
       click, so there is no dead strip between the picture and the edge. The
       Add button stops the event: opening a page is free and adding an anime
       is not, and one should not be a miss-click away from the other. */
    <article
      className={[
        "calendar-card",
        candidate.in_library ? "in-library" : "",
        /* Outlined when it is a season of something already tracked, and
           more strongly when it is one that appears to have been skipped. */
        marks && candidate.related ? `related-${candidate.related}` : "",
      ].filter(Boolean).join(" ")}
      role="button"
      tabIndex={0}
      title={t("calendar.openDetail")}
      onClick={() => onOpen(candidate)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen(candidate);
        }
      }}
    >
      {/* The picture is the card. Name and genres sit on it, one line each:
          the card is wide enough now that a second line would be mostly
          empty, and two cards of different heights in a grid read worse than
          an ellipsis. */}
      <span className="calendar-poster">
        <Cover anime={candidate} />
        <span className="calendar-card-text">
          {/* When it starts, on every card (build 210), the same yellow pill as an upcoming card on
              the watchlist. A date that is not whole says what it knows: ?.12.2026 or ?.?.2026. */}
          <span className={hasStarted(candidate.air_start_date) ? "anime-airs-pill started" : "anime-airs-pill"}>
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <rect x="3" y="5" width="18" height="16" rx="2" />
              <path d="M3 10h18M8 3v4M16 3v4" />
            </svg>
            {dateBubbleText(candidate.air_start_date, year)}
          </span>
          <strong title={candidate.display_name}>{candidate.display_name}</strong>
          {candidate.genres.length > 0 && (
            <small className="genre-line">{candidate.genres.slice(0, 3).join(" · ")}{candidate.genres.length > 3 ? " …" : ""}</small>
          )}
        </span>
      </span>
      {/* Below the picture rather than over it: these are things you read and
          press, and putting a button on artwork makes both worse. */}
      <div className="calendar-card-foot">
        <span className="calendar-card-meta">
          {/* Through `kindLabel` since build 148: the sources spell a kind
              five ways between them, and one grid should not show three of
              them. */}
          <small>{[kindLabel(candidate.format), candidate.episode_count ? `${candidate.episode_count} eps` : null].filter(Boolean).join(" · ")}</small>
          {/* Whether this is a season of something already tracked, and
              whether AniDB knows it, are said in the detail view: both are
              facts about the show, and a card is a picture, a name and a
              button. The outline on the card is what it keeps. */}
          {candidate.is_adult && <small className="adult-tag">18+</small>}
          {stars > 0 && <small className="calendar-stars" title={t("calendar.starsHint")}>{starText(stars)}</small>}
        </span>
        {candidate.in_library
          ? <small className="in-library-tag">✓ {t("calendar.inLibrary")}</small>
          : candidate.addable === false
          /* The calendar knows five sources; adding needs one AniFlow can ask
             for details. A show only MyAnimeList or Shikimori describes is
             real and worth showing, and saying so beats a button that fails
             on the click. */
          ? <small className="muted" title={t("calendar.notAddableHint")}>{t("calendar.notAddable")}</small>
          : (
            <button
              className="button secondary"
              onClick={(event) => { event.stopPropagation(); onPick(candidate); }}
              /* "Add" in both languages, and the whole sentence on the hover.
                 The strip is narrow, the card has already said what the anime
                 is, and a bare "+" would read as a control rather than a
                 label - to a screen reader most of all. */
              title={t("calendar.addHint")}
            >
              {t("calendar.add")}
            </button>
          )}
      </div>
    </article>
  );
}


/** Episode statuses from which AniFlow can offer a (re-)download. */
const DOWNLOADABLE: ReadonlySet<string> = new Set(["AVAILABLE", "MISSING", "UNAVAILABLE"]);
/** Statuses where the torrent client currently owns the episode. */
// States where the user is waiting on something and has nothing to press.
// IMPORTING belongs here: the file has been handed to Shoko Server and is on
// its way into the library, so offering a download again would be wrong.
const IN_FLIGHT: ReadonlySet<string> = new Set(["DOWNLOADING", "DOWNLOADED", "IMPORTING"]);
// The narrower set: states where the *torrent* is what the user is waiting on,
// so its own progress and state are worth showing. Once the file is handed
// over, the torrent being "Completed" says nothing about the episode.
const TORRENT_OWNS: ReadonlySet<string> = new Set(["DOWNLOADING", "DOWNLOADED"]);

/** Which visual treatment an episode row gets. */
function episodeRowClass(status: string): string {
  if (status === "COMPLETE") return "imported";
  if (IN_FLIGHT.has(status)) return "available";
  if (status === "AVAILABLE" || status === "UPDATE") return "available";
  if (status === "ERROR") return "missing";
  return "missing";
}

/**
 * How the state line is coloured (build 125).
 *
 * Three colours for twelve states, and the grouping is the point: the good
 * outcome is green, a fault is red, and **everything that is merely waiting
 * is neutral**. An episode that has not aired, has no release yet, or is
 * being left alone on purpose is not a problem and must not look like one -
 * which is the mistake build 118 had to undo when every Shoko half of every
 * card went red for a situation nobody could act on.
 */
function stateClass(state: string, detail?: string | null): string {
  if (state === "complete") return "good";
  if (state === "failed" || state === "file_gone") return "bad";
  // Two gaps at once (build 266): not in the AniDB list and nothing to fetch.
  if (detail === "not_in_anidb_list") return "bad";
  return "";
}

/** How often an open detail page re-reads an in-flight download. */
const DETAIL_POLL_MS = 5000;

/**
 * The torrent client's own vocabulary, in words.
 *
 * "queuedDL" is genuinely what qBittorrent calls it - not a placeholder - but it
 * does not belong in front of a user. Anything unrecognised falls back to the
 * raw value rather than being hidden.
 */
function clientState(state: string): string {
  const key = `qb.state.${state}`;
  const label = t(key);
  return label === key ? state : label;
}

function AnimePage({ anime, mode, advanced, debugMode, onBack, onUpdated, onDeleted }: { anime: AnimeDetail; mode: StorageMode; advanced: boolean; debugMode: boolean; onBack: () => void; onUpdated: (anime: AnimeSummary) => void; onDeleted: (id: string) => void }) {
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [automationNote, setAutomationNote] = useState<string | null>(null);
  const [configOpen, setConfigOpen] = useState(false);
  /* The four checks, for the flag on the Edit button (build 152). Read here
     and handed to the dialog's own re-reads through onChecked, so fixing
     something in the dialog takes the flag off the button behind it. */
  const [checks, setChecks] = useState<HealthGroups>(null);
  useEffect(() => {
    let current = true;
    getAnimeHealth(anime.id).then((answer) => { if (current) setChecks(answer); }).catch(() => undefined);
    return () => { current = false; };
  }, [anime.id]);
  // A nice-to-have is not a reason to flag the Edit button (build 157).
  const openCheckList = problems(openChecks(checks));
  const [convertOpen, setConvertOpen] = useState(false);
  const [findFilesOpen, setFindFilesOpen] = useState(false);
  /* Which release change is being made, and with what defaults. Null when the
     dialog is closed. */
  const [reacquiring, setReacquiring] = useState<{
    mode: string;
    keepSubgroup: boolean;
    resolution: string | null;
  } | null>(null);
  /**
   * Which additive job the setup dialog is open for, or null (builds 117,
   * 123). "fill" looks for the episodes that are missing and fetches them;
   * "match" works out what the ones already here were released as and
   * fetches nothing. One state rather than two booleans, because they are
   * the same dialog and it can only be open for one of them.
   */
  const [fixing, setFixing] = useState<"fill" | "match" | null>(null);
  const { show } = useToast();
  const reload = useCallback(async () => {
    const { getAnime: refresh } = await import("./api/client");
    onUpdated(await refresh(anime.id));
  }, [anime.id, onUpdated]);

  /**
   * Follow an in-flight download.
   *
   * Queueing used to refresh once and then stop, so the row sat at "0% ·
   * queuedDL" until the page was reloaded by hand. Progress only advances when
   * the worker syncs with the client, so a slow poll is enough - and it runs
   * only while something is actually in flight and the tab is visible.
   */
  const watching = anime.episodes.some((episode) => IN_FLIGHT.has(episode.status));
  useEffect(() => {
    if (!watching) return;
    const id = window.setInterval(() => { if (!document.hidden) void reload(); }, DETAIL_POLL_MS);
    return () => window.clearInterval(id);
  }, [watching, reload]);

  const [lifecycleOpen, setLifecycleOpen] = useState(false);
  const [filesOpen, setFilesOpen] = useState(false);
  async function change(payload: { lifecycle?: string; drop_mode?: string }) { setLoading(true); try { onUpdated(await updateAnime(anime.id, payload)); } finally { setLoading(false); } }
  async function remove() {
    if (!window.confirm(t("detail.deleteConfirm"))) return;
    // What is still in the torrent client, asked about separately - deleting
    // the anime used to leave its torrents behind for ever, with nothing left
    // that knew they were its. Only this anime's are ever touched, and no
    // files are deleted either way.
    let removeTorrents = false;
    try {
      const { animeTorrents } = await import("./api/client");
      const live = await animeTorrents(anime.id);
      if (live.count > 0) {
        removeTorrents = window.confirm(
          t("detail.deleteTorrents").replace("{n}", String(live.count)),
        );
      }
    } catch {
      // An unreachable client is not a reason to refuse the deletion.
    }
    setLoading(true);
    try {
      await deleteAnime(anime.id, { removeTorrents });
      onDeleted(anime.id);
      show(t("toast.animeRemoved").replace("{name}", anime.display_name));
    } finally {
      setLoading(false);
    }
  }
  /** The one reason this episode was not queued, as a translation key. */
  function whyNothingQueued(reasons?: Partial<Record<string, number>>): string {
    const named = Object.keys(reasons ?? {});
    if (named.length === 1) return `detail.notQueued.${named[0]}`;
    return "detail.nothingQueued";
  }

  /** The same for a whole season: named when every episode agrees. */
  function whyNothingQueuedAll(reasons?: Partial<Record<string, number>>): string {
    const named = Object.keys(reasons ?? {});
    if (named.length === 1) return `detail.notQueuedAll.${named[0]}`;
    return "detail.nothingQueuedAll";
  }

  async function downloadOne(episode: number) {
    setDownloading(String(episode));
    try {
      const { queueAvailable, getAnime: refresh } = await import("./api/client");
      const res = await queueAvailable(anime.id, { episode });
      // Was a browser alert with an OK button - for the good case as well as
      // the bad one, which made sending one episode to the torrent client a
      // two-click job.
      // The list is re-read first, whatever the answer was (build 118).
      // Reported: the first press said "that did not work: Internal error"
      // and left the row exactly as it was, while the torrent had in fact
      // gone to the client - so the screen disagreed with the download until
      // it was pressed a second time. What the server did is not conditional
      // on what it said about it.
      onUpdated(await refresh(anime.id));
      if (res.errors?.length) show(res.errors.join(" "), "error");
      // One episode, so there is exactly one reason and it can be named
      // (build 117). The blanket "already owned or downloading" was wrong for
      // the case it was reported on - the episode was neither.
      else if (res.queued === 0) show(t(whyNothingQueued(res.reasons)), "info");
      else show(t("toast.downloadQueued"));
    } catch (e) {
      show(t("toast.failed").replace("{reason}", e instanceof Error ? e.message : String(e)), "error");
      // Even here: the call may have reached the client before it failed.
      try { await reload(); } catch { /* the toast stands */ }
    } finally { setDownloading(null); }
  }
  async function downloadAll() {
    setDownloading("all");
    try {
      const { queueAvailable, getAnime: refresh } = await import("./api/client");
      const res = await queueAvailable(anime.id, { all_available: true });
      onUpdated(await refresh(anime.id));
      if (res.errors?.length) show(res.errors.join(" "), "error");
      else if (res.queued === 0) show(t(whyNothingQueuedAll(res.reasons)), "info");
      else show(t("toast.downloadsQueued").replace("{n}", String(res.queued)));
    } catch (e) {
      show(t("toast.failed").replace("{reason}", e instanceof Error ? e.message : String(e)), "error");
      try { await reload(); } catch { /* the toast stands */ }
    } finally { setDownloading(null); }
  }
  /**
   * The automation switch.
   *
   * On: the worker queues whatever this anime has available, every cycle, and
   * scheduled feed scanning is switched on so new episodes keep being found.
   * Off: nothing happens without a button press. It stays on screen when the
   * download buttons disappear - that is exactly when you want to turn it off.
   */
  async function toggleAutomation(next: boolean) {
    setDownloading("automate");
    setAutomationNote(null);
    try {
      const { updateAnime, getAnime: refresh, getAppSettings, updateAppSettings, queueAvailable } = await import("./api/client");
      await updateAnime(anime.id, { automation_enabled: next });
      let note = t(next ? "detail.automationOn" : "detail.automationOff");
      if (next) {
        const settings = await getAppSettings();
        if (!settings.rss_enabled) await updateAppSettings({ rss_enabled: true });
        note = note.replace("{m}", String(settings.rss_scan_interval_minutes));
        /* Switching it on sends what is already available now (build 239), the same as the
           Download all button - it used to wait for the worker's next pass, or for a press. */
        try {
          const sent = await queueAvailable(anime.id, { all_available: true });
          if (sent.queued > 0) note = `${t("toast.downloadsQueued").replace("{n}", String(sent.queued))} ${note}`;
        } catch { /* the worker's pass is the second chance */ }
      }
      onUpdated(await refresh(anime.id));
      setAutomationNote(note);
    } catch (e) { show(t("toast.failed").replace("{reason}", e instanceof Error ? e.message : String(e)), "error"); } finally { setDownloading(null); }
  }

  // Downloadable = a release is known and the client is not already busy with it.
  /* Configured on paper, matched to nothing (build 176): a release name and
     a profile, but no episode has a release behind it and none has a file.
     Reported with an entry added before its season, where the guessed name
     found nothing and the page then offered no way back into release
     matching. */
  const nothingMatched = anime.episodes.every((ep) => ep.releases.length === 0 && DOWNLOADABLE.has(ep.status));
  const availableCount = anime.episodes.filter((ep) => ep.releases.length > 0 && DOWNLOADABLE.has(ep.status)).length;
  return <section className="detail"><button className="back-link" onClick={onBack}>← {t("detail.back")}</button><div className="detail-hero"><Cover anime={anime} /><div><p className="eyebrow">{t(`status.${anime.lifecycle}`)}</p><h1>{anime.display_name}</h1><p>{[anime.format, anime.episode_count ? `${anime.episode_count} episodes` : null].filter(Boolean).join(" · ")}</p><p className="muted">{[anime.title_english, anime.title_romaji, anime.title_native].filter(Boolean).join(" · ")}</p></div></div><aside className="detail-panel detail-config"><div className="status-line"><h2 style={{ margin: 0 }}>{t("config.title")}</h2><button className="button secondary config-edit" onClick={() => setConfigOpen(true)} title={openCheckList.length ? (openCheckList.length === 1 ? t("config.checks.countOne") : t("config.checks.countMany").replace("{n}", String(openCheckList.length))) : undefined}>{t("config.edit")}{levelOf(openCheckList) && <span className={`field-flag ${levelOf(openCheckList)}`} aria-hidden="true">!</span>}</button>{/* The AniDB page for this show (build 188), for checking what AniDB says - an episode count, say - against what AniFlow holds. A link the user's own browser follows: AniFlow itself never fetches anidb.net pages. */}{anidbUrl(anime.anidb_id) && <a className="button secondary config-edit" href={anidbUrl(anime.anidb_id) ?? undefined} target="_blank" rel="noopener noreferrer">{t("config.openAnidb")}</a>}{malUrl(anime.provider_ids?.myanimelist) && <a className="button secondary config-edit" href={malUrl(anime.provider_ids?.myanimelist) ?? undefined} target="_blank" rel="noopener noreferrer">{t("config.openMal")}</a>}</div><dl className="config-columns"><div className="config-item"><dt>{t("config.releaseName")}</dt><dd>{anime.release_name ?? "—"}</dd></div><div className="config-item"><dt>{t("config.mediaFolder")}</dt><dd>{anime.media_folder ?? (mode === "shokoserver" ? <em className="muted">{t("config.folderPending")}</em> : "—")}</dd></div><AnimeLibraryRow animeId={anime.id} animeName={anime.display_name} libraryId={anime.library_id} pinned={Boolean(anime.library_pinned)} onMoved={() => void reload()} /><div className="config-item"><dt>{t("config.profile")}</dt><dd>{profileSummary(anime.profile, t("profile.custom"))}</dd></div>{anime.anidb_id ? <div className="config-item"><dt>{t("config.anidbId")}</dt><dd>{anime.anidb_id}</dd></div> : null}{anime.provider_ids?.myanimelist ? <div className="config-item"><dt>{t("config.malId")}</dt><dd>{anime.provider_ids.myanimelist}</dd></div> : null}<AnimeAdultRow animeId={anime.id} isAdult={anime.adult_flag} viaLibrary={anime.is_adult} onChanged={() => void reload()} />{/* No feed row (build 94). It said "every enabled feed is scanned",
            which is true of every anime and therefore not information about
            this one. A custom feed URL is the exception and shows up here. */}
        {anime.custom_feed_url && (<div className="config-item"><dt>{t("config.feed")}</dt><dd>{t("config.customFeed")}</dd></div>)}</dl>{/* The way back into release matching for a future entry (build 155):
        *"I added all of the future season animes, so i can remember them.
        then the season starts, the release groups pop up. I need to go back
        and click this new button."*  The same conversion a basic entry has,
        offered from the day it is added - before the premiere it simply says
        there is nothing out yet, rather than hiding the way in. */}{(anime.entry_mode === "future" || (anime.entry_mode !== "basic" && (!anime.release_name || nothingMatched))) && advanced && (
          <div className="future-setup">
            {/* Also for an entry that ended up here without any release
                information (build 174): it had the same missing pieces and
                no way to fill them in. */}
            <p className={notYetAiring(anime) ? "muted" : "warning-message"}>
              {anime.entry_mode !== "future"
                ? t("detail.noReleaseSetup")
                : notYetAiring(anime) ? t("detail.futureSetupEarly") : t("detail.futureSetupDue")}
            </p>
            <button className="button primary" onClick={() => setConvertOpen(true)}>{t("detail.futureSetupButton")}</button>
          </div>
        )}</aside><div className="detail-layout">{anime.entry_mode === "basic" ? (
      /* The episodes are listed here too, since build 138. They were left
         out while a basic entry had no rows at all; it has them now, because
         how many episodes a show has is a fact about the show and the files
         may well be on disk already.

         Its own panel rather than the one below: that branch carries the
         Automate toggle, the download-all button and the release rows, and
         none of the three applies without an indexer. */
      <section className="detail-panel basic-notice">
        <div className="status-line" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>{t("detail.episodes")}</h2>
          {/* Where Automate sits in the other mode, and only here: with no
              indexer there is nothing to automate, and the files are the one
              thing this entry can still be given. */}
          <button className="button secondary" onClick={() => setFindFilesOpen(true)}>
            {t("findFiles.button")}
          </button>
        </div>
        <p className="muted">{t("detail.basicNotice")}</p>
        {anime.episodes.length ? (
          <div className="episode-list" style={{ marginTop: 12 }}>
            {anime.episodes.map((episode) => (
              <EpisodeRow
                key={episode.id}
                episode={episode}
                busy={null}
                /* Nothing to download with, so the row offers nothing: a
                   button that cannot work is worse than no button - and
                   neither does it mention releases (build 140). */
                plain
                onDownload={() => undefined}
                offset={anime.episode_offset ?? 0}
              />
            ))}
          </div>
        ) : (
          <p className="muted">{t("detail.noEpisodes")}</p>
        )}
        {advanced && (
          <>
            <p className="warning-message">{t("config.convertHint")}</p>
            <div className="profile-actions">
              <button className="button primary" onClick={() => setConvertOpen(true)}>
                {t("config.convertButton")}
              </button>
            </div>
          </>
        )}
      </section>
    ) : (<section className="detail-panel"><NoClientNotice /><div className="status-line" style={{ justifyContent: "space-between" }}><h2 style={{ margin: 0 }}>{t("detail.episodes")}</h2><div style={{ display: "flex", gap: 8 }}>{availableCount > 0 && <button className="button primary" style={{ padding: "8px 16px", fontSize: ".82rem", whiteSpace: "nowrap" }} disabled={!!downloading} onClick={() => void downloadAll()}>{downloading === "all" ? t("profile.saving") : t("dl.downloadAll").replace("{n}", String(availableCount))}</button>}<button className="button secondary episode-view-button" disabled={!anime.episodes.length} onClick={() => setFilesOpen(true)} title={t("detail.viewFilesHint")}>{t("detail.viewFiles")}</button><label className={"toggle-check" + (anime.automation_enabled ? " on" : "")} title={t("detail.automateHint")}><input type="checkbox" checked={anime.automation_enabled} disabled={!!downloading} onChange={(event) => void toggleAutomation(event.target.checked)} /><span>{t("detail.automate")}</span></label></div></div>{filesOpen && <EpisodeFilesDialog animeId={anime.id} episodes={anime.episodes} onClose={() => setFilesOpen(false)} onChanged={() => { void getAnime(anime.id).then(onUpdated); }} />}{automationNote && <p className="imported-tag" style={{ margin: "8px 0 0", fontSize: ".82rem" }}>{automationNote}</p>}{anime.episodes.length ? <div className="episode-list" style={{ marginTop: 12 }}>{anime.episodes.map((episode) => (
    <EpisodeRow
      key={episode.id}
      episode={episode}
      busy={downloading}
      onDownload={downloadOne}
      offset={anime.episode_offset ?? 0}
    />
  ))}</div> : <p>{t("detail.noEpisodes")}</p>}</section>)}<div className="detail-side"><aside className="detail-panel"><h2>{t("detail.lifecycle")}</h2>{/* One line and one button instead of five (build 188): the five choices are
        in a dialog, where the current one is shown and cannot be pressed. */}<div className="lifecycle-current"><span className="muted">{t("detail.lifecycleCurrent")}</span><strong>{currentStatusText(anime.lifecycle, anime.drop_mode)}</strong></div><div className="action-stack"><button className="button secondary" disabled={loading} onClick={() => setLifecycleOpen(true)}>{t("detail.lifecycleEdit")}</button><button className="button danger" disabled={loading} onClick={() => void remove()}>{t("detail.delete")}</button></div>{lifecycleOpen && <LifecycleDialog lifecycle={anime.lifecycle} dropMode={anime.drop_mode} busy={loading} onClose={() => setLifecycleOpen(false)} onChoose={(payload) => { void change(payload).then(() => setLifecycleOpen(false)); }} />}</aside>{/* Changing which release this is kept in (build 87). Under Lifecycle
        because it is the same kind of decision - what AniFlow should be
        doing with this anime from now on. */}{advanced && <ReleaseChangePanel anime={anime} onStart={setReacquiring} onFixMissing={() => setFixing("fill")} onMatchOnly={() => setFixing("match")} onUpdated={onUpdated} />}</div></div>{configOpen && <AnimeConfigDialog anime={anime} mode={mode} debugMode={debugMode} onClose={() => setConfigOpen(false)} onUpdated={onUpdated} onChecked={setChecks} onFindFiles={() => { setConfigOpen(false); setFindFilesOpen(true); }} />}{findFilesOpen && <FindFilesDialog animeId={anime.id} animeName={anime.display_name} onClose={() => setFindFilesOpen(false)} /* The whole point is the episode rows filling in, so the page is reloaded rather than patched. */ onLinked={() => { void getAnime(anime.id).then(onUpdated); }} />}{convertOpen && <AnimeSetupDialog candidate={asCandidate(anime)} convertingId={anime.id} startIndexerId={anime.indexer_id} onClose={() => setConvertOpen(false)} onAdded={(updated) => { onUpdated(updated); setConvertOpen(false); show(t("toast.animeConverted").replace("{name}", updated.display_name)); }} />}{reacquiring && (
      <AnimeSetupDialog
        candidate={asCandidate(anime)}
        startIndexerId={anime.indexer_id}
        reacquire={{
          animeId: anime.id,
          mode: reacquiring.mode,
          // The quality buttons keep the group and change the resolution;
          // the subgroup button does the opposite. Either can still be
          // changed in the dialog - these are only what it opens with.
          subgroup: reacquiring.keepSubgroup ? anime.profile?.subgroups?.[0] ?? null : null,
          resolution: reacquiring.resolution,
        }}
        onClose={() => setReacquiring(null)}
        onAdded={(updated) => {
          onUpdated(updated);
          setReacquiring(null);
          show(t("toast.releaseChanged").replace("{name}", updated.display_name));
        }}
      />
    )}
    {/* The same dialog, filling gaps rather than changing anything: it opens
        with the group and quality this anime already uses, and confirming
        looks only for the episodes that have nothing behind them. */}
    {fixing && (
      <AnimeSetupDialog
        candidate={asCandidate(anime)}
        startIndexerId={anime.indexer_id}
        fixMissing={{
          animeId: anime.id,
          subgroup: anime.profile?.subgroups?.[0] ?? null,
          resolution: anime.profile?.resolution ?? null,
          releaseName: anime.release_name ?? null,
          matchOnly: fixing === "match",
        }}
        onClose={() => setFixing(null)}
        onAdded={(updated) => {
          onUpdated(updated);
          setFixing(null);
        }}
      />
    )}</section>;
}

/** One row of the episode list: status, the file it refers to, and its action. */
function EpisodeRow({ episode, busy, onDownload, offset = 0, plain = false }: {
  episode: EpisodeSummary;
  busy: string | null;
  onDownload: (episode: number) => void;
  /**
   * Nothing about releases: no release name, no magnet, no Download button
   * (build 140).
   *
   * For a watchlist-only entry. Reported as *"we are in the no indexer no,
   * basic mode, there should be no torrent magnets, or download this episode
   * appear"* - and the row has to obey that even when release rows exist from
   * a spell in the other mode, because the data outlives the mode.
   */
  plain?: boolean;
  /**
   * How far this anime's group numbers past its own episode 1 (build 113).
   *
   * Zero for almost everything. 66 for a group that counts from the start of
   * the franchise, and then the row says both - "#1 (67)" - because the file
   * on the tracker is called the second one and somebody looking for it
   * needs that number.
   */
  offset?: number;
}) {
  const status = episode.status;
  const best = episode.releases[0] ?? null;
  /* Everything the row derives about itself, decided in episodeRow.ts so it
     can be tested without a browser - which is where all three of this row's
     reported defects lived. The markup below is unchanged. */
  const view = episodeRowView(episode, {
    downloadable: DOWNLOADABLE,
    torrentOwns: TORRENT_OWNS,
  });
  const canDownload = view.canDownload && !plain;

  /* One state, worded once (build 125). The two-vocabulary switch is gone;
     see episodeRow.ts. An empty state means a payload from before this build,
     in which case the row says nothing rather than printing a key. */
  const notInList = view.stateDetail === "not_in_anidb_list";
  const stateLabel = view.state
    ? notInList
      ? `${t("episode.notInAnidbList")} — ${t(`episode.state.${view.state}`)}`
      : t(`episode.state.${view.state}`)
    : "";
  /* When it came out or is due (build 216). Out already: said on the right of the status line, in blue, so
     the row gains no line. Not out yet: said after the state ("No release yet - Release date: ..."). An
     estimate says so in both, and its tooltip says how it was counted. */
  const air = airLine(episode.air_date);
  const airHint = air?.estimated ? t("episode.airEstimateHint") : undefined;
  // In plain mode the file on disk is still named - that is this entry's own
  // file, not a release - and everything a release would say is dropped.
  const filename = plain ? (episode.media?.filename ?? null) : view.filename;
  const secondName = plain ? null : view.secondName;
  const waitingForShoko = status === "IMPORTING";
  const shokoBlocker = episode.download?.attention_reason;
  const shokoStuck = shokoBlocker === "shoko_not_imported";
  // Shoko took the file and it never arrived - a different thing to check.
  const shokoGone = shokoBlocker === "shoko_file_gone";
  const magnetTitle = plain ? undefined : (view.magnetTitle ?? undefined);
  const progress = episode.download?.progress;
  const pending = busy === String(episode.number);

  /* Two separate statements, in two places (build 90). "In library" is
     about AniFlow and belongs with the filename; whether the media server
     can play the file is about the file, and it now sits in its own column
     on the right. They were one sentence joined by a dash, which on a real
     release name wrapped onto three lines and read as one long claim.

     The server's name is a placeholder in the string because this is a
     Jellyfin rule today and Plex will want its own. */
  return (
    <div className={`episode-row ${episodeRowClass(status)}${hasTwoFiles(episode) ? " duplicate" : ""}`}>
      <div className="episode-row-left">
        {/* The number, and only the number (build 125). It used to carry a
            status word as well, which was the same fact the state line below
            states - in a different vocabulary, chosen per row. */}
        <strong className={`episode-number ${episodeNumberSize(episode.number)}`}>
          #{episode.number}
          {offset > 0 && <small className="muted"> ({episode.number + offset})</small>}
        </strong>
      </div>
      <div className="episode-row-main">
        {/* Every line tagged (builds 152, 153): *"make it more obvious ...
            which is the initial file used for the torrent and which is the
            final version in the target folder"*, and then *"a bit more
            symmetric"* - so the one-name rows are tagged as well, with
            whichever of the two that name is, and the state line gets a
            Status tag. One width for every tag, so every text starts in one
            column. */}
        {filename ? (
          <span className="episode-filename" title={filename}>
            {view.filenameIsTarget
              ? <span className="name-tag target">{t("episode.targetName")}</span>
              : <span className="name-tag source">{t("episode.sourceName")}</span>}
            {filename}
          </span>
        ) : null}
        {secondName && (
          <small className="episode-second-name" title={secondName}>
            <span className="name-tag source">{t("episode.sourceName")}</span>
            {secondName}
          </small>
        )}
        {waitingForShoko && !shokoStuck && !shokoGone && <small className="episode-hint">{t("episode.shokoWaitingHint")}</small>}
        {shokoStuck && <small className="reasons">{t("episode.shokoStuckHint")}</small>}
        {shokoGone && <small className="reasons">{t("episode.shokoGoneHint")}</small>}
        {(shokoStuck || shokoGone) && <ShokoHelpButtons compact />}
        {/* The state, for every episode (build 125). This line used to say
            "In library" and only for a completed one; now it is where the
            row's single derived state is read - "Downloading", "Waiting for
            Shoko", "Could not be acquired". Green stays for the good
            outcome, which is what it was made in build 124.

            Beside it, what the media server makes of the file: both are
            about the same file, and the verdict had been given a column of
            its own which centred it against the release name and truncated
            it. */}
        {stateLabel && (
          <div className="episode-owned-line">
            <small
              className={`episode-state ${stateClass(view.state, view.stateDetail)}`}
              style={filename ? undefined : { fontSize: ".8rem" }}
              title={view.stateDetail ? reasonText(view.stateDetail) : (air && !air.released ? airHint : undefined)}
            >
              <span className={`name-tag status ${stateClass(view.state, view.stateDetail)}`}>{t("episode.statusTag")}</span>
              {stateLabel}
              {air && !air.released && ` - ${air.text}`}
            </small>
            {air?.released && (
              <small className={`episode-airing${air.estimated ? " estimated" : ""}`} title={airHint}>{air.text}</small>
            )}
          </div>
        )}
        {status === "MISSING" && <small className="episode-hint">{t("episode.missingHint")}</small>}
        {status === "ERROR" && episode.error && <small className="reasons">{episode.error}</small>}
        {canDownload && best?.has_magnet && <small className="episode-magnet" title={magnetTitle}>✓ {t("episode.magnetReady")}</small>}
        {TORRENT_OWNS.has(status) && progress != null && (
          <>
            <progress max={1} value={progress} style={{ height: 6, width: "100%", maxWidth: 200 }} />
            <small className="muted" style={{ fontSize: ".7rem" }}>{Math.round((progress || 0) * 100)}%{episode.download?.qb_state ? ` · ${clientState(episode.download.qb_state)}` : ""}</small>
          </>
        )}
        {status === "DOWNLOADED" && <small className="episode-hint">{t("episode.downloadedHint")}</small>}
        {canDownload && <small className="episode-hint" title={magnetTitle}>{t("episode.downloadAvailable")}</small>}
      </div>
      {canDownload && (
        <button
          className="button secondary"
          // The floor keeps the button from shrinking when the label switches to
          // the shorter pending text; the grid's auto column lets it grow past it.
          style={{ padding: "8px 14px", fontSize: ".82rem", whiteSpace: "nowrap", minWidth: 172 }}
          disabled={pending}
          onClick={() => onDownload(episode.number)}
        >
          {pending ? t("profile.saving") : t("dl.downloadEpisode")}
        </button>
      )}
    </div>
  );
}

function AddAnimeDialog({ onClose, onSelect, library, initialQuery = "" }: {
  /** The watchlist: what to suggest depends on it. */
  library: AnimeSummary[];
  onClose: () => void;
  onSelect: (candidate: AnimeSearchResult) => void;
  /** A name to start from - what Shoko calls a series it already holds. */
  initialQuery?: string;
}) {
  useLockedPageScroll();
  const [query, setQuery] = useState(initialQuery);
  const [results, setResults] = useState<AnimeSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  // The reason, not a flag. "The anime could not be added" was shown for a
  // failed *search*, which is a different thing that has not been attempted
  // yet - and it hid what the server said, which for a provider that is down,
  // rate-limiting or unreachable is the only useful part.
  const [error, setError] = useState<string | null>(null);
  /* An empty box is not a dead end (build 208): this season's best matches for what the watchlist
     says you like, until you type something. Nothing shown if the calendar has no season yet.
     A failure is silent - these are a convenience, and the search is right there. */
  const [season, setSeason] = useState<AnimeSearchResult[]>([]);
  useEffect(() => {
    let live = true;
    void getCalendar().then((answer) => { if (live) setSeason(answer.anime); }).catch(() => undefined);
    return () => { live = false; };
  }, []);
  const suggestions = useMemo(() => suggestionsFor(season, library), [season, library]);
  const personal = useMemo(() => tasteProfile(library).size > 0, [library]);

  // Opened with a name already in it, which means the user asked for that
  // search by clicking the thing it names - so run it rather than making them
  // press the button they did not ask for.
  const searchFor = useCallback(async (text: string) => {
    if (text.trim().length < 2) return;
    setLoading(true);
    setError(null);
    try {
      setResults(await searchAnime(text.trim()));
      setSearched(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (initialQuery) void searchFor(initialQuery); }, [initialQuery, searchFor]);

  const runSearch = () => searchFor(query);

  const row = (result: AnimeSearchResult) => <button className="search-result" key={`${result.provider}-${result.provider_id}`} disabled={result.in_library} title={result.in_library ? t("add.alreadyTracked") : undefined} onClick={() => onSelect(result)}>{result.cover_url ? <img className="search-result-cover" src={providerCoverUrl(result.cover_url) ?? undefined} alt="" loading="lazy" decoding="async" /> : <div className="search-result-cover search-result-cover-empty">{result.display_name.slice(0, 1)}</div>}<span className="search-result-title">{result.display_name}</span>{result.genres.length > 0 && <small className="search-result-genres">{result.genres.join(", ")}</small>}<small className="search-result-meta">{[result.format, result.episode_count ? `${result.episode_count} eps` : null].filter(Boolean).join(" · ")}{/* On the meta line and to the right, in green: inline after a long
                    title it sat at the end of the second wrapped line and was
                    missed entirely. */}
                    {result.in_library && <span className="in-library-tag" title={result.in_library_why ?? undefined}>✓ {result.in_library_as ? t("add.inLibraryAs").replace("{name}", result.in_library_as) : t("add.inLibrary")}</span>}</small></button>;

  return <div className="dialog-backdrop" role="presentation"><section className="dialog dialog-search" role="dialog" aria-modal="true" aria-label={t("add.title")}><header><h2>{t("add.title")}</h2><button className="close" onClick={onClose} aria-label={t("add.cancel")}>×</button></header><div className="search-row"><input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void runSearch(); }} placeholder={t("add.placeholder")} autoFocus /><button className="button primary" onClick={() => void runSearch()} disabled={loading || query.trim().length < 2}>{loading ? t("add.searching") : t("add.search")}</button></div><div className="search-results">{results.length === 0 && <p className="muted">{searched ? t("add.noResults") : t("add.empty")}</p>}{!searched && results.length === 0 && suggestions.length > 0 && <><h3 className="suggest-head">{t(personal ? "add.suggestedForYou" : "add.suggestedPopular")}</h3>{suggestions.map(row)}</>}{results.map(row)}</div>{error && <p className="error-message">{t("add.searchFailed")}<small className="muted" style={{ display: "block", marginTop: 4 }}>{error}</small></p>}</section></div>;
}

export interface AuthState {
  /** No account exists yet, so the only thing on offer is creating one. */
  needs_setup: boolean;
  authenticated: boolean;
  username: string | null;
}

/** Whether anyone is set up, and whether this browser is logged in. */
export function authState(): Promise<AuthState> {
  return request<AuthState>("/api/auth/state");
}

/** Create the one account, against a one-time code from the log. */
export function authSetup(username: string, password: string, token: string, termsVersion: string): Promise<AuthState> {
  return request<AuthState>("/api/auth/setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, token, terms_version: termsVersion }),
  });
}

/** The version of the first-run notice this build shows (services/terms.TERMS_VERSION). */
export const TERMS_VERSION = "2026-10-15-2";

/** Where a fresh install has got to (build 137). */
export interface SetupState {
  completed: boolean;
  needs_account: boolean;
  /** The mode in force, and whether anybody has chosen it yet. */
  mode: "standalone" | "shokoserver";
  mode_recorded: boolean;
  /** Automate is unreachable without one of these. */
  has_indexer: boolean;
  indexer_names: string[];
  shoko_configured: boolean;
  feeds: number;
  /** The first-run notice has been accepted on this install. */
  terms_accepted: boolean;
}

export function setupState(): Promise<SetupState> {
  return request<SetupState>("/api/setup/state");
}

/** Record which program organises the library. Once, and never again. */
export function chooseStorageMode(mode: "standalone" | "shokoserver"): Promise<SetupState> {
  return request<SetupState>("/api/setup/mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
}

/**
 * Hand an indexer file to AniFlow.
 *
 * The file is read in the browser and posted as text: a multipart endpoint
 * would need a new dependency on the server for the one place AniFlow accepts
 * a file. The server validates it before it is written.
 */
export function uploadIndexer(filename: string, content: string): Promise<SetupState> {
  return request<SetupState>("/api/setup/indexer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, content }),
  });
}

/** Finish setup, so the wizard stops appearing. */
export function completeSetup(): Promise<{ completed: boolean; at: string }> {
  return request("/api/setup/complete", { method: "POST" });
}

export function signIn(username: string, password: string): Promise<AuthState> {
  return request<AuthState>("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

export function signOut(): Promise<AuthState> {
  return request<AuthState>("/api/auth/logout", { method: "POST" });
}

/** Ask the server to write a fresh one-time code to the log. */
export function requestResetCode(): Promise<{ detail: string }> {
  return request("/api/auth/forgot", { method: "POST" });
}

export function resetPassword(password: string, token: string): Promise<AuthState> {
  return request<AuthState>("/api/auth/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password, token }),
  });
}

/** Change your own username and/or password. The current one is required. */
export function changeAccount(payload: {
  current_password: string;
  username?: string;
  new_password?: string;
}): Promise<AuthState> {
  return request<AuthState>("/api/auth/account", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export interface ShokoMissingEpisode {
  number: number | null;
  title: string | null;
  air_date: string | null;
  kind: string | null;
}

export interface ShokoSeries {
  shoko_id: number;
  name: string;
  local_episodes: number;
  total_episodes: number;
  /** The gap between what AniDB lists and what Shoko holds. */
  missing_count: number;
  anidb_id: number | null;
  /** Shoko's MyAnimeList ids - the reliable bridge to AniFlow's own entries. */
  mal_ids: number[];
  tmdb_ids: number[];
  /** The aired episodes Shoko has no file for. */
  missing: ShokoMissingEpisode[];
  episode_error: string | null;
  /** AniFlow's own picture, for when Shoko has none (build 180). */
  cover_url: string | null;
  /** The watchlist entry this series is, if any. */
  watchlist_id: string | null;
}

/** One series Shoko holds that the watchlist has no entry for. */
export interface ShokoUnlinkedSeries {
  shoko_id: number;
  name: string;
  local_episodes: number;
  total_episodes: number;
  missing_count: number;
  anidb_id: number | null;
  mal_id: number | null;
  /** A poster AniFlow already had for this AniDB id, if any. */
  cover_url: string | null;
}

export interface ShokoUnlinked {
  series: ShokoUnlinkedSeries[];
  /** Why the list is empty, when it is empty because Shoko could not be read. */
  detail: string | null;
}

/**
 * The gap between the two libraries, as a list.
 *
 * Nothing in the browser reads this since build 117 - the watchlist showed it
 * as a section of its own and that section is gone, because those series are
 * adopted by the server now. Kept because the endpoint is still the honest
 * answer to "what does Shoko have that AniFlow does not", which is worth
 * being able to ask while something looks wrong.
 */
export function listShokoUnlinked(): Promise<ShokoUnlinked> {
  return request<ShokoUnlinked>("/api/shoko/unlinked");
}

export interface ShokoOverview {
  reachable: boolean;
  detail: string | null;
  state: string | null;
  /** Shoko's own dashboard figures, passed through as it reports them. */
  stats: Record<string, number | string | null>;
  import_folders: Record<string, unknown>[];
  series: ShokoSeries[];
  /** The per-series gap added up; see the note on the page. */
  missing_total: number;
  /** When Shoko was actually asked; the server remembers the answer (build 180). */
  fetched_at: string | null;
}

/** What Shoko Server says about the library. Read-only. */
export function getShokoOverview(refresh = false): Promise<ShokoOverview> {
  return request<ShokoOverview>(refresh ? "/api/shoko?refresh=true" : "/api/shoko");
}

/** One file Shoko holds for an episode. */
export interface ShokoFile {
  name: string;
  size: number | null;
  height: number | null;
}

/** One AniDB episode and what Shoko holds for it (build 180). */
export interface ShokoEpisode {
  number: number | null;
  title: string | null;
  air_date: string | null;
  missing: boolean;
  files: ShokoFile[];
}

/** One series in full: the header and every episode. */
export interface ShokoSeriesDetail {
  shoko_id: number;
  name: string;
  anidb_id: number | null;
  mal_ids: number[];
  local_episodes: number;
  total_episodes: number;
  episodes: ShokoEpisode[];
  error: string | null;
  cover_url: string | null;
  watchlist_id: string | null;
}

export function getShokoSeries(shokoId: number, refresh = false): Promise<ShokoSeriesDetail> {
  return request<ShokoSeriesDetail>(`/api/shoko/series/${shokoId}${refresh ? "?refresh=true" : ""}`);
}

/** Shoko's own poster, served by AniFlow so the API key stays on the server. */
export function shokoPosterUrl(shokoId: number): string {
  return `/api/shoko/series/${shokoId}/poster`;
}

/**
 * AniDB's title dump, and how much of the watchlist it has managed to
 * identify.
 *
 * Reported on the Shoko page because that is where it shows: Shoko names a
 * series whatever AniDB calls it, so an AniDB id on both sides is what keeps
 * the same show from being offered twice.
 */
export interface AnidbTitlesStatus {
  /** False before the first download. Everything still works, less exactly. */
  present: boolean;
  downloaded_at: string | null;
  parsed_at: string | null;
  anime_count: number;
  title_count: number;
  bytes_downloaded: number;
  next_refresh_at: string | null;
  last_error: string | null;
  /** Watchlist anime that have an AniDB id, out of how many there are. */
  linked: number;
  total: number;
}

export function getAnidbTitles(): Promise<AnidbTitlesStatus> {
  return request<AnidbTitlesStatus>("/api/shoko/titles");
}

/** Fetch the dump now rather than waiting for the weekly pass. */
export function refreshAnidbTitles(): Promise<AnidbTitlesStatus> {
  return request<AnidbTitlesStatus>("/api/shoko/titles/refresh", { method: "POST" });
}

export interface Health {
  status: "ok" | "degraded";
  database: "ok" | "unavailable";
  worker: "running" | "stopped";
  /** Whether the selected metadata source is answering. */
  metadata: "ok" | "unreachable" | "unknown";
  /** Shoko Server, when one is configured. "disabled" when there is none. */
  shoko: "disabled" | "ok" | "unauthorised" | "unreachable" | "unknown";
  timestamp: string;
}

export interface AppInfo {
  name: string;
  version: string;
  /** Only ever grows; shown next to the version so a server copy can be verified. */
  build: number;
  /** `AniFlow_b68_v0_1_0_d26_09_06` - the same string as BUILD.md. */
  build_string: string;
  environment: string;
  /** The timezone the server derives airing days in. */
  timezone: string;
  /** Today there: 0 = Monday, 6 = Sunday. */
  today_weekday: number;
  server_time: string;
  /**
   * Which storage pipeline runs. In "shokoserver" AniFlow hands files to
   * Shoko Server and does not name library folders, so screens that show a
   * folder have to say "not created yet" rather than showing an empty value.
   */
  mode: StorageMode;
  /** "basic" (no indexer, nothing to download with) or "advanced". */
  app_mode: "basic" | "advanced";
  /** Anime added in Basic mode that still need a release name. */
  needs_conversion: number;
  /** A torrent client has an address. Without one, downloading does nothing. */
  torrent_client_configured: boolean;
  /** Where the configuration folders live, for pages that name a path. */
  config_dir: string;
}

export type ReleaseDecision = "MATCHED" | "REJECTED" | "IGNORED" | "AMBIGUOUS";

/** How the watchlist is ordered. Persisted per browser. */
export type WatchlistSort = "name" | "added" | "schedule" | "library";

/** A site AniFlow may search. None are shipped; the user adds them. */
export interface Indexer {
  id: string;
  name: string;
  /** As typed - a bare address or a full search URL with the user's filters. */
  url: string;
  enabled: boolean;
  is_default: boolean;
  last_used_at: string | null;
  last_error: string | null;
  created_at: string;
  /** What a search would request, with a sample term filled in. */
  example_request: string;
  /** Whether this URL can ask for anything past the first page. */
  supports_paging: boolean;
  /** The definition file governing this site, or null for the generic rules. */
  definition: string | null;
  /** Whether this indexer's definition supplies a feed URL. */
  rss_available: boolean;
  /** Whether it currently stands up an RSS source. */
  use_as_rss: boolean;
  rss_source_id: string | null;
  /** Everything this site lists is adult; what is added from it is marked so. */
  adult: boolean;
}

export interface IndexerTestResult {
  ok: boolean;
  url: string;
  items: number;
  detail: string | null;
  /** A feed form of the URL that worked when the stored one did not. */
  suggested_url: string | null;
}

export function listIndexers(): Promise<Indexer[]> {
  return request<Indexer[]>("/api/indexers");
}


export function updateIndexer(
  id: string,
  payload: {
    name?: string;
    url?: string;
    enabled?: boolean;
    is_default?: boolean;
    use_as_rss?: boolean;
  },
): Promise<Indexer> {
  return request<Indexer>(`/api/indexers/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteIndexer(id: string): Promise<void> {
  await request<void>(`/api/indexers/${id}`, { method: "DELETE" });
}

export function testIndexer(id: string): Promise<IndexerTestResult> {
  return request<IndexerTestResult>(`/api/indexers/${id}/test`, { method: "POST" });
}

export interface FeedSource {
  id: string;
  name: string;
  url: string;
  enabled: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
  /** Set when an indexer owns this source: it is managed, not editable here. */
  indexer_id: string | null;
  indexer_name: string | null;
  /** Derived from the release history, so it never drifts. */
  release_count: number;
  /** Of those, how many reached a watchlist anime. Zero means the feed is
   *  being read but is not producing anything AniFlow can use. */
  matched_count: number;
  last_release_at: string | null;
  last_match_at: string | null;
}

export interface ReleaseItem {
  id: string;
  raw_name: string;
  torrent_url: string | null;
  published_at: string | null;
  parsed_title: string | null;
  subgroup: string | null;
  episode: string | null;
  version: number;
  resolution: string | null;
  media_source: string | null;
  decision: ReleaseDecision;
  anime_id: string | null;
  anime_name: string | null;
  failure_reasons: string[];
  ambiguous_anime_ids: string[];
  source_name: string | null;
  created_at: string;
}

export interface RssStatus {
  worker_running: boolean;
  scanning: boolean;
  scan_requested: boolean;
  last_scan_at: string | null;
  next_scan_at: string | null;
  last_scan_error: string | null;
  last_scan_summary: Record<string, unknown> | null;
  interval_minutes: number;
  enabled: boolean;
}

export type DownloadStatus = "QUEUED" | "DOWNLOADING" | "COMPLETED" | "SKIPPED" | "ATTENTION" | "FAILED" | "REMOVED";

/** Which storage pipeline runs. Fixed per deployment; see StorageMode. */
export type StorageMode = "standalone" | "shokoserver";

/** One storage root as the Settings page shows it. */
export interface StoragePath {
  key: "media" | "downloads" | "import";
  /** Always the same on every install, so never shown on its own. */
  container: string | null;
  /** The folder it was mounted from - null when it cannot be determined. */
  host: string | null;
  exists: boolean;
  writable: boolean;
  /** False for a root this mode never uses. */
  used: boolean;
}

export interface AnimeSearchResult {
  provider: string;
  provider_id: string;
  display_name: string;
  title_english: string | null;
  title_romaji: string | null;
  title_native: string | null;
  aliases: string[];
  cover_url: string | null;
  genres: string[];
  /** Who made it, and what it is about — the calendar's detail view. */
  studios: string[];
  synopsis: string | null;
  /**
   * How this relates to seasons already tracked: sequel | gap | prequel, or
   * null. Null also when this very season is tracked — `in_library` says
   * that, and two marks on one card is one too many.
   */
  related: string | null;
  format: string | null;
  episode_count: number | null;
  /**
   * Where the show is: NOT_YET_AIRED | AIRING | FINISHED | CANCELLED |
   * UNKNOWN (build 148). UNKNOWN means nobody published one and is never
   * drawn.
   *
   * The difference between "starts next week" and "finished two years ago",
   * which the premiere date alone does not give you — every show on a season
   * page has a date in the same quarter.
   */
  airing_status: string;
  provider_ids: Record<string, string>;
  /** The provider's own adult flag; the calendar filters on it. */
  is_adult: boolean;
  in_library: boolean;
  /** When in_library: the watchlist anime it was matched to, and the rule ("title: ... -> ...", "pair: anilist 1"). */
  in_library_as?: string | null;
  in_library_why?: string | null;
  /**
   * The calendar's own stable id (`al54`), minted once and never changed.
   * Null for a search result, which is not a calendar entry.
   */
  calendar_id?: string | null;
  /**
   * Whether AniFlow can actually fetch details for this entry. The calendar
   * knows five sources; adding needs one AniFlow can query. A card that says
   * so beats a button that fails.
   */
  addable?: boolean;
  /**
   * The title in the form worth searching for: no season designation, no
   * colon. Computed by the backend so the season vocabulary stays in one
   * place. Prefills the setup dialog's search term.
   */
  search_hint: string;
  /**
   * Which episode `air_slot_at` is the airing time of. With it, every other
   * episode is that instant plus seven days each way.
   */
  air_slot_episode?: number | null;
  /** When it premieres, and one instant an episode airs. The calendar orders
   *  a season by these. */
  air_start_date: string | null;
  air_slot_at: string | null;
  /** Every source's list count added together - for ordering only. */
  popularity: number | null;
  /** What to prefill the setup dialog's folder name with. Follows
   *  title_folder_order - a different setting from the display name, so
   *  reading Japanese titles on screen does not rename folders. */
  folder_hint: string;
}

export interface AnimeSummary {
  id: string;
  primary_provider: string;
  primary_provider_id: string;
  local_name: string;
  display_name: string;
  cover_url: string | null;
  lifecycle: string;
  /** When true the worker queues this anime's available episodes on its own. */
  automation_enabled: boolean;
  /** The library it lives in (build 192). */
  library_id: number | null;
  /** The indexer it was chosen from; null follows the default (build 219, item 44). */
  indexer_id?: string | null;
  /** Started, not finished, no new episode for three weeks (build 205). */
  waiting_for_next_part?: boolean;
  /** "Keep here": the season check leaves it in its library. */
  library_pinned?: boolean;
  /** Adult for the watchlist's hide button: the flag, or a library marked Adult (build 198). */
  is_adult: boolean;
  /** The user's own flag alone - what the tick box on the anime's page shows. */
  adult_flag: boolean;
  episode_count: number | null;
  /**
   * What this anime was added as.
   *
   * "basic" - a tracker entry: a name and a lifecycle, nothing more.
   * "future" - added before its season starts (build 121): every fact about
   *   the show, no download configuration, and an offer to set the releases
   *   up once its premiere has passed.
   * "advanced" - the ordinary kind.
   */
  entry_mode: "basic" | "future" | "advanced";
  owned_episodes: number;
  /** How far this anime's group numbers past its own episode 1. */
  episode_offset?: number;
  available_episodes: number;
  /** Episodes AniFlow brought into the library since the watchlist was
   *  last left - the blue corner on the card (build 151). */
  new_episode_count?: number;
  /** When the latest of those arrived. */
  newest_episode_at?: string | null;
  feed_source_id?: string | null;
  custom_feed_url?: string | null;
  created_at: string;
  /** When the show premiered, as the provider reports it (YYYY-MM-DD). */
  air_start_date: string | null;
  /** The season the providers place it in (build 190); null until the calendar says. */
  season?: string | null;
  season_year?: number | null;
  /** The season on screen is yours, set in the Edit window (item 17). */
  season_is_manual?: boolean;
  /** The weekly slot in the server's timezone: 0 = Monday, 6 = Sunday. */
  airs_weekday: number | null;
  /** Airing time as HH:MM, when the provider gives one. */
  airs_time: string | null;
  /** True when the user uploaded their own artwork. */
  has_custom_cover: boolean;
  /** AniDB's id for this show, when it is known (build 116). */
  anidb_id?: number | null;
  /** Every other name AniFlow holds for it, for the watchlist's search box (build 208). */
  search_names?: string[];
  /** Its genres, for the taste window (build 208). */
  genres?: string[];
  /** TV, MOVIE, OVA ...: shown on the card beside the state (build 208). */
  format?: string | null;
  /**
   * One entry per group - metadata, download, storage, shoko (build 120).
   *
   * Each carries a colour and the codes for what is missing, or why the
   * group does not apply here. Four colours: green, yellow, red, and grey
   * for "not applicable" - which is what stops a basic entry showing a
   * permanent red download icon for a feature it has not got.
   */
  health?: Record<
    string,
    {
      colour: "green" | "yellow" | "red" | "grey";
      missing: string[];
      known: { field: string; value: string }[];
      not_applicable: string | null;
    }
  >;
}

/** The states an episode can be in, as derived by the backend. */
export type EpisodeStatus =
  | "UNAVAILABLE"
  | "AVAILABLE"
  | "DOWNLOADING"
  | "DOWNLOADED"
  /** Shoko Server mode: handed over, waiting for Shoko to file it. */
  | "IMPORTING"
  | "COMPLETE"
  | "MISSING"
  | "UPDATE"
  | "ERROR";

/** One release a provider published for an episode. */
export interface EpisodeRelease {
  id: string;
  raw_name: string;
  provider: string;
  provider_id: string;
  has_magnet: boolean;
  magnet_url: string | null;
  has_torrent: boolean;
  torrent_url: string | null;
  seeders: number | null;
  size: number | null;
  subgroup: string | null;
  resolution: string | null;
  version: number;
}

/**
 * An episode with everything that belongs to it nested underneath: the
 * releases found for it, its download, and the three storage stages.
 */
/**
 * What a person reads about one episode (build 125).
 *
 * Derived server-side by services/episode_view.py and never stored. The row
 * shows `state`; `detail` is the reason code, raw qBittorrent state or free
 * text behind it, which reasons.ts already knows how to word.
 */
export interface EpisodeStateView {
  state: string;
  detail: string | null;
  progress: number | null;
  actionable: boolean;
}

/** A file next to an episode's own: a backup, a kept original, an older version (189). */
export interface EpisodeCopy {
  id: string;
  /** renamed | kept_original | previous_version | awaiting_replacement */
  kind: string;
  filename: string;
  size_bytes: number | null;
  subgroup: string | null;
  resolution: string | null;
  recorded_at: string | null;
}

/** `recorded`: stored for this episode. `exact`: the premiere. `from_premiere` / `from_anchor`: counted weekly, an estimate. */
export interface EpisodeAirDate {
  on: string;
  basis: "recorded" | "exact" | "from_anchor" | "from_premiere";
}

export interface EpisodeSummary {
  /** The files next to this episode's own, by the notes AniFlow wrote (build 189). */
  copies?: EpisodeCopy[];
  id: string;
  number: number;
  title: string | null;
  episode_type: string;
  /** The internal value. Still sent, still shown in debug. */
  status: EpisodeStatus;
  /** What the episode row shows. Optional only until every caller sends it. */
  view?: EpisodeStateView;
  /** Why nothing is fetched for it although it has no file here: the AniDB list has it (item 36b). */
  skip_reason?: string | null;
  aired_at: string | null;
  /** The day it came out or is due, and how that is known (build 216). Null when there is nothing to count from. */
  air_date?: EpisodeAirDate | null;
  error: string | null;
  releases: EpisodeRelease[];
  download: {
    id: string;
    folder: string | null;
    filename: string | null;
    status: DownloadStatus;
    progress: number | null;
    qb_state: string | null;
    info_hash: string | null;
    error: string | null;
    attention_reason: string | null;
    label: string | null;
    version: number;
  } | null;
  media: {
    filename: string | null;
    size_bytes: number | null;
    imported_at: string | null;
    version: number | null;
    /** Whether the media server can play it as it is (build 87):
     *  direct | transcode | unknown. Null until the file has been read. */
    playback: string | null;
    /** "1080p · H.264 High 10 · 10-bit · FLAC", assembled by the backend. */
    summary: string | null;
    /** Why it would transcode, as codes this side translates. */
    findings: string[];
    scanned_at: string | null;
  };
}

/** The three storage roots AniFlow is operating with. */
export interface StorageInfo {
  download_root: string;
  import_root: string | null;
  media_root: string;
}

export interface AnimeDetail extends AnimeSummary {
  /** Other databases' ids for it - the MAL id is under "myanimelist". */
  provider_ids?: Record<string, string>;
  /** True when the airing slot on screen is yours, not the provider's. */
  schedule_is_manual: boolean;
  episode_count_is_manual: boolean;
  /** The user has asked for no release hunting at all (build 123). */
  releases_ignored?: boolean;
  title_english: string | null;
  title_romaji: string | null;
  title_native: string | null;
  /** For the Edit dialog (build 156). */
  genres?: string[];
  studios?: string[];
  synopsis?: string | null;
  aliases: string[];
  format: string | null;
  drop_mode: string | null;
  /** How releases name this anime; what feed matching compares against. */
  release_name: string | null;
  media_folder: string | null;
  import_folder: string | null;
  profile: {
    subgroups: string[];
    resolution: string | null;
    media_source: string | null;
    censor_preference: string;
    require_title_match: boolean;
    template_id: string | null;
    template_name: string | null;
  } | null;
  feed_source_name: string | null;
  storage: StorageInfo | null;
  episodes: EpisodeSummary[];
}

/**
 * What changing an anime's release would do to the files already there
 * (build 87). Read before either button is pressed: "delete eleven files" is
 * not something to discover afterwards.
 */
export interface ReacquirePlan {
  mode: string;
  episodes: number;
  files: string[];
  /** Recorded but not on disk, so "nothing to delete" reads as a fact. */
  missing: number;
}

export function reacquirePlan(animeId: string, mode: string): Promise<ReacquirePlan> {
  return request<ReacquirePlan>(
    `/api/anime/${encodeURIComponent(animeId)}/reacquire?mode=${mode}`,
  );
}

export interface ReacquireResult {
  cleared: number;
  deleted: number;
  backed_up: number;
  /** Left on disk with a note, to be deleted when the new release is in. */
  remembered?: number;
  missing: number;
  /** Files that could not be dealt with. Their episodes are left alone. */
  failed: string[];
}

/**
 * Clear the episodes, deal with the files, and set the new profile.
 *
 * Downloads nothing: the episodes come back as MISSING and the normal search
 * path picks them up, which is the same route a new anime takes.
 */
export function reacquireAnime(
  animeId: string,
  body: {
    mode: string;
    subgroups?: string[] | null;
    resolution?: string | null;
    release_name?: string | null;
    /**
     * What this anime is called now (build 116). Groups spell the same
     * season differently, so the label follows the new group - while the
     * storage folders deliberately do not, being where the files are.
     */
    local_name?: string;
    edition?: string[];
    edition_not?: string[];
    /** What to ask the indexer for the new group's back catalogue. */
    search_query?: string | null;
    /** The indexer chosen in the dialog; kept on the anime for its later searches (item 44). */
    indexer_id?: string | null;
  },
): Promise<ReacquireResult> {
  return request<ReacquireResult>(`/api/anime/${encodeURIComponent(animeId)}/reacquire`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export interface MediaScanResult {
  scanned: number;
  direct: number;
  transcode: number;
  unknown: number;
  /** ffprobe | matroska - a verdict is only as good as what read the file. */
  probed_by: string;
}

/** Read this anime's files and record whether the media server can play them. */
export function scanMedia(animeId: string, force = false): Promise<MediaScanResult> {
  return request<MediaScanResult>(
    `/api/anime/${encodeURIComponent(animeId)}/media-scan?force=${force}`,
    { method: "POST" },
  );
}

export interface CalendarSeason {
  /** Always "calendar" since build 143: AniFlow's own data, not a provider's. */
  provider: string;
  season: string;
  year: number;
  anime: AnimeSearchResult[];
  /**
   * ready | loading | updating | empty | error.
   *
   * `loading` means there is nothing yet and a fetch is running; `updating`
   * means what is on screen is about to get better. `error` never means the
   * data has gone.
   */
  status: "ready" | "loading" | "updating" | "empty" | "error";
  /** When a complete snapshot for this season was last applied. */
  loaded_at: string | null;
  updating: boolean;
  /** True when it has not been updated for a while. Still shown, always. */
  stale: boolean;
  unavailable_reason: string | null;
  /** Kept so nothing breaks; same value as `loaded_at`. */
  cached_at: string | null;
  next_refresh_at: string | null;
  stale_hours: number;
  /**
   * Whether this calendar has had its one initial fill (build 148).
   *
   * A fact about the database rather than about this season. It rides along
   * here because the page reads this on every navigation, and the button it
   * decides - "fill the calendar in" - is one the page has to be able to stop
   * offering the moment it has been pressed.
   */
  first_fill_done: boolean;
}

/** What the calendar is doing right now. */
export interface CalendarStatus {
  running: boolean;
  kind: string;
  year: number;
  season: string;
  done: number;
  total: number;
  /** The source being asked, or "artwork". Seasons alone were not watchable. */
  step: string;
  /** Posters fetched so far in this season, and how many were chosen. The
   * total is 0 until the snapshots are in — a bar that guesses its own
   * length is worse than no bar. */
  pictures: number;
  pictures_total: number;
  /** Sources asked, of the sources to ask. Known before any of them answer. */
  sources_done: number;
  sources_total: number;
  summary: string;
  error: string | null;
  /** Set when the calendar database itself could not be opened. */
  blocked_reason: string | null;
}

/** One line of the calendar's own trace. */
export interface CalendarTraceEvent {
  at: string;
  level: "info" | "warn" | "error";
  stage: string;
  message: string;
  detail: Record<string, unknown>;
}

export interface CalendarDebug {
  status: CalendarStatus;
  auto_update: boolean;
  works: number;
  identities: number;
  seasons: {
    year: number;
    season: string;
    works: number;
    last_attempt_at: string | null;
    last_success_at: string | null;
  }[];
  sources: {
    provider: string;
    last_attempt_at: string | null;
    last_success_at: string | null;
    failures: number;
    last_error: string | null;
  }[];
  events: CalendarTraceEvent[];
}

/** What the calendar holds and what it has just been doing. */
export function calendarDebug(): Promise<CalendarDebug> {
  return request<CalendarDebug>("/api/calendar/debug");
}

export function clearCalendarTrace(): Promise<CalendarDebug> {
  return request<CalendarDebug>("/api/calendar/debug/clear", { method: "POST" });
}

export interface CalendarResetPreview {
  works: number;
  identities: number;
  aliases: number;
  remembered_memberships: number;
  summary: string;
}

export interface ReleaseProfile {
  id: string;
  anime_id: string;
  subgroups: string[];
  resolution: string | null;
  media_source: string | null;
  censor_preference: "ANY" | "UNCENSORED" | "CENSORED";
  require_title_match: boolean;
  require_resolution_known: boolean;
  require_source_known: boolean;
  require_censor_known: boolean;
  template_id?: string | null;
  template_name?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReleaseProfileDraft {
  subgroups: string[];
  resolution: string | null;
  media_source: string | null;
  censor_preference: "ANY" | "UNCENSORED" | "CENSORED";
  require_title_match: boolean;
  require_resolution_known: boolean;
  require_source_known: boolean;
  require_censor_known: boolean;
}

export interface ParsedRelease {
  raw_name: string;
  subgroup: string | null;
  title: string | null;
  episode: string | null;
  version: number;
  resolution: string | null;
  media_source: string | null;
  video_codec: string | null;
  audio_codec: string | null;
  censored: boolean | null;
  unknown_tags: string[];
}

export interface FieldVerdict {
  field: string;
  verdict: "PASS" | "FAIL" | "FAIL_UNKNOWN" | "SKIPPED";
  expected: string | null;
  actual: string | null;
}

export interface ProfileTestResult {
  parsed: ParsedRelease;
  matched: boolean;
  fields: FieldVerdict[];
  failures: string[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) throw new Error(await extractError(response));
  return response.json() as Promise<T>;
}

async function extractError(response: Response): Promise<string> {
  const raw = await response.text();
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
    if (parsed.detail !== undefined) return JSON.stringify(parsed.detail);
  } catch {
    /* plain-text body */
  }
  return raw || `${response.status} ${response.statusText}`;
}

export function getHealth(): Promise<Health> {
  return request<Health>("/api/system/health");
}

export function getAppInfo(): Promise<AppInfo> {
  return request<AppInfo>("/api/system/info");
}

export function sendDebugLog(): Promise<{ ok: boolean; message: string }> {
  return request("/api/system/debug-log", { method: "POST" });
}

export function listAnime(): Promise<AnimeSummary[]> {
  return request<AnimeSummary[]>("/api/anime");
}

/**
 * This anime's page was opened: its blue new-episode corner goes (build 210).
 *
 * The count otherwise lasts a day. Per anime, so opening one leaves the others' corners alone.
 */
export function markAnimeSeen(id: string): Promise<{ seen_at: string }> {
  return request(`/api/anime/${id}/seen`, { method: "POST" });
}

/**
 * AniFlow was opened (build 239). What arrived since the user's last visit is new for a day from now;
 * a second call the same day changes nothing.
 */
export function markWatchlistVisit(): Promise<{ previous: string | null; current: string }> {
  return request("/api/anime/visit", { method: "POST" });
}

export function searchAnime(query: string): Promise<AnimeSearchResult[]> {
  return request<AnimeSearchResult[]>(`/api/anime/search?query=${encodeURIComponent(query)}`);
}

/**
 * One anime by its AniDB id - how a Shoko Server series is adopted.
 *
 * No search, deliberately: Shoko states the id, and searching by the name it
 * shows found season three of a show for a season-one name in build 89.
 */
export function animeByAnidbId(anidbId: number): Promise<AnimeSearchResult> {
  return request<AnimeSearchResult>(`/api/anime/by-anidb/${anidbId}`);
}

/**
 * Everything the setup dialog decided, as it goes over the wire.
 *
 * This is the whole package and it is deliberately the same shape as
 * `addSetup`'s return (setupPayload.ts). It was not, until build 116, and
 * that gap was a bug with no symptom at either end: the dialog computed the
 * edition, the refused edition, the scan tickets and the episode offset, and
 * `addAnime` hand-listed the fields it sent and did not list those four. They
 * were dropped between one function and the next.
 *
 * Which is how the reported one survived three builds. Build 112 decided the
 * offset, 113 taught the server's numbering path to apply it and 114 filled
 * the field in from what was already known - and a freshly added Re:Zero
 * still arrived numbered 39-82, because no build had sent the number.
 *
 * So: one interface, every field, and nothing rewritten in between.
 */
/**
 * The four groups for one anime, with what is missing and what was found
 * (build 120). What the Debug windows show - the same answer the card's
 * icons are drawn from, so the two can never disagree.
 */
/** What one press of Refill in the Metadata window managed (build 123). */
export interface RefillResult {
  filled: string[];
  unanswered: string[];
  conflicts: number;
  unanswerable: boolean;
}

export function refillMetadata(animeId: string): Promise<RefillResult> {
  return request<RefillResult>(`/api/anime/${animeId}/refill-metadata`, { method: "POST" });
}

/** One answer of the checklist (build 232): a code and its values, worded by the page. */
export interface SystemCheck {
  id: string;
  group: "setup" | "folders" | "data" | "services" | "activity";
  state: "ok" | "warning" | "error" | "skipped";
  code: string;
  values: Record<string, string>;
  /** A page address that deals with it (`#/settings/downloads`), or null. */
  fix: string | null;
  subject: string | null;
  /** Asked somebody else over the network: only run when a person presses the button. */
  live: boolean;
}

export interface SystemChecks {
  checks: SystemCheck[];
  errors: number;
  warnings: number;
  ran_at: string;
}

export function getSystemChecks(live = false): Promise<SystemChecks> {
  return request<SystemChecks>(`/api/system/checks${live ? "?live=true" : ""}`);
}

/** One AniDB entry an anime might be (build 229). */
export interface AnidbCandidate {
  aid: number;
  title: string;
  names: string[];
  /** The id list says this entry is one of the anime's own ids. */
  agrees: boolean;
  /** The id list gives this entry to another show (kind -> id); empty when it does not. */
  belongs_to: Record<string, string>;
  current: boolean;
}

export interface AnidbChoices {
  current: number | null;
  /** confirmed | unconfirmed | contradicted | unknown */
  state: "confirmed" | "unconfirmed" | "contradicted" | "unknown";
  reason: string;
  by: "list" | "you" | null;
  candidates: AnidbCandidate[];
}

export function getAnidbChoices(animeId: string): Promise<AnidbChoices> {
  return request<AnidbChoices>(`/api/anime/${animeId}/anidb`);
}

/** Store this AniDB id and record that a person checked it. */
export function chooseAnidb(animeId: string, anidbId: number): Promise<AnidbChoices> {
  return request<AnidbChoices>(`/api/anime/${animeId}/anidb`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ anidb_id: anidbId }),
  });
}

export function getAnimeHealth(animeId: string): Promise<
  Record<
    string,
    {
      colour: "green" | "yellow" | "red" | "grey";
      missing: string[];
      known: { field: string; value: string }[];
      not_applicable: string | null;
      notes?: string[];
    }
  >
> {
  return request(`/api/anime/${encodeURIComponent(animeId)}/health`);
}

/** One old file a swap left on disk (build 119). */
export interface LeftoverFile {
  id: string;
  filename: string;
  size_bytes: number | null;
  /** "renamed" - left for you by a swap; "awaiting_replacement" - still needed. */
  kind: string;
  episode_number: number | null;
}

export function listLeftovers(animeId: string): Promise<{ files: LeftoverFile[] }> {
  return request(`/api/anime/${encodeURIComponent(animeId)}/leftovers`);
}

/** Delete the renamed copies. Never the ones still being waited on. */
export function deleteLeftovers(
  animeId: string,
): Promise<{ deleted: number; failed: string[] }> {
  return request(`/api/anime/${encodeURIComponent(animeId)}/leftovers`, {
    method: "DELETE",
  });
}

/**
 * Ask Shoko whether it is there, as cheaply as it can be asked (build 118).
 *
 * One unauthenticated version request with a two-second patience, so the
 * status line can stop saying "not checked yet" on the first page load
 * instead of waiting for the worker's next tick.
 */
export function checkShoko(): Promise<{ state: string; detail: string | null }> {
  return request("/api/shoko/check", { method: "POST" });
}

/**
 * Take every series Shoko holds onto the watchlist, now (build 117).
 *
 * The worker does this by itself at startup and every half hour. This is the
 * other half of the answer: after adding something to Shoko, waiting half an
 * hour to see it is not the same as it working.
 *
 * Nothing is downloaded and no tracker is asked - adoption is about what is
 * already on disk.
 */
export function adoptShokoLibrary(): Promise<{
  considered: number;
  adopted: number;
  enriched: number;
  skipped: number;
  detail: string | null;
  names: string[];
  /** What could not be filled in, one line each (build 194). */
  problems: string[];
}> {
  return request("/api/shoko/adopt-all", { method: "POST" });
}

/**
 * Find what an entry is missing, attach it, and fetch it (build 117).
 *
 * Nothing is cleared and no episode that already has its file is touched -
 * which is the whole difference from changing the release, and why it needed
 * a route of its own rather than a flag on that one.
 */
/** What a match-only scan settled (build 123). */
export interface MatchOnlyResult {
  stored: string[];
  matched: number;
  errors: string[];
}

/**
 * Work out what a library was released as, and download nothing.
 *
 * Same payload as fixMissingEpisodes because it is the same four answers
 * from the same dialog; the difference is entirely in what the server does
 * afterwards, which here is nothing beyond writing them down.
 */
export function matchOnly(
  animeId: string,
  body: {
    search_query?: string | null;
    subgroup?: string | null;
    resolution?: string | null;
    edition?: string[];
    edition_not?: string[];
    scan_ids?: string[];
    release_name?: string | null;
    /** The indexer chosen in the dialog; kept on the anime for its later searches (item 44). */
    indexer_id?: string | null;
  },
): Promise<MatchOnlyResult> {
  return request<MatchOnlyResult>(`/api/anime/${animeId}/match-only`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function fixMissingEpisodes(
  animeId: string,
  body: {
    search_query?: string | null;
    subgroup?: string | null;
    resolution?: string | null;
    edition?: string[];
    edition_not?: string[];
    scan_ids?: string[];
    release_name?: string | null;
    queue?: boolean;
    /** A batch to take the missing episodes out of (build 213). */
    batch?: BatchChoice | null;
    /** The indexer chosen in the dialog; kept on the anime for its later searches (item 44). */
    indexer_id?: string | null;
  },
): Promise<{
  found: number;
  queued: number;
  skipped: number;
  reasons: Record<string, number>;
  errors: string[];
  /** What was taken out of a batch, and which episodes, or why not (build 213). */
  batch_queued?: number;
  batch_episodes?: number[];
  batch_error?: string | null;
}> {
  return request(`/api/anime/${encodeURIComponent(animeId)}/fix-missing`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Which batch torrent to read or take episodes from: the scan's tickets and the release's id (build 213). */
export interface BatchChoice {
  scan_ids: string[];
  release_id: string;
  /** Omitted: every missing episode the batch has. */
  episodes?: number[] | null;
  episode_offset?: number | null;
}

export interface BatchPreviewFile {
  index: number;
  path: string;
  name: string;
  size: number;
  episode: number | null;
  /** wanted | have | not_needed | beyond_count | extra | unreadable | ambiguous | not_video */
  status: string;
}

export interface BatchPreview {
  name: string;
  files: BatchPreviewFile[];
  /** The episodes this batch would give. */
  wanted: number[];
}

/** What is inside a batch torrent, and which of its files this anime is missing (build 213). */
export function getBatchFiles(animeId: string, choice: BatchChoice): Promise<BatchPreview> {
  return request(`/api/anime/${encodeURIComponent(animeId)}/batch-files`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(choice),
  });
}

export interface AnimeSetupChoice {
  subgroup: string | null;
  resolution: string | null;
  /** The template the subgroup/resolution came from; null for hand-typed values. */
  template_id: string | null;
  feed_source_id: string | null;
  search_query?: string | null;
  /**
   * How releases name this anime - the result the user picked. Kept apart from
   * the library folder, which stays renameable without breaking matching.
   */
  release_name?: string | null;
  /** Which copy of each episode the picked row was (build 97). */
  edition?: string[];
  /** What the other copies are recognised by, and this one is not (98). */
  edition_not?: string[];
  /**
   * Tickets for the scan the dialog already ran (build 100). With them the
   * server writes down the releases that were shown instead of asking the
   * tracker again; the magnets never cross the wire, only the ticket.
   */
  scan_ids?: string[];
  /** How far this group numbers past this season's episode 1 (112). */
  episode_offset?: number;
  /** AniDB's id for this season, so its own names are stored too (116). */
  anidb_id?: number | null;
  /** The user's corrected episode count (build 176). */
  episode_count?: number;
  /** The library it goes into (build 192); omitted means the default one. */
  library_id?: number | null;
  /**
   * The indexer the picked release came from (build 219). Kept on the anime so
   * its later searches ask the same site; an adult indexer marks it Adult.
   */
  indexer_id?: string | null;
  /**
   * Add this as a future release (build 121): every fact about the show, no
   * download configuration, and an offer to set the releases up once its
   * premiere has passed.
   */
  future?: boolean;
}

export function addAnime(candidate: AnimeSearchResult, localName: string, setup?: AnimeSetupChoice): Promise<AnimeSummary> {
  return request<AnimeSummary>("/api/anime", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider: candidate.provider,
      provider_id: candidate.provider_id,
      display_name: candidate.display_name,
      local_name: localName,
      lifecycle: "ACTIVE",
      // Everything the dialog decided, as it decided it. Spread rather than
      // re-listed: a field added to the package should reach the server
      // because it was added, not because two files were edited (build 116).
      ...(setup ?? {}),
    }),
  });
}

/** One title from one release group at one quality - a row you can pick. */
/** One search hit for the Release Inspector, with its full parse. */
export interface InspectedRelease {
  id: string;
  raw_name: string;
  size: number | null;
  seeders: number | null;
  published_at: string | null;
  /** Every field services/release_parser produced, or null when unreadable. */
  parsed: Record<string, unknown> | null;
  /** Why this hit could never reach a row. */
  skipped: string | null;
  /** Which search terms turned this up. */
  found_by: string[];
  /** What a library folder for this release would be called. */
  folder_name: string | null;
}

/** One term that was searched, and what asking it achieved. */
export interface InspectQuery {
  term: string;
  /** Rows the indexer handed back, duplicates included: two pages of 75 are 150. */
  found: number;
  /** How many of those were releases nothing had returned before. */
  new: number;
  /** How many requests were made for this term. */
  pages: number;
  error: string | null;
  /** How long this term took, in seconds. */
  seconds: number;
  /** What the site itself says this search has, when it says so at all. */
  site_total: number | null;
  /** True when the indexer re-sent a page it had already sent. */
  repeated: boolean;
  /** Every address this term was asked at, in order. */
  urls: string[];
}

export interface InspectSearch {
  releases: InspectedRelease[];
  queries: InspectQuery[];
  failures: string[];
  /** False when there is no indexer to search at all. */
  reached_provider: boolean;
  /** Whether the indexer's definition says where its search page is. */
  reads_listing: boolean;
  /** Whether it says where the site states its own result count. */
  reports_total: boolean;
  /** Which file said so, for the page to name. */
  definition_file: string | null;
}

/** One entry of a franchise, and where it sits in the numbering. */
export interface NumberingSeason {
  aid: number;
  title: string | null;
  kind: string | null;
  episode_count: number | null;
  absolute_start: number | null;
  /** True for the season that was asked about. */
  asked: boolean;
}

/**
 * Where one season's episode 1 sits in its franchise's numbering (build 112).
 *
 * `absolute_start` is 67 for Re:Zero season 4, because 25 + 13 + 12 + 16
 * episodes came before it - which is why SubsPlease's file called 67 is that
 * season's first.
 */
export interface Numbering {
  anidb_id: number;
  absolute_start: number | null;
  absolute_end: number | null;
  episode_count: number | null;
  /** pool | shoko | anidb | manual | none */
  source: string;
  detail: string | null;
  chain: NumberingSeason[];
}

/**
 * Work out where a season begins.
 *
 * Free when the pool or Shoko already knows. `fetch` lets Shoko pull the
 * entry from AniDB when it has never seen it - their client, their rate
 * limit, and nothing is added to anyone's library.
 */
export function resolveNumbering(anidbId: number, fetch = true): Promise<Numbering> {
  return request<Numbering>("/api/anime/numbering", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ anidb_id: anidbId, fetch }),
  });
}

/** Write down a start number somebody typed. Outranks every other source. */
export function saveNumbering(
  anidbId: number,
  absoluteStart: number,
  episodeCount?: number | null,
): Promise<Numbering> {
  return request<Numbering>("/api/anime/numbering/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      anidb_id: anidbId,
      absolute_start: absoluteStart,
      episode_count: episodeCount ?? null,
    }),
  });
}

/** One name a show goes by, and what kind of name it is. */
export interface InspectName {
  /** english | romaji | japanese | alternative | anidb | anilist | mal | kitsu | shikimori | clean */
  kind: string;
  value: string;
  /** True when this spelling only exists in the tracker's non-English category. */
  native: boolean;
}

/**
 * Every name AniFlow holds for one show (build 105).
 *
 * Costs nothing: the AniDB title comes from the dump already on disk and each
 * source's own title from the downloaded calendar, so selecting a show on the
 * inspector asks nobody anything.
 */
export function inspectNames(candidate: {
  title_english: string | null;
  title_romaji: string | null;
  title_native: string | null;
  aliases?: string[];
  provider_ids?: Record<string, string>;
}): Promise<{ names: InspectName[] }> {
  return request<{ names: InspectName[] }>("/api/anime/inspect-names", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title_english: candidate.title_english,
      title_romaji: candidate.title_romaji,
      title_native: candidate.title_native,
      aliases: candidate.aliases ?? [],
      provider_ids: candidate.provider_ids ?? {},
    }),
  });
}

/**
 * Search and keep everything, unfiltered and ungrouped.
 *
 * `extraTerms` turns one broad search into several narrow ones, merged - the
 * answer to a tracker's fixed page size. `native` searches the category a
 * site keeps its untranslated releases in, which is the only place a Japanese
 * title will ever match.
 *
 * `pages` left out means **every page the tracker has** (build 105). It used
 * to mean three, which is how a search the tracker answered with "1-75 out of
 * 383" showed 75.
 */
export function inspectSearch(
  query: string,
  options: {
    extraTerms?: string[];
    native?: boolean;
    pages?: number;
    /** A name the browser made up, so it can follow the search while it runs. */
    progressToken?: string;
    termIndex?: number;
    termCount?: number;
    /** Which indexer to ask; left out means the default one. */
    indexerId?: string | null;
  } = {},
): Promise<InspectSearch> {
  return request<InspectSearch>("/api/anime/search-inspect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      extra_terms: options.extraTerms ?? [],
      native: options.native ?? false,
      pages: options.pages ?? null,
      progress_token: options.progressToken ?? null,
      term_index: options.termIndex ?? 0,
      term_count: options.termCount ?? 0,
      indexer_id: options.indexerId ?? null,
    }),
  });
}

/** How far one search has got. Decoration: nothing depends on the answer. */
export interface SearchProgress {
  term: string;
  done: number;
  /** Zero while nothing knows yet - a spinner rather than a bar. */
  expected: number;
  term_index: number;
  term_count: number;
}

export function searchProgress(token: string): Promise<SearchProgress> {
  return request<SearchProgress>(
    `/api/anime/search-inspect/progress?token=${encodeURIComponent(token)}`,
  );
}

/** One file a selection scan looked at, and what it decided about it. */
export interface ScannedRelease {
  /** The search hit's own id, for asking about a batch's files (build 213). */
  id?: string | null;
  /** A whole-season torrent: a range in its name and no single episode. */
  batch?: boolean;
  raw_name: string;
  subgroup: string | null;
  resolution: string | null;
  season: number | null;
  /** As written in the name: "01", or "01-12" for a batch. */
  episode: string | null;
  /** The episode this file is, or null for a batch or a range. */
  number: number | null;
  version: number;
  crc32: string | null;
  folder_name: string | null;
  /**
   * Everything the name says about itself that no other field accounted for.
   * Which of these distinguish the copies is decided from the whole set -
   * see editions.ts.
   */
  tags: string[];
  size: number | null;
  seeders: number | null;
  accepted: boolean;
  /** unreadable | no-title | no-episode | other-show | profile */
  reason: string | null;
  /** What the profile objected to, in the evaluator's own words. */
  detail: string[];
  /** What the profile asks for and the name does not say, on a file accepted anyway (build 220). */
  warnings?: string[];
}

/** One name the scan searched under, and what asking it achieved. */
export interface ScanQuery {
  term: string;
  found: number;
  new: number;
  error: string | null;
}

export interface SelectionScan {
  /**
   * Tickets for what this scan found, one per name searched. Sent back when
   * the anime is added so the tracker is not asked twice for one answer -
   * the magnets never cross the wire, only the ticket.
   */
  scan_ids: string[];
  /** How far this group's numbering runs past this season's episode 1. */
  episode_offset?: number;
  releases: ScannedRelease[];
  /** The episode numbers that would be created, sorted. */
  episodes: number[];
  asked: ScanQuery[];
  pages: number;
  failures: string[];
  reached_provider: boolean;
  /** True when there is no indexer at all - not an error. */
  no_indexer: boolean;
  /** What the anime's stored profile also requires, beyond what the dialog asked (item 48). */
  stored_rules?: { kind: string; value: string }[];
}

/**
 * What one group has for one show, judged exactly as adding would judge it.
 *
 * The narrow question, as opposed to the broad preview: one name, one group,
 * one quality. That is the question whose answer fits inside a tracker's page,
 * and the same question the server asks when the anime is added - so the
 * number this returns is the number that gets stored.
 */
export function scanSelection(body: {
  queries: string[];
  reference: string;
  subgroup?: string | null;
  resolution?: string | null;
  /** Which copy of each episode, when the row names one. */
  edition?: readonly string[] | null;
  /** What the other copies are recognised by, which this one is not. */
  editionNot?: readonly string[] | null;
  animeId?: string | null;
  /** Which indexer to ask; left out means the default one. */
  indexerId?: string | null;
}): Promise<SelectionScan> {
  return request<SelectionScan>("/api/anime/scan-selection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      queries: body.queries,
      reference: body.reference,
      subgroup: body.subgroup ?? null,
      resolution: body.resolution ?? null,
      edition: [...(body.edition ?? [])],
      edition_not: [...(body.editionNot ?? [])],
      anime_id: body.animeId ?? null,
      indexer_id: body.indexerId ?? null,
    }),
  });
}

export interface TitleGroup {
  title: string;
  /** How many releases this combination has. */
  count: number;
  /** Null when the release name carries no group the parser could read. */
  subgroup: string | null;
  resolution: string | null;
}

/**
 * The same search, written out release by release as plain text.
 *
 * Returns text rather than JSON because it exists to be read by a person: the
 * counts in the dialog are only useful if they can be checked against what the
 * indexer actually returned.
 */
export async function searchPreviewDebug(payload: {
  title_english: string | null;
  title_romaji: string | null;
  title_native: string | null;
  subgroup: string | null;
  resolution: string | null;
  query?: string | null;
}): Promise<string> {
  const response = await fetch("/api/anime/search-preview/debug", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await extractError(response));
  return response.text();
}

export function previewTitleSearch(payload: {
  title_english: string | null;
  title_romaji: string | null;
  title_native: string | null;
  subgroup: string | null;
  resolution: string | null;
  query?: string | null;
}): Promise<{ groups: TitleGroup[]; indexer_error: string | null }> {
  return request("/api/anime/search-preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function getAnimeDbDump(id: string): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(`/api/anime/${id}/db-dump`);
}

export function getAnime(id: string): Promise<AnimeDetail> {
  return request<AnimeDetail>(`/api/anime/${id}`);
}

export function updateAnime(
  id: string,
  payload: {
    display_name?: string;
    /** How releases name this anime; what matching compares against. */
    release_name?: string;
    /** A folder name inside the media root - not a path. */
    media_folder?: string;
    /** A folder name inside the import root; empty clears it. */
    import_folder?: string;
    lifecycle?: string;
    drop_mode?: string;
    automation_enabled?: boolean;
    /** Mark it adult, or not (build 192). */
    is_adult?: boolean;
    /** Stop looking for releases of this one at all (build 123). */
    releases_ignored?: boolean;
    /** WINTER | SPRING | SUMMER | FALL, with the year; both or neither (item 17). */
    season?: string;
    season_year?: number;
    /** Give the season back to the calendar. */
    reset_season?: boolean;
    feed_source_id?: string | null;
    custom_feed_url?: string | null;
    /** The airing slot you assert: 0 = Monday, and the hour in 24h. Both or neither. */
    airs_weekday?: number;
    airs_hour?: number;
    /** Drop your slot and go back to whatever the provider says - possibly nothing. */
    reset_schedule?: boolean;
    episode_count?: number;
    reset_episode_count?: boolean;
    /** Facts no provider knew, typed in the Edit dialog (build 152). Empty
     *  clears one and hands it back to the providers. */
    title_romaji?: string;
    title_english?: string;
    title_native?: string;
    format?: string;
    /** YYYY-MM-DD, or null to clear. */
    air_start_date?: string | null;
    anidb_id?: number;
    /** Build 156: an empty list or synopsis clears it. */
    genres?: string[];
    studios?: string[];
    synopsis?: string;
  }
): Promise<AnimeSummary> {
  return request<AnimeSummary>(`/api/anime/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
}

/**
 * Give a tracker entry the release name it needs to be downloadable.
 *
 * Separate from updateAnime because it is not an edit: it changes what the
 * anime *is*, creates its episode rows and its profile, and is refused
 * outright while there is no indexer to check the name against.
 */
export function convertAnime(
  id: string,
  payload: {
    release_name: string;
    local_name?: string;
    subgroup?: string | null;
    resolution?: string | null;
    template_id?: string | null;
    feed_source_id?: string | null;
    edition?: string[];
    edition_not?: string[];
    episode_count?: number;
    library_id?: number;
    search_query?: string | null;
    scan_ids?: string[];
    episode_offset?: number;
    /** The indexer chosen in the dialog; kept on the anime for its later searches (item 44). */
    indexer_id?: string | null;
  }
): Promise<AnimeSummary> {
  return request<AnimeSummary>(`/api/anime/${id}/convert`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function connectRelease(animeId: string, rawName: string): Promise<{ connected: boolean; alias: string; decision: string; failures: string[] }> {
  return request(`/api/anime/${animeId}/connect-release`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ raw_name: rawName }),
  });
}

export interface FeedSearchItem {
  raw_name: string;
  torrent_url: string | null;
  decision: "MATCHED" | "REJECTED" | "IGNORED" | "SKIPPED" | "ATTENTION";
  subgroup: string | null;
  episode: string | null;
  version: number;
  resolution: string | null;
  media_source: string | null;
  failures: string[];
  version_action: string | null;
}

export function searchAnimeFeed(
  animeId: string,
  payload: { feed_source_id?: string; url?: string }
): Promise<{ items: FeedSearchItem[]; total: number; note?: string }> {
  return request(`/api/anime/${animeId}/feed-search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function queueRelease(
  animeId: string,
  payload: { raw_name: string; torrent_url: string; episode?: string | null; version?: number }
): Promise<{ id: string; status: string; attention_reason: string | null; error: string | null; label: string | null }> {
  return request(`/api/anime/${animeId}/queue-release`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export interface FolderLinkResult {
  selected_file: string;
  cleaned_filename: string;
  folder_path: string;
}

export interface FolderScanResult {
  folder_path: string;
  files_scanned: number;
  unparseable: number;
  episodes_found: number[];
  completed: number;
  vanished: number;
  created: number;
  max_episode: number;
}

export function linkAnimeFolder(animeId: string, rawName: string): Promise<FolderLinkResult> {
  return request(`/api/anime/${animeId}/link-folder`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ raw_name: rawName }),
  });
}

/** One video file in an anime's folder, for assigning it to an episode by hand (build 213). */
export interface FolderFile {
  filename: string;
  size: number | null;
  /** The episode it is recorded as now, if any. */
  episode: number | null;
  /** What a scan would make of its name, or null. Only a suggestion. */
  suggested: number | null;
}

export interface FolderFiles {
  folder: string;
  files: FolderFile[];
  episode_count: number | null;
  /** The episode numbers that already have a row. */
  episodes: number[];
}

export function listFolderFiles(animeId: string): Promise<FolderFiles> {
  return request(`/api/anime/${animeId}/folder-files`);
}

export function assignFolderFiles(
  animeId: string,
  assignments: { filename: string; episode: number | null }[],
): Promise<{ assigned: number; cleared: number }> {
  return request(`/api/anime/${animeId}/assign-files`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ assignments }),
  });
}

/** Fetch the episodes the AniDB list said you have too, and ask for a queue pass (item 36b). */
export function unskipEpisodes(animeId: string): Promise<{ cleared: number }> {
  return request(`/api/anime/${encodeURIComponent(animeId)}/unskip`, { method: "POST" });
}

export function scanAnimeFolder(animeId: string): Promise<FolderScanResult> {
  return request(`/api/anime/${animeId}/scan-folder`, { method: "POST" });
}

/** The URL an uploaded cover is served from. */
export function coverUrl(animeId: string): string {
  return `/api/anime/${animeId}/cover`;
}

/** Where the browser should load a provider's artwork from.
 *
 *  A provider's own address is never handed to the browser: that tells the
 *  provider who is looking. AniFlow fetches it once and serves it from here.
 *  An address that is already ours (an uploaded cover, calendar art) is left alone. */
export function providerCoverUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  return /^https?:\/\//i.test(url) ? `/api/cover?url=${encodeURIComponent(url)}` : url;
}

/** Upload artwork for one anime. The body is the file itself: one image needs
 *  no multipart form, and the server identifies the format from the bytes. */
export function uploadCover(animeId: string, file: File): Promise<AnimeSummary> {
  return request<AnimeSummary>(`/api/anime/${animeId}/cover`, {
    method: "PUT",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
  });
}

/** Drop the uploaded artwork and fall back to the provider's image. */
export function deleteCover(animeId: string): Promise<AnimeSummary> {
  return request<AnimeSummary>(`/api/anime/${animeId}/cover`, { method: "DELETE" });
}

/** How many of this anime's torrents AniFlow believes are still in the client. */
export function animeTorrents(id: string): Promise<{ count: number; examples: string[] }> {
  return request(`/api/anime/${id}/torrents`);
}

export async function deleteAnime(id: string, options: { removeTorrents?: boolean } = {}): Promise<void> {
  // Opt-in, and asked separately: removing the anime and removing its
  // torrents are two decisions, and the second one reaches outside AniFlow.
  const query = options.removeTorrents ? "?confirm=true&remove_torrents=true" : "?confirm=true";
  const response = await fetch(`/api/anime/${id}${query}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await response.text());
}

/**
 * Why an episode was not queued, as codes the browser puts words to.
 *
 * `already_owned` - the episode is COMPLETE and has its file.
 * `in_flight` - a download for it is queued or running.
 * `importing` - it has finished downloading and is on its way in.
 * `no_release` - nothing known to download.
 *
 * Added in build 117, where "Nothing to queue - this episode is already owned
 * or downloading" was the only answer available and, for the reported case,
 * neither of the two things it named.
 */
export type QueueSkipReason = "already_owned" | "in_flight" | "importing" | "no_release";

export function queueAvailable(animeId: string, payload: { episode?: number | null; all_available?: boolean }): Promise<{ queued: number; skipped: number; errors: string[]; reasons?: Partial<Record<QueueSkipReason, number>> }> {
  return request(`/api/anime/${animeId}/queue-available`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/** Fetch one season again now, ignoring the server's cache. */
/**
 * Update this season and the two after it.
 *
 * One action. Which sources are asked, which answered and which refused is in
 * the log, not in a choice anybody has to make first.
 */
/**
 * What one press of Update should fetch (build 148).
 *
 * `season` is the season named in the call and nothing else. The other two
 * are measured from the season airing now, whatever is on screen: they answer
 * "fill the calendar in", which is not a question about where somebody has
 * navigated to.
 */
export type CalendarUpdateScope = "season" | "upcoming" | "initial";

export function updateCalendar(
  year?: number,
  season?: string,
  scope: CalendarUpdateScope = "season",
): Promise<CalendarStatus> {
  const parameters = new URLSearchParams();
  if (year && season) {
    parameters.set("year", String(year));
    parameters.set("season", season);
  }
  parameters.set("scope", scope);
  return request<CalendarStatus>(`/api/calendar/update?${parameters}`, { method: "POST" });
}

export function calendarStatus(): Promise<CalendarStatus> {
  return request<CalendarStatus>("/api/calendar/status");
}

/** What a reset would destroy. Counted, so the warning can be specific. */
export function calendarResetPreview(): Promise<CalendarResetPreview> {
  return request<CalendarResetPreview>("/api/calendar/reset-preview");
}

/** Clear what providers can hand back; keep every calendar id. */
export function rebuildCalendar(): Promise<{ done: string; kept_ids: number }> {
  return request("/api/calendar/rebuild", { method: "POST" });
}

/** Delete the calendar database. Breaks every stored calendar id. */
export function resetCalendar(): Promise<{ done: string }> {
  return request("/api/calendar/reset?confirm=true", { method: "POST" });
}

export function refreshCalendar(year?: number, season?: string): Promise<CalendarStatus> {
  const query = year && season ? `?year=${year}&season=${encodeURIComponent(season)}` : "";
  return request<CalendarStatus>(`/api/calendar/refresh${query}`, { method: "POST" });
}

export function getCalendar(year?: number, season?: string): Promise<CalendarSeason> {
  const parameters = new URLSearchParams();
  if (year) parameters.set("year", String(year));
  if (season) parameters.set("season", season);
  return request<CalendarSeason>(`/api/calendar?${parameters}`);
}

export function getProfile(animeId: string): Promise<ReleaseProfile | null> {
  return request<ReleaseProfile | null>(`/api/anime/${animeId}/profile`);
}

export async function saveProfile(animeId: string, draft: ReleaseProfileDraft): Promise<ReleaseProfile> {
  const response = await fetch(`/api/anime/${animeId}/profile`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<ReleaseProfile>;
}

export async function deleteProfile(animeId: string): Promise<void> {
  const response = await fetch(`/api/anime/${animeId}/profile`, { method: "DELETE" });
  if (!response.ok) throw new Error(await response.text());
}

export function testRelease(animeId: string, releaseName: string): Promise<ProfileTestResult> {
  return request<ProfileTestResult>(`/api/anime/${animeId}/profile/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ release_name: releaseName }),
  });
}

/** One folder in the library, and how much it looks like an anime (139). */
export interface FolderCandidate {
  name: string;
  /** 0-100. 100 is an exact match on one of the anime's names. */
  score: number;
  /** Which name it matched, so the score is explainable. */
  matched: string | null;
  files: number;
  episodes: number;
}

export interface LibraryFilePlan {
  filename: string;
  episode: number | null;
  placed: boolean;
  reason: string | null;
  quality: string | null;
  subgroup: string | null;
}

export interface LibraryPreview {
  folder: string;
  plans: LibraryFilePlan[];
  placeable: number;
  unplaceable: number;
  subgroup: string | null;
  quality: string | null;
  release_name: string | null;
}

/** The library's folders, the ones that look like this anime first. */
export function libraryFolders(animeId: string, all = false): Promise<FolderCandidate[]> {
  return request<FolderCandidate[]>(
    `/api/anime/${animeId}/library/folders${all ? "?all=true" : ""}`,
  );
}

/** What linking a folder would do. Writes nothing. */
export function libraryPreview(animeId: string, folder: string): Promise<LibraryPreview> {
  return request<LibraryPreview>(
    `/api/anime/${animeId}/library/preview?folder=${encodeURIComponent(folder)}`,
  );
}

/** Record the folder and its files against this anime. */
export function linkLibraryFolder(
  animeId: string,
  folder: string,
): Promise<{ folder: string; placed: number; unplaced: number }> {
  return request(`/api/anime/${animeId}/library/link`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder }),
  });
}

/** Forget the folder and its files. Nothing on disk is touched. */
export async function unlinkLibraryFolder(animeId: string): Promise<void> {
  await request(`/api/anime/${animeId}/library/link`, { method: "DELETE" });
}

export function listSources(): Promise<FeedSource[]> {
  return request<FeedSource[]>("/api/rss/sources");
}

export function createSource(name: string, url: string): Promise<FeedSource> {
  return request<FeedSource>("/api/rss/sources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, url }),
  });
}

export function updateSource(id: string, payload: { name?: string; url?: string; enabled?: boolean; is_default?: boolean }): Promise<FeedSource> {
  return request<FeedSource>(`/api/rss/sources/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteSource(id: string): Promise<void> {
  const response = await fetch(`/api/rss/sources/${id}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await response.text());
}

export function triggerScan(): Promise<{ started: boolean }> {
  return request<{ started: boolean }>("/api/rss/scan", { method: "POST" });
}

export function getRssStatus(): Promise<RssStatus> {
  return request<RssStatus>("/api/rss/status");
}

/** One row on the Schedule page - see backend/app/schemas/schedule.py. */
export interface ScheduleTask {
  id: string;
  category: string;
  /** False only for the one item nobody has built yet. */
  implemented: boolean;
  /** Whether the Schedule page offers a "run now" trigger. */
  manual: boolean;
  enabled: boolean;
  /** "no_indexer" | "no_qbittorrent" | null - why this task can't do
   *  anything right now even if enabled (nothing to search or sync). */
  blocked_reason: string | null;
  /** Always seconds, even for rss_scan. Null with no independent schedule. */
  interval_seconds: number | null;
  /** The AppSettingsView field names this task's own editor reads and
   *  writes - null when there is nothing to edit (see interval_setting). */
  enabled_setting: string | null;
  /** Null means: this task has no independent schedule at all, and its row
   *  offers no editor - it is a "Run" button and nothing else. */
  interval_setting: string | null;
  /** "seconds" | "minutes" - which unit `interval_setting` expects back. */
  interval_unit: string;
  running: boolean;
  requested: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  last_result: Record<string, unknown> | null;
  last_error: string | null;
}

export function listScheduleTasks(): Promise<ScheduleTask[]> {
  return request<ScheduleTask[]>("/api/schedule/tasks");
}

export function runScheduleTask(id: string): Promise<{ started: boolean }> {
  return request<{ started: boolean }>(`/api/schedule/tasks/${id}/run`, { method: "POST" });
}

/** Puts this task's own switch and interval back to the environment's
 *  default - see backend/app/api/routes/schedule.py::reset_task. */
export function resetScheduleTask(id: string): Promise<ScheduleTask> {
  return request<ScheduleTask>(`/api/schedule/tasks/${id}/reset`, { method: "POST" });
}

/** `tracked` filters the noise: true = watchlist only, false = the rest. */
export function listReleases(limit = 100, tracked?: boolean, decision?: string): Promise<ReleaseItem[]> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (tracked !== undefined) query.set("tracked", String(tracked));
  if (decision !== undefined) query.set("decision", decision);
  return request<ReleaseItem[]>(`/api/rss/releases?${query.toString()}`);
}

/** Tidy the discovery list. The rows stay as the scan's guid memory. */
export function clearReleases(): Promise<{ cleared: number }> {
  return request<{ cleared: number }>("/api/rss/releases", { method: "DELETE" });
}

export function triggerQueueing(): Promise<{ started: boolean }> {
  return request<{ started: boolean }>("/api/downloads/queue", { method: "POST" });
}

export function triggerImport(): Promise<{ started: boolean }> {
  return request<{ started: boolean }>("/api/downloads/import", { method: "POST" });
}

/** Drop the torrent entry from qBittorrent as well - never its files. */
export function removeTorrentEntry(id: string): Promise<{ outcome: string }> {
  return request<{ outcome: string }>(`/api/downloads/${id}/remove-torrent`, { method: "POST" });
}

/**
 * Every kind the server can send. models/activity.py:AttentionKind.
 *
 * Had fallen four kinds behind by build 123 - the four added since it was
 * written - which is invisible until something compares against one of them
 * and TypeScript says the two have no overlap. Kept complete because that
 * comparison is how the page decides which buttons a record gets.
 */
export type AttentionKind =
  | "NEW_VERSION"
  | "AMBIGUOUS_RELEASE"
  | "IMPORT_BLOCKED"
  | "DOWNLOAD_MISSING"
  | "FEED_FAILED"
  | "NEEDS_CONVERSION"
  | "NO_RELEASES"
  | "DOWNLOAD_STALLED"
  | "METADATA_CHANGED"
  | "EPISODE_NOT_IDENTIFIABLE";

export interface AttentionItem {
  id: string;
  kind: AttentionKind;
  status: "OPEN" | "RESOLVED" | "DISMISSED";
  anime_id: string | null;
  release_id: string | null;
  download_id: string | null;
  feed_source_id: string | null;
  anime_name: string | null;
  feed_name: string | null;
  /** Which episode, when the record is about a download. */
  episode_number: number | null;
  /** The release that was queued - the name the torrent client knows - or,
   *  before there is a download to ask, the release's own raw name. */
  release_label: string | null;
  /** The episode as AniFlow read it off that name - "01-12" for a batch. */
  episode_label: string | null;
  /** Which resolutions this record offers, decided by the server. */
  actions: string[];
  /** For a duplicate: the downloaded file, and the one already in the library. */
  download_file: string | null;
  library_file: string | null;
  /** How many further warnings about this same episode this row stands for. */
  duplicates: number;
  detail: string | null;
  note: string | null;
  created_at: string;
  resolved_at: string | null;
}

export type ActivityLevel = "routine" | "notable" | "problem";

export interface ActivityItem {
  id: string;
  kind: string;
  /** How much attention it deserves; derived from the kind and its counters. */
  level: ActivityLevel;
  message: string;
  details: Record<string, unknown>;
  anime_id: string | null;
  anime_name: string | null;
  created_at: string;
}

export function listAttention(status: "OPEN" | "ALL" = "OPEN"): Promise<AttentionItem[]> {
  return request<AttentionItem[]>(`/api/activity/attention?status_filter=${status}`);
}

export function deleteDownloadedFile(id: string): Promise<AttentionItem> {
  return request<AttentionItem>(`/api/activity/attention/${id}/delete-download`, { method: "POST" });
}

/** Delete one file next to an episode's own - a backup, a kept original, an older version (189). */
export async function deleteEpisodeCopy(animeId: string, episodeId: string, copyId: string): Promise<void> {
  const response = await fetch(`/api/anime/${animeId}/episodes/${episodeId}/copies/${copyId}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await extractError(response));
}

/** Two files for one episode: delete the older copy, keep the episode's own (build 189). */
export function deleteOlderFile(id: string): Promise<AttentionItem> {
  return request<AttentionItem>(`/api/activity/attention/${id}/delete-older`, { method: "POST" });
}

/** Two files for one episode: delete the episode's file, keep the older one. */
export function deleteNewerFile(id: string): Promise<AttentionItem> {
  return request<AttentionItem>(`/api/activity/attention/${id}/delete-newer`, { method: "POST" });
}

/** Keep the downloaded file: remove the library's copy and import the new one. */
export function replaceLibraryFile(id: string): Promise<AttentionItem> {
  return request<AttentionItem>(`/api/activity/attention/${id}/replace-library`, { method: "POST" });
}

/** Hand a downloaded file that is waiting back to the import pass. */
export function moveFileNow(id: string): Promise<AttentionItem> {
  return request<AttentionItem>(`/api/activity/attention/${id}/move-now`, { method: "POST" });
}

/** Hand a missing torrent to the client again, without waiting for the
 *  automatic queue pass. */
export function retryDownloadNow(id: string): Promise<AttentionItem> {
  return request<AttentionItem>(`/api/activity/attention/${id}/retry-now`, { method: "POST" });
}

export function dismissAttention(id: string, note?: string): Promise<AttentionItem> {
  return request<AttentionItem>(`/api/activity/attention/${id}/dismiss`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ note: note ?? null }),
  });
}

/**
 * Dismiss every open attention record (build 120).
 *
 * The endpoint has existed since build 94; until now nothing pressed it.
 * Dismissing changes nothing about episodes, downloads or files - anything
 * whose cause is still there is raised again on the next pass.
 */
export function dismissAllAttention(note?: string): Promise<{ dismissed: number }> {
  return request("/api/activity/attention/dismiss-all", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ note: note ?? null }),
  });
}

/** Take the provider's value and drop the override that disagreed (123). */
export function acceptMetadataChange(id: string): Promise<AttentionItem> {
  return request<AttentionItem>(`/api/activity/attention/${id}/accept-metadata`, { method: "POST" });
}

/**
 * One attention record per kind and one activity entry per kind (build 126).
 *
 * Most of them fire only when something specific goes wrong on a live
 * deployment, so most have never been rendered - which is how three activity
 * kinds kept no translation for six builds. Debug mode only; the server
 * refuses otherwise. See backend services/debug_samples.py for what makes
 * them safe: no anime, episode or download is ever invented.
 */
export interface SampleCounts {
  attention: number;
  activity: number;
  removed: number;
}

export function seedDebugSamples(): Promise<SampleCounts> {
  return request<SampleCounts>("/api/activity/debug/samples", { method: "POST" });
}

export function clearDebugSamples(): Promise<SampleCounts> {
  return request<SampleCounts>("/api/activity/debug/samples", { method: "DELETE" });
}

export function countDebugSamples(): Promise<SampleCounts> {
  return request<SampleCounts>("/api/activity/debug/samples");
}

export function approveNewVersion(id: string): Promise<AttentionItem> {
  return request<AttentionItem>(`/api/activity/attention/${id}/approve-download`, { method: "POST" });
}

export function assignAnimeToRelease(id: string, animeId: string): Promise<{ matched: boolean; failures: string[] }> {
  return request(`/api/activity/attention/${id}/assign-anime`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ anime_id: animeId }),
  });
}

/** `level` keeps only entries at least that important: routine < notable < problem. */
export function listActivity(limit = 100, level?: ActivityLevel): Promise<ActivityItem[]> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (level) query.set("level", level);
  return request<ActivityItem[]>(`/api/activity?${query.toString()}`);
}

/** Empty the activity log. History only - nothing else is affected. */
export function clearActivity(): Promise<{ deleted: number }> {
  return request<{ deleted: number }>("/api/activity", { method: "DELETE" });
}

/** One step in a progress item's full history (build 165) - every real
 *  change in the episode's own state, not just the curated Activity kinds. */
export interface ProgressHistoryEntry {
  /** Both are one of services/episode_view.py's STATES; null for
   *  `from_state` on the very first step an episode ever had. */
  from_state: string | null;
  to_state: string;
  created_at: string;
}

/**
 * One episode currently moving through the pipeline, or stuck on something
 * (build 164). The one list meant to replace checking the watchlist, Downloads
 * and Attention separately for "where is this episode right now".
 */
export interface ProgressItem {
  episode_id: string;
  anime_id: string;
  anime_name: string;
  episode_number: number;
  /** One of services/episode_view.py's STATES - the same word the watchlist
   *  reads for this episode. */
  state: string;
  detail: string | null;
  progress: number | null;
  /** When the episode entered this state, for "how long has this been like
   *  this" - null when nothing says. */
  since: string | null;
  /** When this episode was archived - only ever set on an archive listing. */
  archived_at: string | null;
  /** The release AniFlow queued, when it has one. */
  release_label: string | null;
  /** Which buttons this card offers - the same ids Attention rows use. */
  actions: string[];
  /** The Attention record these actions belong to, when there is one - acted
   *  on through the existing `/api/activity/attention/{id}/...` endpoints. */
  attention_id: string | null;
  /** The attention's own kind (models.activity.AttentionKind), for reading
   *  attentionRemedy.ts's "this fixes itself" wording. */
  attention_kind: string | null;
  /** The two files a duplicate is about - null whenever there is nothing to
   *  delete or replace. The same pair the Attention page names, carried here
   *  too (167) so a duplicate hidden from that page for having a card here
   *  does not lose the detail its Delete/Replace buttons need. */
  download_file: string | null;
  library_file: string | null;
  /** For two files for one episode (189): the older copies, by name. */
  older_files?: string[];
  history: ProgressHistoryEntry[];
}

export function listProgress(): Promise<ProgressItem[]> {
  return request<ProgressItem[]>("/api/progress");
}

/** Episodes that have left the active list - archived automatically on a
 *  clean finish, or by hand from a stuck one (build 165). */
export function listProgressArchive(): Promise<ProgressItem[]> {
  return request<ProgressItem[]>("/api/progress/archive");
}

/** Send a stuck episode to the Archive by hand; it comes back to the active
 *  list on its own if something about it genuinely changes again. */
/** Have Shoko Server run its import now (build 178). */
export function runShokoImport(): Promise<{ started: boolean; detail: string | null }> {
  return request<{ started: boolean; detail: string | null }>("/api/shoko/run-import", { method: "POST" });
}

/** Put one Shoko series on the watchlist (build 243). */
export function addShokoSeriesToWatchlist(shokoId: number): Promise<{ added: boolean; anime_id: string | null; detail: string | null }> {
  return request(`/api/shoko/series/${shokoId}/add-to-watchlist`, { method: "POST" });
}

/** AniDB's My List filter box (build 242); the defaults are the set the first reference list was made with. */
export interface MylistFilters {
  adult: "ignore" | "hide" | "only";
  airing: "ignore" | "airing" | "finished" | "unaired";
  status: "ignore" | "complete" | "incomplete";
  /** Empty means every type. */
  types: string[];
}

export const MYLIST_TYPES = ["movie", "musicvideo", "other", "ova", "tv", "tvspecial", "unknown", "web"] as const;

export const DEFAULT_MYLIST_FILTERS: MylistFilters = { adult: "hide", airing: "finished", status: "incomplete", types: [] };

export interface MylistRow {
  aid: number;
  title: string;
  english: string | null;
  year: string;
  type: string;
  held: number;
  total: number;
  specials_total: number;
  held_specials: number;
  /** finished | airing | unaired - estimated: the export carries no end date. */
  airing: string;
  /** The watchlist entry for it, when there already is one. */
  watchlist_id: string | null;
}

export interface MylistAnswer {
  items: MylistRow[];
  /** False until an export has been uploaded. */
  has_export: boolean;
  uploaded_at: string | null;
  filename: string | null;
  considered: number;
  files_read: boolean;
}

/** AniDB's My List filters over the export the user uploaded (build 244). */
export function getShokoAnidbMylist(filters: MylistFilters): Promise<MylistAnswer> {
  const query = new URLSearchParams({
    adult: filters.adult,
    airing: filters.airing,
    status: filters.status,
    types: filters.types.join(","),
  });
  return request<MylistAnswer>(`/api/shoko/anidb-mylist?${query.toString()}`);
}

/** Put one anime of the AniDB My List on the watchlist so AniFlow can complete it (build 245). */
export function addMylistEntryToWatchlist(aid: number): Promise<{ added: boolean; anime_id: string | null; detail: string | null }> {
  return request(`/api/shoko/anidb-mylist/${aid}/add-to-watchlist`, { method: "POST" });
}

/** Hand AniFlow the My List export from AniDB's Export page - the raw .tgz as the body (build 244). */
export function uploadAnidbExport(file: File): Promise<MylistAnswer> {
  return request<MylistAnswer>("/api/shoko/anidb-export", {
    method: "POST",
    headers: { "Content-Type": "application/gzip", "X-Filename": encodeURIComponent(file.name) },
    body: file,
  });
}

/** Which build this is and how it is set up (build 262): the Settings page's About panel. */
export interface AppAbout {
  version: string;
  build: number;
  build_string: string;
  environment: string;
  storage_mode: string;
  app_mode: string;
  debug_mode: boolean;
  python: string;
  platform: string;
  docker: boolean;
  timezone: string;
  server_time: string;
  started_at: string;
  uptime_seconds: number;
  worker_running: boolean;
  database: { current: string | null; head: string | null; up_to_date: boolean };
  folders: { name: string; container: string | null; host: string | null; exists: boolean; writable: boolean; free_bytes: number | null }[];
}

export function getAbout(): Promise<AppAbout> {
  return request<AppAbout>("/api/system/about");
}

/** What was uploaded, without the list (build 258). */
export interface AnidbExportStatus {
  has_export: boolean;
  uploaded_at: string | null;
  filename: string | null;
  considered: number;
  /** False for an upload made before the files section was read: upload the export again. */
  files_read: boolean;
}

export function getAnidbExportStatus(): Promise<AnidbExportStatus> {
  return request<AnidbExportStatus>("/api/anidb/export");
}

/** The three questions the AniDB page's other tabs ask of the export's files section (build 258). */
export type AnidbProblemKind = "unwatched" | "unknown" | "groups";

export interface AnidbProblemRow {
  aid: number;
  title: string;
  english: string | null;
  year: string;
  type: string;
  held: number;
  total: number;
  count: number;
  /** The regular episodes concerned, and how many specials were. */
  episodes: number[];
  specials: number;
  /** Files on the disk per release group; AniDB's id, the export has no names. Only for kind "groups". */
  groups: { id: string; files: number }[];
  watchlist_id: string | null;
}

export interface AnidbProblems {
  items: AnidbProblemRow[];
  has_export: boolean;
  files_read: boolean;
  uploaded_at: string | null;
  considered: number;
}

export function getAnidbProblems(kind: AnidbProblemKind, adult: string, doubled: boolean): Promise<AnidbProblems> {
  const query = new URLSearchParams({ kind, adult, doubled: String(doubled) });
  return request<AnidbProblems>(`/api/anidb/problems?${query.toString()}`);
}

export function dismissProgress(episodeId: string): Promise<{ dismissed: boolean }> {
  return request<{ dismissed: boolean }>(`/api/progress/${episodeId}/dismiss`, { method: "POST" });
}

export interface AnalyzeCandidate {
  anime_id: string;
  name: string;
  score: number;
  confidence: number;
  classification: string;
  reasons: string[];
}

export interface AnalyzeItem {
  raw_name: string;
  torrent_url: string | null;
  crc32: string | null;
  season: number | null;
  episode: string | null;
  version: number;
  resolution: string | null;
  subgroup: string | null;
  media_source: string | null;
  source_name: string;
  classification: "EXACT" | "HIGH" | "REVIEW" | "UNKNOWN";
  confidence: number;
  best_anime_id: string | null;
  best_name: string | null;
  candidates: AnalyzeCandidate[];
}

export interface AnalyzeRunResult {
  run_id: string;
  found: number;
  exact: number;
  high: number;
  review: number;
  unknown: number;
  duration_ms: number;
  items: AnalyzeItem[];
}

export function runAnalyze(sourceIds: string[], url?: string): Promise<AnalyzeRunResult> {
  return request<AnalyzeRunResult>("/api/analyze/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_ids: sourceIds, url: url ?? null }),
  });
}

export function learnAlias(animeId: string, rawName: string): Promise<{ alias: string }> {
  return request("/api/analyze/learn", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ anime_id: animeId, raw_name: rawName }),
  });
}

export interface ProfileTemplate {
  id: string;
  name: string;
  subgroups: string[];
  resolution: string | null;
  media_source: string | null;
  censor_preference: "ANY" | "UNCENSORED" | "CENSORED";
  require_title_match: boolean;
  require_resolution_known: boolean;
  require_source_known: boolean;
  require_censor_known: boolean;
  anime_count: number;
  created_at: string;
  updated_at: string;
  is_default: boolean;
}

export interface ProfileTemplateDraft {
  name: string;
  subgroups: string[];
  resolution: string | null;
  media_source: string | null;
  censor_preference: "ANY" | "UNCENSORED" | "CENSORED";
  require_title_match: boolean;
  require_resolution_known: boolean;
  require_source_known: boolean;
  require_censor_known: boolean;
  is_default: boolean;
}

export function listTemplates(): Promise<ProfileTemplate[]> {
  return request<ProfileTemplate[]>("/api/profiles");
}

export function createTemplate(draft: ProfileTemplateDraft): Promise<ProfileTemplate> {
  return request<ProfileTemplate>("/api/profiles", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
  });
}

export function updateTemplate(id: string, draft: ProfileTemplateDraft): Promise<ProfileTemplate> {
  return request<ProfileTemplate>(`/api/profiles/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
  });
}

export async function deleteTemplate(id: string): Promise<void> {
  const response = await fetch(`/api/profiles/${id}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await response.text());
}

export function applyTemplateToAnime(animeId: string, templateId: string | null): Promise<{ applied: boolean; template_name: string | null }> {
  return request(`/api/profiles/apply/${animeId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ template_id: templateId }),
  });
}

/** One feed a profile test read, and what came of reading it. */
export interface ProfileTestFeed {
  name: string;
  items: number;
  error: string | null;
}

/** One subgroup a profile names, and whether the feeds carry it. */
export interface ProfileTestGroup {
  name: string;
  items: number;
  resolutions: string[];
  example: string | null;
}

export interface ProfileTestResult {
  template_id: string;
  name: string;
  /** ok | no-group | no-match */
  verdict: string;
  accepted: number;
  considered: number;
  groups: ProfileTestGroup[];
  examples: string[];
  /** The rules that threw out the most, worst first. */
  top_failures: [string, number][];
}

export interface ProfileTestReport {
  feeds: ProfileTestFeed[];
  results: ProfileTestResult[];
  items: number;
  /** False when not one feed could be read - then every count means nothing. */
  reached_any_feed: boolean;
}

/**
 * Does this profile match anything the feeds are carrying right now?
 *
 * Omit the id to test every profile, which reads the feeds once for all of
 * them rather than once each.
 */
export function testProfiles(templateId?: string): Promise<ProfileTestReport> {
  const path = templateId ? `/api/profiles/${templateId}/test` : "/api/profiles/test";
  return request<ProfileTestReport>(path, { method: "POST" });
}

export interface AppSettingsView {
  /** Which client; the address, username and password below belong to whichever it is. */
  torrent_client: "qbittorrent" | "deluge" | "transmission";
  qb_url: string | null;
  qb_username: string;
  qb_category: string;
  has_qb_password: boolean;
  /**
   * Read-only. Set by ANIFLOW_MODE in docker-compose and recorded in the
   * database on first run: standalone means AniFlow owns the library,
   * shokoserver means Shoko Server does. Changing it on an existing library
   * is refused, so the page reports it rather than offering it.
   */
  mode: StorageMode;
  /**
   * Whether the developer tools are offered: the Debug tab, the Release
   * Inspector, the repair and record views. Stored in aniflow.yaml, and
   * editable from Settings since build 94.
   */
  debug_mode: boolean;
  /** Which look the interface uses. One exists; build 90 adds the rest. */
  theme: string;
  /** The hand-over folder, in Shoko Server mode. Null in standalone mode. */
  import_root: string | null;
  /** The storage roots, with the host folder behind each container mount. */
  storage_paths: StoragePath[];
  /** copy keeps the download seedable; move relocates the file. */
  file_operation: "copy" | "move" | "hardlink";
  after_import_action: "pause" | "remove" | "keep";
  /** What a release change does with the files you already have. */
  reacquire_mode: "backup" | "replace" | "when_replaced" | "keep";
  calendar_hide_in_library: boolean;
  calendar_hide_adult: boolean;
  /** Hide the shows no source had artwork for (build 86). */
  calendar_hide_no_cover: boolean;
  /** Kinds the calendar leaves out, comma-separated and upper case. */
  calendar_hidden_kinds: string;
  calendar_mark_sequels: boolean;
  calendar_show_popularity: boolean;
  /** airing | name | popularity. */
  calendar_sort: string;
  /** How this installation writes a time and a date. */
  time_format: string;
  date_format: string;
  rss_enabled: boolean;
  rss_scan_interval_minutes: number;
  torrent_monitor_interval_seconds: number;
  /** Whether the calendar may update itself - see the Schedule page. */
  calendar_auto_update: boolean;
  /** Everything below is the Scheduling feature: every other background
   *  pass, with its own switch and interval in seconds. See the Schedule
   *  page for what each one actually does. Shoko library adoption and
   *  filling in metadata/schedules are manual only (buttons on the Shoko
   *  Server page and the Watchlist), so they have no switch or interval. */
  torrent_monitor_enabled: boolean;
  anidb_identity_refresh_enabled: boolean;
  anidb_identity_refresh_interval_seconds: number;
  shoko_status_enabled: boolean;
  shoko_status_interval_seconds: number;
  torrent_housekeeping_enabled: boolean;
  torrent_housekeeping_interval_seconds: number;
  attention_review_enabled: boolean;
  attention_review_interval_seconds: number;
  calendar_auto_update_interval_seconds: number;
  conversion_review_enabled: boolean;
  conversion_review_interval_seconds: number;
  disk_recheck_enabled: boolean;
  disk_recheck_interval_seconds: number;
  language: "en" | "de";
  /** IANA name, or empty to follow the container's TZ variable. */
  timezone: string;
  /** none | http | socks4 | socks5 | socks5h - internet-facing requests only. */
  /** The one metadata source AniFlow asks. */
  metadata_provider: "anilist" | "kitsu";
  /** Which corner short notifications appear in. */
  toast_corner: "top-right" | "top-left" | "bottom-right" | "bottom-left";
  /** What happens when a season changes: ask | auto | off (build 205). */
  season_rollover: "ask" | "auto" | "off";
  /** Whether a new anime starts with Automate on (build 239). */
  automate_new_anime: boolean;
  /** Shoko Server's API, for reading what Shoko knows. Optional. */
  shoko_url: string;
  has_shoko_api_key: boolean;
  /** "" = decide from the indexers; a testing aid, and temporary. */
  app_mode_override: "" | "basic" | "advanced";
  /** Read-only: the mode actually in force, after the rule and the override. */
  app_mode: "basic" | "advanced";
  /** Read-only: whether anything is configured that could find a release. */
  has_indexer: boolean;
  proxy_kind: "none" | "http" | "socks4" | "socks5" | "socks5h";
  proxy_host: string;
  proxy_port: number | null;
  proxy_username: string;
  has_proxy_password: boolean;
  proxy_check_url: string;
  /** How much is written to the log. Applies to the running app. */
  log_level: "ERROR" | "WARNING" | "INFO" | "DEBUG";
  /** Preferred titles, comma-separated, most-wanted first. */
  title_search_order: string;
  title_folder_order: string;
  /** What the Calendar and the Watchlist call an anime. Display only. */
  title_display_order: string;
  /** The zone actually in force - TZ when the setting is empty. */
  effective_timezone: string;
  /** Set when the configured name could not be resolved, so times are UTC. */
  timezone_unresolved: string | null;
  anime_library_root: string;
  download_root: string;
  /** Where these settings are stored; editable by hand. */
  config_file: string;
}

export interface SettingsUpdatePayload {
  timezone?: string;
  torrent_client?: "qbittorrent" | "deluge" | "transmission";
  qb_url?: string | null;
  qb_username?: string;
  qb_password?: string | null;
  qb_category?: string;
  file_operation?: "copy" | "move" | "hardlink";
  after_import_action?: "pause" | "remove" | "keep";
  /** Saved by the release-change panel itself, not by the Settings page. */
  reacquire_mode?: "backup" | "replace" | "when_replaced" | "keep";
  calendar_hide_in_library?: boolean;
  calendar_hide_adult?: boolean;
  calendar_hide_no_cover?: boolean;
  calendar_hidden_kinds?: string;
  calendar_mark_sequels?: boolean;
  calendar_show_popularity?: boolean;
  calendar_sort?: string;
  time_format?: string;
  date_format?: string;
  rss_enabled?: boolean;
  rss_scan_interval_minutes?: number;
  torrent_monitor_interval_seconds?: number;
  calendar_auto_update?: boolean;
  torrent_monitor_enabled?: boolean;
  anidb_identity_refresh_enabled?: boolean;
  anidb_identity_refresh_interval_seconds?: number;
  shoko_status_enabled?: boolean;
  shoko_status_interval_seconds?: number;
  torrent_housekeeping_enabled?: boolean;
  torrent_housekeeping_interval_seconds?: number;
  attention_review_enabled?: boolean;
  attention_review_interval_seconds?: number;
  calendar_auto_update_interval_seconds?: number;
  conversion_review_enabled?: boolean;
  conversion_review_interval_seconds?: number;
  disk_recheck_enabled?: boolean;
  disk_recheck_interval_seconds?: number;
  log_level?: "ERROR" | "WARNING" | "INFO" | "DEBUG";
  metadata_provider?: "anilist" | "kitsu";
  toast_corner?: "top-right" | "top-left" | "bottom-right" | "bottom-left";
  season_rollover?: "ask" | "auto" | "off";
  automate_new_anime?: boolean;
  shoko_url?: string;
  shoko_api_key?: string;
  app_mode_override?: "" | "basic" | "advanced";
  proxy_kind?: "none" | "http" | "socks4" | "socks5" | "socks5h";
  proxy_host?: string;
  proxy_port?: number | null;
  proxy_username?: string;
  proxy_password?: string;
  proxy_check_url?: string;
  title_search_order?: string;
  title_folder_order?: string;
  title_display_order?: string;
  theme?: string;
  debug_mode?: boolean;
  language?: "en" | "de";
}

export interface InstalledTheme {
  slug: string;
  name: string;
  author: string;
  version: string;
  description: string;
  /** Which theme format the file states. Compared exactly against ours. */
  themes_api: number;
  /** True for the themes that ship with AniFlow. */
  builtin: boolean;
  /** How many tokens it sets. A theme may set as few as one. */
  tokens: number;
  usable: boolean;
  /** Why it cannot be used, and any values that were dropped. */
  problems: string[];
}

export interface ThemeList {
  themes: InstalledTheme[];
  /** The theme format this build understands. */
  themes_api: number;
  /** Where to put your own, so the page can say it rather than a doc. */
  folder: string;
}

export function listThemes(): Promise<ThemeList> {
  return request<ThemeList>("/api/themes");
}

export function getAppSettings(): Promise<AppSettingsView> {
  return request<AppSettingsView>("/api/settings");
}

export function updateAppSettings(payload: SettingsUpdatePayload): Promise<AppSettingsView> {
  return request<AppSettingsView>("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/** Same endpoint as `updateAppSettings`, for the Schedule page's own editor
 *  (build 161): the field names it writes come from a task's own
 *  `enabled_setting`/`interval_setting` at runtime, not from a fixed shape
 *  known ahead of time, so `SettingsUpdatePayload`'s named fields do not fit. */
export function updateScheduleTaskSettings(
  updates: Record<string, boolean | number>,
): Promise<AppSettingsView> {
  return request<AppSettingsView>("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
}

export function testQbittorrentConnection(
  payload?: { url?: string; username?: string; password?: string; client?: "qbittorrent" | "deluge" | "transmission" }
): Promise<{ ok: boolean; detail: string | null }> {
  return request("/api/settings/test/qbittorrent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload ?? {}),
  });
}

export interface ExitAddress {
  ok: boolean;
  /** False means this describes a direct connection, proxy or no proxy. */
  proxied: boolean;
  ip: string | null;
  country: string | null;
  flag: string | null;
  detail: string | null;
}

/** Which address the internet sees, asked through the proxy in force. */
export function checkExitAddress(): Promise<ExitAddress> {
  return request<ExitAddress>("/api/settings/test/proxy", { method: "POST" });
}

/**
 * Reach Shoko, then try the key.
 *
 * Draft values are sent, so Test works before anything is saved - and the
 * answer says which of the two failed, because a wrong address and a wrong
 * key are the two mistakes people actually make.
 */
export function testShokoConnection(
  payload?: { url?: string; api_key?: string }
): Promise<{ ok: boolean; detail: string | null }> {
  return request("/api/settings/test/shoko", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload ?? {}),
  });
}

export interface PathCheck {
  ok: boolean;
  message: string;
  qb_default_save_path: string | null;
  aniflow_download_root: string;
  aniflow_library_root: string;
  qb_category: string | null;
  translated_example: string | null;
  sample_content_path: string | null;
  sample_translated: string | null;
  sample_within_root: boolean | null;
  /** The source -> target pair the importer would use for the newest download. */
  sample_source_path: string | null;
  sample_target_path: string | null;
  sample_media_folder: string | null;
  download_root_exists: boolean | null;
  download_root_writable: boolean | null;
  library_root_exists: boolean | null;
  library_root_writable: boolean | null;
}

/**
 * Diagnose the paths, optionally with details that are on screen but unsaved -
 * otherwise the button answers "qBittorrent is not configured" until you save.
 */
export function checkPaths(payload?: {
  qb_url?: string | null;
  qb_username?: string;
  qb_password?: string;
  /** Which client the details are for, when it is not saved yet (the install wizard, build 250). */
  client?: "qbittorrent" | "deluge" | "transmission";
}): Promise<PathCheck> {
  return request<PathCheck>("/api/settings/paths/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload ?? {}),
  });
}

// Search types
export type SearchDecision = "MATCHED" | "REJECTED" | "IGNORED" | "AMBIGUOUS" | "SKIPPED" | "ATTENTION";

export interface SearchResult {
  id: string;
  provider: string;
  provider_id: string;
  query_used: string;
  page: number;
  raw_name: string;
  torrent_url: string | null;
  magnet_url: string | null;
  size: number | null;
  seeders: number | null;
  leechers: number | null;
  published_at: string | null;
  category: string | null;
  parsed_title: string | null;
  subgroup: string | null;
  episode: string | null;
  version: number;
  resolution: string | null;
  media_source: string | null;
  decision: SearchDecision;
  anime_id: string | null;
  failure_reasons: string[];
  ambiguous_anime_ids: string[];
  created_at: string;
}

export interface SearchStatus {
  search_enabled: boolean;
  searching: boolean;
  search_requested: boolean;
  last_search_summary: Record<string, unknown> | null;
  last_search_error: string | null;
}

export interface SearchTriggerRequest {
  anime_id?: string;
  query?: string;
  max_pages?: number;
  max_results?: number;
}

export function triggerSearch(payload: SearchTriggerRequest): Promise<{ started: boolean }> {
  return request("/api/search/trigger", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function getSearchStatus(): Promise<SearchStatus> {
  return request<SearchStatus>("/api/search/status");
}

export function listSearchResults(params: {
  limit?: number;
  decision?: string;
  anime_id?: string;
}): Promise<SearchResult[]> {
  const query = new URLSearchParams();
  if (params.limit) query.set("limit", String(params.limit));
  if (params.decision) query.set("decision", params.decision);
  if (params.anime_id) query.set("anime_id", params.anime_id);
  return request<SearchResult[]>(`/api/search/results?${query}`);
}

/** One television that may talk to AniFlow (build 181). */
export interface TvDevice {
  id: number;
  name: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

/** The same, at the moment it is made: the only time the token exists. */
export interface TvDeviceCreated extends TvDevice {
  token: string;
}

export function listTvDevices(): Promise<TvDevice[]> {
  return request<TvDevice[]>("/api/tv-devices");
}

export function createTvDevice(name: string): Promise<TvDeviceCreated> {
  return request<TvDeviceCreated>("/api/tv-devices", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export async function revokeTvDevice(id: number): Promise<void> {
  const response = await fetch(`/api/tv-devices/${id}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await extractError(response));
}

/** A TV that is showing a code and waiting to be approved (build 187). */
export interface TvPairLookup {
  name: string;
  code: string;
  /** The name to start the name field with: the TV's existing entry's, when it is recognised (build 263). */
  suggested_name: string | null;
  /** Existing entries this pairing could replace. */
  replaces: TvReplaceable[];
}

/** An existing entry a pairing could replace (build 263). */
export interface TvReplaceable {
  id: number;
  name: string;
  created_at: string | null;
  last_used_at: string | null;
  /** True: the TV's own id matches, and the entry is replaced when the TV collects its new token. False: only the
   *  name matches (the entry is older than TV ids) - offered, and the person decides. */
  same_device: boolean;
}

export function lookupTvPairing(code: string): Promise<TvPairLookup> {
  return request<TvPairLookup>(`/api/tv-devices/pair/${encodeURIComponent(code)}`);
}

/** Say yes. The token goes to the TV, never back here. */
export function approveTvPairing(code: string, name: string, replaceIds: number[] = []): Promise<TvDevice> {
  return request<TvDevice>("/api/tv-devices/pair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, name: name || null, replace_ids: replaceIds }),
  });
}

/** A library: a name and one target folder (build 192, 0.8.5). */
export interface Library {
  id: number;
  /** Set on the built-in libraries, which cannot be renamed or removed. */
  key: string | null;
  name: string;
  folder: string;
  is_system: boolean;
  is_default: boolean;
  fits_adult: string | null;
  fits_season: string | null;
  shoko_folder_id: number | null;
  /** How many anime live here, counted on the server (build 194). */
  anime_count: number;
  /** Of those, how many have their folder in this library's folder, and how many
   *  folders are really there - read from the disk (build 196). */
  anime_with_folder: number;
  folders_on_disk: number;
  /** Bytes the files take, what is free on the disk and its size; null when it cannot be read (build 233). */
  used_bytes: number;
  free_bytes: number | null;
  total_bytes: number | null;
  /** False for Upcoming: no folder, because nothing is downloaded for it (build 198). */
  has_folder: boolean;
  writable: boolean;
  exists: boolean;
}

export interface LibraryList {
  libraries: Library[];
}

export interface LibraryDraft {
  name: string;
  folder: string;
  fits_adult: string | null;
  fits_season: string | null;
  shoko_folder_id: number | null;
}

export interface FolderBrowsing {
  path: string;
  parent: string | null;
  entries: { name: string; path: string; writable: boolean }[];
  selectable: boolean;
  writable: boolean;
}

export interface ShokoImportFolder {
  id: number | null;
  name: string | null;
  path: string | null;
  drop_type: string | null;
  watching: boolean | null;
}

export interface ShokoImportFolders {
  reachable: boolean;
  detail: string | null;
  folders: ShokoImportFolder[];
}

/** `quick` leaves out the walk of each library's folder (the two folder counts stay 0): slow on a Windows bind mount. */
export function listLibraries(quick = false): Promise<LibraryList> {
  return request<LibraryList>(quick ? "/api/libraries?quick=true" : "/api/libraries");
}

export function browseFolders(path?: string): Promise<FolderBrowsing> {
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  return request<FolderBrowsing>(`/api/libraries/browse${query}`);
}

export function listShokoImportFolders(): Promise<ShokoImportFolders> {
  return request<ShokoImportFolders>("/api/libraries/shoko-folders");
}

export function createLibrary(draft: LibraryDraft): Promise<Library> {
  return request<Library>("/api/libraries", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
  });
}

/** `shoko_folder_id: null` with `clear_shoko_folder` unlinks; the fits are always sent. */
export function updateLibrary(id: number, draft: Partial<LibraryDraft> & { clear_shoko_folder?: boolean }): Promise<Library> {
  return request<Library>(`/api/libraries/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
  });
}

/** Move a library one place up or down among the user's own. */
export async function shiftLibrary(id: number, direction: "up" | "down"): Promise<void> {
  // 204, no body: not `request`, which reads JSON.
  const response = await fetch(`/api/libraries/${id}/shift?direction=${direction}`, { method: "POST" });
  if (!response.ok) throw new Error(await extractError(response));
}

export function makeLibraryDefault(id: number): Promise<Library> {
  return request<Library>(`/api/libraries/${id}/default`, { method: "POST" });
}

/**
 * Remove a library. One that still holds anime needs `moveTo`: where they go
 * (build 194). `moveFiles` is false when the files are already in place.
 */
export async function deleteLibrary(
  id: number,
  options: { moveTo?: number; moveFiles?: boolean } = {},
): Promise<void> {
  const query = new URLSearchParams();
  if (options.moveTo !== undefined) query.set("move_to", String(options.moveTo));
  if (options.moveFiles !== undefined) query.set("move_files", String(options.moveFiles));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const response = await fetch(`/api/libraries/${id}${suffix}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await extractError(response));
}

/** What moving one anime to another library came to (build 192). */
export interface LibraryMoveResult {
  anime_id: string;
  name: string;
  /** moved | reassigned | unchanged | refused */
  outcome: string;
  detail: string | null;
}

export interface RolloverItem {
  anime_id: string;
  name: string;
  /** finished | waiting | running */
  reason: string;
  from_library_id: number;
}

export interface RolloverGroup {
  /** The library they should go to; null when the user has to choose. */
  target_id: number | null;
  items: RolloverItem[];
}

export interface Rollover {
  mode: "ask" | "auto" | "off";
  earlier: RolloverGroup;
  airing: RolloverGroup;
}

/** What the season check has to say (build 205). Lists something only in "ask" mode. */
export function getRollover(): Promise<Rollover> {
  return request<Rollover>("/api/rollover");
}

/** "Keep here": the season check leaves these anime in their library (or takes that back). */
export function pinAnimeInLibrary(animeIds: string[], pinned: boolean): Promise<{ changed: number }> {
  return request<{ changed: number }>("/api/rollover/pin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ anime_ids: animeIds, pinned }),
  });
}

/** Move anime to a library: their folders go with them. Each is answered on its own. */
export function moveToLibrary(
  animeIds: string[],
  libraryId: number,
  moveFiles = true,
): Promise<LibraryMoveResult[]> {
  return request<LibraryMoveResult[]>("/api/libraries/move", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ anime_ids: animeIds, library_id: libraryId, move_files: moveFiles }),
  });
}

/** Which feed has shown a subgroup, how often, and when last (build 193). */
export interface SubgroupFeedCoverage {
  feed_id: string | null;
  feed_name: string;
  items: number;
  last_seen: string | null;
}

export interface SubgroupRow {
  subgroup: string;
  /** Anime you hold at least one file from this group for. */
  anime: number;
  episodes: number;
  /** How many anime profiles name this group. */
  wanted_by: number;
  feeds: SubgroupFeedCoverage[];
  seen_on_a_feed: boolean;
}

export interface SubgroupOverview {
  subgroups: SubgroupRow[];
  /** Airing anime no feed has mentioned in `days` days. */
  silent_anime: { anime_id: string; name: string; last_seen: string | null }[];
  days: number;
  /** Where the scan history starts; nothing earlier can be said about a feed. */
  history_since: string | null;
  feeds: number;
}

/** Read from stored history; no feed is asked anything. */
export function getSubgroupOverview(days = 14): Promise<SubgroupOverview> {
  return request<SubgroupOverview>(`/api/rss/subgroups?days=${days}`);
}

/** What refreshing one library from the disk found (build 195). Only the database changed. */
export interface LibraryRefresh {
  library_id: number;
  name: string;
  checked: number;
  moved_in: string[];
  moved_out: string[];
  missing: string[];
  unknown_folders: string[];
  /** Anime linked to a folder they matched by exact name ("Name -> folder"). */
  linked: string[];
  files_found: number;
  files_gone: number;
  problems: string[];
}

/** Refresh one library, or every library when no id is given. No file is touched. */
export function refreshLibraries(id?: number): Promise<LibraryRefresh[]> {
  return request<LibraryRefresh[]>(id === undefined ? "/api/libraries/refresh" : `/api/libraries/${id}/refresh`, {
    method: "POST",
  });
}

/** A folder in a library that no anime has, and what the AniDB title list says about its name (build 254). */
export interface ImportCandidate {
  library_id: number;
  library_name: string;
  folder: string;
  files: number;
  episodes: number[];
  /** The AniDB entry the name settled on exactly; null when it settles on nothing and the person picks. */
  anidb_id: number | null;
  title: string | null;
  /** The watchlist entry that already has that AniDB id - such a folder is shown but cannot be imported. */
  tracked_as: string | null;
}

export interface AnidbMatch {
  aid: number;
  title: string;
  romaji: string | null;
  native: string | null;
  /** Which search found it: the name as typed, the clean name (no season, group or year) or a shorter beginning. */
  via?: "full" | "clean" | "shorter";
  via_text?: string | null;
}

export interface ImportResult {
  folder: string;
  outcome: "imported" | "skipped" | "failed";
  detail: string | null;
  anime_id: string | null;
  name: string | null;
  files: number;
}

export function listImportCandidates(): Promise<ImportCandidate[]> {
  return request<ImportCandidate[]>("/api/libraries/import-candidates");
}

export function searchAnidbForImport(query: string): Promise<AnidbMatch[]> {
  return request<AnidbMatch[]>(`/api/libraries/import-search?q=${encodeURIComponent(query)}`);
}

/** Make one folder a watchlist entry. Its files are recorded where they are; nothing is moved or downloaded. */
export function importFolder(libraryId: number, folder: string, anidbId: number): Promise<ImportResult> {
  return request<ImportResult>("/api/libraries/import-folder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ library_id: libraryId, folder, anidb_id: anidbId }),
  });
}

/** What scanning an anime's folder found (build 198). Reads only. */
export interface ScanFilesResult {
  folder: string | null;
  files_scanned: number;
  episodes_found: number;
  completed: number;
  created: number;
  vanished: number;
  found_subgroup: string | null;
  found_resolution: string | null;
  release_name: string | null;
  /** What was written down: "folder", "profile", "release_name". */
  filled: string[];
  problems: string[];
}

/** Link the folder's files to the episodes and find the subgroup and quality. */
export function scanAnimeFiles(animeId: string): Promise<ScanFilesResult> {
  return request<ScanFilesResult>(`/api/anime/${animeId}/scan-files`, { method: "POST" });
}

// ------------------------------------------------------------------ Backup and restore (build 266)

export interface BackupGroupInfo {
  name: string;
  files: number;
  bytes: number;
  default: boolean;
}

export interface BackupContents {
  id: string;
  version: string | null;
  build: number | null;
  created_at: string | null;
  groups: { name: string; files: number; bytes: number; usable: boolean; note: string | null }[];
}

export interface BackupRestored {
  restored: string[];
  files: Record<string, number>;
  safety_backup: string | null;
  libraries_kept: number;
  libraries_unmatched: string[];
  warnings: string[];
  restart_recommended: boolean;
}

/** What an export could hold right now, with sizes. */
export function getBackupGroups(): Promise<BackupGroupInfo[]> {
  return request<BackupGroupInfo[]>("/api/backup/groups");
}

/** The download link for the chosen parts: a plain link, so the browser streams the file to disk. */
export function backupExportUrl(groups: string[]): string {
  return `/api/backup/export?groups=${encodeURIComponent(groups.join(","))}`;
}

/** Hand the server a backup to check and describe - the raw zip as the body; nothing is changed yet. */
export function inspectBackup(file: File): Promise<BackupContents> {
  return request<BackupContents>("/api/backup/inspect", {
    method: "POST",
    headers: { "Content-Type": "application/zip" },
    body: file,
  });
}

export function restoreBackup(id: string, groups: string[], keepLocal: boolean): Promise<BackupRestored> {
  return request<BackupRestored>("/api/backup/restore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, groups, keep_local: keepLocal }),
  });
}

export function forgetBackup(id: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/backup/staged/${id}`, { method: "DELETE" });
}

/**
 * Which page the URL means, and which URL a page means (build 145).
 *
 * Lifted out of App.tsx so it can be tested as what it is: two pure functions
 * over a string. It was worth lifting the moment a hash could carry more than
 * a page name.
 *
 * **The rule for sections.** A link into a page may name the part of it you
 * meant — `#/settings/calendar` opens Settings on its Calendar tab. Asked for
 * as a rule rather than as one button's behaviour:
 *
 *     *"This should be a rule for all pages, if we have more of this
 *     configure stuff in the future."*
 *
 * So a page opts in by joining `SECTIONED`, and then takes `section` and
 * `onSection` props: it opens where the link said, and writes the section back
 * to the URL when somebody moves within the page. That is what makes the back
 * button move between tabs instead of out of the page, and what makes a
 * bookmark keep meaning what it meant.
 *
 * `#/anime/<id>` and `#/indexers/<id>` have the same shape and are *not*
 * sections — they carry an identifier. They are matched first, deliberately.
 */
export type Navigation =
  | "dashboard"
  | "calendar"
  | "libraries"
  | "shoko"
  | "anidb"
  | "inspector"
  | "profiles"
  | "rssSources"
  | "indexers"
  | "progress"
  | "checks"
  | "attention"
  | "activity"
  | "schedule"
  | "settings";

export const navigation: Navigation[] = [
  "dashboard",
  "calendar",
  "libraries",
  "shoko",
  "anidb",
  "inspector",
  "profiles",
  "rssSources",
  "indexers",
  "progress",
  "checks",
  "attention",
  "activity",
  "schedule",
  "settings",
];

export type Page = Navigation | "anime" | "calendarDebug";

export interface View {
  page: Page;
  animeId?: string;
  /** An indexer to open the editor for, so the RSS page's "Indexer" button is
   *  a real link rather than transient state. */
  indexerId?: string;
  /** Which part of the page was meant: a settings tab, today. See above. */
  section?: string;
}

/** Pages whose hash may name a section. Adding one is this line plus props. */
/* "shoko" joined in build 180: its section is a Shoko series id, so a
   series page has a URL and the back button leaves it. Both "shoko" and "anidb" are split into tabs since
   build 258, and the tab is the section. */
export const SECTIONED: readonly Page[] = ["settings", "calendar", "shoko", "anidb"];

/**
 * The calendar's section is the season on screen: `#/calendar/2025/SPRING`.
 *
 * It lives in the URL because it has to survive the component being unmounted.
 * It did not, and the result was reported twice in one morning: opening the
 * debug page and closing it came back to the *current* season, and so did
 * finishing an update of an old one - the mount effect asked the server for
 * "now" because nothing had told it otherwise.
 */
export function parseSeason(section: string | undefined): { year: number; season: string } | null {
  if (!section) return null;
  const [year, season] = section.split("/");
  const parsed = Number(year);
  if (!Number.isInteger(parsed) || !season) return null;
  return { year: parsed, season: season.toUpperCase() };
}

/** Map a URL hash to an in-app view (browser back/forward aware). */
export function parseHash(hash: string): View {
  const raw = hash.replace(/^#\/?/, "").trim();
  if (raw === "calendar/debug") return { page: "calendarDebug" };
  if (raw.startsWith("anime/")) return { page: "anime", animeId: decodeURIComponent(raw.slice(6)) };
  if (raw.startsWith("indexers/")) {
    return { page: "indexers", indexerId: decodeURIComponent(raw.slice(9)) };
  }
  const [head, ...rest] = raw.split("/");
  if (rest.length && SECTIONED.includes(head as Page)) {
    return { page: head as Page, section: decodeURIComponent(rest.join("/")) };
  }
  const known = navigation.find((page) => page === raw);
  return { page: known ?? "dashboard" };
}

export function viewToHash(view: View): string {
  if (view.page === "calendarDebug") return "#/calendar/debug";
  if (view.page === "anime" && view.animeId) return `#/anime/${encodeURIComponent(view.animeId)}`;
  if (view.page === "indexers" && view.indexerId) {
    return `#/indexers/${encodeURIComponent(view.indexerId)}`;
  }
  if (view.section && SECTIONED.includes(view.page)) {
    // Encoded per segment: a section may contain a slash of its own - the
    // calendar's is `2025/SPRING` - and encoding the whole string would turn
    // that into %2F and fail to parse back.
    const encoded = view.section.split("/").map(encodeURIComponent).join("/");
    return `#/${view.page}/${encoded}`;
  }
  return `#/${view.page}`;
}

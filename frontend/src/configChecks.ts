/**
 * The four checks, placed where they can be fixed (build 152).
 *
 * Asked for as: *"get rid of this debug window (and its 4 buttons) and add
 * all of this information somehow in Configuration Edit ... The areas which
 * are empty, have the info direct there and user can direct edit it."*
 *
 * So every code the backend can report (services/anime_health.py) is given a
 * home here: the field in the Edit dialog that fixes it, or - where no field
 * can - the action that does, or a plain notice when the answer is outside
 * AniFlow. `WHERE` is exhaustive on purpose; a code missing from it is a
 * check nobody would ever see, and the test says so.
 *
 * Kept free of React so the mapping can be tested without a dialog.
 */
import { formatDate } from "./formatWhen";
import { t } from "./i18n";

/** A field in the Edit dialog that a check points at. */
export type FieldKey =
  | "title_romaji" | "title_english" | "title_native" | "format" | "air_start_date" | "anidb_id"
  | "airs_weekday" | "airs_hour" | "episode_count" | "cover"
  | "release_name" | "profile" | "media_folder" | "import_folder"
  | "genres" | "studios" | "synopsis";

/** What can be done about a check that has no field of its own. */
export type CheckAction = "refill" | "findFiles" | "notice" | "automate" | "chooseAnidb";

type Where = { field: FieldKey | FieldKey[] } | { action: CheckAction };

/** Every code, and where the dialog deals with it. */
export const WHERE: Record<string, Where> = {
  // Metadata - facts about the show.
  display_name: { action: "refill" },
  title_romaji: { field: "title_romaji" },
  title_english: { field: "title_english" },
  title_native: { field: "title_native" },
  cover: { field: "cover" },
  episode_count: { field: "episode_count" },
  format: { field: "format" },
  air_start_date: { field: "air_start_date" },
  air_slot: { field: ["airs_weekday", "airs_hour"] },
  anidb_id: { field: "anidb_id" },
  // AniDB's own names come from the dump once the id is known.
  anidb_titles: { field: "anidb_id" },
  provider_ids: { action: "refill" },
  // A field of their own since build 156 - an old show no provider
  // describes can only be described by the user - and still gathered onto
  // one "Missing:" line with the other gaps a provider can fill.
  genres: { field: "genres" },
  studios: { field: "studios" },
  synopsis: { field: "synopsis" },
  // Download - what is needed to fetch episodes. The release name and the
  // profile are shown here and changed on the detail page, where the change
  // can be reviewed against what it does to the library.
  release_name: { field: "release_name" },
  subgroup: { field: "profile" },
  resolution: { field: "profile" },
  releases_for_aired_episodes: { action: "notice" },
  // Amber, with its own button in the Edit dialog (build 212): Automate is off for an anime still to get.
  automation_off: { action: "automate" },
  // Files and folders.
  media_folder: { field: "media_folder" },
  import_folder: { field: "import_folder" },
  files_not_on_disk: { action: "findFiles" },
  duplicate_files: { action: "notice" },
  // Shoko Server.
  shoko_missing_aired: { action: "notice" },
  matched_by_name_only: { field: "anidb_id" },
  // The AniDB id was found by name only, or the id list gives it to another show (build 229): amber,
  // with a list of the entries it might be to choose from.
  anidb_unconfirmed: { action: "chooseAnidb" },
  anidb_contradicted: { action: "chooseAnidb" },
  fewer_episodes_than_anidb: { field: "episode_count" },
};

/**
 * How loud each check is. Red for the ones that stop an anime doing its job -
 * the same "essential" sets the backend's colours are built from - amber for
 * the rest.
 */
const RED = new Set([
  "display_name", "episode_count", "cover",
  "release_name", "subgroup",
  "files_not_on_disk",
  "shoko_missing_aired",
]);

/** Red, amber, and below both "info": nice to have, never a warning
 *  (build 157) - studio and synopsis, as the user sorted them. */
export type Level = "red" | "yellow" | "info";

/** The same set as NICE_TO_HAVE in services/anime_health.py. */
export const NICE_TO_HAVE = new Set(["studios", "synopsis"]);

export interface Check {
  group: string;
  code: string;
  level: Level;
  where: Where;
  /** What the group found beside it - which episodes, which numbers. */
  facts: Record<string, string>;
}

export interface GroupState {
  colour: string;
  missing?: string[];
  not_applicable?: string | null;
  known?: { field: string; value: string }[];
}

export type HealthGroups = Record<string, GroupState | undefined> | null | undefined;

const GROUP_ORDER = ["metadata", "download", "storage", "shoko"];

/** Every open check, red first, then in the groups' usual order. */
export function openChecks(health: HealthGroups): Check[] {
  const found: Check[] = [];
  for (const group of GROUP_ORDER) {
    const state = health?.[group];
    if (!state || state.not_applicable) continue;
    const facts = Object.fromEntries((state.known ?? []).map((entry) => [entry.field, entry.value]));
    for (const code of state.missing ?? []) {
      found.push({
        group,
        code,
        level: RED.has(code) ? "red" : NICE_TO_HAVE.has(code) ? "info" : "yellow",
        where: WHERE[code] ?? { action: "notice" },
        facts,
      });
    }
  }
  const rank = { red: 0, yellow: 1, info: 2 } as const;
  return found.sort((a, b) => rank[a.level] - rank[b.level]);
}

/** The checks that are problems - everything but the nice-to-haves. */
export function problems(checks: readonly Check[]): Check[] {
  return checks.filter((check) => check.level !== "info");
}

/** The checks that point at one field, for the flag beside it. */
export function checksFor(checks: readonly Check[], field: FieldKey): Check[] {
  return checks.filter((check) => {
    if (!("field" in check.where)) return false;
    const target = check.where.field;
    return Array.isArray(target) ? target.includes(field) : target === field;
  });
}

/** The worse of the checks on one field. A nice-to-have raises no flag. */
export function levelOf(checks: readonly Check[]): "red" | "yellow" | null {
  if (checks.some((check) => check.level === "red")) return "red";
  if (checks.some((check) => check.level === "yellow")) return "yellow";
  return null;
}

/**
 * The facts a check's sentence names, by placeholder (build 153).
 *
 * Reported against 152: a Shoko warning and a Download warning that both
 * looked wrong, because neither said *which* episode - and the Debug window
 * that used to list them was gone. A sentence that names episode 13 can be
 * checked; "an episode" cannot.
 */
const DETAIL: Record<string, Record<string, string>> = {
  releases_for_aired_episodes: { episodes: "episodes_without_a_release" },
  shoko_missing_aired: { episodes: "shoko_missing_episodes" },
  files_not_on_disk: { episodes: "episodes_whose_file_is_gone" },
  duplicate_files: { episodes: "episodes_with_two_files" },
  fewer_episodes_than_anidb: { anidb: "shoko_episodes", count: "episode_count" },
};

/**
 * The server writes the dates inside a fact as "2026-09-19"; the reader asked
 * for another order (build 190), so each one is rewritten as they chose.
 */
export function inReadersDates(text: string): string {
  return text.replace(/\b(\d{4}-\d{2}-\d{2})\b/g, (found) => formatDate(found));
}

/** What a check says, in a sentence - with its episodes, when they are known. */
export function checkText(check: Check): string {
  const wanted = DETAIL[check.code];
  if (wanted && Object.values(wanted).every((field) => check.facts[field])) {
    let text = t(`config.checkDetail.${check.code}`);
    for (const [slot, field] of Object.entries(wanted)) text = text.replace(`{${slot}}`, inReadersDates(check.facts[field]));
    return text;
  }
  return t(`config.check.${check.code}`);
}

/** One row per group: its worst level, the gaps only a provider can fill as
 *  one line, and every other check as a line of its own (build 153). */
export interface CheckRow {
  group: string;
  level: Level;
  /** Codes whose only fix is asking the providers again - "genres". */
  refill: string[];
  /** Nice to have - "studio, synopsis" - shown quietly, never as a problem. */
  nice: string[];
  others: Check[];
  all: Check[];
}

/** The gaps a provider can fill, shown together on one line. */
export const PROVIDER_GAPS = new Set(["display_name", "provider_ids", "genres", "studios", "synopsis"]);

/** Every check a provider could close by being asked again (build 237). The "Fix
 *  metadata" button used to appear only for the gaps above, so an anime whose
 *  only problem was a missing episode count or English title - which have a
 *  field of their own to type into - was offered no button at all. Mirrors
 *  FILLABLE in services/metadata_fill.py. */
export const REFILLABLE = new Set([
  ...PROVIDER_GAPS,
  "title_romaji", "title_english", "title_native", "cover", "episode_count", "format", "air_start_date", "air_slot",
]);

/** Whether asking the providers again could close anything that is open. */
export function canRefill(checks: readonly Check[]): boolean {
  return checks.some((check) => check.level === "info" || REFILLABLE.has(check.code));
}

export function checkRows(checks: readonly Check[]): CheckRow[] {
  const rows = new Map<string, CheckRow>();
  for (const check of checks) {
    const row = rows.get(check.group) ?? { group: check.group, level: check.level, refill: [], nice: [], others: [], all: [] };
    if (check.level === "red" || (check.level === "yellow" && row.level === "info")) row.level = check.level;
    row.all.push(check);
    if (check.level === "info") row.nice.push(check.code);
    else if (PROVIDER_GAPS.has(check.code)) row.refill.push(check.code);
    else row.others.push(check);
    rows.set(check.group, row);
  }
  const rank = { red: 0, yellow: 1, info: 2 } as const;
  return [...rows.values()].sort((a, b) => rank[a.level] - rank[b.level]);
}

/** Where to scroll to for a field: the dialog gives each one this id. */
export function fieldAnchor(field: FieldKey): string {
  return `config-field-${field}`;
}

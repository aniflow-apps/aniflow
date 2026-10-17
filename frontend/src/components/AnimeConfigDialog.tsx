import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useLockedPageScroll } from "../useLockedPageScroll";
import { AnidbChooserDialog } from "./AnidbChooserDialog";
import {
  deleteCover,
  getAnime,
  getAppSettings,
  updateAppSettings,
  getAnimeDbDump,
  getAnimeHealth,
  refillMetadata,
  updateAnime,
  uploadCover,
  type AnimeDetail,
  type AnimeSummary,
  type RefillResult,
  type StorageMode,
} from "../api/client";
import { t } from "../i18n";
import { dateInputHint, formatClock, formatDate, parseDateInput } from "../formatWhen";
import { CALENDAR_KINDS, kindLabel } from "../calendarView";
import {
  checkRows,
  checkText,
  canRefill,
  checksFor,
  fieldAnchor,
  levelOf,
  openChecks,
  problems,
  type Check,
  type FieldKey,
  type HealthGroups,
} from "../configChecks";
import { Cover } from "./Cover";
import { FeedCheckPanel } from "./FeedCheckPanel";

/**
 * The mark beside a field that one of the four checks points at (build 152).
 *
 * *"some kind of bubble text or exclamation mark or something on top or next
 * to the actual field. So user can spot it immediately and fix it."*  The
 * same round `!` as the watchlist card's corner, in the same two colours, so
 * it reads as the same signal; the hover says what is wrong in a sentence.
 */
function FieldFlag({ checks }: { checks: Check[] }) {
  const level = levelOf(checks);
  if (!level) return null;
  const title = checks.filter((check) => check.level !== "info").map(checkText).join("\n");
  return (
    <span className={`field-flag ${level}`} title={title} aria-label={title} role="img">!</span>
  );
}

/** A field's label with its flag, and the id the Checks list scrolls to. */
function FlaggedLabel({ checks, children }: { checks: Check[]; children: ReactNode }) {
  return (
    <span className="field-label-row">
      {children}
      <FieldFlag checks={checks} />
    </span>
  );
}

/** Monday-first, matching the watchlist's columns and the API's 0-6. */
/** The four broadcast seasons, as the server stores them (item 17). Chosen from a list, never typed. */
const SEASON_NAMES = ["WINTER", "SPRING", "SUMMER", "FALL"] as const;

/** The years on offer: two ahead of this one, back to 1980 - and the value already held, if it lies outside. */
function seasonYears(current: string): number[] {
  const top = new Date().getFullYear() + 2;
  const years = Array.from({ length: top - 1980 + 1 }, (_, index) => top - index);
  const held = Number(current);
  if (held && !years.includes(held)) years.unshift(held);
  return years;
}

const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
const HOURS = Array.from({ length: 24 }, (_, hour) => String(hour).padStart(2, "0"));

/** 8 MB, matching the server. Checked here so an obvious mistake is answered
 *  instantly instead of after the upload. */
const MAX_COVER_BYTES = 8 * 1024 * 1024;

/**
 * Everything configurable about one anime, in one window.
 *
 * The split is deliberate: what identifies the anime is shown, what stores it
 * is editable.
 *
 * - The **release name** is what matching compares every feed item against, and
 *   the **release profile** decides which of those items qualify. Both are
 *   shown, neither is edited here: changing a profile silently changes what
 *   gets downloaded next, which belongs on the Subgroup Profiles page where the
 *   same change can be reviewed against every anime using it.
 * - The **folders** are editable, because a library gets reorganised - with a
 *   warning, since nothing on disk is moved by a rename.
 * - The **feed source** is gone. A scan reads every enabled feed and matches
 *   each item against every tracked anime, so a per-anime feed was never
 *   consulted; showing it implied a relationship that does not exist.
 */
export function AnimeConfigDialog({
  anime,
  mode,
  onClose,
  onUpdated,
  debugMode = false,
  onFindFiles,
  onChecked,
}: {
  anime: AnimeDetail;
  mode: StorageMode;
  onClose: () => void;
  onUpdated: (anime: AnimeSummary | AnimeDetail) => void;
  /** From aniflow.yaml. Decides whether the two developer views below are
   *  offered at all (build 94). */
  debugMode?: boolean;
  /** Opens the Find files dialog - the fix for files that are not on disk. */
  onFindFiles?: () => void;
  /** Tells the detail page what the checks say now, for its Edit button. */
  onChecked?: (health: HealthGroups) => void;
}) {
  // Shoko Server names the library folder, so there is nothing to type here:
  // a value entered by hand would either be overwritten by where the file
  // actually landed, or point AniFlow at a folder Shoko is not using.
  const folderIsShokos = mode === "shokoserver";
  const [mediaFolder, setMediaFolder] = useState(anime.media_folder ?? "");
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(0);
  const [error, setError] = useState<string | null>(null);
  /* Reported: *"When I press the Edit Configuration button and the new
     window pops in, and I use mouse wheel it does scroll the page behinde
     it."*  Before any early return, as every hook must be. */
  useLockedPageScroll();
  const [dbDump, setDbDump] = useState<Record<string, unknown> | null>(null);
  const [dbDumpLoading, setDbDumpLoading] = useState(false);
  /** Set for a moment after copying, so the button can say it worked. */
  const [copied, setCopied] = useState(false);

  /** Put the dump on the clipboard, with a fallback for an insecure origin.
   *
   * `navigator.clipboard` needs a secure context, and AniFlow is reached over
   * plain HTTP on a LAN as often as not - so the old textarea trick is kept
   * as the fallback rather than the button silently doing nothing.
   */
  async function copyDump() {
    const text = JSON.stringify(dbDump, null, 2);
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
      } finally {
        document.body.removeChild(area);
      }
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }
  const [coverBusy, setCoverBusy] = useState(false);
  const [hasCustomCover, setHasCustomCover] = useState(!!anime.has_custom_cover);
  const [coverKey, setCoverKey] = useState(0);
  const filePicker = useRef<HTMLInputElement>(null);

  // The airing slot and the episode count as the user asserts them. Empty
  // means "no opinion", which is a different thing from zero and different
  // again from the provider having no answer.
  const [weekday, setWeekday] = useState(anime.airs_weekday === null ? "" : String(anime.airs_weekday));
  const [hour, setHour] = useState(anime.airs_time ? anime.airs_time.slice(0, 2) : "");
  const [episodeCount, setEpisodeCount] = useState(
    anime.episode_count === null ? "" : String(anime.episode_count),
  );
  /* The premiere date as typed, in the order Settings chose (build 190). It
     used to be a browser date box, which writes dates in the browser's own
     locale - month first on an English-US machine - whatever the Settings
     said. `facts.air_start_date` stays the ISO date that is saved; this is
     only what is in the box. */
  const [premiereText, setPremiereText] = useState(anime.air_start_date ? formatDate(anime.air_start_date) : "");
  const [scheduleSaving, setScheduleSaving] = useState(false);
  const [scheduleSavedAt, setScheduleSavedAt] = useState(0);
  // Tracked here as well as on the anime: the dialog keeps the anime it was
  // opened with, so a save would not otherwise reveal the button that undoes
  // it until the dialog was closed and reopened.
  const [hasOverride, setHasOverride] = useState(
    anime.schedule_is_manual || anime.episode_count_is_manual,
  );

  /* The four checks, read fresh when the dialog opens and again after every
     save - a flag that stays up after its field was fixed would teach the
     reader to ignore flags. Null until the first answer. */
  const [health, setHealth] = useState<HealthGroups>(null);
  const [refilling, setRefilling] = useState(false);
  const [choosingAnidb, setChoosingAnidb] = useState(false);
  const [refilled, setRefilled] = useState<RefillResult | null>(null);
  const recheck = useCallback(async () => {
    try {
      const answer = await getAnimeHealth(anime.id);
      setHealth(answer);
      onChecked?.(answer);
    } catch {
      // A check that could not be read is not a fault in the anime; the
      // dialog works without flags.
    }
  }, [anime.id, onChecked]);
  useEffect(() => { void recheck(); }, [recheck]);
  const checks = health ? openChecks(health) : [];
  const on = (field: FieldKey) => checksFor(checks, field);

  // The facts a provider may not know (build 152). Kept as typed; saved as
  // one request, and only the fields that changed are sent.
  const [facts, setFacts] = useState({
    title_romaji: anime.title_romaji ?? "",
    title_english: anime.title_english ?? "",
    title_native: anime.title_native ?? "",
    format: anime.format ?? "",
    air_start_date: anime.air_start_date ?? "",
    season: anime.season ?? "",
    season_year: anime.season_year ? String(anime.season_year) : "",
    anidb_id: anime.anidb_id ? String(anime.anidb_id) : "",
    // Comma-separated in the field, a list on the server (build 156).
    genres: (anime.genres ?? []).join(", "),
    studios: (anime.studios ?? []).join(", "),
    synopsis: anime.synopsis ?? "",
  });
  const premiereInvalid = premiereText.trim() !== "" && parseDateInput(premiereText) === null;
  /* A refill from the provider, or a reset, changes the date from outside the
     box; the text follows it. Typing never triggers this - what is typed
     either parses to the date already held or is held back as invalid. */
  useEffect(() => {
    if ((parseDateInput(premiereText) ?? "") !== facts.air_start_date && !premiereInvalid) {
      setPremiereText(facts.air_start_date ? formatDate(facts.air_start_date) : "");
    }
  }, [facts.air_start_date]);
  const [factsSaving, setFactsSaving] = useState(false);
  const [factsSavedAt, setFactsSavedAt] = useState(0);
  const [importFolder, setImportFolder] = useState(anime.import_folder ?? "");

  /* What the fields were last saved as. The dialog is opened with one anime
     and keeps it, so comparing against the prop would re-send every value
     changed since the dialog opened, on every later save. */
  const [saved, setSaved] = useState<AnimeDetail>(anime);

  /**
   * Save the whole About panel - titles, kind, premiere, AniDB id, and since
   * build 153 the airing slot and the episode count too - as one request.
   *
   * Only what changed is sent. That matters most for the count: sending it
   * makes it the user's own, and a Save pressed for an English title must
   * not quietly freeze the provider's episode count as an override.
   */
  async function saveFacts() {
    setFactsSaving(true);
    setError(null);
    try {
      const payload: Parameters<typeof updateAnime>[1] = {};
      for (const key of ["title_romaji", "title_english", "title_native", "format"] as const) {
        if (facts[key].trim() !== (saved[key] ?? "")) payload[key] = facts[key].trim();
      }
      // A date cannot be an empty string on the server; null clears it.
      if (facts.air_start_date !== (saved.air_start_date ?? "")) payload.air_start_date = facts.air_start_date || null;
      // The season is chosen from lists, both parts or neither (item 17). Emptying both gives it back to the calendar.
      if (facts.season !== (saved.season ?? "") || facts.season_year !== String(saved.season_year ?? "")) {
        if (facts.season && facts.season_year) {
          payload.season = facts.season;
          payload.season_year = Number(facts.season_year);
        } else if (!facts.season && !facts.season_year) {
          payload.reset_season = true;
        } else {
          throw new Error(t("config.seasonIncomplete"));
        }
      }
      const aid = facts.anidb_id.trim();
      if (aid && aid !== String(saved.anidb_id ?? "")) {
        if (!/^\d+$/.test(aid)) throw new Error(t("config.anidbIdInvalid"));
        payload.anidb_id = Number(aid);
      }
      // A weekday without an hour is not a slot, so neither goes alone.
      const savedWeekday = saved.airs_weekday === null ? "" : String(saved.airs_weekday);
      const savedHour = saved.airs_time ? saved.airs_time.slice(0, 2) : "";
      if (weekday !== "" && hour !== "" && (weekday !== savedWeekday || hour !== savedHour)) {
        payload.airs_weekday = Number(weekday);
        payload.airs_hour = Number(hour);
      }
      if (episodeCount !== "" && episodeCount !== String(saved.episode_count ?? "")) {
        payload.episode_count = Number(episodeCount);
      }
      const list = (text: string) => text.split(",").map((item) => item.trim()).filter(Boolean);
      for (const key of ["genres", "studios"] as const) {
        if (list(facts[key]).join("|") !== (saved[key] ?? []).join("|")) payload[key] = list(facts[key]);
      }
      if (facts.synopsis.trim() !== (saved.synopsis ?? "").trim()) payload.synopsis = facts.synopsis.trim();
      if (Object.keys(payload).length > 0) {
        await updateAnime(anime.id, payload);
        // The PATCH answers with a summary; the page wants the whole detail.
        const fresh = await getAnime(anime.id);
        onUpdated(fresh);
        setSaved(fresh);
        setHasOverride(fresh.schedule_is_manual || fresh.episode_count_is_manual);
      }
      setFactsSavedAt(Date.now());
      await recheck();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setFactsSaving(false);
    }
  }

  /** Turn Automate on from the check that says it is off (build 212), as the page's own switch does. */
  async function turnAutomateOn() {
    setRefilling(true);
    try {
      await updateAnime(anime.id, { automation_enabled: true });
      const settings = await getAppSettings();
      if (!settings.rss_enabled) await updateAppSettings({ rss_enabled: true });
      onUpdated(await getAnime(anime.id));
      await recheck();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefilling(false);
    }
  }

  /** Ask the providers again - the fix for the gaps no field can close. */
  async function refill() {
    setRefilling(true);
    setRefilled(null);
    try {
      setRefilled(await refillMetadata(anime.id));
      const fresh = await getAnime(anime.id);
      onUpdated(fresh);
      // The fields show what was just filled in, without closing and
      // reopening the dialog (build 155). Only the ones still as they were
      // saved: something typed and not yet saved is the user's, not the
      // provider's to replace.
      showFilled(fresh);
      setSaved(fresh);
      await recheck();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefilling(false);
    }
  }

  function showFilled(fresh: AnimeDetail) {
    const untouched = (key: keyof typeof facts, was: string) => facts[key] === was;
    setFacts((current) => {
      const next = { ...current };
      for (const key of ["title_romaji", "title_english", "title_native", "format"] as const) {
        if (untouched(key, saved[key] ?? "")) next[key] = fresh[key] ?? "";
      }
      if (untouched("air_start_date", saved.air_start_date ?? "")) next.air_start_date = fresh.air_start_date ?? "";
      if (untouched("anidb_id", saved.anidb_id ? String(saved.anidb_id) : "")) next.anidb_id = fresh.anidb_id ? String(fresh.anidb_id) : "";
      for (const key of ["genres", "studios"] as const) {
        if (untouched(key, (saved[key] ?? []).join(", "))) next[key] = (fresh[key] ?? []).join(", ");
      }
      if (untouched("synopsis", saved.synopsis ?? "")) next.synopsis = fresh.synopsis ?? "";
      return next;
    });
    const savedWeekday = saved.airs_weekday === null ? "" : String(saved.airs_weekday);
    const savedHour = saved.airs_time ? saved.airs_time.slice(0, 2) : "";
    if (weekday === savedWeekday) setWeekday(fresh.airs_weekday === null ? "" : String(fresh.airs_weekday));
    if (hour === savedHour) setHour(fresh.airs_time ? fresh.airs_time.slice(0, 2) : "");
    if (episodeCount === String(saved.episode_count ?? "")) setEpisodeCount(fresh.episode_count === null ? "" : String(fresh.episode_count));
  }

  function jumpTo(field: FieldKey) {
    const target = document.getElementById(fieldAnchor(field));
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
    const editable = target?.querySelector<HTMLElement>("input, select, button");
    editable?.focus({ preventScroll: true });
    if (!editable && target) {
      // Read-only (release name, profile): nothing to focus, so show where it landed - the panel,
      // with the hint under it that says where the field is changed.
      const panel = target.closest(".detail-panel") ?? target;
      panel.classList.remove("config-flash");
      void (panel as HTMLElement).offsetWidth;
      panel.classList.add("config-flash");
    }
  }

  const folderChanged = (anime.media_folder ?? "") !== mediaFolder.trim();
  const importChanged = folderIsShokos && (anime.import_folder ?? "") !== importFolder.trim();
  const kinds: string[] = [...CALENDAR_KINDS];
  if (facts.format && !kinds.includes(facts.format)) kinds.unshift(facts.format);

  async function saveFolders() {
    if (!folderIsShokos && !mediaFolder.trim()) {
      setError(t("config.folderRequired"));
      return;
    }
    setSaving(true);
    setError(null);
    try {
      // Shoko Server names the library folder, so in that mode only the
      // hand-over folder - AniFlow's own to name - is sent.
      onUpdated(await updateAnime(anime.id, folderIsShokos
        ? { import_folder: importFolder.trim() }
        : { media_folder: mediaFolder.trim() }));
      setSavedAt(Date.now());
      void recheck();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  /**
   * Save the slot and the count the user typed.
   *
   * Sent as a weekday and an hour rather than a timestamp: those are the two
   * things a person knows about a weekly show, and the server turns them into
   * an instant in its own timezone - the only place that conversion can be
   * done correctly.
   */
  async function saveSchedule(reset: boolean) {
    setScheduleSaving(true);
    setError(null);
    try {
      const payload: Parameters<typeof updateAnime>[1] = {};
      if (reset) {
        payload.reset_schedule = true;
        payload.reset_episode_count = true;
      } else {
        // A weekday without an hour is not a slot, so neither goes alone.
        if (weekday !== "" && hour !== "") {
          payload.airs_weekday = Number(weekday);
          payload.airs_hour = Number(hour);
        }
        if (episodeCount !== "") payload.episode_count = Number(episodeCount);
      }
      await updateAnime(anime.id, payload);
      const fresh = await getAnime(anime.id);
      onUpdated(fresh);
      setSaved(fresh);
      if (reset) {
        // Back to whatever the provider says.
        setWeekday(fresh.airs_weekday === null ? "" : String(fresh.airs_weekday));
        setHour(fresh.airs_time ? fresh.airs_time.slice(0, 2) : "");
        setEpisodeCount(fresh.episode_count === null ? "" : String(fresh.episode_count));
      }
      setHasOverride(!reset);
      setScheduleSavedAt(Date.now());
      void recheck();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setScheduleSaving(false);
    }
  }

  async function pickCover(file: File | undefined) {
    if (!file) return;
    if (file.size > MAX_COVER_BYTES) {
      setError(t("config.coverTooBig"));
      return;
    }
    setCoverBusy(true);
    setError(null);
    try {
      onUpdated(await uploadCover(anime.id, file));
      setHasCustomCover(true);
      void recheck();
      // The URL never changes, so the browser would keep showing the old image.
      setCoverKey((key) => key + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCoverBusy(false);
      if (filePicker.current) filePicker.current.value = "";
    }
  }

  async function dropCover() {
    setCoverBusy(true);
    setError(null);
    try {
      onUpdated(await deleteCover(anime.id));
      setHasCustomCover(false);
      setCoverKey((key) => key + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCoverBusy(false);
    }
  }

  async function loadDbDump() {
    setDbDumpLoading(true);
    try {
      setDbDump(await getAnimeDbDump(anime.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDbDumpLoading(false);
    }
  }

  const profile = anime.profile;

  return (
    <div className="dialog-backdrop" role="presentation" onClick={onClose}>
      <section
        className="dialog dialog-wide dialog-config"
        role="dialog"
        aria-modal="true"
        aria-label={t("config.title")}
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <h2>{t("config.title")}</h2>
          <button className="close" onClick={onClose} aria-label={t("add.cancel")}>×</button>
        </header>

        <div className="modal-scroll">
          {/* The four checks, in one list (build 152). They used to be four
              Debug windows on the detail page, behind debug_mode, one button
              each. Now every open check says what is wrong and points at the
              field below that fixes it - or offers the one action that can,
              or says plainly that the answer is outside AniFlow. */}
          <div className="detail-panel config-checks">
            <h2>{t("config.checks.title")}</h2>
            {health === null ? (
              <p className="muted">{t("config.checks.loading")}</p>
            ) : checks.length === 0 ? (
              <p className="config-checks-ok">{t("config.checks.none")}</p>
            ) : (
              <>
                {/* Only problems are counted; a nice-to-have is not one. */}
                {problems(checks).length === 0 ? (
                  <p className="config-checks-ok">{t("config.checks.none")}</p>
                ) : (
                  <p className="muted">
                    {problems(checks).length === 1
                      ? t("config.checks.countOne")
                      : t("config.checks.countMany").replace("{n}", String(problems(checks).length))}
                  </p>
                )}
                {/* One row per group (build 153): *"if you have multiple errors
                    of one tag ... it feels like you can combine them, this
                    will save so much space."*  The gaps only a provider can
                    fill share one line; every other check keeps its own,
                    because each points at a different field. */}
                <ul className="config-check-list">
                  {checkRows(checks).map((row) => (
                    <li key={row.group} className={`config-check ${row.level}`}>
                      <FieldFlag checks={row.all} />
                      <span className="config-check-text">
                        <strong>{t(`health.group.${row.group}`)}</strong>
                        {row.refill.length > 0 && (
                          <span>
                            {t("config.checks.missingList").replace(
                              "{fields}",
                              row.refill.map((code) => t(`health.missing.${code}`)).join(", "),
                            )}
                          </span>
                        )}
                        {row.nice.length > 0 && (
                          <span className="config-check-nice">
                            {t("config.checks.niceList").replace(
                              "{fields}",
                              row.nice.map((code) => t(`health.missing.${code}`)).join(", "),
                            )}
                          </span>
                        )}
                        {row.others.map((check) => (
                          <span key={check.code} className="config-check-line">
                            {checkText(check)}
                            {"field" in check.where ? (
                              <button
                                type="button"
                                className="link-button"
                                onClick={() => {
                                  const target = check.where as { field: FieldKey | FieldKey[] };
                                  jumpTo(Array.isArray(target.field) ? target.field[0] : target.field);
                                }}
                              >
                                {t("config.checks.show")}
                              </button>
                            ) : check.where.action === "automate" ? (
                              <button type="button" className="link-button" disabled={refilling} onClick={() => void turnAutomateOn()}>
                                {t("config.checks.automate")}
                              </button>
                            ) : check.where.action === "chooseAnidb" ? (
                              <button type="button" className="link-button" onClick={() => setChoosingAnidb(true)}>
                                {t("config.checks.chooseAnidb")}
                              </button>
                            ) : check.where.action === "findFiles" && onFindFiles ? (
                              <button type="button" className="link-button" onClick={onFindFiles}>
                                {t("config.checks.findFiles")}
                              </button>
                            ) : null}
                          </span>
                        ))}
                      </span>
                    </li>
                  ))}
                </ul>
                {canRefill(checks) && (
                  <div className="profile-actions">
                    <button className="button secondary" disabled={refilling} onClick={() => void refill()}>
                      {refilling ? t("debug.refilling") : t("debug.refill")}
                    </button>
                    <small className="muted">{t("config.checks.refillHint")}</small>
                  </div>
                )}
              </>
            )}
            {refilled && (
              <p className={refilled.filled.length ? "episode-hint" : "muted"}>
                {refilled.unanswerable
                  ? t("debug.refillUnanswerable")
                  : refilled.filled.length
                    ? t("debug.refillFilled").replace(
                        "{fields}",
                        refilled.filled.map((code) => t(`health.missing.${code}`)).join(", "),
                      )
                    : t("debug.refillNothing")}
                {refilled.conflicts > 0 && ` ${t("debug.refillConflicts")}`}
              </p>
            )}
          </div>

          {/* What this anime *is*, as AniFlow knows it. Read-only: every value
              here is either the provider's or the foundation matching is built
              on, and both are changed elsewhere if at all. */}
          <div className="detail-panel">
            <h2>{t("config.identity")}</h2>
            <dl className="parsed-grid">
              <dt id={fieldAnchor("release_name")}>
                <FlaggedLabel checks={on("release_name")}>{t("config.releaseName")}</FlaggedLabel>
              </dt>
              <dd>{anime.release_name ?? "—"}</dd>
              <dt>{t("config.cleanedName")}</dt>
              <dd>{anime.local_name}</dd>
              <dt id={fieldAnchor("profile")}>
                <FlaggedLabel checks={on("profile")}>{t("config.profile")}</FlaggedLabel>
              </dt>
              {/* One line, from the profile itself (build 94). It printed the
                  *template* name above the profile's own values, so an anime
                  moved to Erai-raws 720p read "Erai-raws 1080p" on the first
                  line and "Erai-raws · 720p" on the second. The template it
                  was made from is on the Release profile panel, where it can
                  be changed. */}
              <dd>
                {profile
                  ? [
                      profile.subgroups.length ? profile.subgroups.join(", ") : t("config.anySubgroup"),
                      profile.resolution ?? t("config.anyResolution"),
                      t(`profile.censor.${profile.censor_preference}`),
                    ].join(" · ")
                  : t("profile.custom")}
              </dd>
            </dl>
            {/* Read-only here by design, and the flag says where instead:
                changing what an anime is released as can mean new downloads
                and renamed files, and the detail page's Change release panel
                is where that is shown before it happens. */}
            {(on("release_name").length > 0 || on("profile").length > 0) && (
              <p className="warning-message">{t("config.identityFixHint")}</p>
            )}
          </div>

          {/* Facts about the show that a provider may not know (build 152).
              Every one of them is a Metadata check, and until now none of
              them could be fixed by hand - the only way was the Refill
              button, which cannot help when no provider knows either. What
              is typed here is kept: the next refill leaves it alone. */}
          <div className="detail-panel">
            <h2>{t("config.details")}</h2>
            <div className="settings-grid">
              {(["title_romaji", "title_english", "title_native"] as const).map((key) => (
                <label className="field" key={key} id={fieldAnchor(key)}>
                  <FlaggedLabel checks={on(key)}>{t(`config.${key}`)}</FlaggedLabel>
                  <input
                    className={levelOf(on(key)) ? `flagged ${levelOf(on(key))}` : undefined}
                    value={facts[key]}
                    onChange={(event) => setFacts({ ...facts, [key]: event.target.value })}
                  />
                </label>
              ))}
              <label className="field" id={fieldAnchor("format")}>
                <FlaggedLabel checks={on("format")}>{t("config.format")}</FlaggedLabel>
                <select
                  className={levelOf(on("format")) ? `flagged ${levelOf(on("format"))}` : undefined}
                  value={facts.format}
                  onChange={(event) => setFacts({ ...facts, format: event.target.value })}
                >
                  <option value="">{t("config.scheduleUnknown")}</option>
                  {kinds.map((kind) => <option key={kind} value={kind}>{kindLabel(kind)}</option>)}
                </select>
              </label>
              <label className="field" id={fieldAnchor("air_start_date")}>
                <FlaggedLabel checks={on("air_start_date")}>{t("config.air_start_date")}</FlaggedLabel>
                <input
                  inputMode="numeric"
                  autoComplete="off"
                  placeholder={dateInputHint()}
                  className={
                    premiereInvalid
                      ? "flagged red"
                      : levelOf(on("air_start_date")) ? `flagged ${levelOf(on("air_start_date"))}` : undefined
                  }
                  aria-invalid={premiereInvalid}
                  value={premiereText}
                  onChange={(event) => {
                    const text = event.target.value;
                    setPremiereText(text);
                    if (text.trim() === "") setFacts({ ...facts, air_start_date: "" });
                    else {
                      const parsed = parseDateInput(text);
                      if (parsed !== null) setFacts({ ...facts, air_start_date: parsed });
                    }
                  }}
                />
              </label>
              <label className="field">
                <span>{t("config.season")}</span>
                <select value={facts.season} onChange={(event) => setFacts({ ...facts, season: event.target.value })}>
                  <option value="">{t("config.seasonFromCalendar")}</option>
                  {SEASON_NAMES.map((name) => <option key={name} value={name}>{t(`config.seasonName.${name}`)}</option>)}
                </select>
              </label>
              <label className="field">
                <span>{t("config.seasonYear")}</span>
                <select value={facts.season_year} onChange={(event) => setFacts({ ...facts, season_year: event.target.value })}>
                  <option value="">{t("config.seasonFromCalendar")}</option>
                  {seasonYears(facts.season_year).map((year) => <option key={year} value={year}>{year}</option>)}
                </select>
              </label>
              <label className="field" id={fieldAnchor("anidb_id")}>
                <FlaggedLabel checks={on("anidb_id")}>{t("config.anidb_id")}</FlaggedLabel>
                <input
                  inputMode="numeric"
                  className={levelOf(on("anidb_id")) ? `flagged ${levelOf(on("anidb_id"))}` : undefined}
                  value={facts.anidb_id}
                  onChange={(event) => setFacts({ ...facts, anidb_id: event.target.value })}
                />
              </label>
              {/* When it airs and how long it runs, in the same panel since
                  build 153 - "Airing and length" was three facts about the
                  show with a Save button of its own. */}
              <label className="field" id={fieldAnchor("airs_weekday")}>
                <FlaggedLabel checks={weekday === "" ? on("airs_weekday") : []}>{t("config.airsWeekday")}</FlaggedLabel>
                <select
                  className={weekday === "" && levelOf(on("airs_weekday")) ? `flagged ${levelOf(on("airs_weekday"))}` : undefined}
                  value={weekday}
                  onChange={(event) => setWeekday(event.target.value)}
                >
                  <option value="">{t("config.scheduleUnknown")}</option>
                  {WEEKDAYS.map((day, index) => (
                    <option key={day} value={index}>{t(`dashboard.weekday.${day}`)}</option>
                  ))}
                </select>
              </label>
              <label className="field" id={fieldAnchor("airs_hour")}>
                <FlaggedLabel checks={hour === "" ? on("airs_hour") : []}>{t("config.airsHour")}</FlaggedLabel>
                <select
                  className={hour === "" && levelOf(on("airs_hour")) ? `flagged ${levelOf(on("airs_hour"))}` : undefined}
                  value={hour}
                  onChange={(event) => setHour(event.target.value)}
                >
                  <option value="">{t("config.scheduleUnknown")}</option>
                  {HOURS.map((value) => (
                    <option key={value} value={value}>{formatClock(value + ":00")}</option>
                  ))}
                </select>
              </label>
              {(["genres", "studios"] as const).map((key) => (
                <label className="field" key={key} id={fieldAnchor(key)}>
                  <FlaggedLabel checks={on(key)}>{t(`config.${key}`)}</FlaggedLabel>
                  <input
                    className={levelOf(on(key)) ? `flagged ${levelOf(on(key))}` : undefined}
                    value={facts[key]}
                    placeholder={t("config.listPlaceholder")}
                    onChange={(event) => setFacts({ ...facts, [key]: event.target.value })}
                  />
                </label>
              ))}
              <label className="field" id={fieldAnchor("episode_count")}>
                <FlaggedLabel checks={on("episode_count")}>{t("config.episodeCount")}</FlaggedLabel>
                <input
                  className={levelOf(on("episode_count")) ? `flagged ${levelOf(on("episode_count"))}` : undefined}
                  type="number"
                  min={1}
                  max={10000}
                  value={episodeCount}
                  onChange={(event) => setEpisodeCount(event.target.value)}
                />
              </label>
            </div>
            <label className="field config-synopsis" id={fieldAnchor("synopsis")}>
              <FlaggedLabel checks={on("synopsis")}>{t("config.synopsis")}</FlaggedLabel>
              <textarea
                className={levelOf(on("synopsis")) ? `flagged ${levelOf(on("synopsis"))}` : undefined}
                rows={4}
                value={facts.synopsis}
                onChange={(event) => setFacts({ ...facts, synopsis: event.target.value })}
              />
            </label>
            <p className="muted">
              {t("config.detailsHint")} {t("config.scheduleHint")}
              {hasOverride && (
                <> <span className="imported-tag">{t("config.scheduleManual")}</span></>
              )}
            </p>
            <div className="profile-actions">
              <button className="button primary" disabled={factsSaving || scheduleSaving} onClick={() => void saveFacts()}>
                {factsSaving ? t("settings.saving") : t("settings.save")}
              </button>
              {/* Offered only when there is an override to drop: with none it
                  would read as a way to clear the provider's own values, which
                  it is not. */}
              {hasOverride && (
                <button className="button secondary" disabled={factsSaving || scheduleSaving} onClick={() => void saveSchedule(true)}>
                  {t("config.scheduleReset")}
                </button>
              )}
              {!factsSaving && !scheduleSaving && Math.max(factsSavedAt, scheduleSavedAt) > 0 && <small className="imported-tag">{t("profile.saved")}</small>}
            </div>
          </div>

          {/* Where the files go. Editable, and honest about what a rename does
              and does not do - unless Shoko Server owns the naming. */}
          <div className="detail-panel">
            <h2>{t("config.folders")}</h2>
            <div className="settings-grid two">
              <label className="field" id={fieldAnchor("media_folder")}>
                <FlaggedLabel checks={on("media_folder")}>{t("config.mediaFolder")}</FlaggedLabel>
                <input
                  className={levelOf(on("media_folder")) ? `flagged ${levelOf(on("media_folder"))}` : undefined}
                  value={folderIsShokos && !mediaFolder ? t("config.folderPending") : mediaFolder}
                  readOnly={folderIsShokos}
                  onChange={(event) => setMediaFolder(event.target.value)}
                />
              </label>
              {/* The hand-over folder is AniFlow's to name, so unlike the
                  library folder it can be typed in Shoko Server mode - which
                  is the fix when a file is waiting in a folder AniFlow has
                  lost track of (build 152). */}
              {folderIsShokos && (
                <label className="field" id={fieldAnchor("import_folder")}>
                  <FlaggedLabel checks={on("import_folder")}>{t("config.importFolder")}</FlaggedLabel>
                  <input
                    className={levelOf(on("import_folder")) ? `flagged ${levelOf(on("import_folder"))}` : undefined}
                    value={importFolder}
                    placeholder={anime.local_name}
                    onChange={(event) => setImportFolder(event.target.value)}
                  />
                </label>
              )}
            </div>
            {/* The roots come from the deployment; only the folder inside them
                belongs to the anime, so show the full path being built. */}
            {anime.storage && (
              <dl className="parsed-grid" style={{ marginTop: 12 }}>
                <dt>{t("config.resolvedMedia")}</dt>
                <dd>
                  {/* Shoko names this folder, so until it has filed the first
                      episode there is no path to show - and inventing one
                      would read as a promise AniFlow cannot keep. */}
                  {mediaFolder
                    ? `${anime.storage.media_root}/${mediaFolder}`
                    : folderIsShokos
                      ? <em className="muted">{t("config.folderPending")}</em>
                      : `${anime.storage.media_root}/…`}
                </dd>
                <dt>{t("config.resolvedDownload")}</dt>
                <dd>{anime.storage.download_root}</dd>
                {folderIsShokos && (
                  <>
                    <dt>{t("config.resolvedImport")}</dt>
                    {/* A folder per anime, with the release's own filename
                        inside it - Shoko reads subfolders and does the
                        renaming. Named after the anime, because this folder is
                        AniFlow's to name; the library one is not. */}
                    <dd>
                      {anime.storage.import_root
                        ? `${anime.storage.import_root}/${anime.import_folder ?? anime.local_name}`
                        : t("settings.pathMissing")}
                    </dd>
                  </>
                )}
              </dl>
            )}
            {folderChanged && !folderIsShokos && (
              /* Shown while the change is still pending, which is the only
                 moment the warning can still be acted on. */
              <p className="warning-message">{t("config.folderWarning")}</p>
            )}
            {/* A notice rather than a muted line, because it explains why
                the field above cannot be typed in (build 94). */}
            {folderIsShokos
              ? <p className="warning-message">{t("config.folderShokoHint")}</p>
              : <p className="muted">{t("config.folderHint")}</p>}
            {error && <p className="error-message">{error}</p>}
            {/* In Shoko Server mode only once the hand-over folder has been
                changed: the library folder is read-only there, and a Save
                button with nothing to save was the complaint in build 94. */}
            {(!folderIsShokos || importChanged) && (
              <div className="profile-actions">
                <button className="button primary" disabled={saving} onClick={() => void saveFolders()}>
                  {saving ? t("settings.saving") : t("settings.save")}
                </button>
                {!saving && savedAt > 0 && <small className="imported-tag">{t("profile.saved")}</small>}
              </div>
            )}
          </div>

          {/* Artwork. The provider's image is the default; this replaces it. */}
          <div className="detail-panel" id={fieldAnchor("cover")}>
            <h2><FlaggedLabel checks={on("cover")}>{t("config.artwork")}</FlaggedLabel></h2>
            <div className="cover-editor">
              <div className="cover-preview" key={coverKey}>
                <Cover anime={{ ...anime, has_custom_cover: hasCustomCover }} />
              </div>
              <div>
                <p className="muted" style={{ marginTop: 0 }}>{t("config.artworkHint")}</p>
                <div className="profile-actions" style={{ marginTop: 10 }}>
                  <button
                    className="button secondary"
                    disabled={coverBusy}
                    onClick={() => filePicker.current?.click()}
                  >
                    {coverBusy ? t("settings.saving") : hasCustomCover ? t("config.replaceCover") : t("config.uploadCover")}
                  </button>
                  {hasCustomCover && (
                    <button className="button danger" disabled={coverBusy} onClick={() => void dropCover()}>
                      {t("config.removeCover")}
                    </button>
                  )}
                </div>
                <input
                  ref={filePicker}
                  type="file"
                  accept="image/png,image/jpeg,image/webp,image/gif"
                  hidden
                  onChange={(event) => void pickCover(event.target.files?.[0])}
                />
              </div>
            </div>
          </div>

          {/* Both of these are developer views (build 94): one asks a feed
              what it is carrying, the other prints the database row. Behind
              debug_mode, like the Debug tab and the Release Inspector. */}
          {debugMode && <FeedCheckPanel anime={anime} onUpdatedDetail={onUpdated} />}

          {debugMode && (
          <div className="detail-panel">
            <div className="status-line">
              {/* Already behind debug_mode since build 94 - what it never
                  said was *why* it is there. Marked, on request, so it reads
                  as a developer view rather than as something the interface
                  expects anybody to use. */}
              <h2><span className="chip">{t("debug.title")}</span> {t("detail.dbDumpTitle")}</h2>
              <button className="button secondary" disabled={dbDumpLoading} onClick={() => void loadDbDump()}>
                {dbDumpLoading ? "…" : t("detail.dbDump")}
              </button>
              {/* Asked for because selecting JSON out of a scrolling <pre>
                  with the mouse is miserable: *"its always annoying to copy
                  it out of this windows"*. */}
              {dbDump && (
                <button className="button secondary" onClick={() => void copyDump()}>
                  {copied ? t("detail.dbDumpCopied") : t("detail.dbDumpCopy")}
                </button>
              )}
            </div>
            <p className="muted">{t("config.dbDumpHint")}</p>
            {dbDump && (
              <pre className="db-dump">{JSON.stringify(dbDump, null, 2)}</pre>
            )}
          </div>
          )}
        </div>
      </section>
      {choosingAnidb && (
        <AnidbChooserDialog
          animeId={anime.id}
          animeName={anime.display_name}
          onClose={() => setChoosingAnidb(false)}
          onChosen={() => {
            void getAnime(anime.id).then(onUpdated);
            void recheck();
          }}
        />
      )}
    </div>
  );
}

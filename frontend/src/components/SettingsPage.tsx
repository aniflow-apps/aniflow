import { Fragment, useEffect, useRef, useState } from "react";
import {
  changeAccount,
  checkExitAddress,
  checkPaths,
  getAbout,
  getAppSettings,
  listThemes,
  sendDebugLog,
  testQbittorrentConnection,
  testShokoConnection,
  updateAppSettings,
  type AppSettingsView,
  type AppAbout,
  type AuthState,
  type ExitAddress,
  type InstalledTheme,
  type PathCheck,
  type ThemeList,
} from "../api/client";
import { formatUptime } from "../aboutFormat";
import { formatBytes } from "../formatBytes";
import { formatTimestamp } from "../time";
import { applyTheme } from "../theme";
import { addressForClient, type TorrentClient } from "../clientUrl";
import { t } from "../i18n";
import { savableSettings } from "../settingsFields";
import {
  type EditKind,
  WAIT_MS,
  holdProxy,
  kindOfControl,
  mergeAfterSave,
  proxyIsComplete,
} from "../settingsAutosave";
import { useToast } from "../toasts";
import { TitleOrder } from "./TitleOrder";
import { TvDevicesPanel } from "./TvDevicesPanel";
import { BackupPanel } from "./BackupPanel";
// The kinds the calendar can show, and what each one is called. Shared with
// the cards since build 148, so a box and the entries it governs read alike.
import { CALENDAR_KINDS, kindLabel } from "../calendarView";

type TestState = { ok: boolean; detail: string | null } | null;

/**
 * The tabs, in the order they are shown.
 *
 * The page was one scroll of thirteen panels, which is a lot of scrolling to
 * change a download folder. Grouped by what somebody is trying to do rather
 * than by which part of the code owns the setting.
 */
const TABS = [
  "general",
  "watchlist",
  // Downloads (build 260): the torrent client and what happens after a download finishes, which sat third on
  // Integrations behind the TV devices and the proxy. Hidden in Follow mode, where nothing is downloaded.
  "downloads",
  "calendar",
  "storage",
  "shoko",
  "integrations",
  "notifications",
  "users",
  // Backup and About (build 268) are tabs of their own: both were at the foot of General, below a long page.
  "backup",
  "about",
  "debug",
] as const;

type SettingsTab = (typeof TABS)[number];

/** The About panel's folders, by the name the server gives them. */
const FOLDER_LABEL: Record<string, string> = {
  data: "settings.about.folderData",
  config: "settings.about.folderConfig",
  downloads: "settings.about.folderDownloads",
  media: "settings.about.folderMedia",
};

/** The tab a link asked for, or General when it named nothing usable. */
function asTab(name: string | undefined): SettingsTab {
  return TABS.includes(name as SettingsTab) ? (name as SettingsTab) : "general";
}

/** The stored string as a set. */
function hiddenKinds(stored: string): Set<string> {
  return new Set(
    stored.split(",").map((part) => part.trim().toUpperCase()).filter(Boolean),
  );
}

/** The stored string with one kind hidden or shown. */
function withKind(stored: string, kind: string, hide: boolean): string {
  const hidden = hiddenKinds(stored);
  if (hide) hidden.add(kind);
  else hidden.delete(kind);
  return [...hidden].sort().join(",");
}

export function SettingsPage({
  section,
  onSection,
  language,
  onLanguageChange,
  username,
  onAccountChanged,
  onSaved,
  basicMode = false,
}: {
  /** Which tab the link asked for, from the hash (`#/settings/calendar`). */
  section?: string;
  /** Called when the tab changes, so the URL keeps up with the page. */
  onSection?: (name: string) => void;
  language: "en" | "de";
  onLanguageChange: (next: "en" | "de") => void;
  /** Follow mode: the panels for machinery that is not running stay hidden.
   *  Passed in rather than fetched again - App already derives it from
   *  /api/system/info, and two reads could disagree for a moment. */
  basicMode?: boolean;
  /** Shown as the placeholder, so an empty field means "keep it". */
  username?: string | null;
  onAccountChanged?: (next: AuthState) => void;
  /** The saved settings, for the few the whole app reads - the notification
   *  corner is loaded once at startup, and picking a new one here has to move
   *  it now rather than after a reload. */
  onSaved?: (settings: AppSettingsView) => void;
}) {
  const [settings, setSettings] = useState<AppSettingsView | null>(null);
  const [qbPassword, setQbPassword] = useState("");
  /* Saving on change (build 261). `dirty` is "something was edited and is not saved yet", `saving` a request on
     its way, `result` how the last one ended. `held` is a proxy that is switched on but not complete: the rest was
     saved, the proxy waits for its host and port (settingsAutosave.ts). */
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<"saved" | "error" | "held" | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [changes, setChanges] = useState(0);
  const editKind = useRef<EditKind>("quick");
  const timer = useRef<number | undefined>(undefined);
  const inflight = useRef<Promise<void> | null>(null);
  const again = useRef(false);
  const dirtyNow = useRef(false);
  const stored = useRef<AppSettingsView | null>(null);
  /* What a save reads: the latest render's values, so a save that starts from a timer or a blur never uses a stale
     closure. */
  const live = useRef<{ settings: AppSettingsView | null; qbPassword: string; proxyPassword: string; shokoKey: string; language: string }>({
    settings: null, qbPassword: "", proxyPassword: "", shokoKey: "", language,
  });
  const saveLatest = useRef<() => Promise<void>>(async () => undefined);
  const [qbTest, setQbTest] = useState<TestState>(null);
  const [testing, setTesting] = useState<"qb" | null>(null);
  const [pathCheck, setPathCheck] = useState<PathCheck | null>(null);
  const [checkingPaths, setCheckingPaths] = useState(false);
  const [exitAddress, setExitAddress] = useState<ExitAddress | null>(null);
  const [checkingExit, setCheckingExit] = useState(false);
  const [proxyPassword, setProxyPassword] = useState("");
  const [shokoKey, setShokoKey] = useState("");
  const [shokoTest, setShokoTest] = useState<TestState>(null);
  const [testingShoko, setTestingShoko] = useState(false);
  const [accountName, setAccountName] = useState("");
  const [accountCurrent, setAccountCurrent] = useState("");
  const [accountNew, setAccountNew] = useState("");
  const [accountBusy, setAccountBusy] = useState(false);
  const [accountSavedAt, setAccountSavedAt] = useState(0);
  const [accountError, setAccountError] = useState<string | null>(null);
  const { show } = useToast();
  const [sendingDebugLog, setSendingDebugLog] = useState(false);
  const [debugLogSentAt, setDebugLogSentAt] = useState(0);
  const [seedingNote, setSeedingNote] = useState(false);
  /* Opened on the tab the link asked for. A section nobody recognises falls
     back to General rather than showing an empty page - a stale bookmark
     should be harmless, not broken. */
  const [tabAsked, setTab] = useState<SettingsTab>(() => asTab(section));

  /* Follows the URL, so the back button moves between tabs rather than out of
     Settings altogether. */
  useEffect(() => { setTab(asTab(section)); }, [section]);
  const [themes, setThemes] = useState<ThemeList | null>(null);
  const [about, setAbout] = useState<AppAbout | null>(null);

  useEffect(() => {
    void getAppSettings()
      .then((loaded) => { stored.current = loaded; setSettings(loaded); })
      .catch(() => setLoadFailed(true));
    void listThemes().then(setThemes).catch(() => setThemes(null));
    void getAbout().then(setAbout).catch(() => setAbout(null));
  }, []);

  /* Every edit goes through here: it marks the page unsaved and wakes the autosave below. */
  function touch() {
    dirtyNow.current = true;
    setDirty(true);
    setChanges((count) => count + 1);
  }

  function patch(update: Partial<AppSettingsView>) {
    setSettings((current) => (current ? { ...current, ...update } : current));
    touch();
  }

  /* Waits as long as the kind of control says (settingsAutosave.ts) and then saves; a password waits for the
     field to be left. Leaving a field, or the page, saves at once. */
  useEffect(() => {
    if (changes === 0) return;
    const wait = WAIT_MS[editKind.current];
    editKind.current = "quick";
    window.clearTimeout(timer.current);
    timer.current = wait === null ? undefined : window.setTimeout(() => { timer.current = undefined; void saveLatest.current(); }, wait);
  }, [changes]);

  function flush() {
    if (dirtyNow.current) void saveLatest.current();
  }
  // Leaving the page with something unsaved saves it first.
  useEffect(() => () => { window.clearTimeout(timer.current); if (dirtyNow.current) void saveLatest.current(); }, []);

  /**
   * Moving the file and leaving the torrent seeding cannot both hold - the data
   * the torrent would serve is gone. Switching to Move therefore drops "leave
   * it seeding" and says so; the API enforces the same rule.
   */
  function setFileOperation(next: "copy" | "move" | "hardlink") {
    if (next === "move" && settings?.after_import_action === "keep") {
      patch({ file_operation: next, after_import_action: "remove" });
      setSeedingNote(true);
      return;
    }
    setSeedingNote(false);
    patch({ file_operation: next });
  }

  /**
   * Save what is on screen. One request at a time: a save asked for while one is on its way runs again when that
   * one is done, with what is on screen by then. The promise is for the callers that want to act afterwards (the
   * test toasts, the proxy check).
   */
  async function save(): Promise<void> {
    window.clearTimeout(timer.current);
    timer.current = undefined;
    if (inflight.current) {
      again.current = true;
      return inflight.current;
    }
    const run = (async () => {
      do {
        again.current = false;
        await saveOnce();
      } while (again.current);
    })();
    inflight.current = run;
    try {
      await run;
    } finally {
      inflight.current = null;
    }
  }

  async function saveOnce() {
    const now = live.current;
    if (!now.settings) return;
    dirtyNow.current = false;
    setDirty(false);
    setSaving(true);
    try {
      // Everything on screen, minus what settingsFields.ts excludes.
      let payload = savableSettings(now.settings);
      // The dropdown switches the interface immediately, so the live value is
      // the truth and the stored one may be a step behind.
      payload.language = now.language;
      if (now.qbPassword !== "") payload.qb_password = now.qbPassword;
      if (now.shokoKey !== "") payload.shoko_api_key = now.shokoKey;
      const proxyReady = proxyIsComplete(now.settings);
      if (proxyReady) {
        if (now.proxyPassword !== "") payload.proxy_password = now.proxyPassword;
      } else if (stored.current) {
        payload = holdProxy(payload, stored.current);
      }
      const answer = await updateAppSettings(payload as Parameters<typeof updateAppSettings>[0]);
      stored.current = answer;
      // The server's answer decides what was accepted - except for what was typed while the request was on its
      // way, which stays on screen (and is saved next).
      setSettings((current) => (current ? mergeAfterSave(answer, now.settings as AppSettingsView, current) : answer));
      setQbPassword((typed) => (typed === now.qbPassword ? "" : typed));
      setShokoKey((typed) => (typed === now.shokoKey ? "" : typed));
      if (proxyReady) setProxyPassword((typed) => (typed === now.proxyPassword ? "" : typed));
      onSaved?.(answer);
      setResult(proxyReady ? "saved" : "held");
    } catch {
      setResult("error");
    } finally {
      setSaving(false);
    }
  }
  saveLatest.current = save;
  live.current = { settings, qbPassword, proxyPassword, shokoKey, language };

  /**
   * Ask what the internet sees. Saves first, deliberately.
   *
   * A proxy is only meaningfully tested once it is the one in use - testing a
   * draft would answer a question about a configuration that is not running,
   * which is the opposite of what this button is for.
   */
  /**
   * Change the username and/or the password.
   *
   * Separate from Save settings on purpose: everything else on this page is a
   * preference, and this is the credential that gets you back in. Mixing them
   * into one button would mean typing a password to change a scan interval.
   */
  async function saveAccount() {
    setAccountBusy(true);
    setAccountError(null);
    try {
      const saved = await changeAccount({
        current_password: accountCurrent,
        username: accountName.trim() || undefined,
        new_password: accountNew || undefined,
      });
      onAccountChanged?.(saved);
      setAccountCurrent("");
      setAccountNew("");
      setAccountSavedAt(Date.now());
    } catch (err) {
      setAccountError(err instanceof Error ? err.message : String(err));
    } finally {
      setAccountBusy(false);
    }
  }

  /**
   * Ask Shoko whether it is there and whether the key works.
   *
   * Draft values, so this works before saving - the same as the qBittorrent
   * button beside it, and for the same reason: a credential you
   * cannot test until you have committed it is a credential you test by
   * guessing.
   */
  async function runShokoTest() {
    setTestingShoko(true);
    setShokoTest(null);
    try {
      setShokoTest(
        await testShokoConnection({
          url: settings?.shoko_url || undefined,
          api_key: shokoKey === "" ? undefined : shokoKey,
        }),
      );
    } catch (err) {
      setShokoTest({ ok: false, detail: err instanceof Error ? err.message : String(err) });
    } finally {
      setTestingShoko(false);
    }
  }

  async function runExitCheck() {
    setCheckingExit(true);
    setExitAddress(null);
    try {
      await save();
      setExitAddress(await checkExitAddress());
    } catch {
      setResult("error");
    } finally {
      setCheckingExit(false);
    }
  }

  async function runTest(which: "qb") {
    setTesting(which);
    const apply = (result: { ok: boolean; detail: string | null }) => setQbTest(result);
    try {
      // Draft values are tested directly - saving is NOT required.
      const result = await testQbittorrentConnection({
        url: settings?.qb_url ?? undefined,
        username: settings?.qb_username ?? undefined,
        password: qbPassword === "" ? undefined : qbPassword,
        client: settings?.torrent_client,
      });
      if (result.ok === false && result.detail === null) {
        apply({ ok: false, detail: "Missing URL (and credentials) - fill in the fields above." });
      } else {
        apply(result);
      }
    } catch (err) {
      apply({ ok: false, detail: err instanceof Error ? err.message : String(err) });
    } finally {
      setTesting(null);
    }
  }

  async function runDebugLog() {
    setSendingDebugLog(true);
    try {
      await sendDebugLog();
      setDebugLogSentAt(Date.now());
    } catch {
      setDebugLogSentAt(0);
    } finally {
      setSendingDebugLog(false);
    }
  }

  if (!settings) return loadFailed ? <p className="error-message">{t("settings.error")}</p> : null;

  /* The Debug tab exists only when aniflow.yaml says so, and there is no
     control for that here on purpose - a switch that hides itself is the one
     thing a user should not be able to press by accident. core/config.py. */
  // Debug is off unless aniflow.yaml says otherwise, and the Shoko tab only
  // exists in the mode where Shoko is part of the pipeline.
  const shown = TABS.filter((name) => {
    if (name === "debug") return settings.debug_mode;
    if (name === "shoko") return settings.mode === "shokoserver";
    if (name === "downloads") return !basicMode;
    return true;
  });
  /* The tab on screen. A bookmark to one that is hidden here (#/settings/downloads in Follow mode) shows
     General rather than a page with a tab row and nothing under it. */
  const tab: SettingsTab = shown.includes(tabAsked) ? tabAsked : "general";
  /* What the theme says about itself. Undefined while the list is loading,
     and also when the setting names a theme that is not installed - which
     the dropdown says out loud rather than papering over. */
  const selectedTheme: InstalledTheme | undefined = themes?.themes.find(
    (theme) => theme.slug === settings.theme,
  );

  return <section
    className="settings-page"
    onChangeCapture={(event) => {
      const target = event.target as HTMLInputElement;
      editKind.current = kindOfControl(target.tagName, target.type ?? "");
    }}
    onBlur={flush}
  >
    {/* Saving on change (build 261): this line is what the Save button used to say. It is always there, so the
        page does not jump when it changes. */}
    <p className={`settings-status ${saving ? "saving" : result === "error" ? "error" : dirty ? "pending" : result === "held" ? "held" : "saved"}`}
       role="status" aria-live="polite">
      {saving ? t("settings.status.saving")
        : result === "error" ? <>{t("settings.status.error")} <button type="button" className="link-button" onClick={() => void save()}>{t("settings.status.retry")}</button></>
        : dirty ? t("settings.status.pending")
        : result === "held" ? t("settings.status.proxyHeld")
        : result === "saved" ? t("settings.status.saved") : ""}
    </p>
    {/* Along the top, because the left is already the navigation and a second
        column of names beside it reads as one list broken in half. */}
    <div className="settings-tabs" role="tablist">
      {shown.map((name) => (
        <button
          key={name}
          type="button"
          role="tab"
          aria-selected={tab === name}
          className={`settings-tab ${tab === name ? "active" : ""}`}
          onClick={() => { setTab(name); onSection?.(name); }}
        >
          {t(`settings.tab.${name}`)}
        </button>
      ))}
    </div>

    {/* Reported, never offered. The mode decides who owns the media root and
        who names its folders, so switching it on a library that already has
        files would split every anime in two - which is why it lives in
        docker-compose and is checked against the database at startup. */}
    {tab === "storage" && (
      <div className="detail-panel">
        <h2>{t("settings.mode")}</h2>
        <label className="field">
          <span>{t("settings.modeActive")}</span>
          <input value={t(`settings.mode.${settings.mode}`)} readOnly />
        </label>
        <p className="muted">{t(`settings.modeHint.${settings.mode}`)}</p>
        <p className="warning-message">{t("settings.modeWarning")}</p>
      </div>
    )}

    {/* The old General panel held five controls belonging to three tabs, so
        it is the one that had to come apart: the language and the settings
        file are general, the log level is about logging, and the timezone and
        the log test are developer tools. */}
    {tab === "general" && (
      <div className="detail-panel">
        <h2>{t("settings.general")}</h2>
        <div className="settings-grid two">
          <label className="field">
            <span>{t("settings.language")}</span>
            <select value={language} onChange={(event) => { onLanguageChange(event.target.value as "en" | "de"); touch(); }}>
              <option value="en">English</option>
              <option value="de">Deutsch</option>
            </select>
          </label>
          <label className="field">
            <span>{t("settings.configFile")}</span>
            <input value={settings.config_file} readOnly />
          </label>
          {/* Below the language, because it is the same kind of question:
              how this installation should read, rather than what it does.
              The browser's own locale decided until build 147, which on a
              machine set to US English gives 04:30 PM and 7/3/2026 however
              European the person reading it is. */}
          <label className="field">
            <span>{t("settings.timeFormat")}</span>
            <select
              value={settings.time_format}
              onChange={(event) => patch({ time_format: event.target.value })}
            >
              <option value="24h">{t("settings.time24")}</option>
              <option value="12h">{t("settings.time12")}</option>
            </select>
          </label>
          <label className="field">
            <span>{t("settings.dateFormat")}</span>
            <select
              value={settings.date_format}
              onChange={(event) => patch({ date_format: event.target.value })}
            >
              <option value="dmy">{t("settings.dateDmy")}</option>
              <option value="mdy">{t("settings.dateMdy")}</option>
              <option value="ymd">{t("settings.dateYmd")}</option>
            </select>
          </label>
        </div>
        <p className="muted">{t("settings.configFileHint")}</p>
      </div>
    )}

    {/* Themes (build 89). The fields describe a theme; the system that can
        load a second one is build 90's, so the dropdown offers the one that
        exists and the rest is what AniFlow knows about it. */}
    {tab === "general" && (
      <div className="detail-panel">
        <h2>{t("settings.themes")}</h2>
        <p className="muted">{t("settings.themesHint")}</p>
        <div className="settings-grid two">
          <label className="field">
            <span>{t("settings.theme")}</span>
            {/* Applied as it is chosen, and saved with the rest on Save. A
                theme you cannot see until you press a button somewhere else
                is a theme you cannot choose between. */}
            <select
              value={settings.theme}
              onChange={(event) => {
                patch({ theme: event.target.value });
                applyTheme(event.target.value);
              }}
            >
              {(themes?.themes ?? []).map((theme) => (
                <option key={theme.slug} value={theme.slug} disabled={!theme.usable}>
                  {theme.name}
                  {theme.builtin ? "" : ` — ${t("settings.themeYours")}`}
                  {theme.usable ? "" : ` — ${t("settings.themeUnusable")}`}
                </option>
              ))}
              {/* The setting can name a theme whose folder has since gone.
                  Kept in the list so the dropdown shows what is actually
                  selected rather than silently reading as another theme. */}
              {themes !== null
                && !themes.themes.some((theme) => theme.slug === settings.theme) && (
                <option value={settings.theme}>
                  {settings.theme} — {t("settings.themeMissing")}
                </option>
              )}
            </select>
          </label>
          <label className="field">
            <span>{t("settings.themeAuthor")}</span>
            <input value={selectedTheme?.author ?? ""} readOnly />
          </label>
          <label className="field">
            <span>{t("settings.themeVersion")}</span>
            <input value={selectedTheme?.version ?? ""} readOnly />
          </label>
          <label className="field">
            <span>{t("settings.themeDescription")}</span>
            <input value={selectedTheme?.description ?? ""} readOnly />
          </label>
        </div>
        {/* Where to put your own, and what format it has to be written for.
            Said here because this is where somebody wondering about it is
            already looking. */}
        {themes && (
          <p className="muted">
            {t("settings.themeFolder")
              .replace("{folder}", themes.folder)
              .replace("{version}", String(themes.themes_api))}
          </p>
        )}
        {/* Anything wrong with any installed theme, named. A theme that
            vanishes from the list because of a typo is one nobody can fix. */}
        {(themes?.themes ?? [])
          .filter((theme) => theme.problems.length > 0)
          .map((theme) => (
            <p className="reasons" key={theme.slug}>
              {theme.name}: {theme.problems.join(" · ")}
            </p>
          ))}
      </div>
    )}

    {tab === "calendar" && (
      <div className="detail-panel">
        <h2>{t("settings.calendar.filters")}</h2>
        <label className="check" style={{ margin: "4px 0 10px" }}>
          <input type="checkbox" checked={settings.calendar_hide_in_library}
                 onChange={(event) => patch({ calendar_hide_in_library: event.target.checked })} />
          <span>{t("calendar.hideInLibrary")}</span>
        </label>
        <label className="check" style={{ margin: "4px 0 10px" }} title={t("calendar.hideAdultHint")}>
          <input type="checkbox" checked={settings.calendar_hide_adult}
                 onChange={(event) => patch({ calendar_hide_adult: event.target.checked })} />
          <span>{t("calendar.hideAdult")}</span>
        </label>
        <label className="check" style={{ margin: "4px 0 10px" }} title={t("calendar.hideNoCoverHint")}>
          <input type="checkbox" checked={settings.calendar_hide_no_cover}
                 onChange={(event) => patch({ calendar_hide_no_cover: event.target.checked })} />
          <span>{t("calendar.hideNoCover")}</span>
        </label>

        <h2 style={{ marginTop: 22 }}>{t("settings.calendar.kinds")}</h2>
        <p className="muted">{t("settings.calendar.kindsHint")}</p>
        {/* One box per kind, and the setting stores the ones to *hide* - so a
            kind a source invents next year is shown rather than silently
            dropped. Appearing is the safer failure. */}
        <div className="settings-grid three">
          {CALENDAR_KINDS.map((kind) => {
            const hidden = hiddenKinds(settings.calendar_hidden_kinds);
            return (
              <label className="check" key={kind}>
                <input
                  type="checkbox"
                  checked={!hidden.has(kind)}
                  onChange={(event) => patch({
                    calendar_hidden_kinds: withKind(
                      settings.calendar_hidden_kinds, kind, !event.target.checked,
                    ),
                  })}
                />
                {/* The same words the cards use, so a box and the entries
                    it governs cannot read differently. TV_SHORT had neither
                    until build 148 - it was on the grid as its own raw value
                    and had no box here at all. */}
                <span>{kindLabel(kind)}</span>
              </label>
            );
          })}
        </div>

        <h2 style={{ marginTop: 22 }}>{t("settings.calendar.marks")}</h2>
        <label className="check" style={{ margin: "4px 0 10px" }} title={t("settings.calendar.markSequelsHint")}>
          <input type="checkbox" checked={settings.calendar_mark_sequels}
                 onChange={(event) => patch({ calendar_mark_sequels: event.target.checked })} />
          <span>{t("settings.calendar.markSequels")}</span>
        </label>
        <p className="muted">{t("settings.calendar.markSequelsHint")}</p>
        <label className="check" style={{ margin: "12px 0 10px" }} title={t("settings.calendar.showPopularityHint")}>
          <input type="checkbox" checked={settings.calendar_show_popularity}
                 onChange={(event) => patch({ calendar_show_popularity: event.target.checked })} />
          <span>{t("settings.calendar.showPopularity")}</span>
        </label>
        <p className="muted">{t("settings.calendar.showPopularityHint")}</p>
      </div>
    )}

    {tab === "notifications" && (
      <div className="detail-panel">
        <h2>{t("settings.logOptions")}</h2>
        <label className="field">
          <span>{t("settings.logLevel")}</span>
          <select
            value={settings.log_level}
            onChange={(event) => patch({ log_level: event.target.value as AppSettingsView["log_level"] })}
          >
            <option value="ERROR">{t("settings.logLevel.ERROR")}</option>
            <option value="WARNING">{t("settings.logLevel.WARNING")}</option>
            <option value="INFO">{t("settings.logLevel.INFO")}</option>
            <option value="DEBUG">{t("settings.logLevel.DEBUG")}</option>
          </select>
        </label>
        <p className="muted">{t("settings.logOptionsHint")}</p>
      </div>
    )}

    {/* A developer tool: the log test only makes sense beside a log. (The timezone field that sat here is gone
        since build 260 - it comes from docker-compose and is not edited in the interface.) */}
    {tab === "debug" && (
      <div className="detail-panel">
        <h2>{t("settings.debugEnvironment")}</h2>
        <div className="settings-grid two">
          <div className="field">
            <span>{t("settings.debugLog")}</span>
            {/* Set the level on the General tab, press this, count the lines.
                It applies to the running app, so it is usable while whatever
                you are chasing is still happening. */}
            <button className="button secondary" disabled={sendingDebugLog} onClick={() => void runDebugLog()}>
              {sendingDebugLog ? t("settings.testing") : t("settings.debugLogButton")}
            </button>
            {debugLogSentAt > 0 && <small className="imported-tag">{t("settings.debugLogSent")}</small>}
          </div>
        </div>
      </div>
    )}

    {/* Normally decided by whether an indexer exists. The override is a
        testing aid and says so - it will be removed. */}
    {tab === "debug" && (
      <div className="detail-panel">
        <h2>{t("settings.appMode")}</h2>
        <p className="muted">{t("settings.appModeHint")}</p>
        <div className="settings-grid two">
          <label className="field">
            <span>{t("settings.appMode")}</span>
            <select
              value={settings.app_mode_override}
              onChange={(event) => patch({ app_mode_override: event.target.value as AppSettingsView["app_mode_override"] })}
            >
              <option value="">{t("settings.appMode.auto")}</option>
              <option value="basic">{t("settings.appMode.basic")}</option>
              <option value="advanced">{t("settings.appMode.advanced")}</option>
            </select>
          </label>
          <div className="field">
            <span>{t("settings.appModeNow")}</span>
            <input value={settings.app_mode} readOnly />
            {!settings.has_indexer && <small className="muted">{t("settings.appModeNoIndexer")}</small>}
          </div>
        </div>
      </div>
    )}

    {tab === "users" && (
      <div className="detail-panel">
        <h2>{t("settings.account")}</h2>
        <p className="muted">{t("settings.accountHint")}</p>
        <div className="settings-grid">
          <label className="field">
            <span>{t("settings.accountUsername")}</span>
            <input
              value={accountName}
              placeholder={username ?? ""}
              onChange={(event) => setAccountName(event.target.value)}
            />
          </label>
          <label className="field">
            <span>{t("settings.accountCurrent")}</span>
            <input
              type="password"
              autoComplete="current-password"
              value={accountCurrent}
              onChange={(event) => setAccountCurrent(event.target.value)}
            />
          </label>
          <label className="field">
            <span>{t("settings.accountNew")}</span>
            <input
              type="password"
              autoComplete="new-password"
              value={accountNew}
              onChange={(event) => setAccountNew(event.target.value)}
            />
          </label>
        </div>
        {accountError && <p className="error-message">{accountError}</p>}
        <div className="profile-actions">
          <button
            className="button secondary"
            disabled={accountBusy || !accountCurrent || (!accountName.trim() && !accountNew)}
            onClick={() => void saveAccount()}
          >
            {accountBusy ? t("settings.saving") : t("settings.accountSave")}
          </button>
          {!accountBusy && accountSavedAt > 0 && (
            <small className="imported-tag">{t("settings.accountSaved")}</small>
          )}
        </div>
      </div>
    )}

    {tab === "storage" && (
      <div className="detail-panel">
        <h2>{t("settings.paths")}</h2>
        <p className="muted">{t("settings.pathsHint")}</p>
        <div className="storage-paths">
          {settings.storage_paths.filter((path) => path.used).map((path) => (
            <div className="storage-path" key={path.key}>
              <span className="storage-path-name">{t(`settings.path.${path.key}`)}</span>
              {/* Three facts on one line, in the order you read them: what it is,
                  the host folder you recognise, and the mount it arrives at. They
                  used to wrap onto a second line each, for no gain. */}
              <code className="storage-path-host">{path.host ?? path.container ?? "—"}</code>
              <small className="muted storage-path-mount">{`→ ${path.container}`}</small>
              <span className={path.writable ? "imported-tag" : "reasons"}>
                {path.exists
                  ? (path.writable ? t("settings.pathWritable") : t("settings.pathReadOnly"))
                  : t("settings.pathMissing")}
              </span>
            </div>
          ))}
        </div>
      </div>
    )}

    {tab === "general" && (
      <div className="detail-panel">
        <h2>{t("settings.titles")}</h2>
        <p className="muted">{t("settings.titlesHint")}</p>
        {/* Read outwards (build 90): what you see first, then what AniFlow
            asks an indexer, then what it writes on the disk. */}
        <div className="settings-grid two">
          {/* What the interface calls things, which is nobody else's business:
              it renames no file and changes no search. */}
          <div className="field">
            <span>{t("settings.titleDisplayOrder")}</span>
            <TitleOrder
              value={settings.title_display_order}
              onChange={(next) => patch({ title_display_order: next })}
            />
            <small className="muted">{t("settings.titleDisplayHint")}</small>
          </div>
          <div className="field">
            <span>{t("settings.titleSearchOrder")}</span>
            <TitleOrder
              value={settings.title_search_order}
              onChange={(next) => patch({ title_search_order: next })}
            />
            <small className="muted">{t("settings.titleSearchHint")}</small>
          </div>
          <div className="field">
            <span>{t("settings.titleFolderOrder")}</span>
            <TitleOrder
              value={settings.title_folder_order}
              onChange={(next) => patch({ title_folder_order: next })}
            />
            <small className="muted">{t("settings.titleFolderHint")}</small>
          </div>
        </div>
      </div>
    )}

    {/* AniList is the source and the one to leave alone: switching clears the calendar and
        rebuilds it, and the two sources use different ids. Debug mode only (build 227). */}
    {tab === "integrations" && settings.debug_mode && (
      <div className="detail-panel">
        <h2>{t("settings.metadata")}</h2>
        <p className="muted">{t("settings.metadataHint")}</p>
        <div className="settings-grid two">
          <label className="field">
            <span>{t("settings.metadata")}</span>
            <select
              value={settings.metadata_provider}
              onChange={(event) => {
                // Switching clears the calendar and rebuilds it; with saving on change that must be asked.
                if (window.confirm(t("settings.metadataConfirm"))) patch({ metadata_provider: event.target.value as AppSettingsView["metadata_provider"] });
              }}
            >
              <option value="anilist">{t("settings.metadataProvider.anilist")}</option>
              <option value="kitsu">{t("settings.metadataProvider.kitsu")}</option>
            </select>
            {/* What the choice costs, beside the choice. The two sources are
                not interchangeable and the difference is worth knowing before
                switching, not after. */}
            <small className="muted">{t(`settings.metadataNote.${settings.metadata_provider}`)}</small>
          </label>
        </div>
      </div>
    )}

    {tab === "watchlist" && (
      <div className="detail-panel">
        <h2>{t("settings.watchlist.title")}</h2>
        <p className="muted">{t("settings.watchlist.hint")}</p>
        <div className="settings-grid two">
          <label className="field">
            <span>{t("settings.watchlist.rollover")}</span>
            <select
              value={settings.season_rollover}
              onChange={(event) => patch({ season_rollover: event.target.value as AppSettingsView["season_rollover"] })}
            >
              <option value="ask">{t("settings.watchlist.rollover.ask")}</option>
              <option value="auto">{t("settings.watchlist.rollover.auto")}</option>
              <option value="off">{t("settings.watchlist.rollover.off")}</option>
            </select>
          </label>
        </div>
        <p className={settings.season_rollover === "auto" ? "muted library-shoko-note" : "muted"}>
          {t(`settings.watchlist.note.${settings.season_rollover}`)}
        </p>
      </div>
    )}

    {tab === "watchlist" && (
      <div className="detail-panel">
        <h2>{t("settings.watchlist.newTitle")}</h2>
        <label className="check">
          <input
            type="checkbox"
            checked={settings.automate_new_anime}
            onChange={(event) => patch({ automate_new_anime: event.target.checked })}
          />
          <span>{t("settings.watchlist.automateNew")}</span>
        </label>
        <p className="muted">{t("settings.watchlist.automateNewHint")}</p>
      </div>
    )}

    {tab === "notifications" && (
      <div className="detail-panel">
        <h2>{t("settings.toasts")}</h2>
        <p className="muted">{t("settings.toastsHint")}</p>
        <div className="settings-grid two">
          <label className="field">
            <span>{t("settings.toastCorner")}</span>
            <select
              value={settings.toast_corner}
              onChange={(event) => patch({ toast_corner: event.target.value as AppSettingsView["toast_corner"] })}
            >
              <option value="top-right">{t("settings.toastCorner.top-right")}</option>
              <option value="top-left">{t("settings.toastCorner.top-left")}</option>
              <option value="bottom-right">{t("settings.toastCorner.bottom-right")}</option>
              <option value="bottom-left">{t("settings.toastCorner.bottom-left")}</option>
            </select>
          </label>
        </div>
        <div className="profile-actions">
          {/* Saves first: the corner is read when the app loads, so showing an
              example from the old position would demonstrate the wrong thing.

              Two buttons, because the point of trying one is to see where it
              appears *and* what a bad one looks like - and the red left edge
              is the whole difference between the two. */}
          <button
            className="button secondary"
            onClick={() => { void save().then(() => show(t("settings.toastTestMessage"), "info")); }}
          >
            {t("settings.toastTest")}
          </button>
          <button
            className="button secondary"
            onClick={() => { void save().then(() => show(t("settings.toastTestError"), "error")); }}
          >
            {t("settings.toastTestErrorButton")}
          </button>
        </div>
      </div>
    )}

    {/* Optional, and read-only: the file hand-over works through folders and
        needs none of this. See services/shoko_library.py. */}
    {tab === "shoko" && (
      <div className="detail-panel">
        <h2>{t("settings.shoko")}</h2>
        <p className="muted">{t("settings.shokoHint")}</p>
        <div className="settings-grid two">
          <label className="field">
            <span>{t("settings.shokoUrl")}</span>
            <input
              value={settings.shoko_url}
              placeholder="http://192.168.1.7:8111"
              onChange={(event) => patch({ shoko_url: event.target.value })}
            />
          </label>
          <label className="field">
            <span>{settings.has_shoko_api_key ? t("settings.shokoKeySet") : t("settings.shokoKey")}</span>
            <input
              type="password"
              autoComplete="off"
              value={shokoKey}
              onChange={(event) => { setShokoKey(event.target.value); touch(); }}
            />
          </label>
        </div>
        <div className="profile-actions">
          <button className="button secondary" disabled={testingShoko} onClick={() => void runShokoTest()}>
            {testingShoko ? t("settings.testing") : t("settings.shokoTest")}
          </button>
          {shokoTest && (
            <small className={shokoTest.ok ? "imported-tag" : "reasons"}>{shokoTest.detail}</small>
          )}
          <a className="button secondary" href="#/shoko">{t("settings.shokoOpen")}</a>
        </div>
      </div>
    )}

    {/* Television tokens (build 181): what AniFlow TV signs in with. */}
    {tab === "integrations" && <TvDevicesPanel />}

    {/* Only AniFlow's own internet requests - see services/outbound.py. */}
    {tab === "integrations" && (
      <div className="detail-panel">
        <h2>{t("settings.proxy")}</h2>
        <p className="muted">{t("settings.proxyHint")}</p>
        <div className="settings-grid">
          <label className="field">
            <span>{t("settings.proxyKind")}</span>
            <select
              value={settings.proxy_kind}
              onChange={(event) => patch({ proxy_kind: event.target.value as AppSettingsView["proxy_kind"] })}
            >
              <option value="none">{t("settings.proxyKind.none")}</option>
              <option value="http">HTTP</option>
              <option value="socks4">SOCKS4</option>
              <option value="socks5">SOCKS5</option>
              <option value="socks5h">SOCKS5h</option>
            </select>
          </label>
          <label className="field">
            <span>{t("settings.proxyHost")}</span>
            <input
              value={settings.proxy_host}
              placeholder="10.0.0.5"
              onChange={(event) => patch({ proxy_host: event.target.value })}
            />
          </label>
          <label className="field">
            <span>{t("settings.proxyPort")}</span>
            <input
              type="number"
              min={1}
              max={65535}
              value={settings.proxy_port ?? ""}
              onChange={(event) => patch({ proxy_port: event.target.value === "" ? null : Number(event.target.value) })}
            />
          </label>
          <label className="field">
            <span>{t("settings.proxyUsername")}</span>
            <input
              value={settings.proxy_username}
              onChange={(event) => patch({ proxy_username: event.target.value })}
            />
          </label>
          <label className="field">
            <span>{settings.has_proxy_password ? t("settings.proxyPasswordSet") : t("settings.proxyPassword")}</span>
            <input
              type="password"
              value={proxyPassword}
              onChange={(event) => { setProxyPassword(event.target.value); touch(); }}
            />
          </label>
        </div>
        <div className="profile-actions">
          <button className="button secondary" disabled={checkingExit} onClick={() => void runExitCheck()}>
            {checkingExit ? t("settings.checking") : t("settings.proxyCheck")}
          </button>
          {exitAddress && (
            exitAddress.ok
              /* Direct is only a problem when a proxy was asked for: with none
                 configured it is simply the answer. */
              ? <span className={exitAddress.proxied || settings.proxy_kind === "none" ? "imported-tag" : "reasons"}>
                  {[exitAddress.flag, exitAddress.ip, exitAddress.country].filter(Boolean).join(" ")}
                  {" — "}
                  {exitAddress.proxied ? t("settings.proxyThrough") : t("settings.proxyDirect")}
                </span>
              : <span className="reasons">{exitAddress.detail}</span>
          )}
        </div>
        {/* Last, like the mode warning: read after the thing it is about. */}
        <p className="warning-message">{t("settings.proxyTorrentWarning")}</p>
      </div>
    )}

    {/* Nothing is downloaded in Follow mode, so a torrent client is a
        setting for machinery that is not running. Hidden rather than
        disabled: it keeps its values and comes back with the mode. */}
    {tab === "downloads" && (
      <div className="detail-panel">
        <h2>{t("settings.torrentSection")}</h2>
        <div className="settings-grid two">
          <label className="field">
            <span>{t("settings.torrentClient")}</span>
            <select value={settings.torrent_client}
                    onChange={(event) => {
                      const next = event.target.value as TorrentClient;
                      // The address follows the client: the port the old one listens on becomes the new one's.
                      patch({ torrent_client: next, qb_url: addressForClient(settings.qb_url, settings.torrent_client, next) });
                    }}>
              <option value="qbittorrent">qBittorrent</option>
              <option value="deluge">Deluge</option>
              <option value="transmission">Transmission</option>
            </select>
          </label>
          <label className="field">
            <span>{t("settings.category")}</span>
            <input value={settings.qb_category} onChange={(event) => patch({ qb_category: event.target.value })} />
          </label>
        </div>
        <div className="settings-grid">
          <label className="field">
            <span>{t("settings.url")}</span>
            <input value={settings.qb_url ?? ""} placeholder={{ deluge: "http://deluge:8112", transmission: "http://transmission:9091", qbittorrent: "http://qbittorrent:8080" }[settings.torrent_client]}
                   onChange={(event) => patch({ qb_url: event.target.value })} />
          </label>
          {/* Deluge's Web UI has a password and no username. */}
          {settings.torrent_client !== "deluge" && (
            <label className="field">
              <span>{t("settings.username")}</span>
              <input value={settings.qb_username} onChange={(event) => patch({ qb_username: event.target.value })} />
            </label>
          )}
          <label className="field">
            <span>{settings.has_qb_password ? t("settings.password.stored") : settings.torrent_client === "deluge" ? t("settings.delugePassword") : t("settings.password.new")}</span>
            <input type="password" value={qbPassword} placeholder={settings.has_qb_password ? "••••••••" : ""}
                   onChange={(event) => { setQbPassword(event.target.value); touch(); }} />
          </label>
        </div>
        {settings.torrent_client === "deluge" && <p className="muted">{t("settings.delugeHint")}</p>}
        {settings.torrent_client === "transmission" && <p className="muted">{t("settings.transmissionHint")}</p>}
        <div className="status-line">
          <button className="button secondary" disabled={testing !== null} onClick={() => void runTest("qb")}>
            {testing === "qb" ? t("settings.testing") : t("settings.test")}
          </button>
          {qbTest && <small className={`reasons ${qbTest.ok ? "imported-tag" : ""}`} style={{ whiteSpace: "pre-line", maxWidth: 480 }}>
            {qbTest.ok ? t("settings.testOk") : `${t("settings.testFailed")} — ${qbTest.detail ?? ""}`}
          </small>}
        </div>

        <h3>{t("settings.torrentBehaviour")}</h3>
        <div className="settings-grid two">
          <label className="field">
            <span>{t(`settings.fileOperation.${settings.mode}`)}</span>
            <select value={settings.file_operation}
                    onChange={(event) => setFileOperation(event.target.value as "copy" | "move" | "hardlink")}>
              <option value="copy">{t("settings.fileOperation.copy")}</option>
              <option value="hardlink">{t("settings.fileOperation.hardlink")}</option>
              <option value="move">{t("settings.fileOperation.move")}</option>
            </select>
          </label>
          <label className="field">
            <span>{t("settings.afterImport")}</span>
            <select value={settings.after_import_action}
                    onChange={(event) => patch({ after_import_action: event.target.value as "pause" | "remove" | "keep" })}>
              <option value="pause">{t("settings.afterImport.pause")}</option>
              <option value="remove">{t("settings.afterImport.remove")}</option>
              {/* Unselectable while moving: the file is gone, so there is
                  nothing left to seed. */}
              <option value="keep" disabled={settings.file_operation === "move"}>
                {t("settings.afterImport.keep")}
              </option>
            </select>
          </label>
        </div>
        <p className="muted">{t("settings.fileOperationHint")}</p>
        {settings.file_operation === "move" && (
          <p className="muted">{t("settings.moveSeedingHint")}</p>
        )}
        {settings.file_operation === "hardlink" && (
          <p className="muted">{t("settings.hardlinkHint")}</p>
        )}
        {seedingNote && <p className="muted imported-tag">{t("settings.moveForcedRemove")}</p>}
        <p className="muted">{t("settings.afterImportHint")}</p>
        {/* Intervals live on the Schedule page (build 180). */}
        <p className="muted">{t("settings.monitorIntervalMoved")}</p>
      </div>
    )}

    {/* What AniFlow reads of the torrent client's folders (moved from Storage in build 260): it asks the client where
        it saves, translates that to the path AniFlow sees, and checks the download folder can be read and the
        library written. It needs the client's address and login, which is why it sits with them. */}
    {tab === "downloads" && (
      <div className="detail-panel">
        <h2>{t("settings.downloads.pathsTitle")}</h2>
        <p className="muted">{t("settings.downloads.pathsHint")}</p>
        <div className="profile-actions">
          {/* The same button collapses the panel again, so it does not have to
              stay open once it has been read. */}
          <button className="button secondary" disabled={checkingPaths}
                  onClick={() => {
                    if (pathCheck) { setPathCheck(null); return; }
                    setCheckingPaths(true);
                    // Send what is on screen, so this works before the first save.
                    checkPaths({
                      qb_url: settings.qb_url,
                      qb_username: settings.qb_username,
                      qb_password: qbPassword || undefined,
                    }).then(setPathCheck).catch(() => setPathCheck(null)).finally(() => setCheckingPaths(false));
                  }}>
            {checkingPaths ? t("settings.checking") : pathCheck ? t("settings.pathsCollapse") : t("settings.pathsCheck")}
          </button>
        </div>
        {pathCheck && (
          <div className="analyze-details">
            <strong className={pathCheck.ok ? "imported-tag" : "reasons"}>{pathCheck.message}</strong>
            <dl className="parsed-grid" style={{ marginTop: 10 }}>
              <dt>{t("settings.qbSavePath")}</dt><dd>{pathCheck.qb_default_save_path ?? "—"}</dd>
              <dt>{t("settings.translated")}</dt><dd>{pathCheck.translated_example ?? pathCheck.sample_translated ?? "—"}</dd>
              <dt>{t("settings.sampleContent")}</dt><dd>{pathCheck.sample_content_path ?? "—"}</dd>
              <dt>{t("settings.mediaFolder")}</dt><dd>{pathCheck.sample_media_folder ?? "—"}</dd>
              <dt>{t("settings.sampleSource")}</dt><dd>{pathCheck.sample_source_path ?? "—"}</dd>
              <dt>{t("settings.sampleTarget")}</dt><dd>{pathCheck.sample_target_path ?? "—"}</dd>
              <dt>{t("settings.libraryRootWritable")}</dt><dd>{pathCheck.library_root_writable ? "✓" : "✕"}</dd>
              <dt>{t("settings.downloadRootWritable")}</dt><dd>{pathCheck.download_root_writable ? "✓" : "✕"}</dd>
            </dl>
          </div>
        )}
      </div>
    )}

    {/* No "Automation intervals" panel (build 180): every switch and interval
        it held is on the Schedule page, so it was the same list twice. */}

    {/* No developer-tools switch here (build 268): debug_mode is set in aniflow.yaml only. */}

    {/* Backup and restore (build 266; its own tab since build 268). */}
    {tab === "backup" && (
      <div className="detail-panel">
        <BackupPanel />
      </div>
    )}

    {/* About (build 262; its own tab since build 268): read when something is wrong, and what it offers (the
        diagnostics file) is for sending to someone else. */}
    {tab === "about" && (
      <div className="detail-panel">
        <h2>{t("settings.about")}</h2>
        {about === null ? (
          <p className="muted">{t("shoko.loading")}</p>
        ) : (
          <dl className="parsed-grid">
            <dt>{t("settings.about.version")}</dt>
            <dd>{about.version} · {t("settings.about.build")} {about.build}</dd>
            <dt>{t("settings.about.database")}</dt>
            <dd>
              {about.database.current ?? "—"}
              {about.database.up_to_date
                ? " ✓"
                : <span className="reasons"> {t("settings.about.databaseBehind").replace("{head}", about.database.head ?? "?")}</span>}
            </dd>
            <dt>{t("settings.about.uptime")}</dt>
            <dd>{formatUptime(about.uptime_seconds)} · {t("settings.about.since").replace("{time}", formatTimestamp(about.started_at) || "—")}</dd>
            <dt>{t("settings.about.worker")}</dt>
            <dd className={about.worker_running ? "imported-tag" : "reasons"}>
              {about.worker_running ? t("settings.about.workerRunning") : t("settings.about.workerStopped")}
            </dd>
            <dt>{t("settings.about.timezone")}</dt>
            <dd>{about.timezone} · {about.server_time}</dd>
            <dt>{t("settings.about.mode")}</dt>
            <dd>{about.storage_mode} · {about.app_mode}</dd>
            <dt>{t("settings.about.runtime")}</dt>
            <dd>Python {about.python} · {about.platform}{about.docker ? " · Docker" : ""}</dd>
            {about.folders.map((folder) => (
              <Fragment key={folder.name}>
                <dt>{t(FOLDER_LABEL[folder.name] ?? "settings.about.folderData")}</dt>
                <dd>
                  <code>{folder.container ?? "—"}</code>
                  {" · "}
                  <span className={folder.exists && folder.writable ? "imported-tag" : "reasons"}>
                    {folder.exists ? (folder.writable ? t("settings.pathWritable") : t("settings.pathReadOnly")) : t("settings.pathMissing")}
                  </span>
                  {folder.free_bytes !== null ? ` · ${t("settings.about.free").replace("{size}", formatBytes(folder.free_bytes))}` : ""}
                </dd>
              </Fragment>
            ))}
          </dl>
        )}

        <h3>{t("settings.about.sources")}</h3>
        <p className="muted">{t("settings.about.sourcesHint")}</p>
        <p className="muted">
          <a href="/THIRD-PARTY-NOTICES.txt" target="_blank" rel="noopener noreferrer">{t("settings.about.notices")}</a>
        </p>

        <h3>{t("settings.diagnostics")}</h3>
        <p className="muted">{t("settings.diagnosticsHint")}</p>
        <div className="profile-actions">
          {/* A plain link: the session cookie goes with it, and the browser saves what comes back. */}
          <a className="button secondary" href="/api/system/diagnostics" download>
            {t("settings.diagnosticsButton")}
          </a>
        </div>
      </div>
    )}

  </section>;
}

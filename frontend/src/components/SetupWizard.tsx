/**
 * The install wizard: what a fresh AniFlow asks before it can be used (137).
 *
 * A new deployment used to need four things done in four places - an account
 * on the login page, `ANIFLOW_MODE` in docker-compose.yml, a YAML file dropped
 * into a folder you had to know about, and the settings page - with nothing to
 * say in what order, or that you were finished. This is one path through them.
 *
 * Three rules it holds itself to, all of them learned from the mockup rounds:
 *
 * - **The steps that exist depend on the answers.** Connections only appears
 *   in Shoko mode, Feeds only when downloading is on, and a step with nothing
 *   on it is not shown at all. A simple install is four screens.
 * - **Next waits for what a step needs**, and says why it is waiting. Where
 *   nothing is required there is no Skip button either, because then Next
 *   *is* the skip and two buttons doing one thing only raise questions.
 * - **The controls do not move.** They sit under the step rail rather than at
 *   the foot of the card, so a taller step cannot shift what you are aiming
 *   at. The card grows downwards.
 *
 * The torrent client is here since build 250, but only when downloading is on: all three supported clients
 * (qBittorrent, Deluge, Transmission) are offered, the one picked is tested with the address and login typed
 * in, and the download folder is checked - can AniFlow reach what the client writes? Media servers stay on
 * the settings page.
 */
import { Fragment, type ChangeEvent, type ReactNode, useEffect, useRef, useState } from "react";

import {
  type AuthState,
  type Library,
  type PathCheck,
  type SetupState,
  TERMS_VERSION,
  authSetup,
  calendarStatus,
  checkPaths,
  chooseStorageMode,
  completeSetup,
  createSource,
  listScheduleTasks,
  listSources,
  refreshLibraries,
  runScheduleTask,
  setupState,
  testQbittorrentConnection,
  testShokoConnection,
  updateAppSettings,
  updateCalendar,
  uploadIndexer,
} from "../api/client";
import {
  type FirstSyncState,
  type FirstSyncStep,
  type FirstSyncDeps,
  allDone,
  initialSteps,
  runFirstSync,
} from "../firstSync";
import { getLanguage, setLanguage, t } from "../i18n";
import { addressForClient, DEFAULT_PORTS, type TorrentClient } from "../clientUrl";
import { ToastProvider } from "../toasts";
import { ChecksList } from "./ChecksPage";
import { LibrariesPage } from "./LibrariesPage";
import {
  type ScopeChoice as Scope,
  type SetupStepId as StepId,
  type StorageChoice as Storage,
  isSkippable,
  stepsFor,
} from "../setupSteps";

interface Fields {
  code: string;
  user: string;
  pass: string;
  pass2: string;
  shokoUrl: string;
  shokoKey: string;
  feedUrl: string;
  clientUrl: string;
  clientUser: string;
  clientPass: string;
}

const EMPTY: Fields = {
  code: "",
  user: "",
  pass: "",
  pass2: "",
  shokoUrl: "",
  shokoKey: "",
  feedUrl: "",
  clientUrl: "",
  clientUser: "",
  clientPass: "",
};

/** The clients the wizard offers, in the order shown. */
const CLIENTS: readonly TorrentClient[] = ["qbittorrent", "deluge", "transmission"];

const CLIENT_NAMES: Record<TorrentClient, string> = { qbittorrent: "qBittorrent", deluge: "Deluge", transmission: "Transmission" };

/** The password rule the server enforces (services/auth.MIN_PASSWORD_LENGTH). */
const MIN_PASSWORD = 8;
const MIN_USERNAME = 3;

export function SetupWizard({ onDone }: { onDone: (next: AuthState) => void }) {
  const [state, setState] = useState<SetupState | null>(null);
  // Holding the language here is what repaints the wizard when it changes: `t` reads a module variable.
  const [language, setLanguageState] = useState<"en" | "de">(getLanguage());
  const [step, setStep] = useState(0);
  // The first-run notice (step one): the tick is the person's own, and it is only skipped when the server says it was
  // already accepted - decided once, when the wizard opens, so a step never vanishes from under the counter.
  const [termsStep, setTermsStep] = useState(true);
  const [termsTicked, setTermsTicked] = useState(false);
  const [fields, setFields] = useState<Fields>(EMPTY);
  const [storage, setStorage] = useState<Storage | null>(null);
  const [scope, setScope] = useState<Scope | null>(null);
  const [feeds, setFeeds] = useState<{ id: string; name: string; url: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tested, setTested] = useState<string | null>(null);
  // The torrent client step (build 250): which one, what the connection test said, what the folder check found.
  const [clientKind, setClientKind] = useState<TorrentClient>("qbittorrent");
  const [clientTest, setClientTest] = useState<{ ok: boolean; detail: string | null } | null>(null);
  const [folderTest, setFolderTest] = useState<PathCheck | null>(null);
  // The libraries step (build 250): what the embedded Libraries page last read, so Next can wait for a usable one.
  const [libraries, setLibraries] = useState<Library[] | null>(null);
  const picker = useRef<HTMLInputElement | null>(null);
  // The last step's three jobs, run for real (build 226) - see firstSync.ts.
  const [sync, setSync] = useState<FirstSyncStep[]>(initialSteps);
  const [syncSkipped, setSyncSkipped] = useState(false);
  const syncStarted = useRef(false);
  const syncRunning = useRef(false);
  const gone = useRef(false);

  useEffect(() => {
    gone.current = false;
    return () => {
      gone.current = true;
    };
  }, []);

  function startSync(from?: FirstSyncStep[]) {
    if (syncRunning.current) return;
    syncRunning.current = true;
    const deps: FirstSyncDeps = {
      runTask: runScheduleTask,
      taskState: async (id) => (await listScheduleTasks()).find((task) => task.id === id) ?? null,
      startCalendar: async () => {
        await updateCalendar(undefined, undefined, "upcoming");
      },
      calendarState: calendarStatus,
      scanLibraries: async () => {
        await refreshLibraries();
      },
      wait: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
      cancelled: () => gone.current,
    };
    void runFirstSync(deps, (steps) => {
      if (!gone.current) setSync(steps);
    }, from).finally(() => {
      syncRunning.current = false;
    });
  }

  useEffect(() => {
    void setupState()
      .then((next) => {
        setState(next);
        if (next.terms_accepted) setTermsStep(false);
        // The mode the container was started with is the sensible preselection
        // once somebody has already recorded one; before that the screen asks.
        if (next.mode_recorded) setStorage(next.mode);
      })
      // No answer means no account yet: the wizard opens on its first step,
      // which is the only one that works without a session anyway.
      .catch(() => setState(null));
  }, []);

  const set = (key: keyof Fields) => (event: ChangeEvent<HTMLInputElement>) =>
    setFields((old) => ({ ...old, [key]: event.target.value }));

  const needsAccount = state?.needs_account ?? true;
  const hasIndexer = state?.has_indexer ?? false;

  // Which steps exist, given the answers so far - see setupSteps.ts.
  const steps = stepsFor({ storage, mode: state?.mode ?? null, scope, termsAccepted: !termsStep });

  const current = steps[Math.min(step, steps.length - 1)];
  const last = step === steps.length - 1;

  const accountReady =
    !needsAccount ||
    (fields.code.trim().length > 0 &&
      fields.user.trim().length >= MIN_USERNAME &&
      fields.pass.length >= MIN_PASSWORD &&
      fields.pass === fields.pass2);

  const ready: Record<StepId, boolean> = {
    terms: termsTicked,
    account: accountReady,
    storage: storage !== null,
    scope: scope !== null,
    connections: fields.shokoUrl.trim().length > 0 && fields.shokoKey.trim().length > 0,
    // One library with a folder AniFlow can see and write to; without it nothing can ever be put anywhere.
    libraries: (libraries ?? []).some((library) => library.has_folder && library.exists && library.writable),
    client: fields.clientUrl.trim().length > 0,
    feeds: true,
    // Open AniFlow waits for the three jobs. A failed one can be retried or,
    // as a last resort, skipped - a provider being down must not lock anybody
    // out of their own install.
    sync: syncSkipped || allDone(sync),
    // Informational: what it finds never keeps anybody from opening AniFlow.
    checks: true,
  };
  const hint: Partial<Record<StepId, string>> = {
    terms: t("setup.hint.terms"),
    account: t("setup.hint.account"),
    storage: t("setup.hint.pick"),
    scope: t("setup.hint.pick"),
    connections: t("setup.hint.shoko"),
    client: t("setup.hint.client"),
    libraries: t("setup.hint.libraries"),
    sync: t("setup.hint.sync"),
  };
  const skippable = isSkippable(current);

  useEffect(() => {
    if (current !== "sync" || syncStarted.current) return;
    syncStarted.current = true;
    startSync();
  }, [current]);

  /** Each step's Next does its own saving, so a back-step never re-saves. */
  async function advance() {
    setBusy(true);
    setError(null);
    try {
      if (current === "account" && needsAccount) {
        await authSetup(fields.user.trim(), fields.pass, fields.code.trim(), TERMS_VERSION);
        setState(await setupState());
      }
      if (current === "storage" && storage) {
        setState(await chooseStorageMode(storage));
      }
      if (current === "scope" && scope) {
        // Basic and Advanced are *derived* from whether an indexer exists, so
        // the only way to say "I have one but do not want downloading" is the
        // override - which is what the wizard's own copy promises can be
        // changed later in Settings.
        await updateAppSettings({ app_mode_override: scope === "basic" ? "basic" : "" });
      }
      if (current === "connections") {
        await updateAppSettings({
          shoko_url: fields.shokoUrl.trim(),
          shoko_api_key: fields.shokoKey,
        });
      }
      if (current === "client") {
        await updateAppSettings({
          torrent_client: clientKind,
          qb_url: fields.clientUrl.trim(),
          qb_username: fields.clientUser.trim(),
          qb_password: fields.clientPass,
        });
      }
      if (last) {
        await completeSetup();
        onDone({ needs_setup: false, authenticated: true, username: fields.user.trim() || null });
        return;
      }
      setStep((n) => n + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("setup.error"));
    } finally {
      setBusy(false);
    }
  }

  function skip() {
    setError(null);
    setStep((n) => n + 1);
  }

  async function takeIndexer(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      setState(await uploadIndexer(file.name, await file.text()));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("setup.error"));
    } finally {
      setBusy(false);
    }
  }

  async function checkShoko() {
    setBusy(true);
    setTested(null);
    try {
      const result = await testShokoConnection({
        url: fields.shokoUrl.trim(),
        api_key: fields.shokoKey,
      });
      setTested(result.ok ? t("setup.test.ok") : result.detail || t("setup.test.failed"));
    } catch {
      setTested(t("setup.test.failed"));
    } finally {
      setBusy(false);
    }
  }

  /** The client picked changes the address with it: the port the last one listens on becomes the new one's. */
  function pickClient(next: TorrentClient) {
    setFields((old) => ({ ...old, clientUrl: addressForClient(old.clientUrl, clientKind, next) }));
    setClientKind(next);
    setClientTest(null);
    setFolderTest(null);
  }

  async function testClient() {
    setBusy(true);
    setClientTest(null);
    try {
      setClientTest(
        await testQbittorrentConnection({
          client: clientKind,
          url: fields.clientUrl.trim(),
          username: fields.clientUser.trim(),
          password: fields.clientPass,
        }),
      );
    } catch (reason) {
      setClientTest({ ok: false, detail: reason instanceof Error ? reason.message : t("setup.test.failed") });
    } finally {
      setBusy(false);
    }
  }

  async function testFolders() {
    setBusy(true);
    setFolderTest(null);
    try {
      setFolderTest(
        await checkPaths({
          client: clientKind,
          qb_url: fields.clientUrl.trim(),
          qb_username: fields.clientUser.trim(),
          qb_password: fields.clientPass,
        }),
      );
    } catch {
      setFolderTest(null);
      setError(t("setup.error"));
    } finally {
      setBusy(false);
    }
  }

  async function addFeed() {
    const url = fields.feedUrl.trim();
    if (!url) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createSource(hostOf(url), url);
      setFeeds((old) => [...old, { id: created.id, name: created.name, url: created.url }]);
      setFields((old) => ({ ...old, feedUrl: "" }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("setup.error"));
    } finally {
      setBusy(false);
    }
  }

  // The feeds step lists what is already there, which after the indexer step
  // is the feed the indexer stood up by itself.
  useEffect(() => {
    if (current !== "feeds") return;
    void listSources()
      .then((rows) => setFeeds(rows.map((row) => ({ id: row.id, name: row.name, url: row.url }))))
      .catch(() => undefined);
  }, [current]);

  return (
    <div className="setup-screen">
      <div className="setup-card">
        <header className="setup-head">
          <img className="brand-mark" src="/logo-128.png" alt="" />
          <div className="brand">
            <strong>AniFlow</strong>
            <small>{t("app.tagline")}</small>
          </div>
          <span className="setup-count">
            {t("setup.counter").replace("{n}", String(step + 1)).replace("{total}", String(steps.length))}
          </span>
          {/* The first screen a German reader meets is this one, so the language is chosen here
              rather than only on the Settings page behind it. The browser keeps it (i18n.ts). */}
          <select
            className="setup-language"
            aria-label={t("settings.language")}
            value={language}
            onChange={(event) => {
              setLanguage(event.target.value as "en" | "de");
              setLanguageState(event.target.value as "en" | "de");
            }}
          >
            <option value="en">English</option>
            <option value="de">Deutsch</option>
          </select>
        </header>

        <div className="setup-rail">
          {steps.map((id, index) => (
            <div
              key={id}
              className={`setup-rail-item${index < step ? " done" : ""}${index === step ? " now" : ""}`}
            >
              <div className="setup-rail-bar" />
              <div className="setup-rail-label">{t(`setup.rail.${id}`)}</div>
            </div>
          ))}
        </div>

        <div className="setup-nav">
          <button
            type="button"
            className="button secondary"
            disabled={step === 0 || busy}
            onClick={() => setStep((n) => Math.max(0, n - 1))}
          >
            {t("setup.previous")}
          </button>
          <span className="setup-nav-mid">{ready[current] ? error ?? "" : hint[current] ?? ""}</span>
          <span className="setup-nav-right">
            {skippable ? (
              <button type="button" className="button ghost" disabled={busy} onClick={skip}>
                {t("setup.skip")}
              </button>
            ) : null}
            <button
              type="button"
              className="button primary"
              disabled={!ready[current] || busy}
              onClick={() => void advance()}
            >
              {last ? t("setup.finish") : t("setup.next")}
            </button>
          </span>
        </div>

        <div className="setup-body">
          {error && ready[current] ? <p className="setup-error">{error}</p> : null}

          {current === "terms" ? (
            <div className="setup-terms">
              <p className="eyebrow">{t(`setup.rail.${current}`)}</p>
              <h2>{t("setup.terms.title")}</h2>
              <p className="setup-lede">{t("setup.terms.lede")}</p>
              <div className="setup-terms-text" tabIndex={0}>
                {[
                  [t("setup.terms.s1.title"), t("setup.terms.s1.body")],
                  [t("setup.terms.s2.title"), t("setup.terms.s2.body")],
                  [t("setup.terms.s3.title"), t("setup.terms.s3.body")],
                  [t("setup.terms.s4.title"), t("setup.terms.s4.body")],
                  [t("setup.terms.s5.title"), t("setup.terms.s5.body")],
                  [t("setup.terms.s6.title"), t("setup.terms.s6.body")],
                  [t("setup.terms.s7.title"), t("setup.terms.s7.body")],
                ].map(([title, body]) => (
                  <section key={title}>
                    <h3>{title}</h3>
                    <p>{body}</p>
                  </section>
                ))}
              </div>
              <label className="check setup-terms-agree">
                <input type="checkbox" checked={termsTicked} onChange={(event) => setTermsTicked(event.target.checked)} />
                <span>{t("setup.terms.agree")}</span>
              </label>
            </div>
          ) : null}

          {current === "account" ? (
            <div className="setup-centred">
              <p className="eyebrow">{t(`setup.rail.${current}`)}</p>
              <h2>{t("setup.account.title")}</h2>
              <p className="setup-lede">{t("setup.account.lede")}</p>
              {needsAccount ? (
                <>
                  <p className="setup-hint-block">{t("setup.account.code")}</p>
                  <div className="setup-fields">
                    <label className="field">
                      <span>{t("setup.account.codeLabel")}</span>
                      <input
                        type="text"
                        className="mono"
                        autoComplete="off"
                        value={fields.code}
                        onChange={set("code")}
                      />
                    </label>
                    <div className="setup-divider" />
                    <label className="field">
                      <span>{t("auth.username")}</span>
                      <input type="text" autoComplete="username" value={fields.user} onChange={set("user")} />
                    </label>
                    <label className="field">
                      <span>{t("auth.newPassword")}</span>
                      <input
                        type="password"
                        autoComplete="new-password"
                        value={fields.pass}
                        onChange={set("pass")}
                      />
                    </label>
                    <label className="field">
                      <span>{t("setup.account.repeat")}</span>
                      <input
                        type="password"
                        autoComplete="new-password"
                        value={fields.pass2}
                        onChange={set("pass2")}
                      />
                    </label>
                    <p className="field-hint">{t("setup.account.rules")}</p>
                  </div>
                </>
              ) : (
                <p className="setup-note good">
                  <span>&#10003;</span>
                  <span>{t("setup.account.already")}</span>
                </p>
              )}
            </div>
          ) : null}

          {current === "storage" ? (
            <>
              <p className="eyebrow">{t(`setup.rail.${current}`)}</p>
              <h2>{t("setup.storage.title")}</h2>
              <p className="setup-lede">{t("setup.storage.lede")}</p>
              <div className="setup-choices">
                <Choice
                  selected={storage === "standalone"}
                  onPick={() => setStorage("standalone")}
                  title={t("setup.storage.aniflow")}
                  body={t("setup.storage.aniflowBody")}
                >
                  <Flow
                    steps={[
                      { name: t("setup.folder.downloads"), env: "DOWNLOAD_ROOT", colour: "#64b3ff" },
                      { name: t("setup.folder.library"), env: "ANIME_LIBRARY_ROOT", colour: "#10c469" },
                    ]}
                  />
                </Choice>
                <Choice
                  selected={storage === "shokoserver"}
                  onPick={() => setStorage("shokoserver")}
                  title={t("setup.storage.shoko")}
                  body={t("setup.storage.shokoBody")}
                >
                  <Flow
                    steps={[
                      { name: t("setup.folder.downloads"), env: "DOWNLOAD_ROOT", colour: "#64b3ff" },
                      { name: t("setup.folder.shoko"), colour: "#f9c851", icon: "gear" },
                      { name: t("setup.folder.library"), env: "ANIME_LIBRARY_ROOT", colour: "#10c469" },
                    ]}
                  />
                </Choice>
              </div>
              <p className="setup-note">
                <span>&#9888;</span>
                <span>
                  <b>{t("setup.storage.warnTitle")}</b> {t("setup.storage.warnBody")}
                </span>
              </p>
            </>
          ) : null}

          {current === "scope" ? (
            <>
              <p className="eyebrow">{t(`setup.rail.${current}`)}</p>
              <h2>{t("setup.scope.title")}</h2>
              <p className="setup-lede setup-lede-tight">{t("setup.scope.lede")}</p>
              <p className="setup-note bad tight">
                <span>&#9888;</span>
                <span>{t("setup.scope.noIndexers")}</span>
              </p>
              <div className="setup-choices">
                <Choice
                  selected={scope === "basic"}
                  onPick={() => setScope("basic")}
                  title={t("setup.scope.follow")}
                  body={t("setup.scope.followBody")}
                  extra={
                    (storage ?? state?.mode) === "shokoserver"
                      ? t("setup.scope.followShoko")
                      : t("setup.scope.followLocal")
                  }
                />
                <Choice
                  selected={scope === "advanced"}
                  onPick={() => setScope("advanced")}
                  locked={!hasIndexer}
                  title={t("setup.scope.acquire")}
                  body={t("setup.scope.acquireBody")}
                  extra={t("setup.scope.acquireExtra")}
                />
              </div>
              {hasIndexer ? (
                <p className="setup-note good">
                  <span>&#10003;</span>
                  <span>
                    <b>{t("setup.scope.indexerFound")}</b> {(state?.indexer_names ?? []).join(", ")}
                  </span>
                </p>
              ) : null}
              {/* Always here, with an indexer or without (build 230): there is no reason to close the way in
                  after the first file, and a second site is the common case. */}
              <div
                className="setup-unlock"
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault();
                  void takeIndexer(event.dataTransfer.files?.[0]);
                }}
              >
                <div className="setup-unlock-text">
                  <b>{hasIndexer ? t("setup.scope.addMoreTitle") : t("setup.scope.unlockTitle")}</b>
                  {hasIndexer ? t("setup.scope.addMoreBody") : t("setup.scope.unlockBody")}
                </div>
                <div className="setup-unlock-actions">
                  <span className="tiny">{t("setup.scope.dropHere")}</span>
                  <button
                    type="button"
                    className="button secondary small"
                    disabled={busy}
                    onClick={() => picker.current?.click()}
                  >
                    {t("setup.scope.choose")}
                  </button>
                </div>
                <input
                  ref={picker}
                  type="file"
                  accept=".yaml,.yml"
                  hidden
                  onChange={(event) => {
                    void takeIndexer(event.target.files?.[0]);
                    event.target.value = "";
                  }}
                />
              </div>
              <p className="setup-note">
                <span>&#8635;</span>
                <span>
                  <b>{t("setup.scope.changeTitle")}</b> {t("setup.scope.changeBody")}
                </span>
              </p>
            </>
          ) : null}

          {current === "connections" ? (
            <>
              <p className="eyebrow">{t(`setup.rail.${current}`)}</p>
              <h2>{t("setup.connections.title")}</h2>
              <p className="setup-lede">{t("setup.connections.lede")}</p>
              <div className="setup-panel">
                <div className="setup-panel-head">
                  <b>{t("setup.connections.shoko")}</b>
                  <span className="why">{t("setup.connections.shokoWhy")}</span>
                  <span className="pill need">{t("setup.required")}</span>
                </div>
                <div className="setup-panel-body">
                  <div className="setup-field-row">
                    <label className="field">
                      <span>{t("setup.connections.address")}</span>
                      <input type="text" value={fields.shokoUrl} onChange={set("shokoUrl")} />
                    </label>
                    <label className="field">
                      <span>{t("setup.connections.apiKey")}</span>
                      <input type="password" value={fields.shokoKey} onChange={set("shokoKey")} />
                    </label>
                  </div>
                  <div className="setup-inline">
                    <button
                      type="button"
                      className="button secondary small"
                      disabled={busy || !ready.connections}
                      onClick={() => void checkShoko()}
                    >
                      {t("setup.test")}
                    </button>
                    <span className="tiny">{tested ?? t("setup.connections.shokoNote")}</span>
                  </div>
                </div>
              </div>
            </>
          ) : null}

          {current === "libraries" ? (
            <>
              <p className="eyebrow">{t(`setup.rail.${current}`)}</p>
              <h2>{t("setup.libraries.title")}</h2>
              <p className="setup-lede">{t("setup.libraries.lede")}</p>
              {(storage ?? state?.mode) === "shokoserver" ? <p className="setup-hint-block">{t("setup.libraries.shokoHint")}</p> : null}
              {/* The Libraries page itself, as it is in the app: add, add from a folder, match Shoko's folders. */}
              <ToastProvider>
                <LibrariesPage shokoMode={(storage ?? state?.mode) === "shokoserver"} onLoaded={setLibraries} embedded />
              </ToastProvider>
            </>
          ) : null}

          {current === "client" ? (
            <>
              <p className="eyebrow">{t(`setup.rail.${current}`)}</p>
              <h2>{t("setup.client.title")}</h2>
              <p className="setup-lede">{t("setup.client.lede")}</p>
              {/* A list, not cards (build 253): more clients may come, and a list does not grow. */}
              <label className="field setup-client-select">
                <span>{t("setup.client.kind")}</span>
                <select value={clientKind} onChange={(event) => pickClient(event.target.value as TorrentClient)}>
                  {CLIENTS.map((kind) => (
                    <option key={kind} value={kind}>{CLIENT_NAMES[kind]}</option>
                  ))}
                </select>
                <small className="muted">{t(`setup.client.body.${clientKind}`).replace("{port}", String(DEFAULT_PORTS[clientKind]))}</small>
              </label>
              <div className="setup-panel">
                <div className="setup-panel-body">
                  <div className="setup-field-row">
                    <label className="field">
                      <span>{t("setup.connections.address")}</span>
                      <input
                        type="text"
                        value={fields.clientUrl}
                        placeholder={`http://${clientKind}:${DEFAULT_PORTS[clientKind]}`}
                        onChange={set("clientUrl")}
                      />
                    </label>
                    {clientKind !== "deluge" ? (
                      <label className="field">
                        <span>{t("setup.client.user")}</span>
                        <input type="text" autoComplete="off" value={fields.clientUser} onChange={set("clientUser")} />
                      </label>
                    ) : null}
                    <label className="field">
                      <span>{clientKind === "deluge" ? t("setup.client.delugePassword") : t("setup.client.password")}</span>
                      <input type="password" autoComplete="new-password" value={fields.clientPass} onChange={set("clientPass")} />
                    </label>
                  </div>
                  <div className="setup-inline">
                    <button type="button" className="button secondary small" disabled={busy || !ready.client} onClick={() => void testClient()}>
                      {t("setup.test")}
                    </button>
                    <button type="button" className="button secondary small" disabled={busy || !ready.client} onClick={() => void testFolders()}>
                      {t("setup.client.testFolders")}
                    </button>
                  </div>
                  {clientTest ? (
                    <p className={clientTest.ok ? "setup-note good tight" : "setup-note bad tight"}>
                      <span>{clientTest.ok ? "\u2713" : "\u26A0"}</span>
                      <span>{clientTest.ok ? `${t("setup.test.ok")} ${clientTest.detail ?? ""}` : clientTest.detail || t("setup.test.failed")}</span>
                    </p>
                  ) : null}
                  {folderTest ? (
                    <div className={folderTest.ok ? "setup-note good tight" : "setup-note bad tight"}>
                      <span>{folderTest.ok ? "\u2713" : "\u26A0"}</span>
                      <span>
                        {folderTest.message}
                        <small className="tiny" style={{ display: "block" }}>
                          {t("setup.client.folderAniflow")}: <code>{folderTest.aniflow_download_root}</code>{" "}
                          {folderTest.download_root_exists === false
                            ? t("setup.client.folderMissing")
                            : folderTest.download_root_writable
                              ? t("setup.client.folderWritable")
                              : t("setup.client.folderReadOnly")}
                        </small>
                        {folderTest.qb_default_save_path ? (
                          <small className="tiny" style={{ display: "block" }}>
                            {t("setup.client.folderClient")}: <code>{folderTest.qb_default_save_path}</code>
                          </small>
                        ) : null}
                      </span>
                    </div>
                  ) : null}
                </div>
              </div>
              <p className="setup-note">
                <span>&#9888;</span>
                <span>
                  <b>{t("setup.client.sameFolderTitle")}</b> {t("setup.client.sameFolderBody")}
                </span>
              </p>
            </>
          ) : null}

          {current === "feeds" ? (
            <>
              <p className="eyebrow">{t(`setup.rail.${current}`)}</p>
              <h2>{t("setup.feeds.title")}</h2>
              <p className="setup-lede">{t("setup.feeds.lede")}</p>
              <div className="setup-paste">
                <label className="field">
                  <span>{t("setup.feeds.add")}</span>
                  <input type="text" value={fields.feedUrl} onChange={set("feedUrl")} />
                </label>
                <button type="button" className="button secondary" disabled={busy} onClick={() => void addFeed()}>
                  {t("setup.feeds.addButton")}
                </button>
              </div>
              <h3 className="setup-sub">{t("setup.feeds.fromIndexer")}</h3>
              <div className="setup-feeds">
                {feeds.length === 0 ? (
                  <p className="tiny">{t("setup.feeds.none")}</p>
                ) : (
                  feeds.map((feed) => (
                    <div className="setup-feed" key={feed.id}>
                      <span>&#128225;</span>
                      <div>
                        {feed.name}
                        <small>{feed.url}</small>
                      </div>
                    </div>
                  ))
                )}
              </div>
              <p className="setup-note">
                <span>&#8635;</span>
                <span>
                  <b>{t("setup.feeds.optionalTitle")}</b> {t("setup.feeds.optionalBody")}
                </span>
              </p>
            </>
          ) : null}

          {current === "checks" ? (
            <>
              <p className="eyebrow">{t(`setup.rail.${current}`)}</p>
              <h2>{t("setup.checks.title")}</h2>
              <p className="setup-lede">{t("setup.checks.lede")}</p>
              <ChecksList bare />
            </>
          ) : null}

          {current === "sync" ? (
            <>
              <p className="eyebrow">{t(`setup.rail.${current}`)}</p>
              <h2>{t("setup.sync.title")}</h2>
              <p className="setup-lede">{t("setup.sync.lede")}</p>
              <div className="setup-tasks">
                {sync.map((job) => (
                  <Task
                    key={job.id}
                    name={t(`setup.sync.${job.id}`)}
                    why={t(`setup.sync.${job.id}Why`)}
                    state={job.state}
                    detail={job.detail}
                    progress={job.progress ?? null}
                    reach={job.reach ?? null}
                  />
                ))}
              </div>
              {sync.some((job) => job.state === "failed") && !syncSkipped ? (
                <p className="setup-note bad">
                  <span>&#9888;</span>
                  <span>
                    {t("setup.sync.failedNote")}{" "}
                    <button type="button" className="button secondary" onClick={() => startSync(sync)}>
                      {t("setup.sync.retry")}
                    </button>{" "}
                    <button type="button" className="button ghost" onClick={() => setSyncSkipped(true)}>
                      {t("setup.sync.skipAnyway")}
                    </button>
                  </span>
                </p>
              ) : null}
              <p className="setup-note">
                <span>&#8635;</span>
                <span>
                  {scope === "advanced" ? t("setup.sync.privacyAdvanced") : t("setup.sync.privacyBasic")}
                </span>
              </p>
            </>
          ) : null}

          {/* On every step, because a setup that fails can fail on any of them. A plain link: the session cookie goes
              with it once there is an account, and before that the server allows it. */}
          <p className="setup-help">
            {t("setup.help.text")}{" "}
            <a href="/api/auth/diagnostics" download>
              {t("setup.help.button")}
            </a>
          </p>
        </div>
      </div>
    </div>
  );
}

function Choice({
  selected,
  locked,
  onPick,
  title,
  body,
  extra,
  children,
}: {
  selected: boolean;
  locked?: boolean;
  onPick: () => void;
  title: string;
  body: string;
  extra?: string;
  children?: ReactNode;
}) {
  /**
   * A card, not a button: it holds headings and a diagram, which a button may
   * not legally contain. And a click that ends a text selection is not a
   * pick - otherwise selecting the text inside a card chooses it.
   */
  const choose = () => {
    if (locked) return;
    if ((window.getSelection?.()?.toString() ?? "").trim().length > 0) return;
    onPick();
  };
  return (
    <div
      className={`setup-choice${selected ? " selected" : ""}${locked ? " locked" : ""}`}
      role="button"
      tabIndex={locked ? -1 : 0}
      aria-disabled={locked ? "true" : "false"}
      aria-pressed={selected}
      onClick={choose}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          choose();
        }
      }}
    >
      {selected ? <span className="setup-tick">&#10003;</span> : null}
      {locked ? <span className="setup-lock">&#128274;</span> : null}
      <h3>{title}</h3>
      <p>{body}</p>
      {children}
      {extra ? <div className="setup-extra">{extra}</div> : null}
    </div>
  );
}

function Flow({ steps }: { steps: { name: string; env?: string; colour: string; icon?: "gear" }[] }) {
  return (
    <div className="setup-flow">
      {steps.map((entry, index) => (
        <Fragment key={entry.name}>
          {index ? <div className="setup-flow-arrow">&#8594;</div> : null}
          <div className="setup-flow-step">
            {entry.icon === "gear" ? (
              // A program, not a folder: Shoko Server sits between the two folders and does the work.
              <svg width="30" height="24" viewBox="0 0 30 24" fill="none" aria-hidden="true">
                <g transform="translate(15 12)" fill={`${entry.colour}22`} stroke={entry.colour} strokeWidth="1.4">
                  {[0, 45, 90, 135].map((angle) => (
                    <rect key={angle} x="-1.7" y="-9.6" width="3.4" height="19.2" rx="1" transform={`rotate(${angle})`} />
                  ))}
                  <circle r="6" />
                  <circle r="2.4" fill="none" />
                </g>
              </svg>
            ) : (
              <svg width="30" height="24" viewBox="0 0 30 24" fill="none" aria-hidden="true">
                <path
                  d="M2 5.5A2.5 2.5 0 0 1 4.5 3h6.2c.7 0 1.35.3 1.8.82l1.2 1.38H25.5A2.5 2.5 0 0 1 28 7.7v11.8A2.5 2.5 0 0 1 25.5 22h-21A2.5 2.5 0 0 1 2 19.5v-14Z"
                  fill={`${entry.colour}22`}
                  stroke={entry.colour}
                  strokeWidth="1.4"
                />
              </svg>
            )}
            <b>{entry.name}</b>
            {entry.env ? <code>{entry.env}</code> : null}
          </div>
        </Fragment>
      ))}
    </div>
  );
}

function Task({
  name,
  why,
  state,
  detail,
  progress,
  reach,
}: {
  name: string;
  why: string;
  state: FirstSyncState;
  detail: string;
  progress: number | null;
  reach: number | null;
}) {
  const percent = (value: number) => `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%`;
  return (
    <div className="setup-task">
      <div>
        <b>{name}</b>
        <br />
        <span>{state === "failed" ? detail : why}</span>
      </div>
      <span className={`state ${state}`}>{t(`setup.sync.state.${state}`)}</span>
      {/* A bar and no words while it runs (build 230): the calendar takes minutes the first time, and a row
          that only says "working" looks the same as one that has crashed. Green is done, yellow the stretch
          being worked on; with nothing to measure it just moves. */}
      {state === "running" ? (
        <div
          className={"setup-task-bar" + (progress === null ? " indeterminate" : "")}
          role="progressbar"
          aria-label={name}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress === null ? undefined : Math.round(progress * 100)}
        >
          {progress === null ? (
            <span className="setup-task-bar-moving" />
          ) : (
            <>
              <span className="setup-task-bar-reach" style={{ width: percent(reach ?? progress) }} />
              <span className="setup-task-bar-fill" style={{ width: percent(progress) }} />
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

/** A feed's name from its address, so nobody has to invent one. */
function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url.slice(0, 60);
  }
}

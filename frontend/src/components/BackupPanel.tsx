import { useEffect, useRef, useState } from "react";
import {
  backupExportUrl,
  forgetBackup,
  getBackupGroups,
  inspectBackup,
  restoreBackup,
  type BackupContents,
  type BackupGroupInfo,
  type BackupRestored,
} from "../api/client";
import { formatBytes } from "../formatBytes";
import { t } from "../i18n";
import { formatTimestamp } from "../time";

/** Listed by name rather than built as `backup.group.${name}`, so each key is visible to the coverage tests. */
const LABEL: Record<string, string> = {
  settings: "backup.group.settings",
  definitions: "backup.group.definitions",
  library: "backup.group.library",
  calendar: "backup.group.calendar",
  covers: "backup.group.covers",
  anidb: "backup.group.anidb",
  calendar_art: "backup.group.calendar_art",
};

/** What restoring a part does, where that is worth saying. The file groups share one line. */
const HINT: Record<string, string> = {
  settings: "backup.groupHint.settings",
  library: "backup.groupHint.library",
  calendar: "backup.groupHint.calendar",
  definitions: "backup.groupHint.files",
  covers: "backup.groupHint.files",
  anidb: "backup.groupHint.files",
  calendar_art: "backup.artHint",
};

function label(name: string): string {
  return t(LABEL[name] ?? "backup.group.settings");
}

/** Export and import, like Pi-hole's Teleporter: what to take out, and what to take back. The page wraps it in its panel. */
export function BackupPanel() {
  return (
    <>
      <h2>{t("backup.title")}</h2>
      <p className="muted">{t("backup.hint")}</p>
      <ExportCard />
      <ImportCard />
    </>
  );
}

function ExportCard() {
  const [groups, setGroups] = useState<BackupGroupInfo[] | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getBackupGroups()
      .then((found) => {
        setGroups(found);
        setChosen(found.filter((group) => group.default && group.files > 0).map((group) => group.name));
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  function toggle(name: string, on: boolean) {
    setChosen((now) => (on ? [...now, name] : now.filter((n) => n !== name)));
  }

  return (
    <>
      <h3>{t("backup.export")}</h3>
      <p className="warning-message">{t("backup.exportWarning")}</p>
      {error && <p className="reasons">{error}</p>}
      {groups?.map((group) => (
        <label key={group.name} className="check" style={{ margin: "4px 0 10px" }} title={t(HINT[group.name] ?? "backup.groupHint.files")}>
          <input
            type="checkbox"
            checked={chosen.includes(group.name)}
            disabled={group.files === 0}
            onChange={(event) => toggle(group.name, event.target.checked)}
          />
          <span>
            {label(group.name)} <small className="muted">· {t("backup.filesAndSize").replace("{n}", String(group.files)).replace("{size}", formatBytes(group.bytes))}</small>
          </span>
        </label>
      ))}
      <div className="profile-actions">
        {/* A plain link: the session cookie goes with it, and the browser streams the file to disk. */}
        {chosen.length > 0 ? (
          <a className="button secondary" href={backupExportUrl(chosen)} download>
            {t("backup.exportButton")}
          </a>
        ) : (
          <small className="muted">{t("backup.nothingChosen")}</small>
        )}
      </div>
    </>
  );
}

function ImportCard() {
  const picker = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState<"checking" | "restoring" | null>(null);
  const [staged, setStaged] = useState<BackupContents | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [keepLocal, setKeepLocal] = useState(false);
  const [done, setDone] = useState<BackupRestored | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function check(file: File) {
    setBusy("checking");
    setError(null);
    setDone(null);
    try {
      const found = await inspectBackup(file);
      setStaged(found);
      setChosen(found.groups.filter((group) => group.usable && group.name !== "calendar_art").map((group) => group.name));
    } catch (err) {
      setStaged(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function restore() {
    if (!staged) return;
    setBusy("restoring");
    setError(null);
    try {
      setDone(await restoreBackup(staged.id, chosen, keepLocal));
      setStaged(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  function cancel() {
    if (staged) void forgetBackup(staged.id).catch(() => undefined);
    setStaged(null);
  }

  function toggle(name: string, on: boolean) {
    setChosen((now) => (on ? [...now, name] : now.filter((n) => n !== name)));
  }

  return (
    <>
      <h3>{t("backup.import")}</h3>
      <p className="muted">{t("backup.importHint")}</p>
      <input
        ref={picker}
        type="file"
        accept=".zip,application/zip"
        style={{ display: "none" }}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) void check(file);
        }}
      />
      {!staged && (
        <div className="profile-actions">
          <button className="button secondary" disabled={busy !== null} onClick={() => picker.current?.click()}>
            {busy === "checking" ? t("backup.checking") : t("backup.choose")}
          </button>
        </div>
      )}
      {error && <p className="reasons">{error}</p>}
      {staged && (
        <>
          <p className="muted">
            {t("backup.fromBuild")
              .replace("{version}", staged.version ?? "?")
              .replace("{build}", String(staged.build ?? "?"))
              .replace("{when}", formatTimestamp(staged.created_at ?? "") || "—")}
          </p>
          {staged.groups.map((group) => (
            <div key={group.name}>
              <label className="check" style={{ margin: "4px 0 2px" }}>
                <input
                  type="checkbox"
                  checked={chosen.includes(group.name)}
                  disabled={!group.usable}
                  onChange={(event) => toggle(group.name, event.target.checked)}
                />
                <span>
                  {label(group.name)} <small className="muted">· {t("backup.filesAndSize").replace("{n}", String(group.files)).replace("{size}", formatBytes(group.bytes))}</small>
                </span>
              </label>
              <small className={group.usable ? "muted" : "reasons"} style={{ display: "block", margin: "0 0 10px 26px" }}>
                {group.usable
                  ? `${t(HINT[group.name] ?? "backup.groupHint.files")}${group.note ? ` ${t("backup.willMigrate")}` : ""}`
                  : t("backup.unusable").replace("{reason}", group.note ?? "")}
              </small>
            </div>
          ))}
          <label className="check" style={{ margin: "10px 0 2px" }}>
            <input type="checkbox" checked={keepLocal} onChange={(event) => setKeepLocal(event.target.checked)} />
            <span>{t("backup.keepLocal")}</span>
          </label>
          <small className="muted" style={{ display: "block", margin: "0 0 12px 26px" }}>{t("backup.keepLocalHint")}</small>
          <div className="profile-actions">
            <button className="button primary" disabled={busy !== null || chosen.length === 0} onClick={() => void restore()}>
              {busy === "restoring" ? t("backup.restoring") : t("backup.restore")}
            </button>
            <button className="button secondary" disabled={busy !== null} onClick={cancel}>
              {t("backup.cancel")}
            </button>
          </div>
        </>
      )}
      {done && <RestoredNote done={done} />}
    </>
  );
}

function RestoredNote({ done }: { done: BackupRestored }) {
  return (
    <div>
      <p className="imported-tag">{t("backup.done").replace("{list}", done.restored.map(label).join(", "))}</p>
      {done.safety_backup && <p className="muted">{t("backup.safeFirst").replace("{name}", done.safety_backup)}</p>}
      {done.libraries_kept > 0 && <p className="muted">{t("backup.keptLibraries").replace("{n}", String(done.libraries_kept))}</p>}
      {done.libraries_unmatched.length > 0 && (
        <p className="reasons">{t("backup.unmatched").replace("{list}", done.libraries_unmatched.join(", "))}</p>
      )}
      {done.warnings.includes("accounts") && <p className="reasons">{t("backup.accounts")}</p>}
      {done.warnings.includes("sync") && <p className="reasons">{t("backup.syncWarning")}</p>}
      {done.restart_recommended && <p className="warning-message">{t("backup.restart")}</p>}
    </div>
  );
}

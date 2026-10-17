import { useEffect, useState } from "react";
import { getAppSettings, runShokoImport } from "../api/client";
import { t } from "../i18n";

/**
 * What to do about a file Shoko has not filed (item 2).
 *
 * Two ways out, in the order they are worth trying: ask Shoko to run its import now, which usually
 * clears it, and if it does not, open Shoko - the file has to be hashed there and added to AniDB by
 * hand, which AniFlow cannot do for you. "Run import" is the same as pressing Run Import in Shoko; it
 * writes nothing to AniDB's list by itself beyond what Shoko's own settings already do on import.
 * The link is only there when a Shoko address is set.
 */
/** The Shoko address from the settings, or "" when there is none. */
function useShokoUrl(): string {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let current = true;
    void getAppSettings()
      .then((settings) => { if (current) setUrl((settings.shoko_url || "").trim()); })
      .catch(() => undefined);
    return () => { current = false; };
  }, []);
  return url;
}

/** Just the link, for the places that already have a Run import button of their own. */
export function ShokoOpenLink() {
  const url = useShokoUrl();
  if (!url) return null;
  return (
    <a className="button secondary" href={url} target="_blank" rel="noreferrer" title={t("shoko.openHint")}>
      {t("shoko.open")}
    </a>
  );
}

export function ShokoHelpButtons({ compact = false }: { compact?: boolean }) {
  const [running, setRunning] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  async function run() {
    setRunning(true);
    setNote(null);
    try {
      const answer = await runShokoImport();
      setNote(answer.started
        ? { ok: true, text: t("progress.importStarted") }
        : { ok: false, text: answer.detail ?? t("progress.importFailed") });
    } catch (error) {
      setNote({ ok: false, text: error instanceof Error ? error.message : t("progress.importFailed") });
    } finally {
      setRunning(false);
    }
  }

  return (
    <span className={compact ? "shoko-help compact" : "shoko-help"}>
      <button className="button secondary" disabled={running} title={t("progress.runImportHint")} onClick={() => void run()}>
        {running ? t("progress.importing") : t("progress.runImport")}
      </button>
      <ShokoOpenLink />
      {note && <small className={note.ok ? "muted" : "reasons"}>{note.text}</small>}
    </span>
  );
}

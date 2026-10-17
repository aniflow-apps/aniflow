/**
 * The checklist page (build 232): what has to be true for AniFlow to do its job, each thing tested and answered.
 *
 * Found when an import failed on a read-only library folder - a fact the Libraries page had shown all along and
 * nothing else had acted on. This gathers the scattered answers (torrent client, folders, indexer, calendar,
 * Shoko) into one list with a mark per row and, where there is something to do, a way to the page that does it.
 *
 * The wording is not stored: the server answers with codes and values ("torrent_client", "unreachable",
 * {reason}) and this turns them into sentences, so the language follows the person.
 */
import { useCallback, useEffect, useState } from "react";

import { type SystemCheck, type SystemChecks, getSystemChecks } from "../api/client";
import { has, t } from "../i18n";

const GROUPS = ["setup", "folders", "data", "services", "activity"] as const;

/** The sentence for one answer: the question's own wording, else the generic one for that answer. */
export function checkText(check: SystemCheck): string {
  const specific = `checks.${check.id}.${check.code}`;
  const key = has(specific) ? specific : `checks.generic.${check.code}`;
  const values = { ...check.values };
  // The failing jobs arrive as ids; the Schedule page's own names are what the person knows them by.
  if (values.tasks) {
    values.names = values.tasks
      .split(",")
      .map((id) => (has(`schedule.tasks.${id}.name`) ? t(`schedule.tasks.${id}.name`) : id))
      .join(", ");
  }
  return Object.entries(values).reduce((text, [name, value]) => text.split(`{${name}}`).join(value), t(key));
}

/** What the row is called: a library by its own name, everything else by its question. */
export function checkTitle(check: SystemCheck): string {
  const byName = check.id === "library" || check.id === "indexer_live";
  return byName && check.values.name ? check.values.name : t(`checks.title.${check.id}`);
}

const MARK: Record<string, string> = { ok: "✓", warning: "!", error: "✕", skipped: "–" };

/**
 * The checks and what they found. ``bare`` is the install wizard's version: no page heading, groups without
 * panels, and no Fix buttons - during setup the wizard is the whole screen, so there is nowhere to go yet.
 */
export function ChecksList({
  onResult,
  bare = false,
  live = !bare,
}: {
  onResult?: (result: SystemChecks) => void;
  bare?: boolean;
  /** Also ask the indexers and the metadata provider - one request each. The page does; the wizard does not. */
  live?: boolean;
}) {
  const [result, setResult] = useState<SystemChecks | null>(null);
  const [busy, setBusy] = useState(true);
  const [failed, setFailed] = useState(false);

  const run = useCallback(async () => {
    setBusy(true);
    setFailed(false);
    try {
      const answer = await getSystemChecks(live);
      setResult(answer);
      onResult?.(answer);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }, [onResult, live]);

  useEffect(() => {
    void run();
  }, [run]);

  const again = (
    <button className="button secondary" disabled={busy} onClick={() => void run()}>
      {busy ? t("checks.running") : t("checks.again")}
    </button>
  );

  return (
    <>
      <div className={bare ? "checks-top" : "detail-panel"}>
        {bare ? (
          again
        ) : (
          <>
            <div className="status-line">
              <h2>{t("checks.heading")}</h2>
              {again}
            </div>
            <p className="muted">{t("checks.hint")}</p>
          </>
        )}
        {failed ? <p className="error-message">{t("checks.failed")}</p> : null}
        {result ? (
          <p className={`checks-summary ${result.errors ? "error" : result.warnings ? "warning" : "ok"}`}>
            {result.errors
              ? t("checks.summary.errors").replace("{n}", String(result.errors))
              : result.warnings
                ? t("checks.summary.warnings").replace("{n}", String(result.warnings))
                : t("checks.summary.ok")}
          </p>
        ) : null}
      </div>

      {GROUPS.map((group) => {
        const rows = (result?.checks ?? []).filter((check) => check.group === group && check.state !== "skipped");
        if (rows.length === 0) return null;
        return (
          <div className={bare ? "checks-group" : "detail-panel"} key={group}>
            <h3>{t(`checks.group.${group}`)}</h3>
            <ul className="checks-list">
              {rows.map((check) => (
                <li key={`${check.id}-${check.subject ?? ""}`} className={`checks-row ${check.state}`}>
                  <span className={`checks-mark ${check.state}`} role="img" aria-label={t(`checks.state.${check.state}`)}>
                    {MARK[check.state]}
                  </span>
                  <span className="checks-text">
                    <b>{checkTitle(check)}</b>
                    <small>{checkText(check)}</small>
                  </span>
                  {!bare && check.fix && check.state !== "ok" ? (
                    <a className="button secondary" href={check.fix}>
                      {t("checks.fix")}
                    </a>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </>
  );
}

export function ChecksPage({ onResult }: { onResult?: (result: SystemChecks) => void }) {
  return (
    <section className="checks-page">
      <ChecksList onResult={onResult} />
    </section>
  );
}

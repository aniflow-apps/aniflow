/**
 * What happened, as a diary (build 125).
 *
 * The attention half moved to its own page - see AttentionPage.tsx. The two
 * were one screen because they were built together, not because they belong
 * together: this one is **append-only history**, and the route that empties it
 * says plainly that doing so changes nothing. Nothing here is waiting on the
 * user.
 *
 * Which is also why the level filter matters more now than it did. Routine
 * bookkeeping is the bulk of the log and the least likely thing anybody came
 * to read, so it is hidden by default.
 */
import { useCallback, useEffect, useState } from "react";
import {
  clearActivity,
  listActivity,
  type ActivityItem,
  type ActivityLevel,
} from "../api/client";
import { t } from "../i18n";
import { formatTime, formatTimestamp } from "../time";
import { DebugSamples } from "./DebugSamples";
import { severityClass } from "../severity";

export function ActivityPage({ debugMode = false }: { debugMode?: boolean }) {
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const [error, setError] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [level, setLevel] = useState<ActivityLevel>("notable");
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setActivity(await listActivity(100, level));
      setError(false);
    } catch {
      setError(true);
    }
  }, [level]);

  useEffect(() => {
    void load();
  }, [load]);

  /** The log is history only, so emptying it cannot break anything. */
  async function clearLog() {
    if (!window.confirm(t("activity.log.clearConfirm"))) return;
    setClearing(true);
    setError(false);
    try {
      await clearActivity();
      await load();
    } catch {
      setError(true);
    } finally {
      setClearing(false);
    }
  }

    return <section className="activity-page">
    <div className="detail-panel">
      <div className="status-line">
        <h2>{t("activity.log.title")}</h2>
        <span className="muted">{t("activity.log.count").replace("{n}", String(activity.length))}</span>
        {/* Routine bookkeeping is hidden by default: it is the bulk of the log
            and the least likely thing anyone came here to read. */}
        <div className="filter-row">
          {(["problem", "notable", "routine"] as ActivityLevel[]).map((option) => (
            <button
              key={option}
              className={`button ${level === option ? "primary" : "secondary"}`}
              onClick={() => setLevel(option)}
            >
              {t(`activity.level.${option}`)}
            </button>
          ))}
        </div>
        <button
          className="button danger"
          disabled={clearing || activity.length === 0}
          title={t("activity.log.clearHint")}
          onClick={() => void clearLog()}
        >
          {clearing ? t("settings.saving") : t("activity.log.clear")}
        </button>
      </div>
      <p className="muted" style={{ margin: "10px 0 16px" }}>{t("activity.log.hint")}</p>
      {/* Its own error line (build 125). The attention panel used to carry
          the only one, so after the split a failed load left this page
          showing an empty log and no reason - which reads as "nothing has
          happened" rather than "this could not be read". */}
      {error && <p className="error-message">{t("activity.error")}</p>}
      {activity.length === 0 ? (
        <p className="muted">{t("activity.log.empty")}</p>
      ) : (
        <ul className="release-list">
          {activity.map((entry) => (
            <li key={entry.id} className={`release-row activity-row level-${entry.level}`}>
              <span className={`chip ${severityClass(entry.kind)}`}>{t(`activity.kind.${entry.kind}`) === `activity.kind.${entry.kind}` ? entry.kind.replaceAll("_", " ").toLowerCase() : t(`activity.kind.${entry.kind}`)}</span>
              <div className="release-main">
                <strong>{entry.message}</strong>
                {entry.anime_name && <small className="muted">{entry.anime_name}</small>}
                {/* The counters and named changes behind the summary line. */}
                {expanded === entry.id && (
                  <dl className="parsed-grid" style={{ marginTop: 8 }}>
                    {Object.entries(entry.details ?? {}).map(([key, value]) => (
                      <div key={key} style={{ display: "contents" }}>
                        <dt>{key.replaceAll("_", " ")}</dt>
                        {/* A list gets a line each. Joined with semicolons,
                            three release names read as one broken sentence -
                            which is what the summary line above used to be. */}
                        <dd>{Array.isArray(value)
                          ? (value.length
                              ? <ul className="change-list">{value.map((entry, index) => <li key={index}>{String(entry)}</li>)}</ul>
                              : "—")
                          : String(value ?? "—")}</dd>
                      </div>
                    ))}
                    <dt>{t("activity.detail.when")}</dt>
                    <dd>{formatTimestamp(entry.created_at)}</dd>
                  </dl>
                )}
              </div>
              <div className="analyze-actions" style={{ display: "grid", gap: 6, justifyItems: "end" }}>
                <small className="muted">{formatTime(entry.created_at)}</small>
                {Object.keys(entry.details ?? {}).length > 0 && (
                  <button
                    className="button secondary"
                    style={{ padding: "4px 9px", fontSize: ".72rem" }}
                    onClick={() => setExpanded(expanded === entry.id ? null : entry.id)}
                  >
                    {expanded === entry.id ? t("activity.detail.hide") : t("activity.detail.show")}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>

    {debugMode && <DebugSamples onChanged={() => void load()} />}
  </section>;
}

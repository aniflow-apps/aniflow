import { useCallback, useEffect, useState } from "react";
import {
  deleteTemplate,
  listTemplates,
  testProfiles,
  updateTemplate,
  type ProfileTemplate,
  type ProfileTestReport,
  type ProfileTestResult,
} from "../api/client";
import { t } from "../i18n";
import { matchFailText } from "../reasons";
import { TemplateDialog, templateToDraft } from "./TemplateDialog";

function rulesSummary(tpl: ProfileTemplate): string {
  return [
    tpl.subgroups.length ? tpl.subgroups.join(", ") : t("profile.any"),
    tpl.resolution ?? t("profile.any"),
    tpl.media_source ?? t("profile.any"),
  ].join(" · ");
}

/**
 * What one tested profile amounts to, on one line (build 95).
 *
 * The three verdicts are different problems and must not read alike. A group
 * that published nothing is a name that is wrong - that is the whole reason
 * this button exists, because a misspelled group fails silently for ever. A
 * group that is publishing while the profile still takes nothing is a rule
 * about quality or source, and the answer says which rule and what the group
 * actually offers, so the fix is visible without opening the editor.
 */
function testSummary(result: ProfileTestResult, report: ProfileTestReport): string {
  if (!report.reached_any_feed) return t("profiles.testNoFeed");
  if (result.verdict === "ok")
    return t("profiles.testOk")
      .replace("{n}", String(result.accepted))
      .replace("{total}", String(report.items));
  if (result.verdict === "no-group")
    return t("profiles.testNoGroup").replace(
      "{groups}", result.groups.map((group) => group.name).join(", "),
    );
  const offered = result.groups
    .filter((group) => group.items > 0)
    .map((group) => `${group.name}: ${group.resolutions.join(", ") || t("profile.any")}`)
    .join(" · ");
  const rule = result.top_failures[0]?.[0];
  return [
    t("profiles.testNoMatch"),
    rule ? matchFailText(rule) : "",
    offered,
  ].filter(Boolean).join(" — ");
}

function sortTemplates(templates: ProfileTemplate[]): ProfileTemplate[] {
  return [...templates].sort((a, b) =>
    Number(b.is_default) - Number(a.is_default) || a.name.localeCompare(b.name),
  );
}

export function ProfilesPage() {
  const [templates, setTemplates] = useState<ProfileTemplate[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);
  const [editing, setEditing] = useState<ProfileTemplate | null>(null);
  const [creating, setCreating] = useState(false);
  // Per profile, so testing one profile never blanks another's answer, and
  // testing all of them fills in every row at once.
  const [tested, setTested] = useState<Record<string, ProfileTestResult>>({});
  const [report, setReport] = useState<ProfileTestReport | null>(null);
  const [testing, setTesting] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setTemplates(sortTemplates(await listTemplates()));
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function markDefault(template: ProfileTemplate) {
    try {
      await updateTemplate(template.id, { ...templateToDraft(template), is_default: true });
      await load();
    } catch {
      setError(true);
    }
  }

  /** One profile, or every one of them when no id is given. */
  async function test(templateId?: string) {
    setTesting(templateId ?? "all");
    try {
      const answer = await testProfiles(templateId);
      setReport(answer);
      setTested((current) => {
        const next = { ...current };
        for (const result of answer.results) next[result.template_id] = result;
        return next;
      });
      setError(false);
    } catch {
      setError(true);
    } finally {
      setTesting(null);
    }
  }

  async function remove(template: ProfileTemplate) {
    const suffix = template.anime_count > 0 ? ` ${t("profiles.deleteInUse").replace("{n}", String(template.anime_count))}` : "";
    if (!window.confirm(t("profiles.deleteConfirm") + suffix)) return;
    try {
      await deleteTemplate(template.id);
      await load();
    } catch {
      setError(true);
    }
  }

  return <section className="profiles-page">
    {!loaded ? null : (
      <>
        <div className="profiles-intro">
          <p className="muted" style={{ margin: 0 }}>{t("profiles.favoriteHint")}</p>
          <span className="action-stack horizontal">
            {/* The primary action first, the errand after it (build 120):
                making a profile is what this page is for, and testing every
                subgroup is an occasional check. One feed read answers for
                all of them. */}
            <button className="button primary" onClick={() => setCreating(true)}>{t("profiles.new")}</button>
            <button
              className="button secondary"
              disabled={testing !== null || templates.length === 0}
              onClick={() => void test()}
            >
              {testing === "all" ? t("profiles.testing") : t("profiles.testAll")}
            </button>
          </span>
        </div>
        {report && (
          <p className="muted">
            {report.reached_any_feed
              ? t("profiles.testFeeds")
                  .replace("{feeds}", String(report.feeds.filter((feed) => !feed.error).length))
                  .replace("{items}", String(report.items))
              : t("profiles.testNoFeed")}
          </p>
        )}
        {error && <p className="error-message">{t("activity.error")}</p>}
        {templates.length === 0 ? (
          <section className="empty-state"><h2>{t("profiles.emptyTitle")}</h2><p>{t("profiles.emptyBody")}</p></section>
        ) : (
          <div className="template-grid">
            {templates.map((template) => (
              <article className="template-card" key={template.id}>
                <strong>
                  {template.name}
                  {template.is_default && <span className="default-badge">{t("profiles.isDefault")}</span>}
                </strong>
                <small>{rulesSummary(template)}</small>
                <small className="muted">
                  {template.anime_count > 0 ? t("profiles.usedBy").replace("{n}", String(template.anime_count)) : t("profiles.unused")}
                </small>
                {/* The answer sits on the card it is about. Amber rather than
                    red: a profile matching nothing right now is not
                    necessarily broken - a group between seasons publishes
                    nothing either - so this is a finding, not a failure. */}
                {tested[template.id] && report && (
                  <small className={tested[template.id].verdict === "ok" ? "muted" : "reasons"}>
                    {testSummary(tested[template.id], report)}
                  </small>
                )}
                <div className="action-stack horizontal">
                  <button
                    className="button secondary"
                    disabled={testing !== null}
                    onClick={() => void test(template.id)}
                  >
                    {testing === template.id ? t("profiles.testing") : t("profiles.test")}
                  </button>
                  {!template.is_default && (
                    <button className="button secondary" onClick={() => void markDefault(template)}>{t("profiles.setDefault")}</button>
                  )}
                  <button className="button secondary" onClick={() => setEditing(template)}>{t("profiles.edit")}</button>
                  <button className="button danger" onClick={() => void remove(template)}>{t("rss.sources.delete")}</button>
                </div>
              </article>
            ))}
          </div>
        )}
      </>
    )}
    {(creating || editing) && (
      <TemplateDialog initial={editing} onClose={() => { setCreating(false); setEditing(null); }} onSaved={() => void load()} />
    )}
  </section>;
}

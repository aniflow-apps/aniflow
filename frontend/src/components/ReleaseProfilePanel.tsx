import { useEffect, useState } from "react";
import { useLockedPageScroll } from "../useLockedPageScroll";
import {
  applyTemplateToAnime,
  createTemplate,
  getProfile,
  listTemplates,
  saveProfile,
  type ProfileTemplate,
  type ReleaseProfile,
  type ReleaseProfileDraft,
} from "../api/client";
import { t } from "../i18n";

const RESOLUTIONS = ["4320p", "2160p", "1440p", "1080p", "720p", "540p", "480p"];
const MEDIA_SOURCES = ["web_dl", "webrip", "web", "remux", "bluray", "hdtv", "dvd", "tv", "vhs"];
const CENSOR_OPTIONS = ["ANY", "UNCENSORED", "CENSORED"] as const;

function toDraft(profile: ReleaseProfile): ReleaseProfileDraft {
  return {
    subgroups: [...profile.subgroups],
    resolution: profile.resolution,
    media_source: profile.media_source,
    censor_preference: profile.censor_preference,
    require_title_match: profile.require_title_match,
    require_resolution_known: profile.require_resolution_known,
    require_source_known: profile.require_source_known,
    require_censor_known: profile.require_censor_known,
  };
}

export function ReleaseProfilePanel({ animeId }: { animeId: string }) {
  const [profile, setProfile] = useState<ReleaseProfile | null>(null);
  const [templates, setTemplates] = useState<ProfileTemplate[]>([]);
  const [editorOpen, setEditorOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void Promise.all([getProfile(animeId), listTemplates().catch(() => [])])
      .then(([existing, tpl]) => {
        if (existing) setProfile(existing);
        setTemplates(tpl);
      })
      .catch(() => setError(t("activity.error")))
      .finally(() => setLoaded(true));
  }, [animeId]);

  async function changeTemplate(templateId: string | null) {
    setError(null);
    try {
      await applyTemplateToAnime(animeId, templateId);
      const refreshed = await getProfile(animeId);
      if (refreshed) setProfile(refreshed);
    } catch {
      setError(t("activity.error"));
    }
  }

  function onSaved(updated: ReleaseProfile) {
    setProfile(updated);
    setEditorOpen(false);
  }

  if (!loaded) return null;
  const isCustom = !profile?.template_id;
  const basedOn = templates.find((tpl) => tpl.id === profile?.template_id);

  return <>
    <section className="detail-panel profile-panel compact">
      <h2>{t("detail.releaseProfile")}</h2>
      <label className="field" style={{ maxWidth: 340 }}>
        <span>{t("detail.templateLabel")}</span>
        <select value={profile?.template_id ?? ""} onChange={(event) => void changeTemplate(event.target.value || null)}>
          <option value="">{t("detail.templateCustom")}</option>
          {templates.map((tpl) => (
            <option key={tpl.id} value={tpl.id}>{tpl.name}{tpl.is_default ? " *" : ""}</option>
          ))}
        </select>
      </label>
      {basedOn && <small className="muted">{t("detail.basedOn").replace("{name}", basedOn.name)}</small>}
      {!profile && <p className="muted">{t("profile.noneYet")}</p>}
      {isCustom && profile && (
        <button className="button secondary" style={{ marginTop: 10 }} onClick={() => setEditorOpen(true)}>
          {t("profile.editRules")}
        </button>
      )}
      {error && <p className="error-message">{error}</p>}
    </section>
    {editorOpen && profile && (
      <RulesEditorModal
        animeId={animeId}
        initialDraft={toDraft(profile)}
        onClose={() => setEditorOpen(false)}
        onSaved={onSaved}
      />
    )}
  </>;
}


export function RulesEditorModal({
  animeId,
  initialDraft,
  onClose,
  onSaved,
}: {
  animeId: string;
  initialDraft: ReleaseProfileDraft;
  onClose: () => void;
  onSaved: (updated: ReleaseProfile) => void;
}) {
  useLockedPageScroll();
  const [draft, setDraft] = useState<ReleaseProfileDraft>(initialDraft);
  const [saving, setSaving] = useState(false);
  const [saveAsMode, setSaveAsMode] = useState(false);
  const [templateName, setTemplateName] = useState("");
  const [error, setError] = useState<string | null>(null);

  function patch(update: Partial<ReleaseProfileDraft>) {
    setDraft((c) => ({ ...c, ...update }));
  }

  async function save() {
    setSaving(true); setError(null);
    try {
      const stored = await saveProfile(animeId, draft);
      onSaved(stored); onClose();
    } catch { setError(t("settings.error")); }
    finally { setSaving(false); }
  }

  async function saveAsNew() {
    if (!templateName.trim()) return;
    setSaving(true); setError(null);
    try {
      await createTemplate({
        name: templateName.trim(),
        subgroups: draft.subgroups,
        resolution: draft.resolution,
        media_source: draft.media_source,
        censor_preference: draft.censor_preference,
        require_title_match: draft.require_title_match,
        require_resolution_known: draft.require_resolution_known,
        require_source_known: draft.require_source_known,
        require_censor_known: draft.require_censor_known,
        is_default: false,
      });
      onClose();
    } catch { setError(t("profiles.error")); }
    finally { setSaving(false); }
  }

  return <div className="dialog-backdrop" role="presentation">
    <section className="dialog dialog-wide" role="dialog" aria-modal="true" aria-label={t("profile.editRules")}>
      <header>
        <h2>{t("profile.editRules")}</h2>
        <button className="close" onClick={onClose} aria-label={t("add.cancel")}>×</button>
      </header>

      <div className="settings-grid two" style={{ marginTop: 16 }}>
        <label className="field">
          <span>{t("profile.subgroups")}</span>
          <input value={draft.subgroups.join(", ")} placeholder={t("profile.subgroupsHint")}
                 onChange={(e) => setDraft({ ...draft, subgroups: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
        </label>
        <label className="field">
          <span>{t("profile.resolution")}</span>
          <select value={draft.resolution ?? ""} onChange={(e) => patch({ resolution: e.target.value || null })}>
            <option value="">{t("profile.any")}</option>
            {RESOLUTIONS.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <label className="field">
          <span>{t("profile.mediaSource")}</span>
          <select value={draft.media_source ?? ""} onChange={(e) => patch({ media_source: e.target.value || null })}>
            <option value="">{t("profile.any")}</option>
            {MEDIA_SOURCES.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <label className="field">
          <span>{t("profile.censor")}</span>
          <select value={draft.censor_preference}
                  onChange={(e) => setDraft({ ...draft, censor_preference: e.target.value as typeof draft.censor_preference })}>
            {CENSOR_OPTIONS.map((v) => <option key={v} value={v}>{t(`profile.censor.${v}`)}</option>)}
          </select>
        </label>
      </div>
      <details className="advanced" style={{ marginTop: 14 }}>
        <summary>{t("profile.advanced")}</summary>
        {[["require_title_match", t("profile.requireTitleMatch")],
          ["require_resolution_known", t("profile.requireResolutionKnown")],
          ["require_source_known", t("profile.requireSourceKnown")],
          ["require_censor_known", t("profile.requireCensorKnown")]].map(([key, label]) => (
          <label className="check" key={key}>
            <input type="checkbox" checked={draft[key as keyof ReleaseProfileDraft] as boolean}
                   onChange={(e) => setDraft({ ...draft, [key]: e.target.checked })} />
            <span>{label}</span>
          </label>
        ))}
      </details>

      {error && <p className="error-message">{error}</p>}
      <div className="dialog-actions">
        <button className="button secondary" onClick={onClose}>{t("add.cancel")}</button>
        {saveAsMode ? (
          <>
            <input value={templateName} placeholder={t("profiles.name")} autoFocus
                   onChange={(e) => setTemplateName(e.target.value)}
                   style={{ padding: "6px 10px", border: "1px solid #3d465b", borderRadius: 8, background: "#0e111a", color: "#edf1fa" }} />
            <button className="button primary" disabled={saving || !templateName.trim()} onClick={() => void saveAsNew()}>
              {saving ? t("settings.saving") : t("profiles.saveNew")}
            </button>
          </>
        ) : (
          <>
            <button className="button secondary" disabled={saving} onClick={() => setSaveAsMode(true)}>
              {t("profiles.saveAsTemplate")}
            </button>
            <button className="button primary" disabled={saving} onClick={() => void save()}>
              {saving ? t("settings.saving") : t("settings.save")}
            </button>
          </>
        )}
      </div>
    </section>
  </div>;
}

export { RESOLUTIONS, MEDIA_SOURCES, CENSOR_OPTIONS };

import { useState } from "react";
import { useLockedPageScroll } from "../useLockedPageScroll";
import {
  createTemplate,
  updateTemplate,
  type ProfileTemplate,
  type ProfileTemplateDraft,
} from "../api/client";
import { t } from "../i18n";
import { suggestedTemplateName } from "../templateName";

const RESOLUTIONS = ["4320p", "2160p", "1440p", "1080p", "720p", "540p", "480p"];
const MEDIA_SOURCES = ["web_dl", "webrip", "web", "remux", "bluray", "hdtv", "dvd", "tv", "vhs"];
const CENSOR_OPTIONS = ["ANY", "UNCENSORED", "CENSORED"] as const;

export const EMPTY_DRAFT: ProfileTemplateDraft = {
  name: "",
  subgroups: [],
  resolution: null,
  media_source: null,
  censor_preference: "ANY",
  require_title_match: true,
  require_resolution_known: false,
  require_source_known: false,
  require_censor_known: false,
  is_default: false,
};

export function templateToDraft(template: ProfileTemplate): ProfileTemplateDraft {
  return {
    name: template.name,
    subgroups: [...template.subgroups],
    resolution: template.resolution,
    media_source: template.media_source,
    censor_preference: template.censor_preference,
    require_title_match: template.require_title_match,
    require_resolution_known: template.require_resolution_known,
    require_source_known: template.require_source_known,
    require_censor_known: template.require_censor_known,
    is_default: template.is_default,
  };
}

/**
 * The profile-template editor, shared by the Profiles page and the add-anime
 * setup step.
 *
 * `seed` pre-fills a *new* template - the setup step uses it to hand over the
 * subgroup and quality a search just discovered. The name is built from the
 * parts of the seed that say something ("SubsPlease 720p web") and left empty
 * when none does (build 231); it used to be left out of every seed, which made
 * everybody type what was already on screen. Only a starting point.
 */
export function TemplateDialog({
  initial,
  seed,
  onClose,
  onSaved,
}: {
  initial: ProfileTemplate | null;
  seed?: Partial<ProfileTemplateDraft>;
  onClose: () => void;
  onSaved: (saved: ProfileTemplate) => void;
}) {
  useLockedPageScroll();
  const [draft, setDraft] = useState<ProfileTemplateDraft>(
    () => {
      if (initial) return templateToDraft(initial);
      const start = { ...EMPTY_DRAFT, ...seed };
      return { ...start, name: suggestedTemplateName(start) };
    },
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(false);

  async function save() {
    if (!draft.name.trim()) return;
    setSaving(true);
    setError(false);
    try {
      const saved = initial ? await updateTemplate(initial.id, draft) : await createTemplate(draft);
      onSaved(saved);
      onClose();
    } catch {
      setError(true);
    } finally {
      setSaving(false);
    }
  }

  return <div className="dialog-backdrop" role="presentation">
    <section className="dialog" role="dialog" aria-modal="true" aria-label={t("profiles.editor")}>
      <header><h2>{initial ? t("profiles.editTitle") : t("profiles.newTitle")}</h2>
        <button className="close" onClick={onClose} aria-label={t("add.cancel")}>×</button></header>
      {seed && !initial && <p className="muted" style={{ marginTop: 10 }}>{t("profiles.seededHint")}</p>}
      <div className="settings-grid two" style={{ marginTop: 16 }}>
        <label className="field">
          <span>{t("profiles.name")}</span>
          <input value={draft.name} autoFocus onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
        </label>
        <label className="field">
          <span>{t("profile.subgroups")}</span>
          <input value={draft.subgroups.join(", ")} placeholder={t("profile.subgroupsHint")}
                 onChange={(event) => setDraft({ ...draft, subgroups: event.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
        </label>
        <label className="field">
          <span>{t("profile.resolution")}</span>
          <select value={draft.resolution ?? ""} onChange={(event) => setDraft({ ...draft, resolution: event.target.value || null })}>
            <option value="">{t("profile.any")}</option>
            {RESOLUTIONS.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label className="field">
          <span>{t("profile.mediaSource")}</span>
          <select value={draft.media_source ?? ""} onChange={(event) => setDraft({ ...draft, media_source: event.target.value || null })}>
            <option value="">{t("profile.any")}</option>
            {MEDIA_SOURCES.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label className="field">
          <span>{t("profile.censor")}</span>
          <select value={draft.censor_preference}
                  onChange={(event) => setDraft({ ...draft, censor_preference: event.target.value as typeof draft.censor_preference })}>
            {CENSOR_OPTIONS.map((value) => <option key={value} value={value}>{t(`profile.censor.${value}`)}</option>)}
          </select>
        </label>
      </div>
      <label className="check" style={{ marginTop: 14 }}>
        <input type="checkbox" checked={draft.is_default}
               onChange={(event) => setDraft({ ...draft, is_default: event.target.checked })} />
        <span>{t("profiles.defaultProfile")}</span>
      </label>
      <details className="advanced" style={{ marginTop: 14 }}>
        <summary>{t("profile.advanced")}</summary>
        {[["require_title_match", t("profile.requireTitleMatch")],
          ["require_resolution_known", t("profile.requireResolutionKnown")],
          ["require_source_known", t("profile.requireSourceKnown")],
          ["require_censor_known", t("profile.requireCensorKnown")]].map(([key, label]) => (
          <label className="check" key={key}>
            <input type="checkbox" checked={draft[key as keyof ProfileTemplateDraft] as boolean}
                   onChange={(event) => setDraft({ ...draft, [key]: event.target.checked })} />
            <span>{label}</span>
          </label>
        ))}
      </details>
      {error && <p className="error-message">{t("profiles.error")}</p>}
      <div className="dialog-actions">
        <button className="button secondary" onClick={onClose}>{t("add.cancel")}</button>
        <button className="button primary" disabled={saving || !draft.name.trim()} onClick={() => void save()}>
          {saving ? t("settings.saving") : t("settings.save")}
        </button>
      </div>
    </section>
  </div>;
}

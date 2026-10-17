import { useState } from "react";
import { t } from "../i18n";
import type { AnalyzeItem, AnimeSummary } from "../api/client";

const CLASS_ORDER: Record<string, number> = { EXACT: 0, HIGH: 1, REVIEW: 2, UNKNOWN: 3 };

export function AnalyzeResults({
  items,
  highlightAnimeId,
  onSelect,
  assignCandidates,
  onAssign,
  onAccept,
  onIgnore,
  note,
}: {
  items: AnalyzeItem[];
  highlightAnimeId?: string | null;
  onSelect?: (item: AnalyzeItem) => void;
  assignCandidates?: AnimeSummary[];
  onAssign?: (item: AnalyzeItem, animeId: string) => void;
  onAccept?: (item: AnalyzeItem) => void;
  onIgnore?: (item: AnalyzeItem) => void;
  /** A short remark under a row - on an anime's page, why its profile would
   *  pass the release over. */
  note?: (item: AnalyzeItem) => string | null;
}) {
  const sorted = [...items].sort((a, b) => {
    const aMatch = highlightAnimeId && a.best_anime_id === highlightAnimeId ? 0 : 1;
    const bMatch = highlightAnimeId && b.best_anime_id === highlightAnimeId ? 0 : 1;
    if (aMatch !== bMatch) return aMatch - bMatch;
    if (a.classification !== b.classification) {
      return (CLASS_ORDER[a.classification] ?? 9) - (CLASS_ORDER[b.classification] ?? 9);
    }
    return b.confidence - a.confidence;
  });

  return <ul className="release-list">
    {sorted.map((item) => (
      <AnalyzeRow
        key={item.raw_name + item.source_name}
        item={item}
        highlight={!!highlightAnimeId && item.best_anime_id === highlightAnimeId}
        onSelect={onSelect}
        assignCandidates={assignCandidates}
        onAssign={onAssign}
        onAccept={onAccept}
        onIgnore={onIgnore}
        note={note?.(item) ?? null}
      />
    ))}
  </ul>;
}

function AnalyzeRow({
  item,
  highlight,
  onSelect,
  assignCandidates,
  onAssign,
  onAccept,
  onIgnore,
  note,
}: {
  item: AnalyzeItem;
  highlight: boolean;
  note: string | null;
  onSelect?: (item: AnalyzeItem) => void;
  assignCandidates?: AnimeSummary[];
  onAssign?: (item: AnalyzeItem, animeId: string) => void;
  onAccept?: (item: AnalyzeItem) => void;
  onIgnore?: (item: AnalyzeItem) => void;
}) {
  const [open, setOpen] = useState(false);
  const [assigning, setAssigning] = useState(false);
  const relevant = !!highlight;

  return <li className={`analyze-row ${relevant ? "relevant" : ""}`}>
    <div className="analyze-main">
      {/* Verdict, confidence, then the name (build 153). The percentage used
          to follow the name with no rule to space it, so it read as part of
          the filename - "...mkv0%". */}
      <div className="analyze-head">
        <span className={`chip ${item.classification.toLowerCase()}`}>{t(`analyze.class.${item.classification}`)}</span>
        <span className="confidence">{item.confidence}%</span>
        <strong className="analyze-title" title={item.raw_name}>{item.raw_name}</strong>
      </div>
      <small className="muted">
        {[
          item.subgroup,
          item.episode ? `${t("profile.episode")} ${item.episode}` : null,
          item.resolution,
          `v${item.version}`,
          item.crc32 ? `CRC ${item.crc32}` : null,
        ].filter(Boolean).join(" · ")}
      </small>
      {relevant && item.best_name && <small className="imported-tag">→ {item.best_name}</small>}
      {note && <small className="analyze-note">{note}</small>}
    </div>

    <div className="analyze-actions">
      {onSelect && relevant && item.torrent_url && (
        <button className="button primary" onClick={() => onSelect(item)}>{t("feed.select")}</button>
      )}
      {onAccept && item.classification === "REVIEW" && (
        <button className="button secondary" onClick={() => onAccept(item)}>{t("analyze.accept")}</button>
      )}
      {onAssign && assignCandidates && (item.classification === "REVIEW" || item.classification === "UNKNOWN") && (
        assigning ? (
          <select autoFocus defaultValue=""
                  onChange={(event) => { const id = event.target.value; setAssigning(false); if (id) onAssign(item, id); }}>
            <option value="">{t("attention.assignPlaceholder")}</option>
            {assignCandidates.map((anime) => (
              <option key={anime.id} value={anime.id}>{anime.display_name}</option>
            ))}
          </select>
        ) : (
          <button className="button secondary" onClick={() => setAssigning(true)}>{t("attention.assign")}</button>
        )
      )}
      {onIgnore && (
        <button className="button danger" onClick={() => onIgnore(item)}>{t("attention.dismiss")}</button>
      )}
      <button className="button secondary" onClick={() => setOpen((v) => !v)}>
        {open ? t("analyze.hideDetails") : t("analyze.showDetails")}
      </button>
    </div>

    {open && (
      <div className="analyze-details">
        <h4>{t("analyze.parsed")}</h4>
        <dl className="parsed-grid">
          <dt>{t("field.title")}</dt><dd>{item.raw_name}</dd>
          <dt>{t("profile.group")}</dt><dd>{item.subgroup ?? "—"}</dd>
          <dt>{t("profile.episode")}</dt><dd>{item.episode ?? "—"}</dd>
          <dt>{t("analyze.season")}</dt><dd>{item.season ?? "—"}</dd>
          <dt>{t("profile.resolution")}</dt><dd>{item.resolution ?? "—"}</dd>
          <dt>CRC</dt><dd>{item.crc32 ?? "—"}</dd>
        </dl>
        <h4>{t("analyze.candidates")}</h4>
        <ol className="candidate-list">
          {item.candidates.map((cand) => (
            <li key={`${item.raw_name}-${cand.anime_id}`}>
              <strong>{cand.name}</strong> — {cand.score} ({cand.confidence}%)
              <ul className="verdict-list">
                {cand.reasons.map((reason, index) => <li key={index}>{reason}</li>)}
              </ul>
            </li>
          ))}
        </ol>
      </div>
    )}
  </li>;
}

import { t } from "../i18n";
import { useLockedPageScroll } from "../useLockedPageScroll";
import type { AnalyzeItem } from "../api/client";
import { AnalyzeResults } from "./AnalyzeResults";

export function AnalyzeResultsModal({
  open,
  items,
  highlightAnimeId,
  onClose,
  onSelect,
  onAccept,
  note,
}: {
  open: boolean;
  items: AnalyzeItem[];
  highlightAnimeId?: string | null;
  onClose: () => void;
  onSelect?: (item: AnalyzeItem) => void;
  onAccept?: (item: AnalyzeItem) => void;
  note?: (item: AnalyzeItem) => string | null;
}) {
  useLockedPageScroll();
  if (!open) return null;
  return <div className="dialog-backdrop" role="presentation">
    <section className="dialog dialog-wide" role="dialog" aria-modal="true" aria-label={t("analyze.title")}>
      <header>
        <h2>{t("analyze.resultsTitle")}</h2>
        <button className="close" onClick={onClose} aria-label={t("add.cancel")}>×</button>
      </header>
      <div className="modal-scroll">
        <AnalyzeResults items={items} highlightAnimeId={highlightAnimeId ?? null} onSelect={onSelect} onAccept={onAccept} note={note} />
      </div>
    </section>
  </div>;
}

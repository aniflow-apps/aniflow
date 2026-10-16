/**
 * Summon one of every record, to look at it (build 126).
 *
 * Asked for: *"is it possible to create a debug test button in attention and
 * activity, if pressed it will display some fake warnings and errors, so I can
 * actually verify all of them and the look and the button solution etc.
 * Because else I always need to wait until a real time error appears."*
 *
 * One component on both pages, because it is one set of records: the samples
 * cover every attention kind *and* every activity kind in a single press, and
 * having two buttons that half-seed would be worse than one that does the lot.
 *
 * It says what it will do and what is in place, because a button that invents
 * warnings must not be ambiguous about which ones are real. Every sample
 * carries a marker, Remove takes exactly those, and a genuine record sharing a
 * kind with one is never touched.
 */
import { useCallback, useEffect, useState } from "react";
import { clearDebugSamples, countDebugSamples, seedDebugSamples } from "../api/client";
import { t } from "../i18n";

export function DebugSamples({ onChanged }: { onChanged: () => void }) {
  const [inPlace, setInPlace] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const count = useCallback(() => {
    void countDebugSamples()
      .then((counts) => setInPlace(counts.attention + counts.activity))
      /* The endpoint is refused when debug mode is off. Not an error worth
         showing - this whole panel is behind the same flag - so it simply
         reports nothing. */
      .catch(() => setInPlace(null));
  }, []);
  useEffect(count, [count]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setFailed(false);
    try {
      await action();
      count();
      onChanged();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="detail-panel debug-samples">
      <div className="dialog-head">
        <h2>
          <span className="chip">{t("debug.title")}</span> {t("samples.title")}
        </h2>
      </div>
      <p className="muted">{t("samples.hint")}</p>
      <div className="action-stack horizontal">
        <button className="button secondary" disabled={busy} onClick={() => void run(seedDebugSamples)}>
          {t("samples.seed")}
        </button>
        <button
          className="button secondary"
          disabled={busy || !inPlace}
          onClick={() => void run(clearDebugSamples)}
        >
          {t("samples.clear")}
        </button>
      </div>
      {inPlace ? (
        <p className="muted">{t("samples.inPlace").replace("{n}", String(inPlace))}</p>
      ) : null}
      {failed && <p className="error-message">{t("samples.failed")}</p>}
    </div>
  );
}

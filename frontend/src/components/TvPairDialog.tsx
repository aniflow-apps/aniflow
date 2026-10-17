import { useEffect, useState } from "react";
import { approveTvPairing, lookupTvPairing, type TvPairLookup } from "../api/client";
import { formatDateTime } from "../formatWhen";
import { t } from "../i18n";
import { useLockedPageScroll } from "../useLockedPageScroll";

/** What a person typed, as the server stores it: no dash or space, upper case. */
export function normalisePairCode(raw: string): string {
  return raw.replace(/[^a-z0-9]/gi, "").toUpperCase();
}

/**
 * Say yes to a television that is showing a code (build 187).
 *
 * Reached two ways: by scanning the QR code on the TV, which opens AniFlow
 * with the code already filled in, and by typing the code in Settings when the
 * phone cannot reach the address the QR code names. Either way the user sees
 * what the TV calls itself and the code it shows, so an approval is a
 * comparison with the screen in front of them, not a leap of faith.
 *
 * The token never comes here: it goes to the TV, which is polling for it. This
 * dialog only makes the yes.
 */
export function TvPairDialog({
  initialCode,
  onClose,
  onPaired,
}: {
  initialCode: string;
  onClose: () => void;
  onPaired: () => void;
}) {
  const [code, setCode] = useState(initialCode);
  const [found, setFound] = useState<TvPairLookup | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  // The older entries (made before TVs sent an id of their own) the person ticked as "this is the same TV".
  const [ticked, setTicked] = useState<number[]>([]);

  useLockedPageScroll();

  const clean = normalisePairCode(code);

  /* Looks the code up as soon as it is complete, so there is no "Find" button
     to press between typing eight characters and seeing the TV's name. */
  useEffect(() => {
    setFound(null);
    setError(null);
    if (clean.length !== 8) return;
    let current = true;
    lookupTvPairing(clean)
      .then((result) => {
        if (!current) return;
        setFound(result);
        // A TV that is recognised keeps the name its entry already has, not the generic one it reports.
        setName(result.suggested_name ?? result.name);
        setTicked([]);
      })
      .catch(() => {
        if (current) setError(t("settings.tv.pair.unknown"));
      });
    return () => {
      current = false;
    };
  }, [clean]);

  async function approve() {
    setBusy(true);
    try {
      await approveTvPairing(clean, name.trim(), ticked);
      setDone(true);
      onPaired();
    } catch {
      setError(t("settings.tv.pair.unknown"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true">
      <div className="dialog">
        <header className="dialog-head">
          <h2>{t("settings.tv.pair.title")}</h2>
          <button className="close" onClick={onClose} aria-label={t("scan.close")}>
            ×
          </button>
        </header>

        {done ? (
          <>
            <p>{t("settings.tv.pair.done")}</p>
            <div className="dialog-actions">
              <button className="button primary" onClick={onClose}>
                {t("settings.tv.done")}
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="muted">{t("settings.tv.pair.hint")}</p>
            <label className="field">
              <span>{t("settings.tv.pair.code")}</span>
              <input
                className="tv-pair-code"
                value={code}
                maxLength={12}
                autoFocus
                autoCapitalize="characters"
                autoComplete="off"
                spellCheck={false}
                placeholder="XXXX-XXXX"
                onChange={(event) => setCode(event.target.value)}
              />
            </label>
            {error && <p className="error-message">{error}</p>}
            {found && (
              <>
                <p>{t("settings.tv.pair.found")}</p>
                <label className="field">
                  <span>{t("settings.tv.name")}</span>
                  <input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} />
                </label>
                {/* This TV paired before and told AniFlow its own id: its entry is replaced when it collects the
                    new token, so there is still one entry for it (build 263). */}
                {found.replaces.filter((entry) => entry.same_device).map((entry) => (
                  <p className="tv-pair-note" key={entry.id}>
                    {t("settings.tv.pair.recognised")
                      .replace("{name}", entry.name)
                      .replace("{when}", entry.last_used_at ? formatDateTime(entry.last_used_at) : t("settings.tv.neverUsed").toLowerCase())}
                  </p>
                ))}
                {/* Older entries with the same name: probably the same TV, and only the person can say. */}
                {found.replaces.some((entry) => !entry.same_device) ? (
                  <div className="tv-pair-replace">
                    <p className="muted">{t("settings.tv.pair.sameName")}</p>
                    {found.replaces.filter((entry) => !entry.same_device).map((entry) => (
                      <label className="check" key={entry.id}>
                        <input
                          type="checkbox"
                          checked={ticked.includes(entry.id)}
                          onChange={(event) =>
                            setTicked((current) =>
                              event.target.checked ? [...current, entry.id] : current.filter((id) => id !== entry.id),
                            )
                          }
                        />
                        <span>
                          {t("settings.tv.pair.replaceOne")
                            .replace("{name}", entry.name)
                            .replace("{paired}", entry.created_at ? formatDateTime(entry.created_at) : "-")
                            .replace("{used}", entry.last_used_at ? formatDateTime(entry.last_used_at) : t("settings.tv.neverUsed").toLowerCase())}
                        </span>
                      </label>
                    ))}
                  </div>
                ) : null}
              </>
            )}
            <div className="dialog-actions">
              <button className="button secondary" onClick={onClose}>
                {t("settings.tv.pair.cancel")}
              </button>
              <button className="button primary" disabled={!found || busy} onClick={() => void approve()}>
                {t("settings.tv.pair.approve")}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

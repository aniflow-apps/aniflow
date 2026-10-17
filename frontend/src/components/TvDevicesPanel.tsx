import { useEffect, useState } from "react";
import {
  createTvDevice,
  listTvDevices,
  revokeTvDevice,
  type TvDevice,
  type TvDeviceCreated,
} from "../api/client";
import { formatDateTime } from "../formatWhen";
import { t } from "../i18n";
import { useToast } from "../toasts";
import { TvPairDialog } from "./TvPairDialog";

/**
 * The televisions that may read AniFlow (build 181).
 *
 * A token is made here, shown once, and only its fingerprint is kept - so the
 * one moment to copy it is the moment it appears, and the panel says so. A
 * revoked TV stays in the list, marked, rather than vanishing: "why did the
 * living-room TV stop" is answered by seeing it there.
 */
export function TvDevicesPanel() {
  const { show } = useToast();
  const [devices, setDevices] = useState<TvDevice[]>([]);
  const [name, setName] = useState("");
  const [created, setCreated] = useState<TvDeviceCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<number | null>(null);
  /* The QR code on a TV opens AniFlow as `/?pair=CODE`. Read once, then taken
     off the address so a reload does not reopen a pairing that is done. */
  const [pairing, setPairing] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get("pair"),
  );

  function reload() {
    void listTvDevices().then(setDevices).catch((error: Error) => show(error.message));
  }
  useEffect(reload, []);
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("pair")) {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.hash}`);
    }
  }, []);

  async function add() {
    setBusy(true);
    try {
      setCreated(await createTvDevice(name.trim() || t("settings.tv.namePlaceholder")));
      setName("");
      setCopied(false);
      reload();
    } catch (error) {
      show(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: number) {
    if (confirming !== id) {
      setConfirming(id);
      return;
    }
    setConfirming(null);
    try {
      await revokeTvDevice(id);
      reload();
    } catch (error) {
      show(error instanceof Error ? error.message : String(error));
    }
  }

  async function copy(token: string) {
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
    } catch {
      /* No clipboard here (plain HTTP, say): the token is selectable text. */
    }
  }

  return (
    <div className="detail-panel">
      <h2>{t("settings.tv.title")}</h2>
      <p className="muted">{t("settings.tv.hint")}</p>

      <div className="tv-pair-row">
        <button className="button primary" onClick={() => setPairing("")}>
          {t("settings.tv.pair.button")}
        </button>
        <small className="muted">{t("settings.tv.pair.buttonHint")}</small>
      </div>

      <div className="tv-add-row">
        <label className="field">
          <span>{t("settings.tv.name")}</span>
          <input
            value={name}
            maxLength={80}
            placeholder={t("settings.tv.namePlaceholder")}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <button className="button primary" disabled={busy} onClick={() => void add()}>
          {t("settings.tv.add")}
        </button>
      </div>

      {created && (
        <div className="tv-token-box">
          <strong>{`${t("settings.tv.tokenTitle")} ${created.name}`}</strong>
          <span className="tv-token">{created.token}</span>
          <small className="muted">{t("settings.tv.tokenHint")}</small>
          <small>
            {`${t("settings.tv.address")}: `}
            <span className="tv-token">{window.location.origin}</span>
          </small>
          <div className="profile-actions">
            <button className="button secondary" onClick={() => void copy(created.token)}>
              {copied ? t("settings.tv.copied") : t("settings.tv.copy")}
            </button>
            <button className="button secondary" onClick={() => setCreated(null)}>
              {t("settings.tv.done")}
            </button>
          </div>
        </div>
      )}

      {pairing !== null && (
        <TvPairDialog initialCode={pairing} onClose={() => setPairing(null)} onPaired={reload} />
      )}

      <div className="tv-devices">
        {devices.length === 0 && <p className="muted">{t("settings.tv.none")}</p>}
        {devices.map((device) => (
          <div key={device.id} className={`tv-device-row ${device.revoked_at ? "revoked" : ""}`}>
            <strong>{device.name}</strong>
            <small className="muted">
              {/* Paired as well as last used (build 263): two entries with one name are told apart by it. */}
              {`${t("settings.tv.paired")}: ${formatDateTime(device.created_at)} · `}
              {device.revoked_at
                ? t("settings.tv.revoked")
                : device.last_used_at
                  ? `${t("settings.tv.lastUsed")}: ${formatDateTime(device.last_used_at)}`
                  : t("settings.tv.neverUsed")}
            </small>
            {!device.revoked_at && (
              <button className="button secondary" onClick={() => void revoke(device.id)}>
                {confirming === device.id ? t("settings.tv.revokeConfirm") : t("settings.tv.revoke")}
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

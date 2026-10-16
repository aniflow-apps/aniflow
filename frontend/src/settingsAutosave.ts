/**
 * The rules of saving the Settings page as you change it (build 261).
 *
 * The page used to have a Save button at the bottom, and AniFlow TV has none. Now every change is saved on its own.
 * The rules below are what keep that from being worse than the button:
 *
 * - **When.** A select or a checkbox is a finished decision and is saved a moment later (the moment is only there
 *   so two quick clicks are one save). A text field is finished when it is left, so it is saved on blur, with a
 *   long idle wait as a fallback. A password or key is saved on blur only: a half-typed secret must never be
 *   sent just because the person paused.
 * - **What is saved while it is being edited.** A save that is still travelling must not overwrite what was typed
 *   after it left, or the last keystrokes vanish (`mergeAfterSave`).
 * - **The proxy.** A proxy that is switched on but has no host or port sends every outbound request nowhere, so
 *   it is only saved when it is complete; until then the stored proxy stays as it was.
 */

/** How a control was changed, which decides when the change is saved. */
export type EditKind = "quick" | "typed" | "secret";

/** Milliseconds to wait before saving; `null` means only when the control is left. */
export const WAIT_MS: Record<EditKind, number | null> = {
  quick: 250,
  typed: 5000,
  secret: null,
};

/** The kind of edit a control makes, from its tag and its `type`. */
export function kindOfControl(tag: string, type: string): EditKind {
  const name = tag.toLowerCase();
  if (name === "select") return "quick";
  if (name === "input") {
    const kind = type.toLowerCase();
    if (kind === "checkbox" || kind === "radio") return "quick";
    if (kind === "password") return "secret";
  }
  return "typed";
}

/**
 * The settings on screen after a save came back: the server's answer, except for what was edited while the save
 * was on its way. ``sent`` is what was sent, ``current`` what is on screen now.
 */
export function mergeAfterSave<T extends object>(stored: T, sent: T, current: T): T {
  const merged: Record<string, unknown> = { ...(stored as Record<string, unknown>) };
  const before = sent as Record<string, unknown>;
  for (const [key, value] of Object.entries(current)) {
    if (value !== before[key]) merged[key] = value;
  }
  return merged as T;
}

/** The proxy fields that are saved together, and only when they make sense together. */
export const PROXY_FIELDS = ["proxy_kind", "proxy_host", "proxy_port", "proxy_username"] as const;

interface ProxyDraft {
  proxy_kind: string;
  proxy_host: string;
  proxy_port: number | null;
}

/** Whether the proxy can be saved: off, or on with a host and a port. */
export function proxyIsComplete(draft: ProxyDraft): boolean {
  if (draft.proxy_kind === "none") return true;
  const port = draft.proxy_port;
  return draft.proxy_host.trim() !== "" && port !== null && Number.isInteger(port) && port >= 1 && port <= 65535;
}

/**
 * The payload with the proxy left as it is stored, for a proxy that is not complete yet. Everything else in the
 * payload still goes: a half-typed proxy must not hold a theme change back.
 */
export function holdProxy<T extends Record<string, unknown>>(payload: T, stored: object): T {
  const held: Record<string, unknown> = { ...payload };
  const kept = stored as Record<string, unknown>;
  for (const key of PROXY_FIELDS) held[key] = kept[key];
  delete held.proxy_password;
  return held as T;
}

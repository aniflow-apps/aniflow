import de from "./locales/de.json";
import en from "./locales/en.json";

type Language = "de" | "en";
type Messages = Record<string, string>;

const messages: Record<Language, Messages> = { de, en };

/**
 * The language to start in.
 *
 * Every read here is guarded (build 93). `localStorage` *throws* rather than
 * returning null in a browser with site data blocked, and neither it nor
 * `navigator` exists outside a browser at all - which is what stopped
 * anything importing this module from being unit tested. `reasons.ts` holds
 * the download status strings that were wrong in build 88 and could not be
 * tested for precisely that reason.
 */
function detect(): Language {
  try {
    const stored = localStorage.getItem("aniflow.language");
    if (stored === "de" || stored === "en") return stored;
  } catch {
    // Blocked, or not a browser. The navigator check below still applies.
  }
  try {
    return navigator.language.startsWith("de") ? "de" : "en";
  } catch {
    return "en";
  }
}

let currentLanguage: Language = detect();

export function getLanguage(): Language {
  return currentLanguage;
}

export function setLanguage(language: Language): void {
  currentLanguage = language;
  try {
    localStorage.setItem("aniflow.language", language);
  } catch {
    // The choice still applies to this page; it just will not be remembered.
  }
}

export function t(key: string): string {
  return messages[currentLanguage][key] ?? messages.en[key] ?? key;
}

/**
 * Whether anything is written under this key (build 123).
 *
 * `t` falls back to the key itself, which is right for a label - a missing
 * one shows up as the mistake it is - and wrong for an optional sentence,
 * where the fallback would print `health.why.subgroup` at the user. The
 * Debug window explains the entries worth explaining and stays quiet about
 * the rest, so it needs to ask rather than print and hope.
 */
export function has(key: string): boolean {
  return messages[currentLanguage][key] !== undefined || messages.en[key] !== undefined;
}

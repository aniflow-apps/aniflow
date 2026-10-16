/**
 * What the Settings page sends back when you press Save.
 *
 * Everything on screen goes, except the fields named here. That direction
 * matters: the page used to send a hand-kept list of fields to include, and
 * that list was wrong twice - "Automatic RSS scanning" could not be saved at
 * all, then the log level and the two title orders silently reverted on every
 * save, because adding a control to the page and adding it to the list are two
 * separate acts and the second is easy to forget.
 *
 * Now a new control saves itself, and a field that must NOT be sent has to be
 * named below - which is the failure that gets noticed immediately.
 */

/**
 * Fields the page shows but must never send back: derived from the deployment
 * (the storage roots, the config path), decided by it (the mode), or a report
 * about a secret rather than the secret itself (has_*).
 */
export const READ_ONLY = new Set([
  "mode",
  "import_root",
  "storage_paths",
  "anime_library_root",
  "download_root",
  "config_file",
  "effective_timezone",
  "timezone_unresolved",
  // Derived from the indexers plus the override, and reported back so the
  // page can show what is actually in force.
  "app_mode",
  "has_indexer",
  // Set in aniflow.yaml only (build 268); the interface has no switch for it and never sends it back.
  "debug_mode",
]);

/**
 * Every `has_*` field, by rule rather than by name.
 *
 * These report *whether* a secret is stored, never the secret. Listing them
 * individually meant `has_proxy_password` was being sent from the moment the
 * proxy was added - ignored by the server, but only by luck. A rule covers
 * the next one too.
 */
function reportsASecret(key: string): boolean {
  return key.startsWith("has_");
}

/** The settings object reduced to the fields that are actually editable. */
export function savableSettings(settings: object): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(settings).filter(([key]) => !READ_ONLY.has(key) && !reportsASecret(key)),
  );
}

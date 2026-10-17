import { useState } from "react";
import { requestResetCode, resetPassword, signIn, type AuthState } from "../api/client";
import { t } from "../i18n";

/**
 * The one screen you see before AniFlow lets you in.
 *
 * Three states, not three pages: creating the account, logging in, and
 * resetting the password all ask for a couple of fields and one button, and
 * routing between them would only add ways to end up somewhere with no way
 * back. Which one you get is decided by the server - `needs_setup` means there
 * is no account, and no amount of clicking should offer a login form for an
 * account nobody has created.
 *
 * The one-time code is deliberately not emailed, texted or shown on screen:
 * it goes to the container's log, so the person who can read the deployment is
 * the person who can get in. That is the whole authentication model, and the
 * page says so rather than leaving it to be discovered.
 */
export function LoginPage({
  state,
  onAuthenticated,
}: {
  state: AuthState;
  onAuthenticated: (next: AuthState) => void;
}) {
  const [mode, setMode] = useState<"login" | "reset">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  // Typed twice when it is being *chosen*, never when it is being entered: a
  // password you cannot see and cannot recover without reading a log is worth
  // confirming, and a login already tells you immediately if you got it wrong.
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const setup = state.needs_setup;
  const resetting = !setup && mode === "reset";

  async function submit() {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      // No account is created here: a fresh install is shown the setup wizard instead (App.tsx), which starts
      // with the notice that has to be accepted first.
      if (resetting) {
        onAuthenticated(await resetPassword(password, token.trim()));
      } else {
        onAuthenticated(await signIn(username.trim(), password));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  /** Ask the server to write a fresh code to the log. */
  async function askForCode() {
    setBusy(true);
    setError(null);
    try {
      await requestResetCode();
      setNote(t("auth.codeSent"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const choosing = setup || resetting;
  const mismatch = choosing && confirm.length > 0 && confirm !== password;
  const ready = setup
    ? username.trim().length >= 3 && password.length >= 8 && confirm === password && token.trim().length > 0
    : resetting
      ? password.length >= 8 && confirm === password && token.trim().length > 0
      : username.trim().length > 0 && password.length > 0;

  return (
    <div className="login-screen">
      <section className="login-card">
        <div className="login-brand">
          <img src="/logo-mark.png" alt={t("app.name")} width={44} height={44} />
          <div>
            <strong>{t("app.name")}</strong>
            <small className="muted">{t("app.tagline")}</small>
          </div>
        </div>

        <h1>{setup ? t("auth.setupTitle") : resetting ? t("auth.resetTitle") : t("auth.loginTitle")}</h1>
        <p className="muted">
          {setup ? t("auth.setupHint") : resetting ? t("auth.resetHint") : t("auth.loginHint")}
        </p>

        <form
          className="login-form"
          onSubmit={(event) => { event.preventDefault(); if (ready && !busy) void submit(); }}
        >
          {/* No username field while resetting: there is exactly one account,
              and asking which one to reset would be theatre. */}
          {!resetting && (
            <label className="field">
              <span>{t("auth.username")}</span>
              <input
                value={username}
                autoComplete="username"
                autoFocus
                onChange={(event) => setUsername(event.target.value)}
              />
            </label>
          )}
          <label className="field">
            <span>{setup || resetting ? t("auth.newPassword") : t("auth.password")}</span>
            <input
              type="password"
              value={password}
              autoComplete={setup || resetting ? "new-password" : "current-password"}
              onChange={(event) => setPassword(event.target.value)}
            />
            {choosing && <small className="muted">{t("auth.passwordRule")}</small>}
          </label>
          {choosing && (
            <label className="field">
              <span>{t("auth.confirmPassword")}</span>
              <input
                type="password"
                value={confirm}
                autoComplete="new-password"
                onChange={(event) => setConfirm(event.target.value)}
              />
              {mismatch && <small className="warning-inline">{t("auth.passwordMismatch")}</small>}
            </label>
          )}
          {choosing && (
            <label className="field">
              <span>{t("auth.code")}</span>
              <input
                value={token}
                autoComplete="one-time-code"
                placeholder={t("auth.codePlaceholder")}
                onChange={(event) => setToken(event.target.value)}
              />
              <small className="muted">{t("auth.codeHint")}</small>
            </label>
          )}

          {error && <p className="error-message">{error}</p>}
          {note && <p className="imported-tag">{note}</p>}

          <button className="button primary login-submit" type="submit" disabled={!ready || busy}>
            {busy
              ? t("settings.saving")
              : setup ? t("auth.createAccount") : resetting ? t("auth.setNewPassword") : t("auth.signIn")}
          </button>
        </form>

        {/* Not offered during setup: there is nothing to reset yet, and the
            code for creating the account is already in the log. */}
        {!setup && (
          <div className="login-links">
            {resetting ? (
              <>
                <button className="button secondary" disabled={busy} onClick={() => void askForCode()}>
                  {t("auth.sendCode")}
                </button>
                <button className="link-button" onClick={() => { setMode("login"); setError(null); setNote(null); }}>
                  {t("auth.backToLogin")}
                </button>
              </>
            ) : (
              <button className="link-button" onClick={() => { setMode("reset"); setError(null); }}>
                {t("auth.forgot")}
              </button>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

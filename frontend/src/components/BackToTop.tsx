/**
 * A round arrow that appears once a long page has been scrolled a good way
 * down (build 237) and takes you back to the top. Mounted once in the app
 * shell, so the watchlist, the calendar and every other long page have it.
 */
import { useEffect, useState } from "react";
import { t } from "../i18n";

/** How far down, in pixels, before the button shows. */
export const SHOW_AFTER = 600;

export function BackToTop() {
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const onScroll = () => setShown(window.scrollY > SHOW_AFTER);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  if (!shown) return null;
  const reduced = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  return (
    <button
      type="button"
      className="back-to-top"
      title={t("app.backToTop")}
      aria-label={t("app.backToTop")}
      onClick={() => window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" })}
    >
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M12 19V5M5 12l7-7 7 7" />
      </svg>
    </button>
  );
}

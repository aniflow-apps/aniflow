/**
 * The page behind a dialog stays where it is (build 124).
 *
 * Reported against the configuration dialog: *"When I press the Edit
 * Configuration button and the new window pops in, and I use mouse wheel it
 * does scroll the page behinde it."*
 *
 * The mechanism, because the obvious CSS answer does not do it: the backdrop
 * is `overflow: auto` with `overscroll-behavior: contain`, which stops scroll
 * *chaining* once the backdrop has reached its own end - but a backdrop whose
 * content fits has no scrolling of its own to do, and the wheel goes straight
 * through to the body. Nothing in CSS covers that case; the body has to be
 * told to hold still.
 *
 * Build 120 wrote exactly this effect inline in the setup dialog and every
 * other dialog went without, which is a shape that guarantees a second report
 * - there are nine dialogs. One hook, used by all of them.
 *
 * `active` is for the dialogs that are rendered conditionally inside a
 * component that stays mounted - the Debug windows, the two on the RSS page.
 * A hook cannot be called conditionally, so the condition is an argument.
 *
 * Restoring the *previous* value rather than clearing it is what makes
 * nesting work: the setup dialog can open the template dialog, and when the
 * inner one closes it puts back the "hidden" the outer one set. Clearing
 * would unlock the page while a dialog is still open.
 */
import { useEffect } from "react";

export function useLockedPageScroll(active: boolean = true): void {
  useEffect(() => {
    if (!active) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    /* Always undone, including when the dialog closes by unmounting: a body
       left locked makes the whole application unscrollable, which is a worse
       bug than the one this fixes. */
    return () => {
      document.body.style.overflow = previous;
    };
  }, [active]);
}

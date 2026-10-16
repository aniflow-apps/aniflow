/**
 * The clear "×" on every text field (build 210).
 *
 * One overlay for the whole app rather than a wrapper around each input: forty-odd fields in
 * forty-odd layouts, many styled by `.some-row input` rules that a wrapper would break. The rules
 * for which field gets one, and the clearing itself, live here so they can be tested without a page.
 */

/** Input types that hold text a person typed. Everything else (checkbox, range, file ...) has none. */
const TEXT_TYPES = new Set(["text", "search", "url", "email", "tel", "password"]);

/** Whether an input of this type is a text field. No type attribute means text. */
export function isTextType(type: string | null | undefined): boolean {
  return TEXT_TYPES.has((type ?? "text").trim().toLowerCase() || "text");
}

export interface ClearableFacts {
  tag: string;
  type: string | null;
  disabled: boolean;
  readOnly: boolean;
  /** It already draws its own × (the older `.input-clear` wrapper). */
  hasOwnClear: boolean;
  /** Opted out with `data-no-clear`. */
  optedOut: boolean;
  value: string;
  width: number;
}

/** Whether this field should show the ×: text, editable, not already cleared by something else, with text in it. */
export function wantsClear(facts: ClearableFacts): boolean {
  return (
    facts.tag === "INPUT"
    && isTextType(facts.type)
    && !facts.disabled
    && !facts.readOnly
    && !facts.hasOwnClear
    && !facts.optedOut
    && facts.value !== ""
    // Too narrow for a button inside it: a field that small is a digit box.
    && facts.width >= 60
  );
}

/**
 * Empty a field the way typing would, so React's own onChange runs and the state behind it follows.
 * Setting `.value` directly is swallowed by React's value tracking; the prototype's setter is not.
 */
export function clearField(input: HTMLInputElement): void {
  const setter = typeof HTMLInputElement === "undefined"
    ? undefined
    : Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  if (setter) setter.call(input, "");
  else input.value = "";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.focus();
}

export function factsOf(input: HTMLInputElement): ClearableFacts {
  return {
    tag: input.tagName,
    type: input.getAttribute("type"),
    disabled: input.disabled,
    readOnly: input.readOnly,
    hasOwnClear: input.closest(".input-clear") !== null,
    optedOut: input.hasAttribute("data-no-clear"),
    value: input.value,
    width: input.getBoundingClientRect().width,
  };
}

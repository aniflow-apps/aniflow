/**
 * Whether two values read from the API are the same, so a state is only replaced when something changed.
 *
 * The watchlist asks for its list every time it is opened. Replacing the state with an equal list
 * still draws every card again, which showed as a flicker (build 207). The answer is data from JSON,
 * so comparing it as JSON is exact.
 */
export function sameJson(a: unknown, b: unknown): boolean {
  return a === b || JSON.stringify(a) === JSON.stringify(b);
}

/** `setState((current) => keepIfSame(current, next))`: the old value when nothing differs. */
export function keepIfSame<T>(current: T, next: T): T {
  return sameJson(current, next) ? current : next;
}

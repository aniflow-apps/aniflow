/**
 * Which steps the install wizard has, given what has been answered (137).
 *
 * Its own module because it is the one rule in the wizard that can be got
 * wrong quietly: a step that should not exist is a page asking about a
 * program you are not running, and a step that goes missing is a question
 * nobody ever answers.
 *
 * The brief it comes from: *"I would like to have not so many pages, because
 * people tend to hate it if the wizard is too long ... of course stuff like
 * torrent client or rrs source should only appear if you select advance."*
 * So the shortest install is four screens and the longest is six.
 */
export type SetupStepId =
  | "terms"
  | "account"
  | "storage"
  | "scope"
  | "connections"
  | "libraries"
  | "client"
  | "feeds"
  | "sync"
  | "checks";

export type StorageChoice = "standalone" | "shokoserver";
export type ScopeChoice = "basic" | "advanced";

export interface SetupAnswers {
  /** What the library step has been answered with, if it has. */
  storage: StorageChoice | null;
  /** The mode already in force, used until the step is answered. */
  mode?: StorageChoice | null;
  /** What the scope step has been answered with, if it has. */
  scope: ScopeChoice | null;
  /** The first-run notice was already accepted (a resumed wizard), so it is not shown again. */
  termsAccepted?: boolean;
}

/**
 * The steps that exist, in order.
 *
 * Connections only in Shoko mode. The torrent client has its own step, and only when downloading is on
 * (build 250): it is where one of the three supported clients is picked, tested and its download folder
 * checked - before, a fresh install learned from the last screen that nothing would download. Feeds only
 * when downloading is on, because a feed is how a download gets noticed.
 */
export function stepsFor(answers: SetupAnswers): SetupStepId[] {
  const storage = answers.storage ?? answers.mode ?? null;
  // The notice comes before anything else, the account included: nothing is created until it is accepted.
  const steps: SetupStepId[] = answers.termsAccepted ? [] : ["terms"];
  steps.push("account", "storage", "scope");
  if (storage === "shokoserver") steps.push("connections");
  // Where the anime live (build 250): in Shoko mode right after Shoko is connected, so its import folders can be matched.
  steps.push("libraries");
  if (answers.scope === "advanced") steps.push("client", "feeds");
  steps.push("sync");
  // Last (build 232): the checklist, so a new install ends on what still needs doing - the torrent client, a
  // folder AniFlow cannot write to - instead of finding out from a log. Informational: it never blocks.
  steps.push("checks");
  return steps;
}

/**
 * The steps that offer a way past their required fields.
 *
 * Only where something *is* required. Feeds has a Skip in neither sense: a
 * feed is optional, so Next is the skip, and two buttons doing one thing on
 * one screen only makes people wonder what the difference is.
 */
export const SKIPPABLE: readonly SetupStepId[] = ["connections", "client"];

export function isSkippable(step: SetupStepId): boolean {
  return SKIPPABLE.includes(step);
}

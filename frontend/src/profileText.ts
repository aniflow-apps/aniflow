/** Just the part of a profile this needs, so a test needs no more either. */
export interface ProfileLike {
  subgroups?: string[] | null;
  resolution?: string | null;
  /** The name of the template the profile was created from, if any. */
  template_name?: string | null;
}

/**
 * What an anime's release profile actually asks for (build 91).
 *
 * The Configuration panel used to print `template_name` - the name of the
 * template the profile was created from. Nothing updates that when the
 * profile itself changes, and `ReleaseProfile` has no such column at all: the
 * server derives it from the template the profile still links to.
 *
 * So after a change of group the panel read "SubsPlease 720p" for a profile
 * that said Erai-raws, directly below a button correctly offering to change
 * the group *from* Erai-raws. Reported with the two screenshots side by side.
 *
 * The values win, because they are what AniFlow searches by. The template
 * name is the fallback for a profile that states nothing of its own, where it
 * is the only description there is.
 *
 * `fallback` is passed in rather than translated here, so this module stays
 * clear of the i18n layer - which reads localStorage as it loads, and
 * therefore cannot be imported by a plain unit test.
 */
export function profileSummary(
  profile: ProfileLike | null | undefined,
  fallback: string,
): string {
  if (!profile) return fallback;
  const parts = [
    (profile.subgroups ?? []).filter((group) => group.trim() !== "").join(", "),
    (profile.resolution ?? "").trim(),
  ].filter((part) => part !== "");
  if (parts.length > 0) return parts.join(" · ");
  return profile.template_name?.trim() || fallback;
}

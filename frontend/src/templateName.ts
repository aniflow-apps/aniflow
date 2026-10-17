/**
 * A name for a profile template that is being created from what a search just found (build 231).
 *
 * The setup step hands the template dialog the subgroup, resolution and media source it discovered, and the
 * name used to be left empty on purpose - "the user names their own template". Typing it every time is slower
 * than clearing a good guess, so the name is now built from the parts that are there: group, then resolution,
 * then source, each only when it says something. Anything empty or "any" is left out, and with nothing to
 * say the name stays empty. Only a starting point: the person can clear it and write their own.
 */
export interface NameParts {
  subgroups?: readonly string[];
  resolution?: string | null;
  media_source?: string | null;
}

export function suggestedTemplateName(parts: NameParts): string {
  const groups = (parts.subgroups ?? []).map((group) => group.trim()).filter(Boolean);
  const pieces = [groups.join(" + "), parts.resolution ?? "", parts.media_source ?? ""]
    .map((piece) => piece.trim())
    .filter((piece) => piece !== "" && piece.toLowerCase() !== "any");
  return pieces.join(" ");
}

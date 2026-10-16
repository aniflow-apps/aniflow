"""Deterministic matching of parsed releases against release profiles.

The matcher implements the non-negotiable business rules from ``AGENTS.md``:

- A profile choice is never silently changed; a release either satisfies it
  exactly or the mismatch is reported.
- Unknown release metadata is not automatically a mismatch unless the profile
  explicitly requires that field to be known (the ``require_*_known``
  switches) or the profile pins an explicit value for that field.
- A newer version (V2/V3) of something already owned never replaces it
  automatically; it requires user attention instead.

The module is pure: it touches neither the database nor the filesystem.
"""
import enum
from dataclasses import dataclass

from backend.app.services.release_parser import ParsedRelease, title_base_variants


class Verdict(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    FAIL_UNKNOWN = "FAIL_UNKNOWN"
    SKIPPED = "SKIPPED"
    #: The name does not say, and the caller chose not to hold that against it
    #: (``tolerate_unknown``). Counts as a match, and is reported as a warning
    #: so the person sees what was not checked.
    UNKNOWN_OK = "UNKNOWN_OK"


class VersionAction(str, enum.Enum):
    DOWNLOAD_NEW = "DOWNLOAD_NEW"
    SKIP_DUPLICATE = "SKIP_DUPLICATE"
    SKIP_SUPERSEDED = "SKIP_SUPERSEDED"
    ATTENTION_NEW_VERSION = "ATTENTION_NEW_VERSION"


#: Every field a release is checked against, in the order they are checked.
#: Declared rather than left implicit in the function below because the UI has
#: to be able to name each failure: the codes are built as
#: ``<field>_mismatch`` / ``<field>_unknown``, and a field added here without
#: the two matching strings shows the user a raw identifier. A test walks this
#: tuple against both locale files.
MATCH_FIELDS = ("title", "subgroup", "edition", "resolution", "media_source", "censor")

#: What can be wrong with a field. "mismatch" is the release saying something
#: else; "unknown" is the release not saying at all - different problems with
#: different fixes, so they are never collapsed into one message.
MATCH_FAILURES = ("mismatch", "unknown")

#: Failure codes that are not about any one field - the release matches the
#: profile exactly and is still not a match, for a reason evaluate_release
#: itself has no way to know (services/rss_scan.py:_batch_adds_nothing_new).
#: Named here rather than folded into MATCH_FIELDS x MATCH_FAILURES, which
#: would invent nonsense combinations like "title_already_owned".
WHOLE_RELEASE_FAILURES = ("already_owned",)


@dataclass(frozen=True)
class FieldVerdict:
    field: str
    verdict: Verdict
    expected: str | None = None
    actual: str | None = None


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    fields: tuple[FieldVerdict, ...]

    @property
    def failures(self) -> list[str]:
        """Machine-readable codes for every failing check, e.g. ``subgroup_mismatch``."""
        codes: list[str] = []
        for item in self.fields:
            if item.verdict in (Verdict.PASS, Verdict.SKIPPED, Verdict.UNKNOWN_OK):
                continue
            suffix = "unknown" if item.verdict is Verdict.FAIL_UNKNOWN else "mismatch"
            codes.append(f"{item.field}_{suffix}")
        return codes

    @property
    def warnings(self) -> list[str]:
        """Checks that could not be made because the name says nothing, e.g. ``resolution_unknown``.

        Empty unless the caller asked for ``tolerate_unknown``.
        """
        return [f"{item.field}_unknown" for item in self.fields if item.verdict is Verdict.UNKNOWN_OK]


@dataclass(frozen=True)
class ReleaseDecision:
    match: MatchResult
    version_action: VersionAction


def _title_matches(parsed_title: str, candidate_titles: list[str]) -> bool:
    """Deterministic title correspondence check (base forms + word prefixes)."""
    parsed_bases = {b for b in title_base_variants(parsed_title) if b}
    for candidate in candidate_titles:
        candidate_bases = title_base_variants(candidate)
        if parsed_bases & candidate_bases:
            return True
        # Word-prefix relation in either direction, both sides >= 5 chars.
        for parsed_base in parsed_bases:
            if len(parsed_base) < 5:
                continue
            for candidate_base in candidate_bases:
                if len(candidate_base) < 5:
                    continue
                if candidate_base.startswith(parsed_base + " ") or parsed_base.startswith(candidate_base + " "):
                    return True
    return False


def _verdict_equal(
    field_name: str,
    required: str | None,
    actual: str | None,
    require_known: bool,
) -> FieldVerdict:
    """Shared logic for pinned-value fields (resolution, media source).

    A pinned value must be matched exactly (case-insensitive); unknown actual
    values fail because a pin already demands the field be known. Without a
    pin, only ``require_known`` turns missing metadata into a mismatch.
    """
    if required is not None:
        if actual is None:
            return FieldVerdict(field_name, Verdict.FAIL_UNKNOWN, expected=required)
        verdict = Verdict.PASS if actual.casefold() == required.casefold() else Verdict.FAIL
        return FieldVerdict(field_name, verdict, expected=required, actual=actual)
    if require_known:
        if actual is None:
            return FieldVerdict(field_name, Verdict.FAIL_UNKNOWN, expected="known value")
        return FieldVerdict(field_name, Verdict.PASS, actual=actual)
    return FieldVerdict(field_name, Verdict.SKIPPED)


def unknown_is_fine(anime, profile) -> bool:
    """Whether an automatic download may take a release whose name leaves quality, source or censoring out (item 45).

    Yes once a release name has been picked for the anime: the title and the group already say which show and
    whose release this is, so a missing quality is not a reason to refuse it. A name that states something
    *different* still fails, and a profile that says a field must be known (``require_*_known``) is obeyed.
    """
    return bool(getattr(anime, "release_name", None)) and not (
        profile.require_resolution_known or profile.require_source_known or profile.require_censor_known
    )


def evaluate_release(
    parsed: ParsedRelease,
    *,
    subgroups: list[str] | tuple[str, ...],
    resolution: str | None,
    media_source: str | None,
    censor_preference: str,
    candidate_titles: list[str] | tuple[str, ...],
    edition: list[str] | tuple[str, ...] = (),
    edition_not: list[str] | tuple[str, ...] = (),
    require_title_match: bool = True,
    require_resolution_known: bool = False,
    require_source_known: bool = False,
    require_censor_known: bool = False,
    tolerate_unknown: bool = False,
) -> MatchResult:
    """Check one parsed release against a profile's rules, field by field.

    ``tolerate_unknown`` is for the places where a person looks at the result
    and chooses (the Choose release dialog): a name that does not state its
    quality, source or censoring is a warning there, not a refusal. Not many
    releases say all three, and what a name leaves out says nothing against
    it. A name that says something *different* still fails. Automatic
    downloads leave it off: nobody is looking, so an unverifiable file is not
    taken.
    """
    checks: list[FieldVerdict] = []

    # Anime identity. The title must correspond to one of the anime's known
    # titles/aliases - compared as season-stripped base forms so release
    # abbreviations like "Hell Mode S2" match "… Season 2". Fansubs also
    # shorten long titles ("Hell Mode" for "HELL MODE: The Hardcore …");
    # a word-prefix relation between base forms (min 5 chars per side)
    # covers that without opening the door to one-word coincidences.
    if parsed.title is None:
        checks.append(
            FieldVerdict(
                "title",
                Verdict.FAIL_UNKNOWN if require_title_match else Verdict.SKIPPED,
            )
        )
    else:
        matched = _title_matches(parsed.title, [t for t in candidate_titles if t])
        checks.append(FieldVerdict("title", Verdict.PASS if matched else Verdict.FAIL, actual=parsed.title))

    # Subgroup. An explicit allow-list demands a known group that is on it;
    # an empty list accepts every subgroup including unknown ones.
    #
    # The one exception is the empty *string*, which means "a release whose
    # name credits no group" - a real answer, and a different one from "any
    # group". Without it, picking the Unknown-group row asked for no group
    # constraint at all and accepted every copy of every episode (build 99).
    if any(name == "" for name in subgroups):
        checks.append(
            FieldVerdict(
                "subgroup",
                Verdict.PASS if parsed.subgroup is None else Verdict.FAIL,
                expected="(no group)",
                actual=parsed.subgroup,
            )
        )
    elif subgroups:
        if parsed.subgroup is None:
            checks.append(FieldVerdict("subgroup", Verdict.FAIL_UNKNOWN, expected=" | ".join(subgroups)))
        else:
            allowed = {entry.casefold() for entry in subgroups}
            checks.append(
                FieldVerdict(
                    "subgroup",
                    Verdict.PASS if parsed.subgroup.casefold() in allowed else Verdict.FAIL,
                    expected=" | ".join(subgroups),
                    actual=parsed.subgroup,
                )
            )
    else:
        checks.append(FieldVerdict("subgroup", Verdict.SKIPPED))

    # Which copy of the episode. A pinned edition demands every marker it
    # names; the release may carry more, because a name that says more about
    # itself than the rule asked is still the thing the rule asked for.
    #
    # Empty means any edition, which is what every profile written before
    # build 97 meant - so this check cannot change an existing profile's
    # behaviour.
    if edition or edition_not:
        wanted = {marker.strip().casefold() for marker in edition if marker and marker.strip()}
        refused = {
            marker.strip().casefold() for marker in edition_not if marker and marker.strip()
        }
        carried = {marker.casefold() for marker in (parsed.tags or ())}
        # Both halves. The copies of one episode are alternatives, so a row
        # that is not the dual-audio one has to refuse the dual-audio file -
        # which carries everything the plain row asks for and one thing more
        # (build 98). Tags in neither list say nothing either way, so a group
        # inventing a label cannot break a stored rule.
        failed = sorted(wanted - carried) + sorted(refused & carried)
        checks.append(
            FieldVerdict(
                "edition",
                Verdict.PASS if not failed else Verdict.FAIL,
                expected=" + ".join(
                    [*sorted(wanted), *(f"-{marker}" for marker in sorted(refused))]
                ),
                actual=" + ".join(sorted(carried)) or None,
            )
        )
    else:
        checks.append(FieldVerdict("edition", Verdict.SKIPPED))

    checks.append(_verdict_equal("resolution", resolution, parsed.resolution, require_resolution_known))
    checks.append(_verdict_equal("media_source", media_source, parsed.media_source, require_source_known))

    # Censorship. An explicit preference can only be honoured when the
    # release states where it stands, so unknown metadata fails whenever a
    # concrete preference is pinned. With ANY, require_censor_known decides.
    if censor_preference.upper() == "ANY":
        if parsed.censored is None:
            verdict = Verdict.FAIL_UNKNOWN if require_censor_known else Verdict.SKIPPED
            checks.append(FieldVerdict("censor", verdict))
        else:
            checks.append(FieldVerdict("censor", Verdict.PASS, actual="CENSORED" if parsed.censored else "UNCENSORED"))
    else:
        if parsed.censored is None:
            checks.append(FieldVerdict("censor", Verdict.FAIL_UNKNOWN, expected=censor_preference.upper()))
        else:
            actual = "CENSORED" if parsed.censored else "UNCENSORED"
            checks.append(
                FieldVerdict(
                    "censor",
                    Verdict.PASS if actual == censor_preference.upper() else Verdict.FAIL,
                    expected=censor_preference.upper(),
                    actual=actual,
                )
            )

    if tolerate_unknown:
        checks = [
            FieldVerdict(item.field, Verdict.UNKNOWN_OK, item.expected, item.actual)
            if item.verdict is Verdict.FAIL_UNKNOWN
            and item.field in ("resolution", "media_source", "censor")
            else item
            for item in checks
        ]

    matched = all(
        item.verdict in (Verdict.PASS, Verdict.SKIPPED, Verdict.UNKNOWN_OK) for item in checks
    )
    return MatchResult(matched=matched, fields=tuple(checks))


def decide_version(release_version: int, local_version: int | None) -> VersionAction:
    """Decide how a release version relates to an already-owned version.

    Business rule: versions V2/V3 arriving after a local V1 require attention
    and user action; they are never downloaded automatically.
    """
    if local_version is None:
        return VersionAction.DOWNLOAD_NEW
    if release_version > local_version:
        return VersionAction.ATTENTION_NEW_VERSION
    if release_version == local_version:
        return VersionAction.SKIP_DUPLICATE
    return VersionAction.SKIP_SUPERSEDED

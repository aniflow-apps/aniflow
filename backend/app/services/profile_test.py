"""Does this subgroup profile match anything real? (build 95)

A profile is four or five rules - these groups, this quality, maybe a source -
and every one of them is a string somebody typed. "Erai-Raws" with a capital R
is fine; "Erai raws" is not the group's name and matches nothing, for ever,
silently. The only symptom is an anime that never downloads, which looks like
a dozen other problems.

So a profile can be asked. The question is put to the **feeds**, not to a
tracker search, for three reasons: the feeds are what a profile is applied to
in normal operation, so this tests the real path; a feed is one request that
returns the last few hundred releases, which is a broad enough sample to say
whether a group is publishing at all; and it costs nothing that a search would
cost - no rate limit to respect, no pages to page through.

What comes back is deliberately per-rule:

* how many items the feeds carried, so an empty answer can be told apart from
  an unreachable feed;
* per named subgroup, how many items it published - the number that catches a
  misspelled group, because zero against a feed holding four hundred items
  means the name is wrong, not that the group is quiet;
* how many items the profile accepts once every rule is applied, which is the
  number that catches a quality the group does not publish.

Testing every profile at once reads the feeds **once** and evaluates all of
them against the same items. Ten profiles must not be ten feed fetches.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import for_component
from backend.app.integrations.rss.base import FeedError, RssItem, fetch_feed
from backend.app.models.profile_template import ProfileTemplate
from backend.app.models.rss import FeedSource
from backend.app.services.matching import evaluate_release
from backend.app.services.release_parser import ParsedRelease, parse_release_name

logger = for_component("Profiles")


@dataclass
class FeedRead:
    """One feed, and what reading it produced."""

    name: str
    url: str
    items: int = 0
    error: str | None = None


@dataclass
class GroupSighting:
    """One subgroup the profile names, and whether the feeds have it."""

    name: str
    items: int = 0
    #: The qualities it was seen publishing, best-known order not imposed -
    #: this is a report, and "1080p, 720p" is what the feed said.
    resolutions: list[str] = field(default_factory=list)
    example: str | None = None


@dataclass
class ProfileTestResult:
    """What one profile would take out of everything the feeds are carrying."""

    template_id: str
    name: str
    #: How many feed items the profile accepts in full.
    accepted: int = 0
    #: How many items were looked at.
    considered: int = 0
    #: One line per subgroup the profile names.
    groups: list[GroupSighting] = field(default_factory=list)
    #: A few accepted names, so the answer can be recognised rather than
    #: trusted.
    examples: list[str] = field(default_factory=list)
    #: Which rule threw out the most, and how many - the profile's own words
    #: for it. Empty when it accepted everything it looked at.
    top_failures: list[tuple[str, int]] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """One word for the row, decided here so both surfaces agree.

        * ``ok`` - the profile accepts releases that exist right now.
        * ``no-group`` - a group it names published nothing at all, which is
          what a typo looks like.
        * ``no-match`` - the groups are publishing and the other rules take
          everything out. A quality the group does not offer, usually.
        """
        if self.accepted:
            return "ok"
        if self.groups and all(sighting.items == 0 for sighting in self.groups):
            return "no-group"
        return "no-match"


@dataclass
class ProfileTestReport:
    feeds: list[FeedRead] = field(default_factory=list)
    results: list[ProfileTestResult] = field(default_factory=list)

    @property
    def items(self) -> int:
        return sum(feed.items for feed in self.feeds)

    @property
    def reached_any_feed(self) -> bool:
        return any(feed.error is None for feed in self.feeds)


def _enabled_feeds(session: Session) -> list[FeedSource]:
    return list(
        session.scalars(select(FeedSource).where(FeedSource.enabled.is_(True)).order_by(FeedSource.name))
    )


def _read_feeds(session: Session) -> tuple[list[FeedRead], list[ParsedRelease]]:
    """Every enabled feed, once, parsed.

    A feed that will not load is recorded rather than raised: one broken feed
    out of three still says something useful about a profile.
    """
    reads: list[FeedRead] = []
    parsed: list[ParsedRelease] = []
    for source in _enabled_feeds(session):
        read = FeedRead(name=source.name, url=source.url)
        reads.append(read)
        try:
            items: list[RssItem] = fetch_feed(source.url)
        except FeedError as exc:
            read.error = str(exc)
            logger.info("profile test could not read %s: %s", source.name, exc)
            continue
        for item in items:
            read.items += 1
            try:
                parsed.append(parse_release_name(item.title))
            except ValueError:
                # A name the parser cannot read belongs to no group and no
                # quality, so it cannot say anything about a profile. Counted
                # in `items` and otherwise ignored.
                continue
    return reads, parsed


def _normalized(name: str | None) -> str:
    return (name or "").strip().casefold()


def _test_one(template: ProfileTemplate, releases: list[ParsedRelease]) -> ProfileTestResult:
    """One profile against the items the feeds are carrying."""
    result = ProfileTestResult(
        template_id=template.id, name=template.name, considered=len(releases)
    )
    wanted = [name for name in (template.subgroups or []) if name and name.strip()]
    sightings = {_normalized(name): GroupSighting(name=name) for name in wanted}
    failures: dict[str, int] = {}

    for parsed in releases:
        sighting = sightings.get(_normalized(parsed.subgroup))
        if sighting is not None:
            sighting.items += 1
            if parsed.resolution and parsed.resolution not in sighting.resolutions:
                sighting.resolutions.append(parsed.resolution)
            if sighting.example is None:
                sighting.example = parsed.title
        verdict = evaluate_release(
            parsed,
            subgroups=wanted,
            resolution=template.resolution,
            media_source=template.media_source,
            edition=template.edition or [],
            edition_not=template.edition_not or [],
            censor_preference=(
                template.censor_preference.value
                if hasattr(template.censor_preference, "value")
                else "ANY"
            ),
            # Its own title, which is the same as not checking identity -
            # and identity is not what a profile decides. Every feed item is
            # some other show; which show a release belongs to is the anime's
            # release name, checked where it belongs (services/rss_scan.py).
            #
            # Passing an empty list would not skip the check: a title that
            # contradicts every known name fails even with
            # require_title_match off, which is deliberate in
            # services/matching.py and would make every profile look broken
            # here.
            candidate_titles=[parsed.title or ""],
            require_title_match=False,
            require_resolution_known=template.require_resolution_known,
            require_source_known=template.require_source_known,
            require_censor_known=template.require_censor_known,
        )
        if verdict.matched:
            result.accepted += 1
            if len(result.examples) < 5 and parsed.title:
                result.examples.append(parsed.title)
            continue
        for reason in verdict.failures or ():
            failures[reason] = failures.get(reason, 0) + 1

    result.groups = [sightings[key] for key in sightings]
    result.top_failures = sorted(failures.items(), key=lambda pair: (-pair[1], pair[0]))[:3]
    return result


def test_profiles(session: Session, *, template_id: str | None = None) -> ProfileTestReport:
    """Test one profile, or every one of them against a single feed read."""
    statement = select(ProfileTemplate).order_by(ProfileTemplate.name)
    if template_id is not None:
        statement = statement.where(ProfileTemplate.id == template_id)
    templates = list(session.scalars(statement))

    report = ProfileTestReport()
    if not templates:
        return report
    report.feeds, releases = _read_feeds(session)
    report.results = [_test_one(template, releases) for template in templates]
    return report


__all__ = [
    "FeedRead",
    "GroupSighting",
    "ProfileTestReport",
    "ProfileTestResult",
    "test_profiles",
]

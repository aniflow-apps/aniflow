"""Core of the matching engine: scoring, ranking, classification.

Design notes
------------
- The raw score is a transparent sum of weighted signals. It is NOT a
  calibrated probability; the UI label "Confidence" is a heuristic.
- An authoritative identifier (known CRC) short-circuits to EXACT.
- A margin between the top two candidates is required for automatic
  classes; otherwise the result is downgraded to REVIEW.
- All weights/thresholds/margins live in one place and are easy to tune.
"""
import re
from dataclasses import dataclass, field

from backend.app.services.release_parser import (
    ParsedRelease,
    title_base_variants,
)

# ---------------------------------------------------------------------------
# Tunable constants (single source of truth)
# ---------------------------------------------------------------------------

WEIGHTS = {
    "crc_exact":            100,
    "alias_exact":           90,
    "title_exact":           80,
    "romaji_strong":         75,
    "japanese_strong":       75,
    "token_overlap_strong":  60,
    "partial_title":         45,
    "partial_title_season":  25,   # shortened title + explicit season agreement
    "season_match":          25,
    "episode_match":         15,
    "quality_match":         10,
    "group_match":            5,
}

NEGATIVE_WEIGHTS = {
    "wrong_season": -80,
    "conflicting_title": -50,
}

THRESHOLDS = {
    "HIGH": 90,
    "REVIEW": 70,
}

MARGIN = 8          # required lead of the best candidate over the runner-up
MIN_PREFIX_LEN = 5  # word-prefix title rule needs at least this many chars

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_STOP_TOKENS = {"the", "a", "an", "no", "wa", "ga", "ni", "de", "e", "wo", "o", "season"}


@dataclass
class AnimeCandidateInfo:
    """Everything the engine may know about one watchlist anime."""

    anime_id: str
    display_name: str
    aliases: list[str] = field(default_factory=list)
    title_english: str | None = None
    title_romaji: str | None = None
    title_native: str | None = None
    # Episode numbers already known for this anime. Integers as they come
    # from the database; compared by value, so "05" and 5 are the same
    # episode.
    episode_numbers: set[int] | set[str] | None = None
    pinned_resolution: str | None = None      # from release profile, if any
    pinned_subgroups: list[str] = field(default_factory=list)
    known_crcs: set[str] = field(default_factory=set)

    def all_titles(self) -> list[str]:
        return [
            t for t in [
                self.display_name,
                self.title_english or "",
                self.title_romaji or "",
                self.title_native or "",
                *self.aliases,
            ] if t
        ]


@dataclass
class CandidateScore:
    anime_id: str
    name: str
    score: int
    confidence: int
    classification: str        # EXACT / HIGH / REVIEW / UNKNOWN (after ranking)
    reasons: list[str] = field(default_factory=list)


def _tokens(text: str) -> set[str]:
    lowered = text.casefold()
    return {t for t in _TOKEN_SPLIT.split(lowered) if len(t) >= 3 and t not in _STOP_TOKENS}


def _bases(text: str) -> set[str]:
    return {b for b in title_base_variants(text) if b}


def _prefix_related(a: str, b: str) -> bool:
    if len(a) < MIN_PREFIX_LEN or len(b) < MIN_PREFIX_LEN:
        return False
    return b.startswith(a + " ") or a.startswith(b + " ")


def build_prefilter(candidates: list[AnimeCandidateInfo]) -> dict[str, set[str]]:
    """Token -> anime-id index used to skip obviously unrelated candidates."""
    index: dict[str, set[str]] = {}
    for candidate in candidates:
        tokens: set[str] = set()
        for title in candidate.all_titles():
            tokens |= _tokens(title)
        for token in tokens:
            index.setdefault(token, set()).add(candidate.anime_id)
    return index


def _plausible(parsed: ParsedRelease, candidate: AnimeCandidateInfo, index: dict[str, set[str]], known_crcs: dict[str, set[str]]) -> bool:
    if parsed.crc32 and parsed.crc32 in known_crcs.get("crc", set()) | candidate.known_crcs:
        return True
    title_tokens = _tokens(parsed.title or "")
    for token in title_tokens:
        if candidate.anime_id in index.get(token, set()):
            return True
    # Short titles (e.g. romaji abbreviations) may tokenize away; allow when
    # any name shares a base form.
    bases = title_base_variants(parsed.title or "")
    for title in candidate.all_titles():
        if bases & _bases(title):
            return True
    return False


def analyze_release(
    parsed: ParsedRelease,
    candidates: list[AnimeCandidateInfo],
    *,
    prefilter: dict[str, set[str]] | None = None,
    known_crcs: dict[str, set[str]] | None = None,
) -> list[CandidateScore]:
    """Rank all plausible candidates for one parsed release."""
    crc_map = known_crcs or {}

    scored: list[tuple[CandidateScore, bool]] = []  # (score, crc_hit)
    for candidate in candidates:
        if prefilter is not None and not _plausible(parsed, candidate, prefilter, crc_map):
            continue
        score, reasons, crc_hit = _score_candidate(parsed, candidate)
        confidence = max(0, min(int(score), 100))
        scored.append(
            (
                CandidateScore(
                    anime_id=candidate.anime_id,
                    name=candidate.display_name,
                    score=int(score),
                    confidence=confidence,
                    classification="PENDING",
                    reasons=reasons,
                ),
                crc_hit,
            )
        )

    scored.sort(key=lambda pair: pair[0].score, reverse=True)
    ranked = [item for item, _ in scored]

    for position, candidate in enumerate(ranked):
        second = ranked[1].score if position == 0 and len(ranked) > 1 else None
        candidate.classification = classify(candidate.score, second, crc_hit=scored[position][1])

    return ranked


def classify(score: int, second_score: int | None, *, crc_hit: bool = False) -> str:
    """Map a raw score + candidate context onto the confidence classes."""
    if crc_hit:
        return "EXACT"
    if score >= THRESHOLDS["HIGH"]:
        if second_score is None or (second_score if second_score >= 0 else 0) <= score - MARGIN:
            return "HIGH"
        return "REVIEW"  # top candidates too close together
    if score >= THRESHOLDS["REVIEW"]:
        return "REVIEW"
    return "UNKNOWN"


def _episode_key(value: int | str) -> str:
    """One episode label, comparable across the forms it arrives in.

    A release says "05" or "11"; the database stores 5 and 11. Stripping zeros
    off the text used to be the trick, which broke twice: it called a string
    method on an integer (a 500 from the whole analyze run), and it turned
    episode "0" into the empty string, so a prologue matched anything.
    """
    text = str(value).strip()
    return str(int(text)) if text.isdigit() else text.casefold()


def _score_candidate(parsed: ParsedRelease, candidate: AnimeCandidateInfo) -> tuple[int, list[str], bool]:
    score = 0
    reasons: list[str] = []
    crc_hit = False

    # --- CRC -------------------------------------------------------------
    if parsed.crc32 and parsed.crc32 in candidate.known_crcs:
        score += WEIGHTS["crc_exact"]
        crc_hit = True
        reasons.append(f"\u2713 CRC match: {parsed.crc32}")
    elif parsed.crc32:
        reasons.append("\u26a0 No CRC mapping")

    # --- Title family ------------------------------------------------------
    parsed_bases = {b for b in title_base_variants(parsed.title or "") if b}
    candidate_all = candidate.all_titles()

    alias_hit = any(parsed_bases & _bases(alias) for alias in candidate.aliases)
    display_bases = _bases(candidate.display_name)
    english_hit = parsed_bases & _bases(candidate.title_english or "")
    romaji_hit = parsed_bases & _bases(candidate.title_romaji or "")
    native_hit = parsed_bases & _bases(candidate.title_native or "")

    title_reason: str | None = None
    title_tier = "weak"
    if parsed_bases & display_bases:
        score += WEIGHTS["title_exact"]
        title_reason = f"\u2713 Title match: {candidate.display_name}"
        title_tier = "strong"
    elif alias_hit:
        score += WEIGHTS["alias_exact"]
        matched_alias = next(a for a in candidate.aliases if parsed_bases & _bases(a))
        title_reason = f"\u2713 Alias match: {matched_alias}"
        title_tier = "strong"
    elif english_hit:
        score += WEIGHTS["romaji_strong"]
        title_reason = "\u2713 English title match"
        title_tier = "strong"
    elif romaji_hit:
        score += WEIGHTS["romaji_strong"]
        title_reason = "\u2713 Romaji title match"
        title_tier = "strong"
    elif native_hit:
        score += WEIGHTS["japanese_strong"]
        title_reason = "\u2713 Japanese title match"
        title_tier = "strong"
    else:
        # Token overlap / partial prefix against ANY name.
        parsed_tokens = _tokens(parsed.title or "")
        best_jaccard = 0.0
        best_name = ""
        prefix_hit = False
        for title in candidate_all:
            tokens = _tokens(title)
            union = parsed_tokens | tokens
            jaccard = len(parsed_tokens & tokens) / len(union) if union else 0.0
            if jaccard > best_jaccard:
                best_jaccard, best_name = jaccard, title
            for pb in parsed_bases:
                for cb in _bases(title):
                    if _prefix_related(pb, cb):
                        prefix_hit = True
        if best_jaccard >= 0.6:
            score += WEIGHTS["token_overlap_strong"]
            title_reason = f"\u2713 Strong token match: {best_name}"
            title_tier = "strong"
        elif prefix_hit or best_jaccard >= 0.4:
            score += WEIGHTS["partial_title"]
            title_reason = "\u2713 Partial title match"
            title_tier = "partial"
        else:
            score += NEGATIVE_WEIGHTS["conflicting_title"]
            title_reason = "\u2715 Weak title similarity"

    if title_reason:
        reasons.append(title_reason)

    # --- Season ------------------------------------------------------------
    season_from_candidate = _extract_candidate_season(candidate_all)
    season_agreed = False
    if parsed.season is not None and season_from_candidate is not None:
        if parsed.season == season_from_candidate:
            score += WEIGHTS["season_match"]
            reasons.append(f"\u2713 Season match: {parsed.season}")
            season_agreed = True
        else:
            score += NEGATIVE_WEIGHTS["wrong_season"]
            reasons.append(f"\u2715 Wrong season: release S{parsed.season}, anime S{season_from_candidate}")

    # A shortened/partial title that is corroborated by an explicit season
    # agreement counts as a strong signal (e.g. fansub "Hell Mode S2" for
    # the long "... 2nd Season" name).
    if season_agreed and title_tier == "partial":
        score += WEIGHTS["partial_title_season"]
        reasons.append("\u2713 Shortened title confirmed by season")

    # --- Episode -----------------------------------------------------------
    if parsed.episode and candidate.episode_numbers:
        known = {_episode_key(number) for number in candidate.episode_numbers}
        if _episode_key(parsed.episode) in known:
            score += WEIGHTS["episode_match"]
            reasons.append(f"\u2713 Episode match: {parsed.episode}")

    # --- Quality / group -----------------------------------------------------
    if parsed.resolution and candidate.pinned_resolution:
        if parsed.resolution.casefold() == candidate.pinned_resolution.casefold():
            score += WEIGHTS["quality_match"]
            reasons.append(f"\u2713 Quality match: {parsed.resolution}")
    if parsed.subgroup and candidate.pinned_subgroups:
        if parsed.subgroup.casefold() in {g.casefold() for g in candidate.pinned_subgroups}:
            score += WEIGHTS["group_match"]
            reasons.append(f"\u2713 Group match: {parsed.subgroup}")

    return int(score), reasons, crc_hit




def _extract_candidate_season(titles: list[str]) -> int | None:
    """Season number spelled out in any of the anime's own names, if any."""
    from backend.app.services.release_parser import _SEASON_PATTERNS

    for title in titles:
        for pattern, mapping in _SEASON_PATTERNS:
            match = pattern.search(title)
            if match:
                raw = match.group(1)
                return mapping[raw.lower()] if mapping else int(raw)
    return None


__all__ = [
    "AnimeCandidateInfo",
    "CandidateScore",
    "MARGIN",
    "THRESHOLDS",
    "WEIGHTS",
    "analyze_release",
    "build_prefilter",
    "classify",
]

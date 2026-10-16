"""Deterministic RSS matching engine.

Transparent weighted scoring over parsed release information - no fuzzy
guessing, no AI. Every point comes with a human-readable explanation, and
uncertain results are classified for manual review instead of being trusted.
"""
from .engine import (
    AnimeCandidateInfo,
    CandidateScore,
    THRESHOLDS,
    WEIGHTS,
    analyze_release,
    build_prefilter,
    classify,
)

__all__ = [
    "AnimeCandidateInfo",
    "CandidateScore",
    "THRESHOLDS",
    "WEIGHTS",
    "analyze_release",
    "build_prefilter",
    "classify",
]

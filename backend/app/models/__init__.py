"""Persistent domain models."""

from backend.app.models.activity import Activity, Attention, AttentionKind, AttentionStatus
from backend.app.models.analysis import AliasSource, AnalysisRun, AnimeAlias
from backend.app.models.anime import (
    DOWNLOADABLE_STATUSES,
    IN_FLIGHT_STATUSES,
    Anime,
    AnimeLifecycle,
    Episode,
    EpisodeStatus,
    EpisodeType,
)
from backend.app.models.cleared_torrent import ClearedTorrent
from backend.app.models.download import DownloadStatus, TorrentDownload
from backend.app.models.episode_release import EpisodeRelease
from backend.app.models.indexer import Indexer
from backend.app.models.library import Library
from backend.app.models.profile_template import ProfileTemplate
from backend.app.models.progress_event import EpisodeProgressEvent
from backend.app.models.release_profile import CensorPreference, ReleaseProfile
from backend.app.models.rss import FeedSource, Release, ReleaseDecision
from backend.app.models.schedule_state import ScheduleTaskState
from backend.app.models.search_result import SearchDecision, SearchResult
from backend.app.models.setting import Setting
from backend.app.models.superseded import SupersededFile
from backend.app.models.tv_token import TvToken

__all__ = [
    "DOWNLOADABLE_STATUSES",
    "IN_FLIGHT_STATUSES",
    "Activity",
    "AliasSource",
    "AnalysisRun",
    "Anime",
    "AnimeAlias",
    "AnimeLifecycle",
    "Attention",
    "AttentionKind",
    "AttentionStatus",
    "CensorPreference",
    "DownloadStatus",
    "Episode",
    "EpisodeProgressEvent",
    "EpisodeRelease",
    "EpisodeStatus",
    "EpisodeType",
    "FeedSource",
    "Indexer",
    "Library",
    "ProfileTemplate",
    "Release",
    "ReleaseDecision",
    "ReleaseProfile",
    "ScheduleTaskState",
    "SearchDecision",
    "SearchResult",
    "Setting",
    "SupersededFile",
    "ClearedTorrent",
    "TorrentDownload",
    "TvToken",
]

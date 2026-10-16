"""What is actually inside a video file (build 87).

Jellyfin will play some things directly and has to transcode others, and the
difference is invisible until you are sitting in front of it wondering why the
fan is loud. AniFlow already knows every file it imported, so it can answer
that question before Jellyfin has to - but only if it can read what is in
them.

Two ways, and both are here on purpose:

**ffprobe**, when the image has it. It is the standard tool, it understands
every container and codec worth naming, and its JSON output needs no guessing.
Used whenever it is on the PATH.

**The container's own header**, when it is not. Anime is overwhelmingly
Matroska, and everything this needs - codec, H.264 profile and level, bit
depth, resolution, frame rate, the audio and subtitle formats - sits in the
first few kilobytes of an MKV in a structure simple enough to walk directly.
That keeps the feature working on a machine with no ffmpeg, and it reads a few
kilobytes rather than opening the whole file.

Neither is guessing. When a file is in a container this cannot read and
ffprobe is not installed, the answer is "unknown" rather than an assumption -
a compatibility report that invents a codec is worse than no report.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from backend.app.core.logging import for_component

logger = for_component("Media")

#: How long ffprobe gets. It reads headers, so this is generous; a file on a
#: network share that has gone away should fail rather than hang the pass.
PROBE_TIMEOUT_SECONDS = 30

#: How much of an MKV to read when parsing it directly. The Tracks element is
#: near the start by design - a player needs it before it can play anything -
#: and four megabytes covers even files with a large attachment first.
MKV_HEAD_BYTES = 4 * 1024 * 1024


@dataclass
class Track:
    """One stream in the file, reduced to what compatibility depends on."""

    #: video | audio | subtitle
    kind: str
    #: The codec as the container names it, lowercased: h264, hevc, av1, aac,
    #: dts, truehd, pgs, subrip...
    codec: str = ""
    #: H.264/HEVC profile as a word ("high", "high 10", "main"), when known.
    profile: str | None = None
    #: The level as its raw number - 51 for 5.1, 52 for 5.2.
    level: int | None = None
    bit_depth: int | None = None
    width: int | None = None
    height: int | None = None
    #: Frames per second, as a number rather than a ratio.
    fps: float | None = None
    language: str | None = None
    title: str | None = None


@dataclass
class MediaInfo:
    """Everything the compatibility rules ask about one file."""

    path: str = ""
    container: str = ""
    tracks: list[Track] = field(default_factory=list)
    #: How this was read: ffprobe | matroska | unknown. Reported because a
    #: verdict is only as good as what it was based on.
    probed_by: str = "unknown"
    error: str | None = None

    @property
    def video(self) -> Track | None:
        return next((track for track in self.tracks if track.kind == "video"), None)

    @property
    def audio(self) -> list[Track]:
        return [track for track in self.tracks if track.kind == "audio"]

    @property
    def subtitles(self) -> list[Track]:
        return [track for track in self.tracks if track.kind == "subtitle"]

    @property
    def known(self) -> bool:
        return self.probed_by != "unknown" and self.video is not None


def available() -> str:
    """Which method would be used, without probing anything."""
    return "ffprobe" if shutil.which("ffprobe") else "matroska"


def probe(path: str | Path) -> MediaInfo:
    """Read one file. Never raises - an unreadable file is a result."""
    target = Path(path)
    info = MediaInfo(path=str(target))
    if not target.exists():
        info.error = "the file is not there"
        return info

    if shutil.which("ffprobe"):
        found = _with_ffprobe(target)
        if found.known or found.error:
            return found
        # ffprobe is present but said nothing useful. Fall through rather than
        # report "unknown": the header parser may still manage.
    if target.suffix.lower() in (".mkv", ".mka", ".webm"):
        return _with_matroska(target)
    info.error = f"{target.suffix or 'that file'} needs ffprobe, which is not installed"
    return info


# -- ffprobe -----------------------------------------------------------------


def _with_ffprobe(target: Path) -> MediaInfo:
    info = MediaInfo(path=str(target))
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(target),
    ]
    try:
        finished = subprocess.run(  # noqa: S603 - a fixed command, a path argument
            command,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        info.error = f"ffprobe could not be run: {exc}"
        return info
    if finished.returncode != 0:
        info.error = (finished.stderr or "ffprobe failed").strip()[:200]
        return info
    try:
        payload = json.loads(finished.stdout or "{}")
    except ValueError:
        info.error = "ffprobe's output could not be read"
        return info

    info.probed_by = "ffprobe"
    container = (payload.get("format") or {}).get("format_name") or ""
    info.container = str(container).split(",")[0]
    for stream in payload.get("streams") or []:
        if not isinstance(stream, dict):
            continue
        kind = str(stream.get("codec_type") or "")
        if kind not in ("video", "audio", "subtitle"):
            continue
        tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
        info.tracks.append(
            Track(
                kind=kind,
                codec=str(stream.get("codec_name") or "").lower(),
                profile=(str(stream.get("profile")).lower() if stream.get("profile") else None),
                level=_as_int(stream.get("level")),
                bit_depth=_as_int(stream.get("bits_per_raw_sample"))
                or _depth_from_pixel_format(stream.get("pix_fmt")),
                width=_as_int(stream.get("width")),
                height=_as_int(stream.get("height")),
                fps=_as_fps(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
                language=(tags or {}).get("language"),
                title=(tags or {}).get("title"),
            )
        )
    return info


def _depth_from_pixel_format(value: object) -> int | None:
    """10 from "yuv420p10le", which is where 10-bit anime declares itself."""
    text = str(value or "")
    for depth in ("12", "10"):
        if f"p{depth}" in text:
            return int(depth)
    return 8 if text else None


def _as_fps(value: object) -> float | None:
    """ffprobe reports a ratio: "24000/1001"."""
    text = str(value or "")
    if "/" not in text:
        return _as_float(text)
    top, _, bottom = text.partition("/")
    try:
        divisor = float(bottom)
        return round(float(top) / divisor, 3) if divisor else None
    except (TypeError, ValueError):
        return None


# -- Matroska ----------------------------------------------------------------
#
# EBML is a tree of (id, size, payload). Only three ids matter here: Tracks,
# TrackEntry and the handful of fields inside one. Everything else is skipped
# by its declared size, which is why this needs no library and no full read.

_TRACKS = 0x1654AE6B
_TRACK_ENTRY = 0xAE
_TRACK_TYPE = 0x83
_CODEC_ID = 0x86
_CODEC_PRIVATE = 0x63A2
_TRACK_NAME = 0x536E
_LANGUAGE = 0x22B59C
_VIDEO = 0xE0
_PIXEL_WIDTH = 0xB0
_PIXEL_HEIGHT = 0xBA
_BIT_DEPTH = 0x55B2
_COLOUR = 0x55B0
_DEFAULT_DURATION = 0x23E383
_SEGMENT = 0x18538067

#: Matroska's names for codecs, mapped to the ones the rules speak. Matroska
#: is explicit where ffprobe is terse, which makes this the easy half.
_CODEC_IDS = {
    "V_MPEG4/ISO/AVC": "h264",
    "V_MPEGH/ISO/HEVC": "hevc",
    "V_AV1": "av1",
    "V_VP9": "vp9",
    "V_VP8": "vp8",
    "V_MPEG2": "mpeg2video",
    "A_AAC": "aac",
    "A_AC3": "ac3",
    "A_EAC3": "eac3",
    "A_DTS": "dts",
    "A_TRUEHD": "truehd",
    "A_FLAC": "flac",
    "A_OPUS": "opus",
    "A_VORBIS": "vorbis",
    "A_MPEG/L3": "mp3",
    "A_PCM/INT/LIT": "pcm_s16le",
    "S_TEXT/UTF8": "subrip",
    "S_TEXT/ASS": "ass",
    "S_TEXT/SSA": "ssa",
    "S_TEXT/WEBVTT": "webvtt",
    "S_VOBSUB": "dvd_subtitle",
    "S_HDMV/PGS": "hdmv_pgs_subtitle",
    "S_DVBSUB": "dvb_subtitle",
    "S_IMAGE/BMP": "bmp",
}

_TRACK_KINDS = {1: "video", 2: "audio", 17: "subtitle"}


def _with_matroska(target: Path) -> MediaInfo:
    """Read the Tracks element out of an MKV's header."""
    info = MediaInfo(path=str(target), container="matroska")
    try:
        with target.open("rb") as handle:
            head = handle.read(MKV_HEAD_BYTES)
    except OSError as exc:
        info.error = f"the file could not be read: {exc}"
        return info
    if head[:4] != b"\x1a\x45\xdf\xa3":
        info.error = "not a Matroska file"
        return info

    tracks = _find_tracks(head)
    if tracks is None:
        info.error = "the track list was not in the part of the file that was read"
        return info
    info.probed_by = "matroska"
    info.tracks = tracks
    return info


def _find_tracks(data: bytes) -> list[Track] | None:
    """Walk the top level for the Tracks element, then read its entries."""
    position = 0
    # The EBML header, then the Segment, whose children are what we want.
    while position < len(data):
        element = _read_element(data, position)
        if element is None:
            return None
        element_id, payload_start, payload_end, position = element
        if element_id == _SEGMENT:
            data_end = min(payload_end, len(data))
            inner = payload_start
            while inner < data_end:
                child = _read_element(data, inner)
                if child is None:
                    return None
                child_id, child_start, child_end, inner = child
                if child_id == _TRACKS:
                    if child_end > len(data):
                        return None
                    return _read_track_entries(data[child_start:child_end])
            return None
    return None


def _read_track_entries(block: bytes) -> list[Track]:
    tracks: list[Track] = []
    position = 0
    while position < len(block):
        element = _read_element(block, position)
        if element is None:
            break
        element_id, start, end, position = element
        if element_id == _TRACK_ENTRY and end <= len(block):
            track = _read_one_track(block[start:end])
            if track is not None:
                tracks.append(track)
    return tracks


def _read_one_track(block: bytes) -> Track | None:
    kind = ""
    codec_id = ""
    codec_private = b""
    name = None
    language = None
    width = height = depth = None
    default_duration = None

    position = 0
    while position < len(block):
        element = _read_element(block, position)
        if element is None:
            break
        element_id, start, end, position = element
        payload = block[start:end]
        if element_id == _TRACK_TYPE:
            kind = _TRACK_KINDS.get(_as_uint(payload), "")
        elif element_id == _CODEC_ID:
            codec_id = payload.split(b"\x00")[0].decode("ascii", "replace")
        elif element_id == _CODEC_PRIVATE:
            codec_private = payload
        elif element_id == _TRACK_NAME:
            name = payload.decode("utf-8", "replace") or None
        elif element_id == _LANGUAGE:
            language = payload.split(b"\x00")[0].decode("ascii", "replace") or None
        elif element_id == _DEFAULT_DURATION:
            default_duration = _as_uint(payload)
        elif element_id == _VIDEO:
            width, height, depth = _read_video_block(payload)
    if not kind:
        return None

    track = Track(
        kind=kind,
        codec=_CODEC_IDS.get(codec_id, codec_id.lower().lstrip("vas_")),
        width=width,
        height=height,
        bit_depth=depth,
        language=language,
        title=name,
    )
    if default_duration:
        # Nanoseconds per frame.
        track.fps = round(1_000_000_000 / default_duration, 3)
    if track.codec == "h264" and codec_private:
        track.profile, track.level, avc_depth = _read_avcc(codec_private)
        track.bit_depth = track.bit_depth or avc_depth
    elif track.codec == "hevc" and codec_private:
        track.profile, track.level, hevc_depth = _read_hvcc(codec_private)
        track.bit_depth = track.bit_depth or hevc_depth
    elif track.codec == "av1" and codec_private:
        track.profile, track.level, av1_depth = _read_av1c(codec_private)
        track.bit_depth = track.bit_depth or av1_depth
    return track


def _read_video_block(payload: bytes) -> tuple[int | None, int | None, int | None]:
    width = height = depth = None
    position = 0
    while position < len(payload):
        element = _read_element(payload, position)
        if element is None:
            break
        element_id, start, end, position = element
        if element_id == _PIXEL_WIDTH:
            width = _as_uint(payload[start:end])
        elif element_id == _PIXEL_HEIGHT:
            height = _as_uint(payload[start:end])
        elif element_id == _COLOUR:
            inner = start
            while inner < end:
                child = _read_element(payload, inner)
                if child is None:
                    break
                child_id, child_start, child_end, inner = child
                if child_id == _BIT_DEPTH:
                    depth = _as_uint(payload[child_start:child_end])
    return width, height, depth


#: H.264's profile_idc values, by the names people use for them. 110 is the
#: one that matters most here: High 10 is what "Hi10P" means, and it is the
#: single most common reason an anime release will not play directly.
_AVC_PROFILES = {
    66: "baseline",
    77: "main",
    88: "extended",
    100: "high",
    110: "high 10",
    122: "high 4:2:2",
    244: "high 4:4:4 predictive",
}


def _read_avcc(private: bytes) -> tuple[str | None, int | None, int | None]:
    """profile, level and bit depth from an avcC record.

    Its first bytes are: version, profile_idc, profile_compatibility,
    level_idc. High 10 implies ten bits, which is why the depth can be
    inferred here at all.
    """
    if len(private) < 4:
        return None, None, None
    profile_idc = private[1]
    level_idc = private[3]
    profile = _AVC_PROFILES.get(profile_idc, f"profile {profile_idc}")
    depth = 10 if profile_idc in (110, 122, 244) else 8
    return profile, level_idc, depth


def _read_hvcc(private: bytes) -> tuple[str | None, int | None, int | None]:
    """HEVC: profile_space/tier/idc in byte 1, level in byte 12."""
    if len(private) < 13:
        return None, None, None
    profile_idc = private[1] & 0x1F
    level_idc = private[12]
    names = {1: "main", 2: "main 10", 3: "main still picture"}
    depth = 10 if profile_idc == 2 else 8
    return names.get(profile_idc, f"profile {profile_idc}"), level_idc, depth


def _read_av1c(private: bytes) -> tuple[str | None, int | None, int | None]:
    """AV1: seq_profile and seq_level_idx, then the high-bitdepth flag."""
    if len(private) < 4:
        return None, None, None
    seq_profile = (private[1] >> 5) & 0x07
    seq_level = private[1] & 0x1F
    high_bitdepth = bool(private[2] & 0x40)
    twelve_bit = bool(private[2] & 0x20)
    depth = 12 if twelve_bit else (10 if high_bitdepth else 8)
    names = {0: "main", 1: "high", 2: "professional"}
    return names.get(seq_profile, f"profile {seq_profile}"), seq_level, depth


# -- EBML primitives ---------------------------------------------------------


def _read_element(data: bytes, position: int):
    """``(id, payload start, payload end, next position)`` or None."""
    element_id, position = _read_id(data, position)
    if element_id is None:
        return None
    size, position = _read_size(data, position)
    if size is None:
        return None
    return element_id, position, position + size, position + size


def _read_id(data: bytes, position: int):
    if position >= len(data):
        return None, position
    first = data[position]
    length = _leading_length(first)
    if length == 0 or position + length > len(data):
        return None, position
    return int.from_bytes(data[position : position + length], "big"), position + length


def _read_size(data: bytes, position: int):
    if position >= len(data):
        return None, position
    first = data[position]
    length = _leading_length(first)
    if length == 0 or position + length > len(data):
        return None, position
    value = first & (0xFF >> length)
    for offset in range(1, length):
        value = (value << 8) | data[position + offset]
    # An all-ones size means "unknown", which only the Segment uses.
    if value == (1 << (7 * length)) - 1:
        value = len(data) - (position + length)
    return value, position + length


def _leading_length(first: int) -> int:
    """How many bytes this variable-length integer occupies."""
    for length in range(1, 9):
        if first & (0x80 >> (length - 1)):
            return length
    return 0


def _as_uint(payload: bytes) -> int:
    return int.from_bytes(payload, "big") if payload else 0


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


__all__ = ["MKV_HEAD_BYTES", "MediaInfo", "Track", "available", "probe"]

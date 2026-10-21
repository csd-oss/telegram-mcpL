"""Video metadata for uploads: display size, duration and a thumbnail.

Telethon fills DocumentAttributeVideo only through hachoir, and sends 1x1 with
duration 0 when it can't read the file, so phones show a landscape video as a
cropped square tile. We probe the file ourselves (ffprobe when it is installed,
else hachoir) and pass the attribute explicitly; ffmpeg, when present, also
renders the preview thumbnail Telegram shows before the video loads.
"""

from __future__ import annotations

import json
import math
import mimetypes
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

from telethon import types

VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm")
THUMBNAIL_EDGE_PIXELS = 320  # Telegram's limit for a document thumbnail
THUMBNAIL_SEEK_SECONDS = 1.0
PROBE_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class VideoInfo:
    """Size as displayed (rotation applied) and duration in seconds."""

    width: int
    height: int
    duration: float

    def attribute(self) -> types.DocumentAttributeVideo:
        return types.DocumentAttributeVideo(
            duration=self.duration,
            w=self.width,
            h=self.height,
            supports_streaming=True,
        )


def is_video(path: str) -> bool:
    mime = mimetypes.guess_type(path)[0] or ""
    return mime.startswith("video/") or path.lower().endswith(VIDEO_EXTENSIONS)


def _displayed(width: int, height: int, rotation: float) -> tuple[int, int]:
    """Swap the coded size for a quarter-turn rotation (portrait phone videos)."""
    if round(rotation) % 180 == 90:
        return height, width
    return width, height


def _rotation(value) -> float:
    try:
        degrees = abs(float(value))
    except (TypeError, ValueError):
        return 0.0
    return degrees if math.isfinite(degrees) else 0.0


def _positive_float(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _probe_ffprobe(path: str) -> Optional[VideoInfo]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                path,
            ],
            capture_output=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=True,
        )
        report = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    streams = report.get("streams") or []
    if not streams:
        return None
    stream = streams[0]
    width, height = stream.get("width"), stream.get("height")
    if not (isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0):
        return None
    # Newer ffmpeg reports rotation as Display Matrix side data, older as a "rotate" tag.
    rotation = next(
        (
            _rotation(side["rotation"])
            for side in stream.get("side_data_list") or []
            if "rotation" in side
        ),
        _rotation((stream.get("tags") or {}).get("rotate")),
    )
    duration = _positive_float(stream.get("duration")) or _positive_float(
        (report.get("format") or {}).get("duration")
    )
    width, height = _displayed(width, height, rotation)
    return VideoInfo(width, height, duration or 0.0)


def _mp4_rotation(parser) -> float:
    """Rotation of the first video track, from its track header matrix (MP4/MOV only)."""
    if parser.PARSER_TAGS.get("id") != "mov":
        return 0.0
    try:
        for atom in parser:
            if "movie" not in atom:
                continue
            for field in atom["movie"]:
                if "track" not in field:
                    continue
                for track_field in field["track"]:
                    if "track_hdr" not in track_field:
                        continue
                    header = track_field["track_hdr"]
                    if header["frame_size_width"].value and header["frame_size_height"].value:
                        return abs(
                            math.degrees(
                                math.atan2(header["geom_b"].value, header["geom_a"].value)
                            )
                        )
    except Exception:
        pass  # an unreadable matrix leaves the coded size, as Telethon would
    return 0.0


def _probe_hachoir(path: str) -> Optional[VideoInfo]:
    try:
        from hachoir.metadata import extractMetadata
        from hachoir.parser import createParser
    except ImportError:
        return None
    try:
        parser = createParser(path)
        if parser is None:
            return None
        with parser:
            metadata = extractMetadata(parser)
            if metadata is None:
                return None
            # MKV/WebM keep the size on a per-track group, MP4 on the top level.
            sources = [metadata, *getattr(metadata, "iterGroups", lambda: [])()]
            sized = next((m for m in sources if m.has("width") and m.has("height")), None)
            if sized is None:
                return None
            width, height = int(sized.get("width")), int(sized.get("height"))
            timed = next((m for m in sources if m.has("duration")), None)
            duration = timed.get("duration").total_seconds() if timed else 0.0
            rotation = _mp4_rotation(parser)
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    width, height = _displayed(width, height, rotation)
    return VideoInfo(width, height, max(duration, 0.0))


def probe_video(path: str) -> Optional[VideoInfo]:
    """Display size and duration of a video file, or None when neither prober can read it."""
    return _probe_ffprobe(path) or _probe_hachoir(path)


def make_thumbnail(path: str, info: VideoInfo, out_dir: str) -> Optional[str]:
    """JPEG frame at ~1 s, longest edge 320 px, in out_dir; None without ffmpeg or on failure."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    out = os.path.join(out_dir, os.path.basename(path) + ".thumb.jpg")
    seek = min(THUMBNAIL_SEEK_SECONDS, info.duration / 2) if info.duration else 0.0
    edge = THUMBNAIL_EDGE_PIXELS
    # ffmpeg applies the rotation itself (autorotate), so the frame comes out upright.
    for start in dict.fromkeys((seek, 0.0)):
        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-y",
                    "-ss",
                    f"{start:.3f}",
                    "-i",
                    path,
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={edge}:{edge}:force_original_aspect_ratio=decrease",
                    "-q:v",
                    "5",
                    out,
                ],
                capture_output=True,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            return out
    return None

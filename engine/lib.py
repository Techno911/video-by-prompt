"""Shared paths, ffprobe helpers and runtime capabilities."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from functools import lru_cache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.abspath(os.path.expanduser(os.environ.get("STORIS_SOURCE_DIR", os.path.join(ROOT, "media"))))
OUT = os.path.abspath(os.path.expanduser(os.environ.get("STORIS_OUT_DIR", os.path.join(ROOT, "out"))))
CACHE = os.path.abspath(os.path.expanduser(os.environ.get("STORIS_CACHE_DIR", os.path.join(ROOT, "cache"))))

W, H, FPS = 1080, 1920, 30

for directory in (OUT, CACHE):
    os.makedirs(directory, exist_ok=True)


def configure_video(video: dict | None = None) -> None:
    """Apply width, height and fps before rendering any titles or clips."""
    global W, H, FPS
    video = video or {}
    width = int(video.get("width", 1080))
    height = int(video.get("height", 1920))
    fps = int(video.get("fps", 30))
    if width < 240 or height < 240 or width % 2 or height % 2:
        raise ValueError("video width and height must be even integers >= 240")
    if fps < 1 or fps > 60:
        raise ValueError("video fps must be between 1 and 60")
    W, H, FPS = width, height, fps


def set_source_dir(path: str) -> str:
    global SRC
    SRC = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(SRC):
        raise FileNotFoundError(f"source directory does not exist: {SRC}")
    return SRC


def binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} was not found in PATH; install ffmpeg first")
    return path


def command(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-3000:]
        raise RuntimeError(f"command failed: {' '.join(args[:3])}\n{detail}")
    return result


@lru_cache(maxsize=1)
def capabilities() -> dict[str, bool]:
    ffmpeg = binary("ffmpeg")
    binary("ffprobe")
    filters = command([ffmpeg, "-hide_banner", "-filters"]).stdout
    encoders = command([ffmpeg, "-hide_banner", "-encoders"]).stdout
    return {
        "scale_vt": " scale_vt " in filters,
        "zscale": " zscale " in filters,
        "tonemap": " tonemap " in filters,
        "libx264": " libx264 " in encoders,
    }


def media_duration(path: str) -> float:
    result = command([
        binary("ffprobe"), "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", os.path.abspath(os.path.expanduser(path)),
    ])
    try:
        return round(float(result.stdout.strip()), 6)
    except ValueError as exc:
        raise ValueError(f"cannot read media duration: {path}") from exc


def has_audio(path: str) -> bool:
    result = command([
        binary("ffprobe"), "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1",
        os.path.abspath(os.path.expanduser(path)),
    ], check=False)
    return bool(result.stdout.strip())


def probe(path: str) -> dict:
    """Read the first video stream, including display-matrix rotation."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"file does not exist: {path}")

    data = json.loads(command([
        binary("ffprobe"), "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames,codec_name,"
        "color_space,color_transfer,color_primaries,pix_fmt:format=duration",
        "-of", "json", path,
    ]).stdout)
    streams = data.get("streams") or []
    if not streams:
        raise ValueError(f"file has no video stream: {path}")
    stream = streams[0]

    rotation_lines = command([
        binary("ffprobe"), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream_side_data=rotation",
        "-of", "default=nw=1:nk=1", path,
    ], check=False).stdout.strip().splitlines()
    try:
        rotation = int(round(float(rotation_lines[0]))) if rotation_lines else 0
    except (TypeError, ValueError):
        rotation = 0

    width, height = int(stream["width"]), int(stream["height"])
    if abs(rotation) == 90:
        width, height = height, width

    rate = stream.get("r_frame_rate") or "0/1"
    numerator, denominator = rate.split("/", 1)
    fps = float(numerator) / float(denominator) if float(denominator) else 0.0
    transfer = stream.get("color_transfer") or ""
    pixel_format = stream.get("pix_fmt") or ""
    duration = float((data.get("format") or {}).get("duration") or 0.0)

    return {
        "path": path,
        "w": width,
        "h": height,
        "rot": rotation,
        "hdr": transfer in ("arib-std-b67", "smpte2084"),
        "trc": transfer,
        "space": stream.get("color_space") or "",
        "primaries": stream.get("color_primaries") or "",
        "pix_fmt": pixel_format,
        "depth": 10 if "10" in pixel_format else 8,
        "fps": round(fps, 6),
        "dur": round(duration, 6),
        "frames": int(stream["nb_frames"]) if str(stream.get("nb_frames", "")).isdigit() else None,
        "codec": stream.get("codec_name") or "",
        "audio": has_audio(path),
    }


def run(args: list[str], quiet: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = [
        binary("ffmpeg"), "-hide_banner", "-loglevel",
        "error" if quiet else "info", "-y", *args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + result.stderr.strip()[-3000:])
    return result


def font(requested: str | None = None, *, bold: bool = False) -> str:
    """Find a user-supplied or common system TrueType/OpenType font."""
    if requested:
        expanded = os.path.abspath(os.path.expanduser(requested))
        if os.path.isfile(expanded):
            return expanded

    candidates = []
    if bold:
        candidates.extend([
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        ])
    candidates.extend([
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    ])
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(
        "no usable system font found; set hero_font/font in storyboard to a .ttf/.otf file"
    )

"""Deterministic preflight and final-master acceptance gates."""
from __future__ import annotations

import json
import os
import re
import subprocess

import lib


def validate_storyboard(config: dict) -> float:
    scenes = config.get("scenes") or []
    if not scenes:
        raise ValueError("storyboard has no scenes")
    total = round(sum(float(scene["dur"]) for scene in scenes), 6)
    declared = float(config.get("duration_seconds", total))
    if abs(total - declared) > 0.001:
        raise ValueError(f"duration_seconds is {declared}s, scene sum is {total}s")
    if total <= 0 or total > 180:
        raise ValueError(f"unsupported duration: {total}s")

    numbers = [scene.get("n") for scene in scenes]
    if numbers != list(range(1, len(scenes) + 1)):
        raise ValueError("scene numbers must be consecutive starting with 1")

    hdr_seen = False
    for scene in scenes:
        duration = float(scene["dur"])
        if duration <= 0 or abs(duration * lib.FPS - round(duration * lib.FPS)) > 0.001:
            raise ValueError(
                f"scene {scene['n']}: {duration}s does not align to {lib.FPS} fps"
            )
        source = os.path.join(lib.SRC, scene["src"])
        if not os.path.isfile(source):
            raise FileNotFoundError(f"scene {scene['n']}: missing {source}")
        info = lib.probe(source)
        if scene.get("kind") == "photo":
            continue
        start = float(scene.get("ss", 0))
        if start < 0 or start + duration > info["dur"] + 1 / lib.FPS:
            raise ValueError(
                f"scene {scene['n']}: {scene['src']} ends at {info['dur']:.3f}s, "
                f"requested {start:.3f}-{start + duration:.3f}s"
            )
        hdr_seen = hdr_seen or info["hdr"]

    caps = lib.capabilities()
    if not caps["libx264"]:
        raise RuntimeError("ffmpeg has no libx264 encoder")
    if hdr_seen and not (caps["scale_vt"] or (caps["zscale"] and caps["tonemap"])):
        raise RuntimeError("HDR source exists, but ffmpeg has no safe HDR to SDR path")

    audio = config.get("audio") or {}
    audio_source = os.path.join(lib.SRC, audio.get("src", ""))
    if not os.path.isfile(audio_source):
        raise FileNotFoundError(f"audio source is missing: {audio_source}")
    if not lib.has_audio(audio_source):
        raise ValueError(f"audio source has no audio stream: {audio_source}")
    audio_start = float(audio.get("ss", 0))
    audio_duration = lib.media_duration(audio_source)
    if audio_start + total > audio_duration + 0.05:
        raise ValueError(
            f"audio is too short: need until {audio_start + total:.2f}s, "
            f"source is {audio_duration:.2f}s"
        )

    for title in config.get("titles", []):
        start, end = float(title["start"]), float(title["end"])
        if not 0 <= start < end <= total + 0.001:
            raise ValueError(f"title {title.get('name')}: invalid time {start}-{end}")
        for value in (start, end):
            if abs(value * lib.FPS - round(value * lib.FPS)) > 0.001:
                raise ValueError(
                    f"title {title.get('name')}: {value}s does not align to fps"
                )

    output = config.get("output", "")
    if not output.lower().endswith(".mp4") or os.path.basename(output) != output:
        raise ValueError("output must be a safe .mp4 filename without directories")
    return total


def _full_probe(path: str) -> dict:
    result = subprocess.run([
        lib.binary("ffprobe"), "-v", "error", "-show_streams", "-show_format",
        "-of", "json", path,
    ], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def _loudness(path: str) -> tuple[float, float]:
    result = subprocess.run([
        lib.binary("ffmpeg"), "-hide_banner", "-nostats", "-i", path,
        "-af", "ebur128=peak=true", "-f", "null", "-",
    ], capture_output=True, text=True)
    integrated = re.findall(r"I:\s+(-?[0-9.]+) LUFS", result.stderr)
    peaks = re.findall(r"Peak:\s+(-?[0-9.]+) dBFS", result.stderr)
    if not integrated or not peaks:
        raise RuntimeError("cannot measure master loudness")
    return float(integrated[-1]), float(peaks[-1])


def _content_anomalies(path: str) -> list[str]:
    result = subprocess.run([
        lib.binary("ffmpeg"), "-hide_banner", "-nostats", "-i", path,
        "-vf", "blackdetect=d=0.15:pix_th=0.02,freezedetect=n=-45dB:d=0.5",
        "-af", "silencedetect=n=-45dB:d=0.5", "-f", "null", "-",
    ], capture_output=True, text=True)
    markers = {
        "black_start": "black segment detected",
        "freeze_start": "frozen frame detected",
        "silence_start": "silence longer than 0.5s detected",
    }
    return [label for marker, label in markers.items() if marker in result.stderr]


def check_master(path: str, config: dict, contact_path: str | None = None) -> dict:
    total = float(config["duration_seconds"])
    expected_frames = int(round(total * lib.FPS))
    data = _full_probe(path)
    video = next((stream for stream in data["streams"] if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in data["streams"] if stream.get("codec_type") == "audio"), None)
    failures = []
    if not video:
        failures.append("no video stream")
    else:
        if (int(video["width"]), int(video["height"])) != (lib.W, lib.H):
            failures.append(f"size is {video['width']}x{video['height']}")
        expected_rate = f"{lib.FPS}/1"
        if video.get("r_frame_rate") != expected_rate or video.get("avg_frame_rate") != expected_rate:
            failures.append(
                f"not CFR: r={video.get('r_frame_rate')}, avg={video.get('avg_frame_rate')}"
            )
        if int(video.get("nb_frames") or 0) != expected_frames:
            failures.append(f"{video.get('nb_frames')} frames, expected {expected_frames}")
        for key in ("color_space", "color_transfer", "color_primaries"):
            if video.get(key) != "bt709":
                failures.append(f"{key}={video.get(key)}, expected bt709")
        if video.get("pix_fmt") != "yuv420p":
            failures.append(f"pix_fmt={video.get('pix_fmt')}")
    if not audio:
        failures.append("no audio stream")
    else:
        if audio.get("codec_name") != "aac" or audio.get("sample_rate") != "48000":
            failures.append(f"audio is {audio.get('codec_name')}/{audio.get('sample_rate')}")
        if int(audio.get("channels") or 0) != 2:
            failures.append(f"audio has {audio.get('channels')} channels, expected stereo")
    duration = float(data["format"]["duration"])
    if abs(duration - total) > 1 / lib.FPS + 0.001:
        failures.append(f"duration is {duration:.3f}s, expected {total:.3f}s")

    decode = subprocess.run([
        lib.binary("ffmpeg"), "-v", "error", "-i", path, "-f", "null", "-",
    ], capture_output=True, text=True)
    if decode.returncode != 0:
        failures.append("full decode failed: " + decode.stderr[-500:])
    failures.extend(_content_anomalies(path))

    loudness, true_peak = _loudness(path)
    target = float(config["audio"].get("target_lufs", -14))
    peak_limit = float(config["audio"].get("true_peak", -1.5))
    if abs(loudness - target) > 0.5:
        failures.append(f"loudness is {loudness} LUFS, target is {target}")
    if true_peak > peak_limit + 0.2:
        failures.append(f"true peak is {true_peak} dBFS, limit is {peak_limit}")

    if contact_path:
        columns, rows = 4, 3
        lib.run([
            "-i", path, "-vf",
            f"fps={columns * rows}/{total},scale={max(120, lib.W // 4)}:-2,"
            f"tile={columns}x{rows}:padding=8:margin=8:color=0x111111",
            "-frames:v", "1", contact_path,
        ])

    if failures:
        raise RuntimeError("master failed QA:\n- " + "\n- ".join(failures))
    result = {
        "path": path,
        "duration": duration,
        "frames": expected_frames,
        "loudness_lufs": loudness,
        "true_peak_dbfs": true_peak,
        "size_mb": round(os.path.getsize(path) / 1048576, 1),
    }
    print(
        f"QA: PASS | {lib.W}x{lib.H} | {lib.FPS} CFR | {expected_frames}f | "
        f"{duration:.2f}s | {loudness:.1f} LUFS | TP {true_peak:.1f} dBFS"
    )
    return result

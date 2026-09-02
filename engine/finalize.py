"""Two-pass EBU R128 loudness normalization and atomic audio mux."""
from __future__ import annotations

import json
import os
import re
import subprocess

import lib


def _audio_prefix(audio: dict, total: float) -> str:
    fade_in = float(audio.get("fade_in", 0.3))
    fade_out = float(audio.get("fade_out", 0.8))
    fade_out_start = max(0.0, total - fade_out)
    return (
        f"afade=t=in:st=0:d={fade_in},"
        f"afade=t=out:st={fade_out_start:.3f}:d={fade_out}"
    )


def measure(audio_path: str, audio: dict, total: float) -> dict:
    target = float(audio.get("target_lufs", -14.0))
    peak = float(audio.get("true_peak", -1.5))
    lra = float(audio.get("lra", 11.0))
    audio_filter = (
        f"{_audio_prefix(audio, total)},"
        f"loudnorm=I={target}:TP={peak}:LRA={lra}:print_format=json"
    )
    result = subprocess.run([
        lib.binary("ffmpeg"), "-hide_banner", "-nostats", "-loglevel", "info",
        "-ss", str(audio.get("ss", 0)), "-t", str(total), "-i", audio_path,
        "-af", audio_filter, "-f", "null", "-",
    ], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("cannot measure audio:\n" + result.stderr[-2500:])
    blocks = re.findall(r"\{\s*\"input_i\".*?\}", result.stderr, flags=re.DOTALL)
    if not blocks:
        raise RuntimeError("ffmpeg loudnorm returned no measurements")
    return json.loads(blocks[-1])


def mux_audio(video: str, audio_path: str, audio: dict, output: str, total: float) -> str:
    stats = measure(audio_path, audio, total)
    target = float(audio.get("target_lufs", -14.0))
    peak = float(audio.get("true_peak", -1.5))
    lra = float(audio.get("lra", 11.0))
    loudnorm = (
        f"loudnorm=I={target}:TP={peak}:LRA={lra}:"
        f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
        f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:linear=true:print_format=summary"
    )
    audio_filter = f"{_audio_prefix(audio, total)},{loudnorm}"
    part = output + ".part.mp4"
    lib.run([
        "-i", video,
        "-ss", str(audio.get("ss", 0)), "-t", str(total), "-i", audio_path,
        "-filter_complex", f"[1:a]{audio_filter}[a]", "-map", "0:v:0", "-map", "[a]",
        "-c:v", "copy", "-bsf:v",
        "h264_metadata=video_full_range_flag=0:colour_primaries=1:"
        "transfer_characteristics=1:matrix_coefficients=1",
        "-c:a", "aac", "-b:a", str(audio.get("bitrate", "192k")),
        "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", "-t", str(total), part,
    ])
    info = lib.probe(part)
    expected_frames = int(round(total * lib.FPS))
    if info["frames"] != expected_frames or abs(info["dur"] - total) > 1 / lib.FPS + 0.001:
        os.remove(part)
        raise RuntimeError(
            f"mux is not exact: {info['frames']}f/{info['dur']}s, "
            f"expected {expected_frames}f/{total}s"
        )
    if not info["audio"]:
        os.remove(part)
        raise RuntimeError("muxed master has no audio")
    os.replace(part, output)
    return output

"""Normalize source shots: HDR, rotation, crop, look and exact CFR."""
from __future__ import annotations

import json
import os

import grade
import lib


def _rotate(rotation: int | float | None) -> list[str]:
    value = (round((rotation or 0) / 90) * 90) % 360
    return {
        270: ["transpose=1"],
        90: ["transpose=2"],
        180: ["transpose=1", "transpose=1"],
    }.get(value, [])


def pre_filters(info: dict) -> list[str]:
    filters = []
    if info["hdr"]:
        caps = lib.capabilities()
        if caps["scale_vt"]:
            software_format = "p010le" if info["depth"] == 10 else "nv12"
            filters.extend([
                "scale_vt=color_matrix=bt709:color_primaries=bt709:color_transfer=bt709",
                "hwdownload",
                f"format={software_format}",
                "format=yuv420p",
            ])
        elif caps["zscale"] and caps["tonemap"]:
            transfer = "smpte2084" if info["trc"] == "smpte2084" else "arib-std-b67"
            filters.extend([
                f"zscale=t=linear:npl=100:tin={transfer}:min=2020_ncl:pin=bt2020",
                "format=gbrpf32le",
                "tonemap=tonemap=hable:desat=0",
                "zscale=t=bt709:m=bt709:p=bt709:r=tv",
                "format=yuv420p",
            ])
        else:
            raise RuntimeError(
                "HDR source cannot be converted to SDR: ffmpeg has neither scale_vt "
                "nor zscale + tonemap"
            )
    filters.extend(_rotate(info["rot"]))
    return filters


def input_args(info: dict) -> list[str]:
    args = ["-noautorotate"]
    if info["hdr"] and lib.capabilities()["scale_vt"]:
        args.extend([
            "-hwaccel", "videotoolbox",
            "-hwaccel_output_format", "videotoolbox_vld",
        ])
    return args


def _assert_clip(path: str, expected_duration: float, expected_frames: int) -> None:
    info = lib.probe(path)
    if info["frames"] != expected_frames:
        raise RuntimeError(
            f"shot has {info['frames']} frames, expected {expected_frames}: {path}"
        )
    if abs(info["dur"] - expected_duration) > (1 / lib.FPS + 0.001):
        raise RuntimeError(
            f"shot is {info['dur']}s, expected {expected_duration}s: {path}"
        )


def video_clip(scene: dict, index: int) -> str:
    source = os.path.join(lib.SRC, scene["src"])
    info = lib.probe(source)
    destination = os.path.join(lib.CACHE, f"shot_{index:02d}.mp4")
    duration = float(scene["dur"])
    frames = int(round(duration * lib.FPS))
    filters = pre_filters(info) + [grade.fit(lib.W, lib.H)]
    if scene.get("push"):
        zoom = float(scene["push"])
        filters.append(
            f"zoompan=z='min(1+0.00055*on,{zoom})':d=1:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={lib.W}x{lib.H}:fps={lib.FPS}"
        )
    filters.extend([
        grade.look(scene.get("look", "normal")),
        f"fps={lib.FPS}",
        f"setpts=N/({lib.FPS}*TB)",
        "scale=in_range=auto:out_range=tv",
        "format=yuv420p",
        "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
    ])
    lib.run(input_args(info) + [
        "-ss", str(scene.get("ss", 0)), "-t", str(duration), "-i", source,
        "-vf", ",".join(filters), "-an", "-frames:v", str(frames),
        "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-video_track_timescale", "90000", destination,
    ])
    _assert_clip(destination, duration, frames)
    return destination


def photo_clip(scene: dict, index: int) -> str:
    source = os.path.join(lib.SRC, scene["src"])
    destination = os.path.join(lib.CACHE, f"shot_{index:02d}.mp4")
    duration = float(scene["dur"])
    frames = int(round(duration * lib.FPS))
    zoom = float(scene.get("push", 1.08))
    filters = (
        f"{grade.fit(int(lib.W * 1.25), int(lib.H * 1.25))},"
        f"zoompan=z='min(zoom+0.0012,{zoom})':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={lib.W}x{lib.H}:fps={lib.FPS},"
        f"{grade.look(scene.get('look', 'warm'))},"
        f"setpts=N/({lib.FPS}*TB),scale=in_range=auto:out_range=tv,"
        "format=yuv420p,setparams=range=limited:color_primaries=bt709:"
        "color_trc=bt709:colorspace=bt709"
    )
    lib.run([
        "-i", source, "-vf", filters, "-frames:v", str(frames),
        "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-video_track_timescale", "90000", destination,
    ])
    _assert_clip(destination, duration, frames)
    return destination


def build(storyboard_path: str) -> list[str]:
    with open(storyboard_path, encoding="utf-8") as file:
        storyboard = json.load(file)
    parts = []
    for index, scene in enumerate(storyboard["scenes"]):
        path = photo_clip(scene, index) if scene.get("kind") == "photo" else video_clip(scene, index)
        info = lib.probe(path)
        source_is_hdr = (
            scene.get("kind") != "photo"
            and lib.probe(os.path.join(lib.SRC, scene["src"]))["hdr"]
        )
        role = str(scene.get("role", scene.get("роль", "SHOT")))
        flag = "HDR->709" if source_is_hdr else "SDR"
        print(
            f"  {index + 1:2}/{len(storyboard['scenes'])} {role:12} "
            f"{scene['src'][:22]:22} {info['dur']:.2f}s {info['frames']:4}f {flag}"
        )
        parts.append(path)
    return parts

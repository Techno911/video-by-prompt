#!/usr/bin/env python3
"""Reproducible short-video assembly driven by storyboard.json."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time

from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import build  # noqa: E402
import compose  # noqa: E402
import finalize  # noqa: E402
import lib  # noqa: E402
import qa  # noqa: E402
import titles  # noqa: E402

VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".png", ".webp"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".aac", ".m4a", ".flac", ".ogg"}


def load_config(path: str, source_override: str | None = None) -> dict:
    path = os.path.abspath(os.path.expanduser(path))
    with open(path, encoding="utf-8") as file:
        config = json.load(file)
    lib.configure_video(config.get("video"))
    source = source_override or config.get("source_dir") or os.environ.get("STORIS_SOURCE_DIR")
    if not source:
        raise ValueError("set source_dir in storyboard or pass --source-dir")
    if not os.path.isabs(os.path.expanduser(source)):
        source = os.path.join(os.path.dirname(path), source)
    lib.set_source_dir(source)
    config["_path"] = path
    return config


def media_datetime(path: str, is_video_or_audio: bool) -> dt.datetime:
    if is_video_or_audio:
        result = lib.command([
            lib.binary("ffprobe"), "-v", "error", "-show_entries",
            "format_tags=creation_time", "-of", "default=nw=1:nk=1", path,
        ], check=False).stdout.strip()
        if result:
            try:
                return dt.datetime.fromisoformat(result.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
            except ValueError:
                pass
    else:
        try:
            with Image.open(path) as image:
                exif = image.getexif()
                value = exif.get(36867) or exif.get(306)
            if value:
                return dt.datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
        except (OSError, ValueError):
            pass
    return dt.datetime.fromtimestamp(os.stat(path).st_mtime)


def inventory(date: str | None = None) -> None:
    rows = []
    supported = VIDEO_EXTENSIONS | PHOTO_EXTENSIONS | AUDIO_EXTENSIONS
    for name in sorted(os.listdir(lib.SRC)):
        path = os.path.join(lib.SRC, name)
        extension = os.path.splitext(name)[1].lower()
        if extension not in supported or not os.path.isfile(path):
            continue
        is_video = extension in VIDEO_EXTENSIONS
        is_audio = extension in AUDIO_EXTENSIONS
        captured = media_datetime(path, is_video or is_audio)
        if date and captured.strftime("%d.%m.%y") != date:
            continue
        rows.append((captured, name, is_video, is_audio))

    for captured, name, is_video, is_audio in rows:
        extra = ""
        if is_video:
            info = lib.probe(os.path.join(lib.SRC, name))
            kind = "VIDEO"
            extra = (
                f"{info['dur']:.2f}s {info['w']}x{info['h']} "
                f"{'HDR ' + info['trc'] if info['hdr'] else 'SDR'} rot={info['rot']}"
            )
        elif is_audio:
            kind = "AUDIO"
            extra = f"{lib.media_duration(os.path.join(lib.SRC, name)):.2f}s"
        else:
            kind = "PHOTO"
        print(f"{captured:%d.%m %H:%M}  {kind:5}  {name:28} {extra}")
    print(f"\ntotal: {len(rows)}")


def contact_sheets(date: str | None = None) -> None:
    directory = os.path.join(ROOT, "preview", "roll")
    os.makedirs(directory, exist_ok=True)
    built = 0
    for name in sorted(os.listdir(lib.SRC)):
        path = os.path.join(lib.SRC, name)
        extension = os.path.splitext(name)[1].lower()
        if extension not in VIDEO_EXTENSIONS or not os.path.isfile(path):
            continue
        captured = media_datetime(path, True)
        if date and captured.strftime("%d.%m.%y") != date:
            continue
        info = lib.probe(path)
        count = max(4, min(10, int(info["dur"] // 3) or 4))
        frames = []
        for index in range(1, count + 1):
            timestamp = round(info["dur"] * index / (count + 1), 3)
            destination = os.path.join(
                directory, f"{os.path.splitext(name)[0]}_{index:02d}.jpg"
            )
            filters = build.pre_filters(info) + [
                "scale=250:444:force_original_aspect_ratio=increase",
                "crop=250:444", "format=yuv420p",
            ]
            lib.run(build.input_args(info) + [
                "-ss", str(timestamp), "-i", path, "-frames:v", "1",
                "-vf", ",".join(filters), destination,
            ])
            frames.append(destination)
        list_path = os.path.join(directory, f".{os.path.splitext(name)[0]}.txt")
        with open(list_path, "w", encoding="utf-8") as file:
            for frame in frames:
                escaped = frame.replace("'", "'\\''")
                file.write(f"file '{escaped}'\n")
        output = os.path.join(directory, f"ROLL_{os.path.splitext(name)[0]}.jpg")
        lib.run([
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-vf", f"tile={count}x1:margin=3:padding=3:color=0x0a0a0a",
            "-frames:v", "1", output,
        ])
        os.remove(list_path)
        built += 1
        print(f"{name}: {info['dur']:.2f}s -> {output}")
    print(f"\ncontact sheets: {built}")


def concat(parts: list[str], total: float) -> str:
    list_path = os.path.join(lib.CACHE, "concat.txt")
    with open(list_path, "w", encoding="utf-8") as file:
        for path in parts:
            escaped = path.replace("'", "'\\''")
            file.write(f"file '{escaped}'\n")
    output = os.path.join(lib.CACHE, "sequence.mp4")
    lib.run([
        "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy",
        "-video_track_timescale", "90000", output,
    ])
    info = lib.probe(output)
    expected_frames = int(round(total * lib.FPS))
    if info["frames"] != expected_frames or abs(info["dur"] - total) > 1 / lib.FPS + 0.001:
        raise RuntimeError(
            f"sequence is not exact: {info['frames']}f/{info['dur']}s, "
            f"expected {expected_frames}f/{total}s"
        )
    return output


def assemble(config: dict, output_override: str | None = None) -> str:
    started = time.time()
    total = qa.validate_storyboard(config)
    title_plan = titles.render_plan(config.get("titles", []))
    titles.assert_safe(title_plan, config.get("safe_zone", {}))

    print("Shots:")
    parts = build.build(config["_path"])
    sequence = concat(parts, total)

    video_config = config.get("video", {})
    silent = os.path.join(lib.CACHE, "titled.mp4")
    print("Titles and final H.264:")
    compose.compose(
        sequence, title_plan, silent, total,
        fade_out=float(video_config.get("fade_out", 0.5)),
        crf=int(video_config.get("crf", 18)),
        preset=str(video_config.get("preset", "slow")),
    )

    output_name = output_override or config["output"]
    if os.path.basename(output_name) != output_name or not output_name.lower().endswith(".mp4"):
        raise ValueError("--output must be an .mp4 filename without directories")
    output = os.path.join(lib.OUT, output_name)
    candidate = output + ".qa.mp4"
    audio = config["audio"]
    print("Audio: two-pass EBU R128")
    finalize.mux_audio(
        silent, os.path.join(lib.SRC, audio["src"]), audio, candidate, total,
    )

    stem = os.path.splitext(output_name)[0]
    contact = os.path.join(lib.OUT, f"{stem}_contact.jpg")
    qa.check_master(candidate, config, contact)
    os.replace(candidate, output)
    print(
        f"\nMASTER: {output}\nCONTACT: {contact}\n"
        f"finished in {time.time() - started:.0f}s"
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storyboard", default=os.path.join(ROOT, "storyboard.json"))
    parser.add_argument("--source-dir", help="media directory instead of source_dir in JSON")
    parser.add_argument("--output", help="final .mp4 filename")
    parser.add_argument("--inv", nargs="?", const="", help="inventory, optional date DD.MM.YY")
    parser.add_argument("--roll", nargs="?", const="", help="contact sheets, optional date DD.MM.YY")
    parser.add_argument("--check", action="store_true", help="validate inputs without rendering")
    parser.add_argument("--qa", metavar="MP4", help="validate an existing master")
    args = parser.parse_args()

    if (args.inv is not None or args.roll is not None) and not os.path.isfile(args.storyboard):
        lib.configure_video()
        lib.set_source_dir(args.source_dir or os.getcwd())
        if args.inv is not None:
            inventory(args.inv or None)
        else:
            contact_sheets(args.roll or None)
        return

    config = load_config(args.storyboard, args.source_dir)
    if args.inv is not None:
        inventory(args.inv or None)
    elif args.roll is not None:
        contact_sheets(args.roll or None)
    elif args.check:
        total = qa.validate_storyboard(config)
        plan = titles.render_plan(config.get("titles", []))
        titles.assert_safe(plan, config.get("safe_zone", {}))
        caps = lib.capabilities()
        hdr_path = "scale_vt" if caps["scale_vt"] else "zscale" if caps["zscale"] else "none"
        print(
            f"CHECK: PASS | {len(config['scenes'])} scenes | {total:.2f}s | "
            f"{lib.W}x{lib.H}/{lib.FPS} | HDR={hdr_path}"
        )
    elif args.qa:
        qa.check_master(os.path.abspath(args.qa), config)
    else:
        assemble(config, args.output)


if __name__ == "__main__":
    main()

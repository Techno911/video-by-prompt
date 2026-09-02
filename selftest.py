#!/usr/bin/env python3
"""Generate synthetic media and run the complete render pipeline."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(ROOT, ".selftest")
MEDIA = os.path.join(WORK, "media")
STORYBOARD = os.path.join(WORK, "storyboard.json")
OUTPUT = os.path.join(WORK, "out", "selftest_result.mp4")
CONTACT = os.path.join(WORK, "out", "selftest_result_contact.jpg")


def run(command: list[str], *, env: dict | None = None) -> None:
    result = subprocess.run(command, cwd=ROOT, text=True, env=env)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("SELFTEST: FAIL | ffmpeg is not installed")
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(MEDIA, exist_ok=True)
    source = os.path.join(MEDIA, "source.mp4")

    run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
        "-vf", "drawbox=x=mod(t*180\\,600):y=500:w=120:h=120:color=red@0.8:t=fill",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-colorspace", "bt709",
        "-color_primaries", "bt709", "-color_trc", "bt709",
        "-c:a", "aac", "-ar", "48000", "-shortest", source,
    ])

    storyboard = {
        "title": "Synthetic selftest",
        "source_dir": MEDIA,
        "output": os.path.basename(OUTPUT),
        "duration_seconds": 3.0,
        "video": {
            "width": 540, "height": 960, "fps": 30,
            "crf": 22, "preset": "veryfast", "fade_out": 0.1,
        },
        "safe_zone": {"top": 100, "bottom": 220, "side": 40},
        "audio": {
            "src": "source.mp4", "ss": 0.0, "target_lufs": -14.0,
            "true_peak": -1.5, "lra": 11.0, "fade_in": 0.1,
            "fade_out": 0.2, "bitrate": "128k",
        },
        "titles": [{
            "kind": "bottom_bar", "name": "test", "left": "VIDEO",
            "right": "SELFTEST", "start": 0.2, "end": 1.2,
        }],
        "scenes": [
            {"n": 1, "role": "HOOK", "src": "source.mp4", "ss": 0.0,
             "dur": 1.0, "look": "normal", "note": "synthetic motion"},
            {"n": 2, "role": "BODY", "src": "source.mp4", "ss": 2.0,
             "dur": 1.0, "look": "warm", "note": "synthetic motion"},
            {"n": 3, "role": "PEAK", "src": "source.mp4", "ss": 4.0,
             "dur": 1.0, "look": "night", "note": "synthetic motion"},
        ],
    }
    with open(STORYBOARD, "w", encoding="utf-8") as file:
        json.dump(storyboard, file, ensure_ascii=False, indent=2)

    try:
        environment = dict(os.environ)
        environment["STORIS_CACHE_DIR"] = os.path.join(WORK, "cache")
        environment["STORIS_OUT_DIR"] = os.path.join(WORK, "out")
        run(
            [sys.executable, os.path.join(ROOT, "storis.py"), "--storyboard", STORYBOARD],
            env=environment,
        )
        if not os.path.isfile(OUTPUT):
            raise SystemExit("SELFTEST: FAIL | master was not created")
        print("SELFTEST: PASS | synthetic master rendered and accepted")
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()

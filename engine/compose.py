"""Stream frames through Pillow and perform exactly one final H.264 encode."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from PIL import Image

import lib


@dataclass
class Overlay:
    image: Image.Image
    alpha: Image.Image
    xy: tuple[int, int]
    start_frame: int
    end_frame: int
    fade_in_frames: int
    fade_out_frames: int


def _prepare(spec: dict) -> Overlay:
    image = Image.open(spec["path"]).convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    if not bbox:
        raise ValueError(f"empty title: {spec['path']}")
    cropped = image.crop(bbox)
    return Overlay(
        image=cropped,
        alpha=cropped.getchannel("A"),
        xy=(bbox[0], bbox[1]),
        start_frame=int(round(float(spec["start"]) * lib.FPS)),
        end_frame=int(round(float(spec["end"]) * lib.FPS)),
        fade_in_frames=max(1, int(round(float(spec.get("fade_in", 0.28)) * lib.FPS))),
        fade_out_frames=max(1, int(round(float(spec.get("fade_out", 0.32)) * lib.FPS))),
    )


def _opacity(overlay: Overlay, frame_index: int) -> float:
    if frame_index < overlay.start_frame or frame_index >= overlay.end_frame:
        return 0.0
    into = frame_index - overlay.start_frame + 1
    remaining = overlay.end_frame - frame_index
    return min(1.0, into / overlay.fade_in_frames, remaining / overlay.fade_out_frames)


def _read_frame(stream, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def compose(
    video: str,
    title_specs: list[dict],
    output: str,
    total: float,
    *,
    fade_out: float = 0.5,
    crf: int = 18,
    preset: str = "slow",
) -> str:
    frame_count = int(round(total * lib.FPS))
    frame_size = lib.W * lib.H * 3
    overlays = [_prepare(spec) for spec in title_specs]
    black = Image.new("RGB", (lib.W, lib.H), "black")
    part = output + ".part.mp4"

    decoder_cmd = [
        lib.binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", video,
        "-map", "0:v:0", "-fps_mode", "passthrough", "-frames:v", str(frame_count),
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]
    encoder_cmd = [
        lib.binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-video_size", f"{lib.W}x{lib.H}",
        "-framerate", str(lib.FPS), "-i", "pipe:0", "-frames:v", str(frame_count),
        "-fps_mode", "cfr", "-an", "-c:v", "libx264", "-preset", preset,
        "-crf", str(crf),
        "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited",
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-video_track_timescale", "90000", "-movflags", "+faststart", part,
    ]

    decoder = subprocess.Popen(decoder_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    encoder = subprocess.Popen(encoder_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    last_progress = -1
    try:
        assert decoder.stdout is not None
        assert encoder.stdin is not None
        for index in range(frame_count):
            raw = _read_frame(decoder.stdout, frame_size)
            if len(raw) != frame_size:
                detail = (decoder.stderr.read() if decoder.stderr else b"").decode(errors="replace")
                raise RuntimeError(
                    f"decoder stopped at frame {index}/{frame_count} "
                    f"({len(raw)}/{frame_size} bytes)\n{detail[-1500:]}"
                )
            frame = Image.frombytes("RGB", (lib.W, lib.H), raw)

            for overlay in overlays:
                opacity = _opacity(overlay, index)
                if opacity <= 0:
                    continue
                if opacity >= 0.999:
                    mask = overlay.alpha
                else:
                    table = [min(255, int(value * opacity)) for value in range(256)]
                    mask = overlay.alpha.point(table)
                frame.paste(overlay.image, overlay.xy, mask)

            fade_start = frame_count - int(round(fade_out * lib.FPS))
            if fade_out > 0 and index >= fade_start:
                fade = min(1.0, (index - fade_start + 1) / max(1, frame_count - fade_start))
                frame = Image.blend(frame, black, fade)

            encoder.stdin.write(frame.tobytes())
            progress = int((index + 1) * 10 / frame_count)
            if progress != last_progress:
                print(f"  titles: {progress * 10:3}%")
                last_progress = progress

        encoder.stdin.close()
        decoder.stdout.close()
        decoder_return = decoder.wait()
        encoder_return = encoder.wait()
        if decoder_return != 0:
            detail = (decoder.stderr.read() if decoder.stderr else b"").decode(errors="replace")
            raise RuntimeError("ffmpeg decoder failed:\n" + detail[-2000:])
        if encoder_return != 0:
            detail = (encoder.stderr.read() if encoder.stderr else b"").decode(errors="replace")
            raise RuntimeError("ffmpeg encoder failed:\n" + detail[-2000:])
    except Exception:
        decoder.kill()
        encoder.kill()
        if os.path.exists(part):
            os.remove(part)
        raise

    info = lib.probe(part)
    if info["frames"] != frame_count or abs(info["dur"] - total) > 1 / lib.FPS + 0.001:
        os.remove(part)
        raise RuntimeError(f"composite is not exact: {info['frames']}f, {info['dur']}s")
    os.replace(part, output)
    return output

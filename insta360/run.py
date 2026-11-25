"""Подготовка исходников X5 и сборка вертикального видео по плану агента."""
import argparse
from hashlib import sha256
import io
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
ASSET = "https://github.com/rgryta/insta360-stitch/releases/download/v0.0.3/insta360-stitch-v0.0.3-aarch64-apple-darwin.tar.gz"
DIGEST = "d1cbc9a4c2940117f947f89fce4c652820042cc48b5ae9926ec6d8349143d9af"
FPS = 30

def run(*args, cwd=None):
    subprocess.run([str(a) for a in args], cwd=cwd, check=True)

def probe(path):
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)
    ]))

def atomic_json(path, data):
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    pending.replace(path)

def tools():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("Этот выпуск X5 проверяется на Mac с Apple Silicon.")
    if not all(shutil.which(name) for name in ("ffmpeg", "ffprobe")):
        brew = shutil.which("brew")
        if not brew:
            raise RuntimeError("Для установки FFmpeg нужен настроенный Homebrew.")
        run(brew, "install", "ffmpeg")

def bootstrap(work):
    if not work.is_dir():
        work.mkdir(parents=True)
    archive = work / "insta360-stitch.tar.gz"
    if not archive.is_file():
        with urlopen(ASSET, timeout=65) as response:
            payload = response.read(8_000_000)
        if sha256(payload).hexdigest() != DIGEST:
            raise RuntimeError("Скачанный архив insta360-stitch не совпал с опубликованной контрольной суммой.")
        pending = archive.with_suffix(".tmp")
        pending.write_bytes(payload)
        pending.replace(archive)
        print("Скачан и проверен insta360-stitch v0.0.3", flush=True)
    payload = archive.read_bytes()
    if sha256(payload).hexdigest() != DIGEST:
        raise RuntimeError("Не совпала контрольная сумма локального архива инструментов.")
    binary = work / "insta360-stitch"
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as package:
        member = package.extractfile(package.getmember("insta360-stitch"))
        if member is None:
            raise RuntimeError("В архиве отсутствует исполняемый файл.")
        pending = binary.with_suffix(".tmp")
        pending.write_bytes(member.read())
        pending.chmod(0o700)
        pending.replace(binary)
    return binary

def prepare(source, job):
    files = sorted(f.resolve() for f in source.iterdir() if f.is_file() and f.suffix.lower() == ".insv")
    if not files:
        raise ValueError("В папке нет оригиналов .insv.")
    tools()
    binary = bootstrap(job / ".tools")
    manifest_path = job / "sources.json"
    saved = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    signature = [{"id": i, "source": str(f), "bytes": f.stat().st_size, "mtime": f.stat().st_mtime_ns}
                 for i, f in enumerate(files, 1)]
    if saved and [{k: row[k] for k in signature[0]} for row in saved] != signature:
        raise ValueError("Состав исходников изменился. Для новой съёмки используй другую рабочую папку.")
    manifest = []
    for data, file in zip(signature, files):
        number = data["id"]
        pano = job / f"pano-{number:03d}.mp4"
        if not pano.is_file():
            print(f"Склейка {number}/{len(files)}. Оригинал: {file.name}", flush=True)
            metadata = json.loads(subprocess.check_output([str(binary), "info", str(file)]))
            if not metadata.get("stitchable"):
                raise ValueError(f"Для склейки не хватает данных: {file.name}")
            part = pano.with_suffix(".part.mp4")
            run(binary, "video", "--width", "2880", "--threads", "4", "--stabilization", "follow",
                "--encoder", "hevc_videotoolbox", "--encoder-arg=-b:v", "--encoder-arg=30M",
                "--hwaccel", "videotoolbox", "--no-seam-flow",
                "--ffmpeg", shutil.which("ffmpeg"), "--ffprobe", shutil.which("ffprobe"), file, part)
            streams = probe(part)["streams"]
            if not any(s.get("width") == 2880 and s.get("height") == 1440 for s in streams):
                raise RuntimeError("Склейка не дала панораму 2:1.")
            part.replace(pano)
        info = probe(pano)
        duration = float(info["format"]["duration"])
        pages = job / f"frames-{number:03d}"
        pages.mkdir(exist_ok=True)
        run("ffmpeg", "-nostdin", "-v", "error", "-y", "-i", pano, "-an", "-filter_threads", "2",
            "-vf", "fps=1:start_time=0,scale=288:-2,tile=4x4:padding=4:margin=4:color=black",
            "-q:v", "3", pages / "%03d.jpg")
        manifest.append({**data, "panorama": pano.name, "seconds": duration, "sheets": pages.name})
    atomic_json(manifest_path, manifest)
    print(f"Готовы панорамы и контактные листы: {manifest_path}", flush=True)

def arc(start, stop, fraction):
    return (start + ((stop - start + 180) % 360 - 180)*fraction + 180) % 360 - 180

def validate_plan(scenes, lengths):
    if not isinstance(scenes, list) or not scenes or len(scenes) > 24:
        raise ValueError("Нужен непустой план из 1–24 сцен.")
    if sum(scene["frames"] for scene in scenes) > 180*FPS:
        raise ValueError("План превышает длительность 180 секунд.")
    for scene in scenes:
        if scene["source"] not in lengths:
            raise ValueError("В плане неизвестный исходник.")
        length = scene["frames"]/FPS
        if scene["frames"] < FPS or scene["start"] < 0 or scene["start"] + length > lengths[scene["source"]]:
            raise ValueError("Кадры выходят за длительность исходника или план короче секунды.")
        keys = scene["keys"]
        if len(keys) < 2 or keys[0][0] != 0 or abs(keys[-1][0] - length) > .001:
            raise ValueError("Дай ключи камеры от нуля до конца сцены.")
        if any(not (all(math.isfinite(v) for v in key) and -180 <= key[1] <= 180 and -85 <= key[2] <= 85)
               for key in keys) or any(a[0] >= b[0] for a,b in zip(keys, keys[1:])):
            raise ValueError("Некорректная последовательность ключей камеры.")
    return scenes

def camera_commands(keys, frames):
    lines = []
    i = 0
    for number in range(frames):
        t = number/FPS
        while i + 2 < len(keys) and t >= keys[i+1][0]:
            i += 1
        start, yaw0, pitch0 = keys[i]
        stop, yaw1, pitch1 = keys[i+1]
        fraction = (t - start)/(stop - start)
        yaw = arc(yaw0, yaw1, fraction)
        pitch = pitch0 + (pitch1 - pitch0)*fraction
        lines.append(f"{t:.7f} v360 yaw {yaw:.4f}, v360 pitch {pitch:.4f};")
    return "\n".join(lines)+"\n"

def projection_filter(yaw, pitch, width, height):
    return (f"v360=input=equirect:output=flat:w={width}:h={height}:"
            f"v_fov=85:h_fov=55:yaw={yaw}:pitch={pitch}:reset_rot=1,setsar=1")

def reframe_filter(yaw, pitch, width=1080, height=1920):
    # Ключи содержат абсолютные углы. Без reset_rot v360 накапливает повороты.
    return f"fps=30,sendcmd=f=camera.txt,{projection_filter(yaw, pitch, width, height)}"

def read_sources(job):
    return {row["id"]: row for row in json.loads((job / "sources.json").read_text())}

def preview(job, source, second, x, y):
    row = read_sources(job).get(source)
    if row is None:
        raise ValueError("Номер исходника отсутствует в sources.json.")
    if not 0 <= second < row["seconds"]:
        raise ValueError("Метка времени за пределами длительности исходника.")
    if not (0 <= x <= 1 and 0 <= y <= 1):
        raise ValueError("Координаты героя x и y задаются долями от 0 до 1.")
    yaw = round(360*x - 180, 4)
    pitch = round(max(-85, min(85, 90 - 180*y)), 4)
    target = job / f"preview-{source:03d}-{second:.3f}-{x:.4f}-{y:.4f}.jpg"
    image = subprocess.check_output([
        "ffmpeg", "-nostdin", "-v", "error", "-ss", str(second), "-i", str(job / row["panorama"]),
        "-frames:v", "1", "-vf", projection_filter(yaw, pitch, 432, 768),
        "-q:v", "2", "-f", "image2pipe", "-c:v", "mjpeg", "-",
    ])
    if not image:
        raise ValueError("Кадр недоступен. Попробуй метку времени раньше на несколько кадров.")
    target.write_bytes(image)
    print(json.dumps({"frame": str(target), "yaw": yaw, "pitch": pitch}, ensure_ascii=False))

def render(job, plan):
    tools()
    manifest = read_sources(job)
    scenes = validate_plan(json.loads(plan.read_text())["scenes"], {k:v["seconds"] for k,v in manifest.items()})
    material = job/"clips"
    material.mkdir(exist_ok=True)
    total = sum(s["frames"] for s in scenes)/FPS
    for number, scene in enumerate(scenes, 1):
        print(f"Рефрейминг {number}/{len(scenes)}", flush=True)
        camera = material/"camera.txt"
        camera.write_text(camera_commands(scene["keys"], scene["frames"]))
        source = job/manifest[scene["source"]]["panorama"]
        yaw, pitch = scene["keys"][0][1:]
        vf = reframe_filter(yaw, pitch)
        run("ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", scene["start"], "-i", source,
            "-frames:v", scene["frames"], "-filter_threads", "4", "-vf", vf, "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            material/f"shot-{number:02d}.mp4", cwd=material)

    first = manifest[scenes[0]["source"]]
    original = job/first["panorama"]
    if not any(s.get("codec_type") == "audio" for s in probe(original)["streams"]):
        raise ValueError("В исходнике нет звука для этой сборки.")
    run("ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", scenes[0]["start"], "-i", original,
        "-map", "0:a:0", "-af", "acompressor=threshold=0.12:ratio=3:attack=8:release=150,apad",
        "-t", total+0.1, "-c:a", "pcm_s16le", material/"source.wav")
    storyboard = {"title":"Мой проезд", "source_dir":str(material), "output":"reel.mp4",
        "duration_seconds":total, "video":{"width":1080,"height":1920,"fps":FPS,"preset":"veryfast"},
        "audio":{"src":"source.wav", "target_lufs":-16, "true_peak":-2.2}, "titles":[],
        "scenes":[{"n":i,"role":"ACTION","src":f"shot-{i:02d}.mp4","ss":0,
                   "dur":s["frames"]/FPS,"look":"normal"} for i,s in enumerate(scenes,1)]}
    atomic_json(job/"storyboard.json",storyboard)
    venv = job/".python"
    python = venv/"bin/python"
    if not python.is_file():
        run(sys.executable,"-m","venv",venv)
    pillow = subprocess.run([str(python),"-c","import PIL"],capture_output=True)
    if pillow.returncode:
        run(python,"-m","pip","--disable-pip-version-check","install","-r",ROOT/"requirements.txt")
    env = dict(os.environ, STORIS_CACHE_DIR=str(job/"cache"), STORIS_OUT_DIR=str(job/"result"))
    subprocess.run([str(python),str(ROOT/"storis.py"),"--storyboard",str(job/"storyboard.json")],env=env,check=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    scan = sub.add_parser("prepare")
    scan.add_argument("source", type=Path)
    scan.add_argument("job", type=Path)
    frame = sub.add_parser("preview")
    frame.add_argument("job", type=Path)
    frame.add_argument("source", type=int)
    frame.add_argument("second", type=float)
    frame.add_argument("x", type=float)
    frame.add_argument("y", type=float)
    build = sub.add_parser("render")
    build.add_argument("job", type=Path)
    build.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "prepare":
            prepare(args.source.resolve(), args.job.resolve())
        elif args.operation == "preview":
            preview(args.job.resolve(), args.source, args.second, args.x, args.y)
        else:
            render(args.job.resolve(), args.plan.resolve())
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"X5: {error}\n")

if __name__ == "__main__":
    main()

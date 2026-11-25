import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from run import arc, camera_commands, reframe_filter, validate_plan


class PlanTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "Требуется FFmpeg")
    def test_preview_returns_angles_and_writes_a_frame(self):
        with TemporaryDirectory() as folder:
            job = Path(folder)
            panorama = job / "panorama.mp4"
            subprocess.run([
                "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                "testsrc2=s=320x160:r=30:d=1", "-c:v", "libx264", panorama,
            ], check=True)
            (job / "sources.json").write_text(json.dumps([
                {"id": 1, "panorama": panorama.name, "seconds": 1},
            ]))
            command = [sys.executable, str(Path(__file__).with_name("run.py")), "preview", folder, "1"]
            sample = subprocess.run([*command, "0.5", "0.75", "0.6"], capture_output=True, text=True)
            self.assertEqual(sample.returncode, 0, sample.stderr)
            angles = json.loads(sample.stdout)
            self.assertEqual((angles["yaw"], angles["pitch"]), (90, -18))
            frame = Path(angles["frame"])
            self.assertEqual(frame.parent, job.resolve())
            self.assertTrue(frame.read_bytes().startswith(b"\xff\xd8"))
            rejected = subprocess.run([*command, "1.5", "0.75", "0.6"], capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("длительности", rejected.stderr)

    def test_interpolation_crosses_seam_by_shortest_arc(self):
        self.assertEqual(arc(175, -175, .5), -180)

    @unittest.skipUnless(shutil.which("ffmpeg"), "Требуется FFmpeg")
    def test_camera_keys_are_absolute_yaw_and_pitch(self):
        def frame_digest(filters, folder):
            return subprocess.check_output([
                "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                "testsrc2=s=320x160:r=30:d=1", "-filter_threads", "2", "-vf",
                f"{filters},select=eq(n\\,15)", "-frames:v", "1", "-f", "hash", "-",
            ], cwd=folder)

        with TemporaryDirectory() as folder:
            keys = [[0, 140, -10], [1, 100, -24]]
            (Path(folder) / "camera.txt").write_text(camera_commands(keys, 30))
            moving = frame_digest(reframe_filter(140, -10, 108, 192), folder)
            stationary = frame_digest(
                "v360=input=equirect:output=flat:w=108:h=192:v_fov=85:h_fov=55:yaw=120:pitch=-17,setsar=1",
                folder,
            )
        self.assertEqual(moving, stationary)

    def test_plan_requires_tracking_and_respects_source_duration(self):
        sample = [{"source": 1, "start": .5, "frames": 30, "keys": [[0, 175, 0], [1, -175, -5]]}]
        self.assertEqual(validate_plan(sample, {1: 2}), sample)
        with self.assertRaisesRegex(ValueError, "длительност"):
            validate_plan(sample, {1: .6})
        with self.assertRaisesRegex(ValueError, "ключ"):
            validate_plan([{**sample[0], "keys": [[0, 0, 0]]}], {1: 2})
        with self.assertRaisesRegex(ValueError, "исходник"):
            validate_plan(sample, {2: 2})


if __name__ == "__main__":
    unittest.main()

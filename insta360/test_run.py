import unittest
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from run import arc, camera_commands, reframe_filter, validate_plan


class PlanTest(unittest.TestCase):
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

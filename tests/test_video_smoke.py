import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from video_generator.config import VideoRenderConfig, Overlay, TextLayer
from video_generator.models import TrackAnalysis, Section, QuietRegion, Drop
from video_generator.media import probe
from video_generator.renderer import Clip, build_command, run_render


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
class SmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.video = cls.root / "source.mp4"
        cls.video2 = cls.root / "source2.mp4"
        cls.audio = cls.root / "beat.wav"

        def ffmpeg(args):
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + args,
                check=True,
            )

        ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "color=c=white:s=160x90:r=24",
                "-t",
                "0.4",
                "-c:v",
                "libx264",
                str(cls.video),
            ]
        )
        ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=90x160:r=24",
                "-t",
                "0.6",
                "-c:v",
                "libx264",
                str(cls.video2),
            ]
        )
        ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:sample_rate=48000",
                "-t",
                "2",
                "-c:a",
                "pcm_s16le",
                str(cls.audio),
            ]
        )
        cls.alpha = cls.root / "alpha.png"
        Image.new("RGBA", (64, 32), (255, 0, 0, 128)).save(cls.alpha)
        cls.screen = cls.root / "screen.jpg"
        Image.new("RGB", (64, 32), "black").save(cls.screen)
        cls.analysis = TrackAnalysis(
            2,
            120,
            beats=[0, 0.5, 1, 1.5],
            bars=[0],
            sections=[Section(0, 1, "Intro"), Section(1, 2, "Drop")],
            quiet_regions=[QuietRegion(0.5, 0.9, 1, manual=True)],
            drops=[Drop(1, 0.9, 0.1, 0.8)],
        ).to_dict()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def render(self, name, cfg, clips=None, preview=None):
        output = self.root / (name + ".mp4")
        work = self.root / name
        work.mkdir(exist_ok=True)
        cmd = build_command(
            self.audio,
            clips or [Clip(str(self.video), 0, 2)],
            cfg,
            self.analysis,
            output,
            work,
            42,
            preview,
            {"artist": "WUTSHY", "title": "Test"},
        )
        asyncio.run(run_render(cmd, work / "render.log", asyncio.Event()))
        info = probe(output, "video")
        audio = probe(output, "audio")
        self.assertEqual((info["width"], info["height"]), (cmd.width, cmd.height))
        self.assertEqual(info["codec"], "h264")
        self.assertEqual(audio["codec"], "aac")
        self.assertAlmostEqual(audio["duration"], cmd.duration, delta=0.08)
        self.assertAlmostEqual(info["duration"], cmd.duration, delta=0.09)
        self.assertEqual(audio["tags"].get("sample_rate", 48000), 48000)
        return output, cmd

    def cfg(self):
        cfg = VideoRenderConfig()
        cfg.canvas.width = 640
        cfg.canvas.height = 360
        cfg.foreground.width = 360
        cfg.foreground.height = 360
        cfg.export.preset = "ultrafast"
        cfg.beat_reaction.enabled = False
        return cfg

    def test_ambient_alpha_screen_and_blackout(self):
        cfg = self.cfg()
        cfg.overlays = [
            Overlay(source=str(self.alpha)),
            Overlay(source=str(self.screen), blend="Screen"),
        ]
        cfg.branding = [TextLayer(text="{artist} {title} {bpm}", size=18)]
        output, cmd = self.render("ambient", cfg)
        self.assertIn("all_mode=screen", cmd.graph)
        self.assertIn("[comp1]", cmd.graph)

        def frame(time):
            raw = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    str(time),
                    "-i",
                    str(output),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=1:1",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "pipe:1",
                ]
            )
            return sum(raw) / 3

        self.assertLess(frame(0.7), frame(0.2) * 0.3)
        self.assertGreater(frame(1.2), frame(0.7) * 3)

    def test_black_sides(self):
        cfg = self.cfg()
        cfg.background.mode = "Black"
        cfg.blackout.enabled = False
        output, _ = self.render("black", cfg)
        raw = subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(output),
                "-frames:v",
                "1",
                "-vf",
                "crop=100:100:0:0,scale=1:1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ]
        )
        self.assertLess(sum(raw), 10)

    def test_sequence_transition(self):
        cfg = self.cfg()
        cfg.blackout.enabled = False
        clips = [
            Clip(str(self.video), 0, 1),
            Clip(str(self.video2), 1, 2, transition="Crossfade"),
        ]
        output, cmd = self.render("sequence", cfg, clips)
        self.assertIn("xfade=", cmd.graph)
        self.assertEqual(probe(output, "video")["fps"], 24)

    def test_background_image_and_preview(self):
        cfg = self.cfg()
        cfg.background.mode = "Ambient Image"
        cfg.background.media = str(self.alpha)
        cfg.canvas.guides = True
        output, cmd = self.render("preview", cfg, preview=[0.5, 1.5])
        self.assertIn("drawbox=", cmd.graph)
        self.assertAlmostEqual(cmd.duration, 1)

    def test_cancel_terminates(self):
        cfg = VideoRenderConfig()
        work = self.root / "cancel"
        work.mkdir()
        cmd = build_command(
            self.audio,
            [Clip(str(self.video), 0, 2)],
            cfg,
            self.analysis,
            self.root / "cancel.mp4",
            work,
        )

        async def cancel_render():
            event = asyncio.Event()
            event.set()
            with self.assertRaises(asyncio.CancelledError):
                await run_render(cmd, work / "render.log", event)

        asyncio.run(cancel_render())
